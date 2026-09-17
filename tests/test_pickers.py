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
from aqt.qt import QApplication, QPlainTextEdit, QPoint, Qt, QWidget
from card_janitor import conflict_dialog, policy_editor, ui
from card_janitor.action_row import ActionRow
from card_janitor.actions import ConflictDetail
from card_janitor.condition_row import CardStatePicker, ConditionRow
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
    TagAction,
    TagCondition,
    parse_config,
    parse_policy,
    policy_to_dict,
)
from card_janitor.note_type_picker import NoteTypePicker
from card_janitor.presentation import LAST_CLEANUP_KEY, last_cleanup, record_cleanup
from PyQt6.QtTest import QTest

pytestmark = pytest.mark.usefixtures("_application")


def test_last_cleanup_status_is_local_and_clickable(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw]})
    dashboard = ui.CardJanitorDialog(parsed, ())
    dashboard.show()
    QApplication.processEvents()
    assert dashboard.cleanup_status.isHidden()
    record_cleanup(edit_fixture.parent.pm.profile, automatic=False, affected_cards=3, conflicts=2)
    dashboard._update_cleanup_status()
    assert not dashboard.cleanup_status.isHidden()
    assert "3 cards cleaned up; 2 skipped due to conflicts." in dashboard.cleanup_status.text()
    shown = []
    monkeypatch.setattr(ui, "showText", lambda text, **_kwargs: shown.append(text))
    dashboard.cleanup_status.linkActivated.emit("cleanup")
    assert "Manual" in shown[0]
    assert "<b>Cards skipped</b>" in shown[0]
    assert '<td valign="top">2</td>' in shown[0]
    assert "<b>Triggers</b>" not in shown[0]
    assert LAST_CLEANUP_KEY not in DEFAULT_CONFIG
    edit_fixture.parent.pm.profile = {}
    dashboard._update_cleanup_status()
    assert dashboard.cleanup_status.isHidden()
    dashboard.close()
    assert not dashboard._cleanup_status_timer.isActive()


def test_last_cleanup_failure_does_not_claim_zero_changes() -> None:
    profile = {}
    record_cleanup(profile, affected_cards=None, failure="Partial cleanup failed")
    summary, details = last_cleanup(profile)
    assert "failed" in summary
    assert "Unknown; some changes may have been applied" in details
    assert "Partial cleanup failed" in details
    assert "Automatic" in details


def test_cleanup_details_escape_names_and_failure_text() -> None:
    profile = {}
    record_cleanup(profile, policies=("<b>Policy</b>", "Second"), failure="Bad <tag>\nNext line")
    _, details = last_cleanup(profile)
    assert "&lt;b&gt;Policy&lt;/b&gt;<br>Second" in details
    assert "Bad &lt;tag&gt;<br>Next line" in details
    assert "<th>" not in details


def test_changed_policy_definitions_recompute_dashboard_columns(
    edit_fixture: SimpleNamespace,
) -> None:
    original = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw]})
    updated = parse_config(
        {**DEFAULT_CONFIG, "policies": [{**edit_fixture.raw, "triggers": [{"type": "on_sync"}]}]}
    )
    dashboard = ui.CardJanitorDialog(original, ())
    dashboard.show()
    QApplication.processEvents()
    dashboard.table.setColumnWidth(dashboard.COLUMN_POLICY, 60)
    dashboard.set_dashboard(updated, ())
    QApplication.processEvents()
    reopened = ui.CardJanitorDialog(updated, ())
    reopened.resize(dashboard.size())
    reopened.show()
    QApplication.processEvents()
    assert [
        dashboard.table.columnWidth(column) for column in range(dashboard.table.columnCount())
    ] == [reopened.table.columnWidth(column) for column in range(reopened.table.columnCount())]
    dashboard.table.setColumnWidth(dashboard.COLUMN_POLICY, 90)
    dashboard.set_dashboard(updated, ())
    QApplication.processEvents()
    assert dashboard.table.columnWidth(dashboard.COLUMN_POLICY) == 90
    dashboard.close()
    reopened.close()


