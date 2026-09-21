# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
import os
import sys
import traceback
from collections.abc import Iterator
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from anki.collection import Collection
from aqt.qt import (
    QApplication,
    QDialog,
    QEvent,
    QHeaderView,
    QPlainTextEdit,
    QPoint,
    QRect,
    QStyle,
    QStyleOptionViewItem,
    Qt,
    QTextBrowser,
    QUrl,
    QWidget,
)
from card_janitor import cleanup_preview, policy_editor, settings_dialog, ui
from card_janitor.action_row import ActionRow
from card_janitor.condition_row import CardStatePicker, ConditionGroup, ConditionRow
from card_janitor.configuration import DEFAULT_CONFIG
from card_janitor.deck_picker import DeckPicker
from card_janitor.engine import CardFacts, PolicyReport, ResolvedAction, evaluate_facts
from card_janitor.execution import StalePolicyDefinitionsError
from card_janitor.line_numbers import LineNumberArea
from card_janitor.models import (
    AgeCondition,
    AnswerCountCondition,
    CardFlagCondition,
    CardStateCondition,
    CorrectAnswerRateCondition,
    DeckSelector,
    FsrsRetrievabilityCondition,
    MoveAction,
    NoteTypeSelector,
    PolicyRecord,
    SetFlagAction,
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


def test_preview_initial_height_ends_on_whole_row_and_only_adjusts_once() -> None:
    parent = QWidget()
    rows = tuple(
        cleanup_preview.PreviewRow(index, ("A",), ("Suspend card", "Add tag 'retired'"))
        for index in range(30)
    )
    dialog = cleanup_preview.CleanupPreviewDialog(rows, parent)
    dialog.show()
    QApplication.processEvents()
    QApplication.processEvents()
    boundaries = []
    height = 0
    for row in range(dialog.table.rowCount()):
        height += dialog.table.rowHeight(row)
        boundaries.append(height)
    assert dialog.table.viewport().height() in boundaries
    dialog.resize(dialog.width(), dialog.height() + 11)
    QApplication.processEvents()
    manual = dialog.size()
    dialog.set_rows(rows)
    QApplication.processEvents()
    assert dialog.size() == manual
    dialog.hide()
    dialog.show()
    QApplication.processEvents()
    assert dialog.size() == manual
    dialog.close()


def test_settings_disable_cancels_pending_automatic_work(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parse_config(DEFAULT_CONFIG)
    dialog = settings_dialog.SettingsDialog(parsed.config, edit_fixture.parent)
    assert dialog.automatic_enabled.isChecked()
    writes = []
    cancelled = []
    monkeypatch.setattr(settings_dialog, "save_settings", lambda **values: writes.append(values))
    monkeypatch.setattr(settings_dialog, "cancel_automatic_run", lambda: cancelled.append(True))
    assert dialog.notify.isEnabled()
    assert dialog.warn_invalid.isEnabled()
    assert dialog.debug_logging.isEnabled()
    dialog.automatic_enabled.setChecked(False)
    assert not dialog.notify.isEnabled()
    assert not dialog.warn_invalid.isEnabled()
    assert dialog.notify.isChecked()
    assert dialog.warn_invalid.isChecked()
    assert dialog.debug_logging.isEnabled()
    dialog._save()
    assert writes[0]["automatic_cleanup_enabled"] is False
    assert writes[0]["notify_after_automatic_run"] is True
    assert writes[0]["warn_on_invalid_automatic_policies"] is True
    assert cancelled == [True]
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_policy_rows_fit_without_horizontal_scrolling_at_minimum_width(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    editor.show()
    QApplication.processEvents()
    editor.resize(editor.minimumSizeHint().width(), editor.height())
    QApplication.processEvents()
    for scroll in (editor.conditions_scroll, editor.actions_scroll):
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.horizontalScrollBar().value() == 0
    editor.reject()


def test_last_cleanup_links_only_existing_policies() -> None:
    profile = {}
    record_cleanup(
        profile,
        policies=("<Old name>", "Deleted"),
        policy_ids=("existing", "deleted"),
    )
    _, details = last_cleanup(profile, existing_policy_ids={"existing"})
    assert '<a href="policy:existing">&lt;Old name&gt;</a>' in details
    assert "Deleted" in details
    assert 'href="policy:deleted"' not in details


def test_last_cleanup_policy_link_opens_current_editor(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw]})
    policy = parsed.config.policies[0]
    record_cleanup(edit_fixture.parent.pm.profile, policies=(policy.name,), policy_ids=(policy.id,))
    monkeypatch.setattr(ui, "_load_configured", lambda: parsed)
    dashboard = ui.CardJanitorDialog(parsed, ())
    dialog = QDialog(dashboard)
    browser = QTextBrowser(dialog)
    monkeypatch.setattr(ui, "showText", lambda *_args, **_kwargs: (dialog, None))
    opened = []
    monkeypatch.setattr(dashboard, "_open_editor", opened.append)
    dashboard._show_cleanup_status("last_cleanup")
    browser.anchorClicked.emit(QUrl(f"policy:{policy.id}"))
    assert opened == [parsed.policy_records[0]]
    assert dialog.isVisible()
    assert not dialog.isModal()
    dialog.close()
    dashboard.close()


def test_disabling_automatic_cleanup_keeps_manual_button_available(
    edit_fixture: SimpleNamespace,
) -> None:
    parsed = parse_config(
        {**DEFAULT_CONFIG, "automatic_cleanup_enabled": False, "policies": [edit_fixture.raw]}
    )
    card = CardFacts(1, 10, 1, 0, 2, 2, 100, 1000, None, frozenset({"leech"}))
    reports = (
        evaluate_facts(
            parsed.config.policies[0], [card], {1}, (ResolvedAction(TagAction("test")),)
        ),
    )
    dashboard = ui.CardJanitorDialog(parsed, reports)
    assert dashboard.run_button.isEnabled()
    assert not dashboard.automatic_disabled.isHidden()
    dashboard.close()


def test_grouped_condition_wrapping_retains_block_indentation(
    edit_fixture: SimpleNamespace,
) -> None:
    raw = {
        **edit_fixture.raw,
        "match": "all",
        "conditions": [
            {"type": "card_state", "states": ["review"]},
            {
                "match": "any",
                "conditions": [
                    {
                        "type": "age",
                        "days": 365,
                        "source": "first_review",
                        "operator": "gte",
                    },
                    {"type": "card_state", "states": ["learning", "relearning"]},
                ],
            },
        ],
    }
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [raw]})
    dashboard = ui.CardJanitorDialog(parsed, ())
    column = dashboard.COLUMN_CONDITIONS
    dashboard.table.horizontalHeader().setSectionResizeMode(
        column,
        QHeaderView.ResizeMode.Interactive,
    )
    dashboard.table.setColumnWidth(column, 140)
    index = dashboard.table.model().index(0, column)
    delegate = dashboard._condition_text_delegate
    option = QStyleOptionViewItem()
    option.initFrom(dashboard.table)
    option.rect = QRect(0, 0, dashboard.table.columnWidth(column), 10_000)
    text_option, text_rect = delegate._text_option_and_rect(option, index)
    document = ui._condition_text_document(
        dashboard.table.item(0, column).text(),
        text_option,
        text_rect.width(),
    )
    document.size()

    blocks = {}
    block = document.begin()
    while block.isValid():
        blocks[block.text()] = block
        block = block.next()

    group_indent = text_option.fontMetrics.horizontalAdvance("  ")
    first = blocks["Age since first review ≥ 365 days"]
    assert first.layout().lineCount() > 1
    assert {
        first.layout().lineAt(line_number).x() for line_number in range(first.layout().lineCount())
    } == {group_indent}
    later = blocks["OR Card state is Learning, Relearning"]
    assert later.layout().lineCount() > 1
    assert later.layout().lineAt(0).x() == group_indent
    later_indent = group_indent + text_option.fontMetrics.horizontalAdvance("OR ")
    assert {
        later.layout().lineAt(line_number).x()
        for line_number in range(1, later.layout().lineCount())
    } == {later_indent}
    assert blocks["AND ("].layout().lineAt(0).x() == 0
    assert blocks[")"].layout().lineAt(0).x() == 0

    dashboard.table.resizeRowsToContents()
    hint = delegate.sizeHint(option, index)
    native_padding = 2 * dashboard.table.style().pixelMetric(
        QStyle.PixelMetric.PM_FocusFrameVMargin,
        text_option,
        dashboard.table,
    )
    assert hint.height() <= document.size().height() + native_padding + 1
    assert dashboard.table.rowHeight(0) >= hint.height()
    dashboard.close()


