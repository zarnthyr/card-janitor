# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from anki.collection import Collection
from aqt.qt import QApplication, QPlainTextEdit, Qt, QWidget
from card_janitor import conflict_dialog, policy_editor, ui
from card_janitor.action_row import ActionRow
from card_janitor.actions import ConflictDetail
from card_janitor.condition_row import ConditionRow
from card_janitor.configuration import DEFAULT_CONFIG
from card_janitor.deck_picker import DeckPicker
from card_janitor.engine import CardFacts, ResolvedAction, evaluate_facts
from card_janitor.line_numbers import LineNumberArea
from card_janitor.models import (
    AgeCondition,
    CardStateCondition,
    DeckSelector,
    MoveAction,
    PolicyRecord,
    SiblingReviewHistoryCondition,
    SuspensionCondition,
    TagCondition,
    parse_config,
    parse_policy,
    policy_to_dict,
)
from card_janitor.note_type_picker import NoteTypePicker

pytestmark = pytest.mark.usefixtures("_application")


def test_json_line_numbers_resize_and_scroll() -> None:
    application = QApplication.instance()
    editor = QPlainTextEdit()
    editor.resize(400, 200)
    editor.show()
    application.processEvents()
    # Anki's bulk ConfigEditor shows itself before we attach the gutter.
    gutter = LineNumberArea(editor)
    application.processEvents()
    assert gutter.isVisible()
    initial_width = gutter.width()
    editor.setPlainText("\n".join(str(number) for number in range(120)))
    application.processEvents()
    assert gutter.width() > initial_width
    assert editor.viewport().geometry().left() == gutter.geometry().right() + 1
    editor.verticalScrollBar().setValue(60)
    application.processEvents()
    assert editor.firstVisibleBlock().blockNumber() > 0
    assert not gutter.grab().isNull()
    font = editor.font()
    font.setPointSize(font.pointSize() + 4)
    previous_width = gutter.width()
    editor.setFont(font)
    editor.resize(500, 300)
    application.processEvents()
    assert gutter.width() > previous_width
    assert gutter.height() == editor.contentsRect().height()
    editor.setPlainText("{}")
    application.processEvents()
    assert gutter.width() < previous_width
    editor.close()


@pytest.fixture(scope="module")
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_leaf_cycle_preserves_future_subdeck_choice() -> None:
    parent = QWidget()
    picker = DeckPicker(["Mining"], (), parent)
    item = picker._items["Mining"]
    picker._clicked(item, 0)
    assert picker.selectors() == (DeckSelector("Mining", include_subdecks=True),)
    assert item.checkState(0) == Qt.CheckState.Checked
    picker._clicked(item, 0)
    assert picker.selectors() == (DeckSelector("Mining", include_subdecks=False),)
    assert item.checkState(0) == Qt.CheckState.PartiallyChecked
    picker._clicked(item, 0)
    assert picker.selectors() == ()


def test_child_change_splits_recursive_ancestor() -> None:
    parent = QWidget()
    picker = DeckPicker(
        ["Mining", "Mining::A", "Mining::B"],
        (DeckSelector("Mining", include_subdecks=True),),
        parent,
    )
    picker._clicked(picker._items["Mining::A"], 0)
    assert picker.selectors() == (
        DeckSelector("Mining", include_subdecks=False),
        DeckSelector("Mining::A", include_subdecks=False),
        DeckSelector("Mining::B", include_subdecks=True),
    )
    assert not picker._state.recursive("Mining::Future")
    assert picker._state.recursive("Mining::B::Future")
    picker._clicked(picker._items["Mining::A"], 0)
    picker._clicked(picker._items["Mining::A"], 0)
    assert picker.selectors() == (DeckSelector("Mining", include_subdecks=True),)


def test_all_decks_and_explicit_roots_are_distinct() -> None:
    parent = QWidget()
    picker = DeckPicker(["A", "B"], (), parent, all_decks=True)
    assert picker._state.recursive("Future")
    picker._clicked(picker._items["A"], 0)
    assert not picker.all_decks
    assert not picker._state.recursive("Future")
    assert picker.selectors() == (
        DeckSelector("A", include_subdecks=False),
        DeckSelector("B", include_subdecks=True),
    )
    picker._clicked(picker._root, 0)
    assert picker.all_decks
    assert picker.selectors() == ()


def test_all_note_types_is_not_all_current_types() -> None:
    parent = QWidget()
    picker = NoteTypePicker(["Basic", "Cloze"], None, parent)
    picker._clicked(picker._items["Basic"], 0)
    assert picker.selected() == ("Cloze",)
    picker._clicked(picker._items["Basic"], 0)
    assert picker.selected() == ("Basic", "Cloze")
    assert picker._root.checkState(0) == Qt.CheckState.PartiallyChecked
    picker._clicked(picker._root, 0)
    assert picker.selected() is None