@pytest.mark.parametrize("failed", [False, True])
def test_manual_cleanup_records_result(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    class Operation:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def success(self, callback: object) -> "Operation":
            self.applied = callback
            return self

        def failure(self, callback: object) -> "Operation":
            self.failed = callback
            return self

        def run_in_background(self) -> None:
            if failed:
                self.failed(RuntimeError("Manual cleanup failed"))
            else:
                self.applied(SimpleNamespace(affected_cards=4, conflicts=1))

    monkeypatch.setattr(ui, "CollectionOp", Operation)
    monkeypatch.setattr(ui, "tooltip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ui, "showWarning", lambda *_args, **_kwargs: None)
    report = SimpleNamespace(policy=edit_fixture.policy, actionable=(), errors=())
    ui.execute_on_demand_reports(SimpleNamespace(close=lambda: None), (report,))
    result = edit_fixture.parent.pm.profile[LAST_CLEANUP_KEY]
    assert result["automatic"] is False
    assert result["policies"] == [edit_fixture.policy.name]
    assert result["triggers"] == ["Manual"]
    assert result["affected_cards"] == (None if failed else 4)
    assert result["failure"] == ("Manual cleanup failed" if failed else "")


@pytest.mark.parametrize("value", [None, {}, {"time": []}, {"time": "not a date"}])
def test_last_cleanup_ignores_invalid_local_state(value: object) -> None:
    assert last_cleanup({LAST_CLEANUP_KEY: value}) is None


@pytest.mark.parametrize("change", ["profile", "collection"])
def test_manual_cleanup_ignores_stale_collection_context(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    captured = {}

    class Operation:
        def __init__(self, *, parent: object, op: object) -> None:
            captured["parent"] = parent
            captured["op"] = op

        def success(self, callback: object) -> "Operation":
            captured["success"] = callback
            return self

        def failure(self, callback: object) -> "Operation":
            captured["failure"] = callback
            return self

        def run_in_background(self) -> None:
            pass

    monkeypatch.setattr(ui, "CollectionOp", Operation)
    shown = []
    monkeypatch.setattr(ui, "tooltip", lambda *_args, **_kwargs: shown.append("tooltip"))
    monkeypatch.setattr(ui, "showWarning", lambda *_args, **_kwargs: shown.append("warning"))
    profile = edit_fixture.parent.pm.profile
    report = SimpleNamespace(policy=edit_fixture.policy, actionable=(), errors=())
    ui.execute_on_demand_reports(SimpleNamespace(close=lambda: None), (report,))
    if change == "profile":
        edit_fixture.parent.pm.profile = {}
    else:
        edit_fixture.parent.col = object()
    with pytest.raises(RuntimeError, match="collection changed"):
        captured["op"](edit_fixture.collection)
    captured["success"](SimpleNamespace(affected_cards=4, conflicts=0))
    captured["failure"](RuntimeError("stale"))
    assert not shown
    assert not profile
    assert LAST_CLEANUP_KEY not in edit_fixture.parent.pm.profile


def test_card_state_popup_uses_consistent_click_and_anchor_behavior() -> None:
    parent = QWidget()
    picker = CardStatePicker(parent)
    picker.resize(300, 30)
    parent.show()
    QApplication.processEvents()
    picker.showPopup()
    QApplication.processEvents()
    assert picker._menu.width() == picker.width()
    checkbox = picker._checkboxes["review"]
    QTest.mouseClick(
        checkbox,
        Qt.MouseButton.LeftButton,
        pos=QPoint(checkbox.width() - 2, checkbox.height() // 2),
    )
    assert picker.states() == ("new", "review")
    assert picker._menu.isVisible()
    QTest.mouseClick(picker._menu, Qt.MouseButton.LeftButton, pos=QPoint(0, 0))
    assert picker._menu.isVisible()
    QTest.keyClick(picker._menu, Qt.Key.Key_Escape)
    assert not picker._menu.isVisible()
    picker.showPopup()
    QApplication.processEvents()
    parent.move(parent.pos() + QPoint(10, 10))
    assert not picker._menu.isVisible()
    parent.close()


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
            "triggers": [],
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
    parent.pm = SimpleNamespace(profile={})
    monkeypatch.setattr(ui, "mw", parent)
    raw = [
        {
            "id": name,
            "name": name,
            "triggers": [],
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
    assert details.table.item(0, 2).text() == "A:\n  Move cards to 'A'\nB:\n  Move cards to 'B'"
    assert details.table.item(0, 3).text() == "Move actions specify different destination decks"
    browsed = []
    monkeypatch.setattr(
        conflict_dialog, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids)
    )
    details.browse_button.click()
    assert browsed == [{1}]
    monkeypatch.setattr(ui, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids))
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
    parent.pm = SimpleNamespace(profile={})
    monkeypatch.setattr(policy_editor, "mw", parent)
    monkeypatch.setattr(ui, "mw", parent)
    raw = {
        "id": "original",
        "name": "Leeches",
        "triggers": [],
        "scope": {"decks": [{"deck": "Mining", "include_subdecks": True}]},
        "match": "all",
        "conditions": [{"type": "tags", "tags": ["leech"], "operator": "contains_any"}],
        "actions": [{"type": "remove_tags", "tags": ["leech"]}],
    }
    policy = parse_policy(raw)
    yield SimpleNamespace(parent=parent, collection=collection, raw=raw, policy=policy)
    parent.deleteLater()
    collection.close()


def test_policy_trigger_picker_round_trip_and_fixed_height(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    assert not editor.triggers.selected()
    assert editor.triggers._none.isChecked()
    assert editor.triggers.text() == "None"
    assert editor.automatic_warning.isHidden()
    assert editor._form_payload()["triggers"] == []
    initial_height = editor.triggers.sizeHint().height()
    for checkbox in editor.triggers._checks.values():
        checkbox.click()
    assert [trigger.type for trigger in editor.triggers.selected()] == [
        "daily",
        "on_open",
        "on_sync",
    ]
    assert editor.triggers.sizeHint().height() == initial_height
    assert not editor.automatic_warning.isHidden()
    assert not editor.triggers._none.isChecked()
    raw = editor._form_payload()
    parsed = parse_policy(raw)
    editor._apply_policy(parsed)
    assert editor._form_payload() == raw
    for checkbox in editor.triggers._checks.values():
        checkbox.click()
    assert editor._form_payload()["triggers"] == []
    assert editor.automatic_warning.isHidden()
    assert editor.triggers.text() == "None"
    assert editor.triggers._none.isChecked()
    editor.triggers._checks["daily"].click()
    editor.triggers._checks["on_sync"].click()
    editor.triggers._none.click()
    assert editor.triggers.selected() == ()
    assert all(not checkbox.isChecked() for checkbox in editor.triggers._checks.values())
    assert editor.automatic_warning.isHidden()
    editor.triggers._none.click()
    assert editor.triggers._none.isChecked()
    editor.reject()


def test_policy_text_fields_have_horizontal_padding(edit_fixture: SimpleNamespace) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    fields = [
        editor.name,
        editor._conditions[0].tags,
        editor._conditions[0].days.lineEdit(),
        editor._actions[0].tags,
        editor._actions[0].deck.lineEdit(),
    ]
    for field in fields:
        margins = field.textMargins()
        assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (6, 0, 6, 0)
    assert editor.automatic_warning.text() == (
        "⚠ Triggers apply this policy <b>without confirmation</b>"
    )
    assert editor.automatic_warning.isHidden()
    editor.reject()


@pytest.mark.parametrize(
    ("triggers", "summary", "help_fragments", "absent_fragments"),
    [
        ([], "None", ["started manually"], ["once per", "sync attempt"]),
        (
            [{"type": "on_open"}, {"type": "on_sync"}],
            "On open\nOn sync",
            ["when Anki opens", "after collection sync"],
            ["once per", "started manually"],
        ),
        (
            [{"type": "daily"}],
            "Daily",
            ["once per Anki day"],
            ["after collection sync", "started manually"],
        ),
        (
            [{"type": "on_open"}],
            "On open",
            ["when Anki opens"],
            ["once per", "collection sync"],
        ),
        (
            [{"type": "on_sync"}],
            "On sync",
            ["after collection sync"],
            ["once per", "when Anki opens"],
        ),
    ],
)
def test_dashboard_trigger_tooltip_describes_only_selected_setting(
    edit_fixture: SimpleNamespace,
    triggers: list[dict],
    summary: str,
    help_fragments: list[str],
    absent_fragments: list[str],
) -> None:
    parsed = parse_config(
        {**DEFAULT_CONFIG, "policies": [{**edit_fixture.raw, "triggers": triggers}]}
    )
    dashboard = ui.CardJanitorDialog(parsed, ())
    column = dashboard.COLUMN_TRIGGERS
    assert dashboard.table.horizontalHeaderItem(column).text() == "Trigger"
    item = dashboard.table.item(0, column)
    assert item.text() == summary
    for fragment in help_fragments:
        assert fragment in item.toolTip()
    for fragment in absent_fragments:
        assert fragment not in item.toolTip()
    dashboard.close()


def test_trigger_popup_stays_open_and_matches_field_width(edit_fixture: SimpleNamespace) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    picker = editor.triggers
    picker._menu.popup(picker.mapToGlobal(picker.rect().bottomLeft()))
    QApplication.processEvents()
    assert picker._menu.width() == picker.width()
    checkbox = picker._checks["daily"]
    QTest.mouseClick(
        checkbox,
        Qt.MouseButton.LeftButton,
        pos=QPoint(checkbox.width() - 2, checkbox.height() // 2),
    )
    assert picker._menu.isVisible()
    assert picker.text() == "Daily"
    QTest.mouseClick(
        picker._none,
        Qt.MouseButton.LeftButton,
        pos=QPoint(picker._none.width() - 2, picker._none.height() // 2),
    )
    assert picker._menu.isVisible()
    assert picker.text() == "None"
    picker._menu.hide()
    editor.reject()


@pytest.mark.parametrize("field_name", ["triggers", "decks", "note_types"])
def test_picker_padding_keeps_popup_open(edit_fixture: SimpleNamespace, field_name: str) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    picker = getattr(editor, field_name)
    menu = picker._menu
    menu.popup(picker.mapToGlobal(picker.rect().bottomLeft()))
    QApplication.processEvents()
    for point in (QPoint(0, 0), QPoint(menu.width() - 1, menu.height() - 1)):
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=point)
        assert menu.isVisible()
    QTest.mouseClick(picker._container, Qt.MouseButton.LeftButton, pos=QPoint(1, 1))
    assert menu.isVisible()
    QTest.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
    menu.popup(picker.mapToGlobal(picker.rect().bottomLeft()))
    QApplication.processEvents()
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=QPoint(-5, -5))
    assert not menu.isVisible()
    editor.reject()


def test_trigger_warning_waits_until_popup_closes(edit_fixture: SimpleNamespace) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    picker = editor.triggers
    for selecting in (True, False):
        picker._menu.popup(picker.mapToGlobal(picker.rect().bottomLeft()))
        QApplication.processEvents()
        original_position = picker.mapToGlobal(QPoint(0, 0))
        picker._checks["daily"].click()
        QApplication.processEvents()
        assert picker._menu.isVisible()
        assert picker.mapToGlobal(QPoint(0, 0)) == original_position
        assert editor.automatic_warning.isHidden() == selecting
        picker._menu.close()
        QApplication.processEvents()
        assert editor.automatic_warning.isHidden() != selecting
    editor.reject()


@pytest.mark.parametrize("field_name", ["triggers", "decks", "note_types"])
@pytest.mark.parametrize("movement", ["window", "field", "resize"])
def test_picker_dismisses_popup_when_anchor_changes(
    edit_fixture: SimpleNamespace, field_name: str, movement: str
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    picker = getattr(editor, field_name)
    picker._menu.popup(picker.mapToGlobal(picker.rect().bottomLeft()))
    QApplication.processEvents()
    assert picker._menu.isVisible()
    if movement == "window":
        editor.move(editor.pos() + QPoint(10, 10))
    elif movement == "field":
        picker.move(picker.pos() + QPoint(0, 10))
    else:
        picker.resize(picker.width() + 10, picker.height())
    QApplication.processEvents()
    assert not picker._menu.isVisible()
    editor.reject()


def test_dashboard_scope_displays_each_deck_on_its_own_line(edit_fixture: SimpleNamespace) -> None:
    scope = {
        "decks": [
            {"deck": "Mining", "include_subdecks": True},
            {"deck": "Archive", "include_subdecks": False},
        ],
        "note_types": ["Basic"],
    }
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [{**edit_fixture.raw, "scope": scope}]})
    dashboard = ui.CardJanitorDialog(parsed, ())
    assert dashboard.table.item(0, dashboard.COLUMN_SCOPE).text() == (
        "Mining + subdecks\nArchive\nNote type: Basic"
    )
    dashboard.close()


def test_conflict_actions_group_multiple_actions_under_each_policy() -> None:
    parent = QWidget()
    reports = tuple(
        SimpleNamespace(
            policy=SimpleNamespace(name=name, actions=(TagAction(name), MoveAction(name)))
        )
        for name in ("A", "B")
    )
    dialog = conflict_dialog.ConflictDialog(
        (ConflictDetail(1, ("A", "B"), ("Conflicting moves",)),), parent, reports
    )
    assert dialog.table.item(0, 2).text() == (
        "A:\n  Add tag 'A'\n  Move cards to 'A'\nB:\n  Add tag 'B'\n  Move cards to 'B'"
    )
    dialog.close()
    parent.deleteLater()


def test_invalid_trigger_type_can_be_repaired_in_form(edit_fixture: SimpleNamespace) -> None:
    raw = {**edit_fixture.raw, "triggers": [{"type": {"invalid": True}}]}
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, None, ()), set(), edit_fixture.parent
    )
    assert editor._form_payload()["triggers"] == []
    assert editor._policy_from_form() is not None
    editor.reject()


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
    monkeypatch.setattr(
        conflict_dialog, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids)
    )
    dialog.browse_button.click()
    dialog.table.selectRow(1)
    assert dialog.browse_button.text() == "Browse selected"
    dialog.browse_button.click()
    assert browsed == [{1, 2}, {2}]
    dialog.close()
