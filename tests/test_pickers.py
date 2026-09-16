# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from anki.collection import Collection
from aqt.qt import QApplication, Qt, QWidget
from card_janitor import conflict_dialog, policy_editor, ui
from card_janitor.action_row import ActionRow
from card_janitor.condition_row import ConditionRow
from card_janitor.configuration import DEFAULT_CONFIG
from card_janitor.deck_picker import DeckPicker
from card_janitor.engine import CardFacts, ResolvedAction, evaluate_facts
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
)
from card_janitor.note_type_picker import NoteTypePicker

pytestmark = pytest.mark.usefixtures("_application")


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
    assert details.table.item(0, 2).text() == "Move actions specify different destination decks"
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