@pytest.mark.parametrize(
    "condition",
    [
        AgeCondition(100000, "card_created", "lte"),
        CardStateCondition(("new", "relearning")),
        TagCondition(("leech", "retired"), "contains_all"),
        SuspensionCondition("is_suspended"),
        SiblingReviewHistoryCondition("none"),
    ],
)
def test_extracted_condition_row_round_trip(condition: object) -> None:
    row = ConditionRow(condition)
    assert row.condition() == condition


@pytest.mark.parametrize(
    "kind",
    [
        "add_tags",
        "remove_tags",
        "replace_tags",
        "suspend",
        "unsuspend_note",
        "move_note",
        "delete_card",
        "delete_note",
    ],
)
def test_extracted_action_row_preserves_kind(kind: str) -> None:
    row = ActionRow(["Mining"], kind=kind, tags=("leech",), deck="Mining")
    assert row.action_kind() == kind
    assert row.actions()


def test_extracted_policy_editor_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = Collection(str(tmp_path / "editor.anki2"))
    try:
        collection.decks.id("Mining")
        monkeypatch.setattr(policy_editor, "mw", SimpleNamespace(col=collection))
        raw = {
            "id": "editor",
            "name": "Editor",
            "mode": "on_demand",
            "scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]},
            "match": "all",
            "conditions": [{"type": "tags", "operator": "contains_any", "tags": ["leech"]}],
            "actions": [{"type": "remove_tags", "tags": ["leech"]}],
        }
        policy = parse_policy(raw)
        parent = QWidget()
        editor = policy_editor.PolicyEditorDialog(PolicyRecord(0, raw, policy, ()), set(), parent)
        assert editor._policy_from_form() == policy
        assert not editor.isModal()
        editor.close()
    finally:
        collection.close()


def test_conflict_summary_opens_modeless_details_and_browses_skipped_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = QWidget()
    parent.col = SimpleNamespace()
    monkeypatch.setattr(ui, "mw", parent)
    raw = [
        {
            "id": name,
            "name": name,
            "mode": "on_demand",
            "scope": {"all_decks": True},
            "match": "all",
            "conditions": [{"type": "all_cards"}],
            "actions": [{"type": "move", "deck": name}],
        }
        for name in ("A", "B")
    ]
    parsed = parse_config({**DEFAULT_CONFIG, "policies": raw})
    assert not parsed.issues
    card = CardFacts(1, 10, 1, 0, 2, 2, 100, 1000, None, frozenset())
    reports = tuple(
        evaluate_facts(policy, [card], {1}, (ResolvedAction(MoveAction(policy.name), index + 2),))
        for index, policy in enumerate(parsed.config.policies)
    )
    dashboard = ui.CardJanitorDialog(parsed, reports)
    summary_panel = dashboard.summary.parentWidget()
    assert dashboard.conflict_summary.parentWidget() is summary_panel
    assert summary_panel.layout().spacing() == 0
    assert summary_panel.layout().contentsMargins().top() == 0
    assert summary_panel.layout().contentsMargins().bottom() == 0
    assert 'href="conflicts"' in dashboard.conflict_summary.text()
    assert "0 cards would be cleaned up" in dashboard.summary.text()
    dashboard.conflict_summary.linkActivated.emit("conflicts")
    details = dashboard._conflict_dialog
    assert details is not None
    assert not details.isModal()
    assert details.table.item(0, 1).text() == "A\nB"
    assert details.table.item(0, 2).text() == "A: Move cards to 'A'\nB: Move cards to 'B'"
    assert details.table.item(0, 3).text() == "Move actions specify different destination decks"
    browsed = []
    monkeypatch.setattr(conflict_dialog, "open_cards_in_browser", browsed.append)
    details.browse_button.click()
    assert browsed == [{1}]
    monkeypatch.setattr(ui, "open_cards_in_browser", lambda ids: browsed.append(ids) or QWidget())
    dashboard._view_included()
    assert browsed == [{1}, {1}]
    dashboard.table.item(1, dashboard.COLUMN_RUN).setCheckState(Qt.CheckState.Unchecked)
    assert dashboard._conflict_dialog is None
    assert dashboard.conflict_summary.isHidden()
    assert "1 card would be cleaned up" in dashboard.summary.text()
    dashboard.close()


@pytest.fixture
def edit_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    collection = Collection(str(tmp_path / "policy-ux.anki2"))
    collection.decks.id("Mining")
    parent = QWidget()
    parent.col = collection
    monkeypatch.setattr(policy_editor, "mw", parent)
    monkeypatch.setattr(ui, "mw", parent)
    raw = {
        "id": "original",
        "name": "Leeches",
        "mode": "on_demand",
        "scope": {"decks": [{"deck": "Mining", "include_subdecks": True}]},
        "match": "all",
        "conditions": [{"type": "tags", "tags": ["leech"], "operator": "contains_any"}],
        "actions": [{"type": "remove_tags", "tags": ["leech"]}],
    }
    policy = parse_policy(raw)
    yield SimpleNamespace(parent=parent, collection=collection, raw=raw, policy=policy)
    parent.deleteLater()
    collection.close()