@pytest.mark.parametrize(
    ("text", "target", "base_indent", "operator"),
    [
        pytest.param(
            "A sufficiently long first condition that wraps onto another line",
            "A sufficiently long first condition that wraps onto another line",
            "",
            "",
            id="single_condition",
        ),
        pytest.param(
            "First condition\nAND A sufficiently long second condition that wraps",
            "AND A sufficiently long second condition that wraps",
            "",
            "AND ",
            id="flat_and",
        ),
        pytest.param(
            "First condition\nOR A sufficiently long second condition that wraps",
            "OR A sufficiently long second condition that wraps",
            "",
            "OR ",
            id="flat_or",
        ),
        pytest.param(
            "First condition\nAND Second condition\n"
            "AND A sufficiently long third condition that wraps",
            "AND A sufficiently long third condition that wraps",
            "",
            "AND ",
            id="later_top_level_condition",
        ),
        pytest.param(
            "AND (\n  A sufficiently long first grouped condition that wraps\n  OR Second\n)",
            "A sufficiently long first grouped condition that wraps",
            "  ",
            "",
            id="first_grouped_condition",
        ),
        pytest.param(
            "AND (\n  First\n  OR A sufficiently long later grouped condition that wraps\n)",
            "OR A sufficiently long later grouped condition that wraps",
            "  ",
            "OR ",
            id="later_grouped_or_condition",
        ),
        pytest.param(
            "OR (\n  First\n  AND A sufficiently long later grouped condition that wraps\n)",
            "AND A sufficiently long later grouped condition that wraps",
            "  ",
            "AND ",
            id="later_grouped_and_condition",
        ),
    ],
)
def test_condition_renderer_uses_expression_hanging_indents(
    text: str,
    target: str,
    base_indent: str,
    operator: str,
) -> None:
    widget = QWidget()
    option = QStyleOptionViewItem()
    option.initFrom(widget)
    document = ui._condition_text_document(text, option, 120)
    document.size()
    block = document.begin()
    while block.isValid() and block.text() != target:
        block = block.next()
    assert block.isValid()
    layout = block.layout()

    expected_first = option.fontMetrics.horizontalAdvance(base_indent)
    expected_continuation = expected_first + option.fontMetrics.horizontalAdvance(operator)
    assert layout.lineCount() > 1
    assert layout.lineAt(0).x() == expected_first
    assert {layout.lineAt(line_number).x() for line_number in range(1, layout.lineCount())} == {
        expected_continuation
    }


def test_condition_renderer_preserves_unwrapped_group_structure() -> None:
    widget = QWidget()
    option = QStyleOptionViewItem()
    option.initFrom(widget)
    document = ui._condition_text_document(
        "AND (\n  Interval ≥ 365 days\n  OR Review history exists\n)",
        option,
        500,
    )
    document.size()
    blocks = []
    block = document.begin()
    while block.isValid():
        blocks.append(block)
        block = block.next()

    group_indent = option.fontMetrics.horizontalAdvance("  ")
    assert [block.text() for block in blocks] == [
        "AND (",
        "Interval ≥ 365 days",
        "OR Review history exists",
        ")",
    ]
    assert all(block.layout().lineCount() == 1 for block in blocks)
    assert [block.layout().lineAt(0).x() for block in blocks] == [
        0,
        group_indent,
        group_indent,
        0,
    ]


def test_closing_linked_policy_editor_returns_to_cleanup_details(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw]})
    policy = parsed.config.policies[0]
    record_cleanup(edit_fixture.parent.pm.profile, policies=(policy.name,), policy_ids=(policy.id,))
    monkeypatch.setattr(ui, "_load_configured", lambda: parsed)
    dashboard = ui.CardJanitorDialog(parsed, ())
    dashboard.show()
    dialog = QDialog(dashboard)
    browser = QTextBrowser(dialog)
    monkeypatch.setattr(ui, "showText", lambda *_args, **_kwargs: (dialog, None))
    activated = []
    monkeypatch.setattr(dialog, "activateWindow", lambda: activated.append(True))
    dashboard._show_cleanup_status("cleanup")
    browser.anchorClicked.emit(QUrl(f"policy:{policy.id}"))
    assert dashboard._policy_editor is not None
    activated.clear()
    dashboard._policy_editor.reject()
    assert activated == [True]
    assert dialog.isVisible()
    dialog.close()
    dashboard.close()


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


def test_dashboard_refresh_preserves_selected_policy(edit_fixture: SimpleNamespace) -> None:
    second = {**edit_fixture.raw, "id": "second", "name": "Second"}
    original = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw, second]})
    updated = parse_config(
        {
            **DEFAULT_CONFIG,
            "policies": [edit_fixture.raw, {**second, "name": "Second updated"}],
        }
    )
    dashboard = ui.CardJanitorDialog(original, ())
    dashboard.table.selectRow(1)

    dashboard.set_dashboard(updated, ())

    assert dashboard.table.currentRow() == 1
    assert dashboard._selected_record().policy.id == "second"
    dashboard.close()


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


def test_manual_stale_policy_failure_is_recorded_and_shown(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = (
        "Cleanup cancelled because one or more selected policies changed. "
        "Refresh Card Janitor and try again."
    )

    class Operation:
        def __init__(self, *, parent: object, op: object) -> None:
            self.parent = parent
            self.op = op

        def success(self, callback: object) -> "Operation":
            self.applied = callback
            return self

        def failure(self, callback: object) -> "Operation":
            self.failed = callback
            return self

        def run_in_background(self) -> None:
            try:
                self.op(edit_fixture.collection)
            except StalePolicyDefinitionsError as exc:
                self.failed(exc)

    monkeypatch.setattr(ui, "CollectionOp", Operation)
    monkeypatch.setattr(
        ui,
        "execute_approved_reports",
        lambda *_args: (_ for _ in ()).throw(StalePolicyDefinitionsError(message)),
    )
    warnings = []
    monkeypatch.setattr(ui, "showWarning", lambda value, **_kwargs: warnings.append(value))
    report = SimpleNamespace(policy=edit_fixture.policy, actionable=(), errors=())

    ui.execute_on_demand_reports(SimpleNamespace(close=lambda: None), (report,))

    assert warnings == [message]
    assert edit_fixture.parent.pm.profile[LAST_CLEANUP_KEY]["failure"] == message


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
    picker = NoteTypePicker([("Basic", ("Card 1",)), ("Cloze", ("Cloze",))], None, parent)
    picker._clicked(picker._items["Basic"], 0)
    assert picker.selected() == (NoteTypeSelector("Cloze"),)
    picker._clicked(picker._items["Basic"], 0)
    assert picker.selected() == (NoteTypeSelector("Basic"), NoteTypeSelector("Cloze"))
    assert picker._root.checkState(0) == Qt.CheckState.PartiallyChecked
    picker._clicked(picker._root, 0)
    assert picker.selected() is None


def test_note_type_picker_can_restrict_card_types_without_selecting_them_by_default() -> None:
    parent = QWidget()
    picker = NoteTypePicker(
        [("Basic", ("Card 1", "Card 2"))],
        (NoteTypeSelector("Basic"),),
        parent,
    )
    picker._clicked(picker._card_type_items["Basic", "Card 1"], 0)
    assert picker.selected() == (NoteTypeSelector("Basic", ("Card 2",)),)
    assert picker._items["Basic"].checkState(0) == Qt.CheckState.PartiallyChecked


@pytest.mark.parametrize(
    "condition",
    [
        AgeCondition(100000, "card_created", "lte"),
        AgeCondition(30, "last_review", "gte"),
        AnswerCountCondition(50, "gte"),
        CorrectAnswerRateCondition(80, "lt"),
        CardFlagCondition(("none", "purple")),
        FsrsRetrievabilityCondition(75, "lte"),
        CardStateCondition(("new", "relearning")),
        TagCondition(("leech", "retired"), "contains_all"),
        SuspensionCondition("is_suspended"),
        SiblingReviewHistoryCondition("none"),
    ],
)
def test_extracted_condition_row_round_trip(condition: object) -> None:
    row = ConditionRow(condition)
    assert row.condition() == condition


def test_condition_picker_separates_sm2_and_names_the_unflagged_state() -> None:
    row = ConditionRow(CardFlagCondition(("none", "red")))
    labels = [row.kind.itemText(index) for index in range(row.kind.count())]
    assert labels.index("SM-2") < labels.index("FSRS")
    assert row.flags.itemText(0) == "No flag, Red"


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
        "set_flag",
        "clear_flag",
    ],
)
def test_extracted_action_row_preserves_kind(kind: str) -> None:
    row = ActionRow(["Mining"], kind=kind, tags=("leech",), deck="Mining")
    assert row.action_kind() == kind
    assert row.actions()
    if kind == "set_flag":
        assert row.actions() == (SetFlagAction("red"),)


def test_action_picker_uses_one_grouped_action_field() -> None:
    row = ActionRow(["Mining"], kind="move_note", deck="Mining")
    labels = [row.kind.itemText(index) for index in range(row.kind.count())]
    assert (
        labels.index("Tags") < labels.index("Cards") < labels.index("Notes") < labels.index("Flags")
    )
    assert row.kind.currentText() == "Move note to deck"
    assert row.deck.lineEdit().cursorPosition() == 0
    assert not hasattr(row, "operator")


def test_condition_and_action_rows_share_control_columns() -> None:
    condition = ConditionRow(AgeCondition(30, "last_review", "gte"))
    action = ActionRow(["Mining"], kind="move", deck="Mining")

    assert condition.layout().itemAtPosition(0, 1).widget() is condition.kind
    assert condition.layout().itemAtPosition(0, 2).widget() is condition.operator_stack
    assert condition.layout().itemAtPosition(0, 3).widget() is condition.value_stack
    assert condition.layout().itemAtPosition(0, 4).widget() is condition.remove_button
    assert action.layout().itemAtPosition(0, 1).widget() is action.kind
    assert action.layout().itemAtPosition(0, 2).widget() is action.value_stack
    assert action.layout().itemAtPosition(0, 3).widget() is action.blank_column
    assert action.layout().itemAtPosition(0, 4).widget() is action.remove_button

    for row in (condition, action):
        row.resize(1000, row.sizeHint().height())
        row.layout().activate()
    for condition_widget, action_widget in (
        (condition.kind, action.kind),
        (condition.operator_stack, action.value_stack),
        (condition.remove_button, action.remove_button),
    ):
        assert condition_widget.x() == action_widget.x()
        assert condition_widget.width() == action_widget.width()


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
            "scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]},
            "match": "all",
            "conditions": [{"type": "tags", "operator": "contains_any", "tags": ["leech"]}],
            "actions": [{"type": "remove_tags", "tags": ["leech"]}],
        }
        policy = parse_policy(raw)
        parent = QWidget()
        editor = policy_editor.PolicyEditorDialog(PolicyRecord(0, raw, policy, ()), set(), parent)
        assert editor._policy_from_form() == policy
        assert editor._scope_form.labelForField(editor.note_types).text() == "Notes"
        assert editor.name.cursorPosition() == 0
        assert editor._conditions[0].tags.cursorPosition() == 0
        assert editor._actions[0].tags.cursorPosition() == 0
        assert editor.add_condition_button.minimumWidth() == editor.add_action_button.minimumWidth()
        assert not editor.isModal()
        editor.close()
    finally:
        collection.close()


def test_conflict_summary_opens_modeless_details_and_browses_skipped_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = QWidget()
    parent.col = SimpleNamespace(decks=SimpleNamespace(all_names_and_ids=list))
    parent.pm = SimpleNamespace(profile={})
    monkeypatch.setattr(ui, "mw", parent)
    raw = [
        {
            "id": name,
            "name": name,
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
    assert "href=" not in dashboard.conflict_summary.text()
    assert "href=" not in dashboard.summary.text()
    assert "0 cards would be cleaned up" in dashboard.summary.text()
    dashboard.preview_button.click()
    details = dashboard._preview_dialog
    assert details is not None
    assert not details.isModal()
    assert dashboard._preview_dialog is details
    assert details.table.rowCount() == 0
    details.view.setCurrentIndex(details.view.findData("overlapping"))
    assert details.table.rowCount() == 1
    details.view.setCurrentIndex(details.view.findData("conflicts"))
    assert details.table.item(0, 1).text() == "A\nB"
    assert details.table.item(0, 2).text() == "None"
    assert details.table.item(0, 3).text() == "Skipped"
    assert details.table.item(0, 4).text() == (
        "Different move destinations\nA: Move cards to 'A'\nB: Move cards to 'B'"
    )
    browsed = []
    monkeypatch.setattr(
        cleanup_preview, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids)
    )
    details.browse_button.click()
    assert browsed == [{1}]
    monkeypatch.setattr(ui, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids))
    dashboard._view_included()
    assert browsed == [{1}, {1}]
    dashboard.table.item(1, dashboard.COLUMN_RUN).setCheckState(Qt.CheckState.Unchecked)
    assert dashboard._preview_dialog is details
    assert details.table.rowCount() == 0
    assert dashboard.conflict_summary.isHidden()
    assert "1 card would be cleaned up" in dashboard.summary.text()
    dashboard.preview_button.click()
    assert details.table.rowCount() == 1
    assert details.table.item(0, 3).text() == "Planned"
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
    assert "triggers" not in editor._form_payload()
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
    assert "triggers" not in editor._form_payload()
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
        "⚠ This policy is applied <b>without confirmation</b>"
    )
    assert editor.automatic_warning.isHidden()
    editor.reject()


@pytest.mark.parametrize(
    ("triggers", "summary", "help_fragments", "absent_fragments"),
    [
        (None, "None", ["started manually"], ["once per", "sync attempt"]),
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
    triggers: list[dict] | None,
    summary: str,
    help_fragments: list[str],
    absent_fragments: list[str],
) -> None:
    policy = {
        **edit_fixture.raw,
        **({"triggers": triggers} if triggers is not None else {}),
    }
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [policy]})
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
        checkbox = picker._checks["daily"]
        QTest.mouseClick(
            checkbox,
            Qt.MouseButton.LeftButton,
            pos=QPoint(checkbox.width() - 2, checkbox.height() // 2),
        )
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
        "note_types": [{"name": "Basic"}],
    }
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [{**edit_fixture.raw, "scope": scope}]})
    dashboard = ui.CardJanitorDialog(parsed, ())
    assert dashboard.table.item(0, dashboard.COLUMN_SCOPE).text() == (
        "Mining + subdecks\nArchive\nNote type: Basic"
    )
    dashboard.close()


def test_invalid_policy_row_is_visibly_marked(edit_fixture: SimpleNamespace) -> None:
    invalid = {**edit_fixture.raw, "actions": [{"type": "unknown"}]}
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [invalid]})
    dashboard = ui.CardJanitorDialog(parsed, ())
    assert dashboard.table.item(0, dashboard.COLUMN_POLICY).text().startswith("⚠ ")
    for column in range(dashboard.table.columnCount()):
        assert dashboard.table.item(0, column).background().color().alpha() > 0
    assert dashboard.summary.isHidden()
    assert not dashboard.run_button.isEnabled()
    dashboard.close()