def test_policy_json_round_trip_unsaved_form_and_managed_id(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor.name.setText("Changed name")
    editor._toggle_json()
    raw = json.loads(editor.json_text.toPlainText())
    assert raw["name"] == "Changed name"
    raw["id"] = "copied-policy-id"
    raw["scope"] = {"all_decks": True, "note_types": ["Basic"]}
    raw["conditions"] = [{"type": "all_cards"}]
    raw["actions"] = [{"type": "replace_tags", "tags": []}]
    editor.json_text.setPlainText(json.dumps(raw))
    editor.save_button.click()
    assert editor.result_policy is None
    assert not editor._json_mode
    assert editor.save_button.text() == "Save"
    result = editor._policy_from_form()
    assert result.id == edit_fixture.policy.id
    assert result.scope.all_decks
    assert result.scope.note_types == ("Basic",)
    assert policy_to_dict(result)["actions"] == [{"type": "replace_tags", "tags": []}]
    assert editor._has_unsaved_changes()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.close()


def test_new_policy_json_can_start_from_incomplete_form(edit_fixture: SimpleNamespace) -> None:
    editor = policy_editor.PolicyEditorDialog(None, {"original"}, edit_fixture.parent)
    editor._toggle_json()
    assert editor._json_mode
    raw = dict(edit_fixture.raw)
    editor.json_text.setPlainText(json.dumps(raw))
    result = editor._policy_from_form()
    assert result is not None
    assert result.id != "original"
    editor._accept()
    assert editor.result_policy is None
    assert not editor._json_mode
    editor._accept()
    assert editor.result_policy == result


def test_invalid_json_keeps_json_editor_and_form_unchanged(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = []
    monkeypatch.setattr(policy_editor, "showWarning", lambda text, **_kwargs: warnings.append(text))
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor._toggle_json()
    editor.json_text.setPlainText("{")
    editor._accept()
    assert editor._json_mode
    assert editor.name.text() == "Leeches"
    assert editor._policy_from_form() is None
    assert warnings


def test_json_cancel_returns_to_incomplete_form_without_validation(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = []
    prompts = []
    monkeypatch.setattr(policy_editor, "showWarning", lambda text, **_kwargs: warnings.append(text))
    monkeypatch.setattr(
        policy_editor, "askUser", lambda *args, **_kwargs: prompts.append(args) or False
    )
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    initial = editor._form_payload()
    editor.json_button.click()
    assert editor.save_button.text() == "Apply"
    assert editor.json_button.isHidden()
    editor.cancel_button.click()
    assert not editor._json_mode
    assert editor.isVisible()
    assert editor._form_payload() == initial
    assert not warnings
    assert not prompts
    editor.reject()


def test_json_cancel_discards_only_json_and_preserves_unsaved_form(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor.name.setText("Unsaved form name")
    initial = editor._form_payload()
    editor._toggle_json()
    editor.json_text.setPlainText("{")
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: False)
    editor.reject()
    assert editor._json_mode
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()
    assert not editor._json_mode
    assert editor._form_payload() == initial
    assert editor._has_unsaved_changes()
    editor.reject()


def test_unsaved_close_can_be_cancelled_or_confirmed(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    editor.name.setText("Unsaved")
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: False)
    assert not editor.close()
    assert editor.isVisible()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    assert editor.close()
    assert not editor.isVisible()


def test_duplicate_opens_draft_with_fresh_id_and_does_not_save(
    edit_fixture: SimpleNamespace,
) -> None:
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw]})
    dashboard = ui.CardJanitorDialog(parsed, ())
    dashboard.duplicate_button.click()
    editor = dashboard._policy_editor
    assert editor is not None
    assert editor._record is None
    duplicate = editor._policy_from_form()
    assert duplicate == replace(edit_fixture.policy, id=duplicate.id, name="Leeches (copy)")
    assert duplicate.id != edit_fixture.policy.id
    assert not editor._has_unsaved_changes()
    editor.reject()
    dashboard.close()


def test_conflict_browse_respects_selected_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = QWidget()
    dialog = conflict_dialog.ConflictDialog(
        tuple(ConflictDetail(card_id, ("Policy",), ("Reason",)) for card_id in (1, 2)), parent, ()
    )
    browsed = []
    monkeypatch.setattr(conflict_dialog, "open_cards_in_browser", browsed.append)
    dialog.browse_button.click()
    dialog.table.selectRow(1)
    assert dialog.browse_button.text() == "Browse selected"
    dialog.browse_button.click()
    assert browsed == [{1, 2}, {2}]
    dialog.close()