def test_runtime_policy_error_is_shown_in_row_and_summary(
    edit_fixture: SimpleNamespace,
) -> None:
    message = "FSRS conditions require FSRS to be enabled"
    report = PolicyReport(edit_fixture.policy, (), (), 0, (), (), (message,))
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw]})
    dashboard = ui.CardJanitorDialog(parsed, (report,))
    assert dashboard.table.item(0, dashboard.COLUMN_POLICY).text().startswith("⚠ ")
    assert message in dashboard.table.item(0, dashboard.COLUMN_POLICY).toolTip()
    assert message in dashboard.table.item(0, dashboard.COLUMN_COUNT).toolTip()
    assert dashboard.summary.text() == "0 cards would be cleaned up."
    dashboard.close()


def test_preview_conflicts_show_no_planned_changes() -> None:
    parent = QWidget()
    dialog = cleanup_preview.CleanupPreviewDialog(
        (cleanup_preview.PreviewRow(1, ("A", "B"), (), ("Conflicting moves",), overlapping=True),),
        parent,
        view="conflicts",
    )
    assert dialog.table.item(0, 2).text() == "None"
    assert dialog.table.item(0, 3).text() == "Skipped"
    assert dialog.table.item(0, 4).text() == "Conflicting moves"
    assert dialog.table.item(0, 1).textAlignment() & Qt.AlignmentFlag.AlignTop
    dialog.close()
    parent.deleteLater()


def test_dashboard_preview_sits_between_close_and_cleanup(edit_fixture: SimpleNamespace) -> None:
    parsed = parse_config({**DEFAULT_CONFIG, "policies": [edit_fixture.raw]})
    dashboard = ui.CardJanitorDialog(parsed, ())
    dashboard.show()
    QApplication.processEvents()
    positions = [
        button.mapTo(dashboard, QPoint(0, 0)).x()
        for button in (
            dashboard.close_button,
            dashboard.preview_button,
            dashboard.run_button,
        )
    ]
    assert positions[0] < positions[1] < positions[2]
    assert not dashboard.preview_button.autoDefault()
    assert dashboard.run_button.isDefault()
    dashboard.close()


def test_preview_filters_and_browse_use_visible_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = QWidget()
    rows = (
        cleanup_preview.PreviewRow(1, ("A",), ("Suspend card",)),
        cleanup_preview.PreviewRow(2, ("A", "B"), ("Suspend card",), overlapping=True),
        cleanup_preview.PreviewRow(3, ("A", "C"), (), ("Conflicting moves",), overlapping=True),
    )
    dialog = cleanup_preview.CleanupPreviewDialog(rows, parent)
    browsed = []
    monkeypatch.setattr(
        cleanup_preview, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids)
    )
    for view, ids in (
        ("planned", {1, 2}),
        ("overlapping", {2, 3}),
        ("conflicts", {3}),
        ("all", {1, 2, 3}),
    ):
        dialog.view.setCurrentIndex(dialog.view.findData(view))
        assert dialog.table.rowCount() == len(ids)
        dialog.browse_button.click()
        assert browsed[-1] == ids
    dialog.set_rows(())
    assert dialog.table.rowCount() == 0
    assert not dialog.browse_button.isEnabled()
    dialog.close()


def test_preview_does_not_browse_stale_context(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = QWidget()
    current = [True]
    dialog = cleanup_preview.CleanupPreviewDialog(
        (cleanup_preview.PreviewRow(1, ("A",), ("Suspend card",)),),
        parent,
        is_current=lambda: current[0],
    )
    dialog.show()
    browsed = []
    monkeypatch.setattr(
        cleanup_preview, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids)
    )
    current[0] = False
    dialog.browse_button.click()
    assert not browsed
    assert not dialog.isVisible()


def test_deleted_preview_disconnects_parent_close_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = QDialog()
    first = cleanup_preview.CleanupPreviewDialog((), parent)
    first.show()
    first.close()
    first.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    with pytest.raises(RuntimeError):
        first.isVisible()
    second = cleanup_preview.CleanupPreviewDialog((), parent)
    second.show()
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    parent.reject()
    assert errors == []
    assert not second.isVisible()


def test_preview_column_widths_remain_stable_across_views() -> None:
    parent = QWidget()
    rows = (
        cleanup_preview.PreviewRow(1, ("A",), ("Suspend card",)),
        *(
            cleanup_preview.PreviewRow(
                1234567890000 + index,
                ("B", "C"),
                (),
                ("Conflicting moves\n" * 6,),
                overlapping=True,
            )
            for index in range(12)
        ),
    )
    dialog = cleanup_preview.CleanupPreviewDialog(rows, parent)
    dialog.show()
    QApplication.processEvents()
    widths = [dialog.table.columnWidth(column) for column in range(5)]
    for view in ("all", "conflicts", "overlapping", "planned"):
        dialog.view.setCurrentIndex(dialog.view.findData(view))
        QApplication.processEvents()
        assert [dialog.table.columnWidth(column) for column in range(5)] == widths
    dialog.close()


@pytest.mark.parametrize("json_mode", [False, True])
def test_editor_preview_uses_unsaved_policy_without_requiring_name(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    json_mode: bool,
) -> None:
    operations = []

    class Query:
        def __init__(self, *, parent: object, op: object, success: object) -> None:
            self.parent = parent
            self.op, self.success = op, success
            operations.append(self)

        def failure(self, _callback: object) -> "Query":
            return self

        def with_progress(self, _text: str) -> "Query":
            return self

        def run_in_background(self) -> None:
            pass

    monkeypatch.setattr(policy_editor, "QueryOp", Query)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    editor.name.clear()
    if json_mode:
        editor._toggle_json()
    editor.show()
    before = editor._form_payload()
    json_before = editor.json_text.toPlainText()
    editor.preview_button.click()
    assert len(operations) == 1
    operations[0].success(operations[0].op(edit_fixture.collection))
    assert editor._preview_dialog is not None
    assert editor._preview_dialog.isVisible()
    assert editor._preview_dialog.windowTitle().endswith("Cleanup Preview: Unnamed policy")
    assert editor._preview_dialog.view.isHidden()
    assert editor._preview_dialog.view.currentData() == "planned"
    assert editor.result_policy is None
    assert editor._form_payload() == before
    assert editor.json_text.toPlainText() == json_before
    assert not editor._preview_dialog.isModal()
    editor.name.setText("Changed")
    if json_mode:
        editor.json_text.setPlainText(json_before + " ")
    assert not editor._preview_dialog._check_context()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    if json_mode:
        editor.reject()
    editor.reject()


def test_editor_browse_matches_requires_only_scope_and_conditions(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.decks._clicked(editor.decks._items["Mining"], 0)
    editor.match.setCurrentIndex(editor.match.findData("all_cards"))

    policy = editor._policy_from_form(browse=True)

    assert policy is not None
    assert policy.name == "Unnamed policy"
    assert policy.actions == ()
    assert editor._policy_from_form(preview=True) is None
    assert "Add at least one action" in editor.actions_error_warning.text()
    assert editor.browse_button.text() == "Browse"
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()


def test_editor_json_browse_ignores_name_and_actions(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    editor._toggle_json()
    raw = json.loads(editor.json_text.toPlainText())
    raw["name"] = 123
    raw["triggers"] = [{"type": {"invalid": True}}]
    raw["actions"] = [{"type": "unknown"}]
    editor.json_text.setPlainText(json.dumps(raw))

    policy = editor._policy_from_form(browse=True)

    assert policy is not None
    assert policy.name == "Unnamed policy"
    assert policy.actions == ()
    assert editor._policy_from_form(preview=True) is None
    assert "unknown action type" in editor.policy_error_warning.text()
    editor.json_text.setPlainText(editor._json_initial_text)
    editor.reject()
    editor.reject()


def test_editor_browse_opens_all_matching_cards(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operations = []

    class Query:
        def __init__(self, *, parent: object, op: object, success: object) -> None:
            self.parent = parent
            self.op, self.success = op, success
            operations.append(self)

        def failure(self, _callback: object) -> "Query":
            return self

        def with_progress(self, _text: str) -> "Query":
            return self

        def run_in_background(self) -> None:
            pass

    matching = SimpleNamespace(card_id=123)
    report = SimpleNamespace(qualifying=(matching,), actionable=(), errors=())
    browsed = []
    monkeypatch.setattr(policy_editor, "QueryOp", Query)
    monkeypatch.setattr(policy_editor, "evaluate_policy", lambda *_args: report)
    monkeypatch.setattr(
        policy_editor, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids)
    )
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    editor.show()
    editor._set_policy_errors(("Existing save error",))

    editor.browse_button.click()
    operations[0].success(operations[0].op(edit_fixture.collection))

    assert browsed == [{123}]
    assert "Existing save error" in editor.policy_error_warning.text()
    editor.reject()


def test_new_policy_starts_empty_and_requires_explicit_choices(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []
    monkeypatch.setattr(policy_editor, "showWarning", lambda text, **_kwargs: warnings.append(text))
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    assert editor._conditions == []
    assert editor._actions == []
    assert editor.conditions_scroll.isHidden()
    assert editor.actions_scroll.isHidden()
    assert editor._form_payload()["conditions"] == []
    assert editor._form_payload()["actions"] == []
    assert not editor._has_unsaved_changes()
    editor._accept()
    assert warnings == []
    assert "Enter a policy name" in editor.general_error_warning.text()
    assert "Choose at least one deck" in editor.scope_error_warning.text()
    assert "Add at least one condition" in editor.conditions_error_warning.text()
    assert "Add at least one action" in editor.actions_error_warning.text()
    editor.decks._clicked(editor.decks._items["Mining"], 0)
    editor.name.setText("Test")
    assert editor._policy_from_form() is None
    assert (
        "Add at least one condition or choose 'All cards'" in editor.conditions_error_warning.text()
    )
    assert "Add at least one action" in editor.actions_error_warning.text()
    editor.match.setCurrentIndex(editor.match.findData("all_cards"))
    assert editor._policy_from_form() is None
    assert "Add at least one action" in editor.actions_error_warning.text()
    editor._add_action("suspend")
    assert not editor.actions_scroll.isHidden()
    assert editor._policy_from_form() is not None
    editor._remove_action(editor._actions[0])
    assert editor._actions == []
    assert editor.actions_scroll.isHidden()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()


def test_successful_policy_save_restores_manager_focus(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []
    editor = SimpleNamespace(
        result_policy=edit_fixture.policy,
        deleteLater=lambda: events.append("deleted"),
    )
    dashboard = SimpleNamespace(
        _policy_editor=editor,
        _set_editor_controls_enabled=lambda **_kwargs: events.append("enabled"),
        _refresh=lambda **_kwargs: events.append("refreshed"),
        raise_=lambda: events.append("raised"),
        activateWindow=lambda: events.append("activated"),
    )
    monkeypatch.setattr(ui, "save_policy", lambda *_args, **_kwargs: events.append("saved"))

    ui.CardJanitorDialog._finish_editor(
        dashboard,
        editor,
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        QDialog.DialogCode.Accepted,
    )

    assert events[-3:] == ["refreshed", "raised", "activated"]


def test_unrestricted_manual_policy_round_trips_as_omitted_components(
    edit_fixture: SimpleNamespace,
) -> None:
    raw = {
        "id": "minimal",
        "name": "Minimal",
        "actions": [{"type": "suspend"}],
    }
    policy = parse_policy(raw)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, policy, ()), set(), edit_fixture.parent
    )

    assert editor.triggers.selected() == ()
    assert editor.decks.all_decks
    assert editor.note_types.selected() is None
    assert editor.match.currentData() == "all_cards"
    assert editor._form_payload() == raw
    assert editor._policy_from_form() == policy
    editor.done(QDialog.DialogCode.Rejected)


def test_editor_blocks_collection_wide_deletion_in_top_error_area(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.name.setText("Delete everything")
    editor.decks._clicked(editor.decks._root, 0)
    editor.match.setCurrentIndex(editor.match.findData("all_cards"))
    editor._add_action("delete_note")

    assert editor._policy_from_form() is None
    assert "Collection-wide deletion is not allowed" in editor.policy_error_warning.text()
    assert editor.scope_error_warning.isHidden()
    assert editor.conditions_error_warning.isHidden()
    assert editor.actions_error_warning.isHidden()

    editor.add_condition_button.click()
    assert editor._policy_from_form() is not None
    editor.done(QDialog.DialogCode.Rejected)


def test_add_condition_switches_all_cards_to_and(edit_fixture: SimpleNamespace) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.match.setCurrentIndex(editor.match.findData("all_cards"))
    editor.show()
    QApplication.processEvents()
    assert editor.add_condition_button.isVisible()
    assert not editor._conditions

    editor.add_condition_button.click()

    assert editor.match.currentData() == "all"
    assert len(editor._conditions) == 1
    assert not editor.conditions_scroll.isHidden()

    editor.match.setCurrentIndex(editor.match.findData("all_cards"))

    assert not editor._conditions
    assert editor.conditions_scroll.isHidden()

    editor.add_condition_button.click()

    assert editor.match.currentData() == "all"
    assert len(editor._conditions) == 1
    editor.done(QDialog.DialogCode.Rejected)


def test_condition_group_editor_round_trips_nested_policy(
    edit_fixture: SimpleNamespace,
) -> None:
    raw = {
        **edit_fixture.raw,
        "conditions": [
            edit_fixture.raw["conditions"][0],
            {
                "match": "any",
                "conditions": [
                    {"type": "interval", "days": 180, "operator": "gte"},
                    {"type": "suspension", "operator": "is_suspended"},
                ],
            },
        ],
    }
    policy = parse_policy(raw)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, policy, ()), set(), edit_fixture.parent
    )

    assert len(editor._condition_items) == 2
    group = editor._condition_items[1]
    assert isinstance(group, ConditionGroup)
    assert group.match.currentData() == "any"
    assert [row.number_label.text() for row in editor._conditions] == ["1.", "2.", "3."]
    assert editor._form_payload() == raw
    assert editor._policy_from_form() == policy
    editor.done(QDialog.DialogCode.Rejected)


def test_add_condition_group_and_collapse_single_remaining_child(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.match.setCurrentIndex(editor.match.findData("all_cards"))

    editor.add_condition_group_button.click()

    assert editor.match.currentData() == "all"
    assert len(editor._condition_items) == 1
    group = editor._condition_items[0]
    assert isinstance(group, ConditionGroup)
    assert len(group.rows) == 2
    assert group.match_label.text() == "Match"
    assert [group.match.itemText(index) for index in range(group.match.count())] == [
        "All conditions (AND)",
        "Any condition (OR)",
    ]
    assert group.focus_widgets()[:3] == (
        group.match,
        group.add_condition_button,
        group.remove_button,
    )
    assert [row.number_label.text() for row in group.rows] == ["1.", "2."]

    editor._remove_condition(group.rows[0])

    assert editor._condition_items == [editor._conditions[0]]
    assert not isinstance(editor._condition_items[0], ConditionGroup)
    assert editor._conditions[0].number_label.text() == "1."
    editor.done(QDialog.DialogCode.Rejected)


def test_policy_editor_condition_group_height_caps_and_shrinks(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()
    QApplication.processEvents()
    compact_height = editor.height()

    group = editor._add_condition_group()
    QApplication.processEvents()
    QApplication.processEvents()
    small_group_height = editor.height()
    viewport_limit = editor.conditions_scroll.maximumHeight()
    margins = editor.conditions_layout.contentsMargins()

    assert viewport_limit >= group.sizeHint().height() + margins.top() + margins.bottom()
    assert editor.conditions_scroll.verticalScrollBar().maximum() == 0

    added = [editor._add_condition(None, group=group, focus=False) for _ in range(6)]
    QApplication.processEvents()
    QApplication.processEvents()

    assert editor.conditions_scroll.maximumHeight() == viewport_limit
    assert editor.height() == small_group_height
    assert editor.conditions_scroll.verticalScrollBar().maximum() > 0

    for row in reversed(added):
        editor._remove_condition(row)
    QApplication.processEvents()
    QApplication.processEvents()

    assert editor.conditions_scroll.maximumHeight() == viewport_limit
    assert editor.height() == small_group_height
    assert editor.conditions_scroll.verticalScrollBar().maximum() == 0

    editor._remove_condition_group(group)
    QApplication.processEvents()
    QApplication.processEvents()

    assert editor.conditions_scroll.isHidden()
    assert editor.height() == compact_height
    editor.done(QDialog.DialogCode.Rejected)


@pytest.mark.parametrize(
    "arrangement",
    ["group_first", "group_last", "multiple_groups"],
)
def test_large_mixed_condition_layout_has_disjoint_geometry(
    edit_fixture: SimpleNamespace,
    arrangement: str,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()
    groups = []
    if arrangement == "group_first":
        groups.append(editor._add_condition_group())
        for _ in range(30):
            editor._add_condition(None)
    elif arrangement == "group_last":
        for _ in range(30):
            editor._add_condition(None)
        groups.append(editor._add_condition_group())
    else:
        groups.append(editor._add_condition_group())
        for _ in range(10):
            editor._add_condition(None)
        groups.append(editor._add_condition_group())
        for _ in range(20):
            editor._add_condition(None)
    QApplication.processEvents()
    QApplication.processEvents()

    assert editor.conditions_scroll.verticalScrollBar().maximum() > 0
    _assert_condition_geometry_is_disjoint(editor, groups)
    editor.done(QDialog.DialogCode.Rejected)


def _assert_condition_geometry_is_disjoint(
    editor: policy_editor.PolicyEditorDialog,
    groups: list[ConditionGroup],
) -> None:
    container = editor.conditions_container
    layout_height = editor.conditions_layout.sizeHint().height()
    assert container.minimumHeight() >= layout_height
    assert container.height() >= layout_height
    assert (
        editor.conditions_scroll.verticalScrollBar().maximum()
        == container.height() - editor.conditions_scroll.viewport().height()
    )

    item_rects = [item.geometry() for item in editor._condition_items]
    for previous, current in pairwise(item_rects):
        assert previous.bottom() < current.top()
    assert item_rects[-1].bottom() < container.height()

    for row in editor._conditions:
        assert row.height() >= row.sizeHint().height()
    for group in groups:
        assert group.height() >= group.sizeHint().height()
        assert group.match.height() >= group.match.sizeHint().height()
        nested_rects = [group.layout().itemAt(0).geometry()]
        nested_rects.extend(row.geometry() for row in group.rows)
        for previous, current in pairwise(nested_rects):
            assert previous.bottom() < current.top()
        assert nested_rects[-1].bottom() < group.height()


def test_condition_content_and_scroll_range_grow_after_viewport_caps(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()
    group = editor._add_condition_group()
    checkpoints = {0, 1, 2, 3, 10, 30}
    measurements = []
    for count in range(31):
        if count:
            editor._add_condition(None)
        if count not in checkpoints:
            continue
        QApplication.processEvents()
        QApplication.processEvents()
        scroll = editor.conditions_scroll
        container = editor.conditions_container
        _assert_condition_geometry_is_disjoint(editor, [group])
        measurements.append(
            (
                scroll.viewport().height(),
                container.height(),
                scroll.verticalScrollBar().maximum(),
            )
        )

    viewport_heights, content_heights, scroll_ranges = zip(*measurements, strict=True)
    assert len(set(viewport_heights)) == 1
    assert all(previous < current for previous, current in pairwise(content_heights))
    assert all(previous < current for previous, current in pairwise(scroll_ranges))
    editor.done(QDialog.DialogCode.Rejected)


def test_flat_condition_layout_retains_natural_row_heights(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()
    checkpoints = {1, 2, 3, 10, 30}
    measurements = []
    for count in range(1, 31):
        editor._add_condition(None)
        if count not in checkpoints:
            continue
        QApplication.processEvents()
        QApplication.processEvents()
        scroll = editor.conditions_scroll
        container = editor.conditions_container
        _assert_condition_geometry_is_disjoint(editor, [])
        measurements.append(
            (
                scroll.viewport().height(),
                container.height(),
                scroll.verticalScrollBar().maximum(),
            )
        )

    viewport_heights, content_heights, scroll_ranges = zip(*measurements, strict=True)
    assert viewport_heights[0] < viewport_heights[1] < viewport_heights[2]
    assert len(set(viewport_heights[2:])) == 1
    assert all(previous < current for previous, current in pairwise(content_heights))
    assert scroll_ranges[:3] == (0, 0, 0)
    assert scroll_ranges[2] < scroll_ranges[3] < scroll_ranges[4]
    editor.done(QDialog.DialogCode.Rejected)


def test_existing_flat_condition_layout_retains_natural_row_heights(
    edit_fixture: SimpleNamespace,
) -> None:
    raw = {
        **edit_fixture.raw,
        "conditions": [edit_fixture.raw["conditions"][0]] * 10,
    }
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, parse_policy(raw), ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    QApplication.processEvents()

    _assert_condition_geometry_is_disjoint(editor, [])
    assert editor.conditions_scroll.verticalScrollBar().maximum() > 0
    editor.done(QDialog.DialogCode.Rejected)


def test_policy_editor_action_height_caps_and_shrinks(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()
    QApplication.processEvents()
    compact_height = editor.height()

    editor._add_action("add_tags", tags=("one",))
    QApplication.processEvents()
    QApplication.processEvents()
    one_action_height = editor.height()
    editor._add_action("add_tags", tags=("two",))
    QApplication.processEvents()
    QApplication.processEvents()
    two_action_height = editor.height()
    viewport_limit = editor.actions_scroll.maximumHeight()
    second = editor._actions[-1]
    editor._add_action("add_tags", tags=("three",))
    QApplication.processEvents()
    QApplication.processEvents()

    assert compact_height < one_action_height < two_action_height
    assert editor.actions_scroll.maximumHeight() == viewport_limit
    assert editor.height() == two_action_height
    assert editor.actions_scroll.verticalScrollBar().maximum() > 0

    editor._remove_action(editor._actions[-1])
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.height() == two_action_height
    assert editor.actions_scroll.verticalScrollBar().maximum() == 0

    editor._remove_action(second)
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.height() == one_action_height
    editor._remove_action(editor._actions[0])
    QApplication.processEvents()
    QApplication.processEvents()
    assert editor.height() == compact_height
    editor.done(QDialog.DialogCode.Rejected)


def test_policy_editor_reveals_items_added_past_section_caps(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()

    for _ in range(3):
        editor._add_condition(None)
    group = editor._add_condition_group()
    QApplication.processEvents()
    QApplication.processEvents()
    group_top = group.mapTo(editor.conditions_scroll.viewport(), QPoint()).y()
    assert editor.conditions_scroll.verticalScrollBar().maximum() > 0
    assert 0 <= group_top < editor.conditions_scroll.viewport().height()

    for _ in range(5):
        condition = editor._add_condition(None, group=group)
    for _ in range(3):
        editor._add_action("add_tags", tags=(f"tag_{len(editor._actions)}",))
    QApplication.processEvents()
    QApplication.processEvents()

    condition_top = condition.mapTo(editor.conditions_scroll.viewport(), QPoint()).y()
    action = editor._actions[-1]
    action_top = action.mapTo(editor.actions_scroll.viewport(), QPoint()).y()
    assert editor.conditions_scroll.verticalScrollBar().maximum() > 0
    assert 0 <= condition_top < editor.conditions_scroll.viewport().height()
    assert editor.actions_scroll.verticalScrollBar().maximum() > 0
    assert 0 <= action_top < editor.actions_scroll.viewport().height()
    editor.done(QDialog.DialogCode.Rejected)


def test_policy_editor_section_fitting_preserves_manual_dialog_size(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()
    QApplication.processEvents()
    editor.resize(editor.width() + 200, editor.height() + 350)
    QApplication.processEvents()
    manual_size = editor.size()

    group = editor._add_condition_group()
    for _ in range(6):
        editor._add_condition(None, group=group)
    for _ in range(3):
        editor._add_action("add_tags", tags=(f"tag_{len(editor._actions)}",))
    QApplication.processEvents()
    QApplication.processEvents()

    assert editor.size() == manual_size
    editor.done(QDialog.DialogCode.Rejected)


def test_incomplete_rows_do_not_clear_existing_errors(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    error = "note type 'Definitely Missing' not found"
    editor.match.setCurrentIndex(editor.match.findData("all_cards"))
    editor._set_policy_errors((error,))
    monkeypatch.setattr(
        policy_editor,
        "validate_policy_references",
        lambda *_args: (error,),
    )

    editor.add_condition_button.click()

    assert error in editor.scope_error_warning.text()

    editor._add_action("add_tags")

    assert error in editor.scope_error_warning.text()
    editor.done(QDialog.DialogCode.Rejected)


def test_empty_section_error_panels_stay_hidden(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()

    assert editor.general_error_warning.text() == ""
    assert editor.general_error_warning.isHidden()
    assert editor.name.height() >= editor.name.sizeHint().height()
    assert editor.triggers.height() >= editor.triggers.sizeHint().height()
    editor.done(QDialog.DialogCode.Rejected)


def test_editor_tab_order_forces_non_text_controls_into_keyboard_navigation(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    assert editor.triggers.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert editor.decks.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert editor.add_condition_button.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert editor.save_button.focusPolicy() & Qt.FocusPolicy.TabFocus

    editor.name.setFocus()
    QTest.keyClick(editor.name, Qt.Key.Key_Tab)
    QApplication.processEvents()

    assert QApplication.focusWidget() is editor.triggers
    editor.close()


def test_section_errors_are_combined_below_section_help(
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    editor._set_policy_errors(
        (
            "Add at least one condition or choose 'All cards'",
            "FSRS conditions require FSRS to be enabled",
        )
    )

    assert (
        editor.conditions_error_warning.parentWidget()
        .layout()
        .indexOf(editor.conditions_error_warning)
        == 1
    )
    assert (
        editor.scope_error_warning.parentWidget().layout().indexOf(editor.scope_error_warning) == 1
    )
    assert (
        editor.actions_error_warning.parentWidget().layout().indexOf(editor.actions_error_warning)
        == 1
    )
    assert "Fix the following:" in editor.conditions_error_warning.text()
    assert editor.conditions_error_warning.text().count("•") == 2
    editor.close()


def test_incomplete_new_action_stays_quiet_until_preview(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []
    monkeypatch.setattr(
        policy_editor,
        "showWarning",
        lambda text, **_kwargs: warnings.append(text),
    )
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    assert editor.preview_button.isEnabled()
    assert editor.policy_error_warning.isHidden()

    editor._add_action("add_tags")
    QApplication.processEvents()

    assert editor.policy_error_warning.isHidden()
    assert editor.actions_error_warning.isHidden()
    assert editor.browse_button.isEnabled()
    assert editor.preview_button.isEnabled()

    editor.preview_button.click()

    assert warnings == ["Cannot preview changes:\n\nEnter one or more tags for action 2."]
    assert editor.actions_error_warning.isHidden()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()


def test_impossible_scheduler_mix_warns_immediately(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    first = editor._conditions[0]
    first.kind.setCurrentIndex(first.kind.findData("fsrs_stability"))
    editor._add_condition(None)
    second = editor._conditions[1]
    second.kind.setCurrentIndex(second.kind.findData("sm2_ease"))
    QApplication.processEvents()

    assert "cannot be used together" in editor.conditions_error_warning.text()
    assert editor.policy_error_warning.isHidden()
    assert editor.browse_button.isEnabled()
    assert editor.preview_button.isEnabled()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()


def test_fsrs_availability_warns_immediately_without_scope(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor._add_condition(None)

    editor._conditions[0].kind.setCurrentIndex(
        editor._conditions[0].kind.findData("fsrs_stability")
    )
    QApplication.processEvents()

    assert "FSRS conditions require FSRS to be enabled" in editor.conditions_error_warning.text()
    assert not editor.conditions_error_warning.isHidden()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()


def test_editor_refuses_to_save_scheduler_incompatible_policy(
    edit_fixture: SimpleNamespace,
) -> None:
    raw = {
        **edit_fixture.raw,
        "conditions": [{"type": "fsrs_stability", "days": 30, "operator": "gte"}],
    }
    policy = parse_policy(raw)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, policy, ()), set(), edit_fixture.parent
    )
    editor._accept()
    assert editor.result_policy is None
    assert editor.result() != QDialog.DialogCode.Accepted
    assert editor.policy_error_warning.isHidden()
    assert "FSRS conditions require FSRS to be enabled" in editor.conditions_error_warning.text()
    editor.close()


def test_new_policy_save_validates_scheduler_compatibility(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.name.setText("FSRS policy")
    editor.decks._clicked(editor.decks._items["Mining"], 0)
    editor._add_condition(None)
    editor._conditions[0].kind.setCurrentIndex(
        editor._conditions[0].kind.findData("fsrs_stability")
    )
    editor._add_action("suspend")

    editor._accept()

    assert editor.result_policy is None
    assert "FSRS conditions require FSRS to be enabled" in editor.conditions_error_warning.text()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.close()


def test_save_reports_scheduler_error_alongside_incomplete_draft_errors(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []
    monkeypatch.setattr(policy_editor, "showWarning", lambda text, **_kwargs: warnings.append(text))
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor._add_condition(None)
    editor._conditions[0].kind.setCurrentIndex(
        editor._conditions[0].kind.findData("fsrs_stability")
    )

    editor._accept()

    assert warnings == []
    assert "Enter a policy name" in editor.general_error_warning.text()
    assert "Choose at least one deck" in editor.scope_error_warning.text()
    assert "Add at least one action" in editor.actions_error_warning.text()
    assert "FSRS conditions require FSRS to be enabled" in editor.conditions_error_warning.text()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.close()


def test_editor_explains_why_saved_policy_is_invalid(edit_fixture: SimpleNamespace) -> None:
    raw = {
        **edit_fixture.raw,
        "scope": {"decks": [{"deck": "Definitely Missing Deck", "include_subdecks": True}]},
    }
    policy = parse_policy(raw)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, policy, ()), set(), edit_fixture.parent
    )
    assert editor.policy_error_warning.isHidden()
    assert "deck 'Definitely Missing Deck' not found" in editor.scope_error_warning.text()
    assert "&#" not in editor.scope_error_warning.text()
    assert "•" not in editor.scope_error_warning.text()
    editor.close()


def test_editor_invalid_browse_shows_popup_and_resolved_error_clears(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings = []
    monkeypatch.setattr(
        policy_editor,
        "showWarning",
        lambda text, **_kwargs: warnings.append(text),
    )
    raw = {
        **edit_fixture.raw,
        "scope": {"decks": [{"deck": "Definitely Missing Deck", "include_subdecks": True}]},
    }
    policy = parse_policy(raw)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, policy, ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    assert "deck 'Definitely Missing Deck' not found" in editor.scope_error_warning.text()
    assert not editor.scope_error_warning.isHidden()
    assert editor.browse_button.isEnabled()
    assert editor.preview_button.isEnabled()

    editor.browse_button.click()

    assert warnings == ["Cannot browse matching cards:\n\ndeck 'Definitely Missing Deck' not found"]

    missing = editor.decks._items["Definitely Missing Deck"]
    editor.decks._clicked(missing, 0)
    editor.decks._clicked(missing, 0)
    editor.decks._clicked(editor.decks._items["Mining"], 0)
    QTest.qWait(1)
    QApplication.processEvents()

    assert editor.scope_error_warning.isHidden()
    assert editor.browse_button.isEnabled()
    assert editor.preview_button.isEnabled()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.close()


def test_editor_scheduler_change_clears_resolved_saved_error(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = {
        **edit_fixture.raw,
        "conditions": [{"type": "fsrs_stability", "days": 30, "operator": "gte"}],
    }
    policy = parse_policy(raw)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, policy, ()), set(), edit_fixture.parent
    )
    editor.show()
    QApplication.processEvents()
    assert "FSRS conditions require FSRS to be enabled" in editor.conditions_error_warning.text()
    assert not editor.conditions_error_warning.isHidden()
    assert editor.browse_button.isEnabled()

    condition = editor._conditions[0]
    condition.kind.setCurrentIndex(condition.kind.findData("interval"))
    QTest.qWait(1)
    QApplication.processEvents()

    assert editor.conditions_error_warning.isHidden()
    assert editor.browse_button.isEnabled()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.close()


def test_editor_shows_schema_errors_from_bulk_json(edit_fixture: SimpleNamespace) -> None:
    invalid = {**edit_fixture.raw, "actions": [{"type": "unknown"}]}
    record = parse_config({**DEFAULT_CONFIG, "policies": [invalid]}).policy_records[0]
    editor = policy_editor.PolicyEditorDialog(record, set(), edit_fixture.parent)
    assert editor.policy_error_warning.isHidden()
    assert record.issues[0].path in editor.actions_error_warning.text()
    assert "unknown action type" in editor.actions_error_warning.text()
    editor.close()


@pytest.mark.parametrize("expanded", [False, True])
def test_single_policy_preview_hides_redundant_columns(expanded: bool) -> None:
    parent = QWidget()
    rows = (cleanup_preview.PreviewRow(1, ("A",), ("Suspend card",), expanded_sibling=expanded),)
    dialog = cleanup_preview.CleanupPreviewDialog(rows, parent, policy_name="A")
    assert dialog.table.isColumnHidden(1)
    assert dialog.table.isColumnHidden(3)
    assert dialog.table.isColumnHidden(4) is not expanded
    if expanded:
        assert dialog.table.item(0, 4).text() == "Included by note action"
    combined = cleanup_preview.CleanupPreviewDialog(rows, parent)
    assert not combined.table.isColumnHidden(1)
    assert not combined.table.isColumnHidden(3)
    assert not combined.table.isColumnHidden(4)
    dialog.close()
    combined.close()


def test_popup_guard_ignores_events_after_menu_is_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    parent = QWidget()
    picker = CardStatePicker(parent)
    parent.show()
    QApplication.processEvents()
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    picker._menu.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert picker._anchor_guard.menu is None
    parent.move(parent.x() + 10, parent.y() + 10)
    QApplication.processEvents()
    assert not errors, "".join(traceback.format_exception(*errors[0])) if errors else ""
    parent.close()


def test_removing_final_condition_does_not_leave_stale_popup_watchers(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    editor.show()
    QApplication.processEvents()
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    editor._remove_condition(editor._conditions[0])
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    editor.resize(editor.width() + 10, editor.height() + 10)
    QApplication.processEvents()
    assert errors == []
    assert editor.conditions_scroll.isHidden()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()


@pytest.mark.parametrize("apply", [False, True])
def test_returning_from_json_preserves_form_size(
    edit_fixture: SimpleNamespace,
    apply: bool,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()),
        set(),
        edit_fixture.parent,
    )
    editor.show()
    QApplication.processEvents()
    original = editor.size()
    for _ in range(3):
        editor._toggle_json()
        QApplication.processEvents()
        if apply:
            editor._apply_json()
        else:
            editor._cancel_json()
        QApplication.processEvents()
        assert editor.size() == original
    editor.reject()


def test_editor_body_grows_with_added_content(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    editor = policy_editor.PolicyEditorDialog(None, set(), edit_fixture.parent)
    editor.show()
    QApplication.processEvents()
    initial = editor.height()
    for _ in range(8):
        editor._add_condition(None)
    editor._set_policy_errors(tuple(f"Condition error {index}" for index in range(12)))
    QApplication.processEvents()
    assert editor.height() > initial
    assert editor.save_button.isVisible()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.reject()


def test_invalid_trigger_type_can_be_repaired_in_form(edit_fixture: SimpleNamespace) -> None:
    raw = {**edit_fixture.raw, "triggers": [{"type": {"invalid": True}}]}
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, None, ()), set(), edit_fixture.parent
    )
    assert "triggers" not in editor._form_payload()
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
    raw["scope"] = {"note_types": [{"name": "Basic"}]}
    raw.pop("match", None)
    raw.pop("conditions", None)
    raw["actions"] = [{"type": "replace_tags", "tags": []}]
    editor.json_text.setPlainText(json.dumps(raw))
    editor.save_button.click()
    assert editor.result_policy is None
    assert not editor._json_mode
    assert editor.save_button.text() == "Save"
    result = editor._policy_from_form()
    assert result.id == edit_fixture.policy.id
    assert result.scope.all_decks
    assert result.scope.note_types == (NoteTypeSelector("Basic"),)
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
    edit_fixture: SimpleNamespace,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor._toggle_json()
    editor.json_text.setPlainText("{")
    editor._accept()
    assert editor._json_mode
    assert editor.name.text() == "Leeches"
    assert editor._policy_from_form() is None
    assert "Policy JSON has errors" in editor.policy_error_warning.text()
    assert "Expecting property name" in editor.policy_error_warning.text()
    assert not editor.policy_error_warning.isHidden()


def test_scheduler_incompatible_json_applies_before_save_validation(
    edit_fixture: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, edit_fixture.raw, edit_fixture.policy, ()), set(), edit_fixture.parent
    )
    editor._toggle_json()
    raw = json.loads(editor.json_text.toPlainText())
    raw["conditions"] = [{"type": "fsrs_stability", "days": 30, "operator": "gte"}]
    editor.json_text.setPlainText(json.dumps(raw))

    editor._accept()

    assert not editor._json_mode
    assert editor.policy_error_warning.isHidden()
    assert editor._conditions[0].kind.currentData() == "fsrs_stability"
    assert "FSRS conditions require FSRS to be enabled" in editor.conditions_error_warning.text()
    monkeypatch.setattr(policy_editor, "showWarning", lambda *_args, **_kwargs: None)
    editor._accept()
    assert editor.result_policy is None
    assert "FSRS conditions require FSRS to be enabled" in editor.conditions_error_warning.text()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor.close()


def test_cancel_json_restores_saved_policy_errors(
    edit_fixture: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        **edit_fixture.raw,
        "scope": {"decks": [{"deck": "Definitely Missing Deck", "include_subdecks": True}]},
    }
    policy = parse_policy(raw)
    editor = policy_editor.PolicyEditorDialog(
        PolicyRecord(0, raw, policy, ()), set(), edit_fixture.parent
    )
    assert "deck 'Definitely Missing Deck' not found" in editor.scope_error_warning.text()
    editor._toggle_json()
    editor.json_text.setPlainText("{")
    editor._apply_json()
    assert "Policy JSON has errors" in editor.policy_error_warning.text()
    monkeypatch.setattr(policy_editor, "askUser", lambda *_args, **_kwargs: True)
    editor._cancel_json()
    assert editor.policy_error_warning.isHidden()
    assert "deck 'Definitely Missing Deck' not found" in editor.scope_error_warning.text()
    editor.close()


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
    dialog = cleanup_preview.CleanupPreviewDialog(
        tuple(
            cleanup_preview.PreviewRow(card_id, ("Policy",), (), ("Reason",)) for card_id in (1, 2)
        ),
        parent,
        view="conflicts",
    )
    browsed = []
    monkeypatch.setattr(
        cleanup_preview, "open_cards_in_browser", lambda ids, **_kwargs: browsed.append(ids)
    )
    dialog.browse_button.click()
    dialog.table.selectRow(1)
    assert dialog.browse_button.text() == "Browse"
    dialog.browse_button.click()
    assert browsed == [{1, 2}, {2}]
    dialog.close()
