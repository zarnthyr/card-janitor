# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from anki.collection import OpChanges
from aqt.qt import QApplication, QDialog, QDialogButtonBox, QDir, QStyleOptionViewItem, Qt, QWidget
from card_janitor import history_dialog
from card_janitor.actions import ExecutionResult, build_execution_plan
from card_janitor.engine import CardFacts, ResolvedAction, evaluate_facts
from card_janitor.history_dialog import (
    CleanupHistoryDialog,
    _coalesced_effects,
    _history_record_fallback,
)
from card_janitor.history_events import (
    CleanupEvent,
    CleanupEventContext,
    EntityCounts,
    EventAction,
    FailureRecord,
    Invocation,
    PolicyActivation,
    Producer,
    TerminalState,
    build_cleanup_event,
    build_success_event,
)
from card_janitor.history_semantics import (
    ExecutionLedger,
    ExecutionLedgerStep,
    ExecutionLedgerTarget,
    PolicyMatchProvenance,
)
from card_janitor.history_store import SOURCE_ID_PROFILE_KEY, HistoryRecord, HistoryStore
from card_janitor.models import (
    AllConditions,
    AnyConditions,
    CardStateCondition,
    DeckSelector,
    IntervalCondition,
    Policy,
    ReviewHistoryCondition,
    Scope,
    SuspendAction,
    TagAction,
    TagCondition,
    Trigger,
    UnsuspendAction,
)
from card_janitor.presentation import LAST_CLEANUP_KEY

pytestmark = pytest.mark.usefixtures("_application")


@pytest.fixture(scope="module")
def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def cleanup_event(
    source_id: str,
    *,
    index: int = 0,
    automatic: bool = False,
    changed: bool = True,
    alternatives: bool = True,
    note_wide_sibling: bool = False,
    unavailable_provenance: bool = False,
    failed: bool = False,
    mixed_alternatives: bool = False,
    multiple_actions: bool = False,
    nested_conditions: bool = False,
    mixed_siblings: bool = False,
    invocation_events: tuple[str, ...] | None = None,
    activation_triggers: tuple[str, ...] | None = None,
) -> CleanupEvent:
    conditions = (
        AnyConditions(
            (IntervalCondition(100, "gte"), TagCondition(("matched",), "contains_any"))
            if mixed_alternatives
            else (IntervalCondition(1, "gte"), IntervalCondition(200, "lte"))
        )
        if alternatives
        else IntervalCondition(1, "gte")
    )
    if nested_conditions:
        conditions = AllConditions(
            (
                IntervalCondition(1, "gte"),
                AnyConditions(
                    (IntervalCondition(100, "gte"), TagCondition(("matched",), "contains_any"))
                ),
            )
        )
    actions = (
        (TagAction("history-marked"), SuspendAction())
        if multiple_actions
        else (SuspendAction("note" if note_wide_sibling else "card"),)
    )
    policy_trigger_types = activation_triggers or (("daily",) if automatic else ())
    policy = Policy(
        id="audit-policy",
        name="Audit <Policy>",
        triggers=tuple(Trigger(trigger) for trigger in policy_trigger_types),
        scope=Scope((DeckSelector("Mining"),)),
        conditions=conditions,
        actions=actions,
    )
    item = CardFacts(
        1,
        10,
        1,
        0,
        -1 if (note_wide_sibling and not mixed_siblings) or not changed else 2,
        2,
        100,
        1_000,
        2_000,
        frozenset(),
    )
    resolved_actions = tuple(ResolvedAction(action) for action in policy.actions)
    cards = [item]
    if mixed_alternatives:
        cards = [
            item,
            replace(item, card_id=2, note_id=20, interval=1, tags=frozenset({"matched"})),
            replace(item, card_id=3, note_id=30, interval=100, tags=frozenset({"matched"})),
        ]
    report = evaluate_facts(
        policy,
        cards,
        {1},
        resolved_actions,
        now_ms=3_000,
        collect_provenance=True,
    )
    if unavailable_provenance:
        report = replace(
            report,
            match_provenance=PolicyMatchProvenance(
                "unavailable",
                reason_code="trace_evaluation_failed",
            ),
        )
    target_ids = (1, 2, 3) if mixed_alternatives else (1,)
    if note_wide_sibling:
        sibling = replace(item, card_id=2, deck_id=2, queue=2)
        actionable = (item, sibling) if mixed_siblings else (sibling,)
        report = replace(
            report,
            actionable=actionable,
            card_actions=((item, resolved_actions), (sibling, resolved_actions)),
            evaluation_actionable_cards=len(actionable),
        )
        target_ids = tuple(card.card_id for card in actionable)
    plan = build_execution_plan((report,), collect_history=True)
    steps = (
        (
            ExecutionLedgerStep(
                0,
                "add_tag",
                "note",
                (ExecutionLedgerTarget("history-marked", (10,)),),
                "completed",
            ),
            ExecutionLedgerStep(
                1,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, target_ids),),
                "completed",
            ),
        )
        if multiple_actions
        else (
            ExecutionLedgerStep(
                0,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, target_ids),),
                "failed_unknown" if failed else "completed",
            ),
        )
    )
    ledger = ExecutionLedger(
        "partial" if failed else "complete",
        steps if changed or note_wide_sibling else (),
        effects_complete=not failed,
        unknown_effects_possible=failed,
    )
    result = ExecutionResult(
        OpChanges(),
        len(target_ids) if changed or note_wide_sibling else 0,
        0,
        plan.semantics,
        ledger,
    )
    started = datetime(2026, 9, 23, 12, tzinfo=UTC) + timedelta(minutes=index)
    effective_invocation_events = invocation_events or policy_trigger_types
    invocation = (
        Invocation("automatic", effective_invocation_events) if automatic else Invocation("manual")
    )
    context = CleanupEventContext(
        str(uuid4()),
        source_id,
        Producer("0.8.0", "26.8.1"),
        started,
        started,
        invocation,
        (
            PolicyActivation(
                policy.id,
                invocation.kind,
                policy_trigger_types,
            ),
        ),
    )
    if failed:
        return build_cleanup_event(
            context=context,
            policies=(policy,),
            semantics=plan.semantics,
            ledger=ledger,
            terminal=TerminalState(
                "failed",
                "execution",
                FailureRecord("backend_error", "Anki could not apply the requested change."),
            ),
        )
    return build_success_event(context=context, policies=(policy,), result=result)


def test_dialog_selects_newest_event_and_renders_audit_details(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    older = cleanup_event(source_id, index=1)
    newest = cleanup_event(source_id, index=2, automatic=True)
    store.append(older)
    store.append(newest)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)

    assert dialog.table.rowCount() == 2
    assert dialog.table.currentRow() == 0
    assert dialog._records[0].event == newest
    assert dialog.table.columnCount() == 6
    assert [dialog.table.horizontalHeaderItem(column).text() for column in range(6)] == [
        "Time",
        "Source",
        "Trigger",
        "Result",
        "Cards",
        "Policies",
    ]
    assert dialog.table.item(0, 0).text().startswith("Sep 23, 2026 ")
    assert dialog.table.item(0, 1).text() == "Automatic"
    assert dialog.table.item(0, 2).text() == "Daily"
    assert dialog.table.item(0, 3).text() == "Changed"
    assert dialog.table.item(0, 4).text() == "1"
    assert dialog.table.item(0, 5).text() == "1"
    details = dialog.details.toPlainText()
    assert "Audit <Policy>" in details
    assert "Interval ≥ 1d OR Interval ≤ 200d" in details
    assert "    ↳ 1 · Interval ≥ 1d + Interval ≤ 200d" in details
    assert "Suspended" in details
    assert "1 card" in details
    assert "Complete" not in details
    assert "/conditions" not in details
    assert "m0" not in details
    assert "Exact policy definition" not in details
    assert newest.event_id not in details
    assert newest.source_id not in details
    assert newest.policies[0].definition_hash not in details
    assert newest.producer.anki_version not in details
    assert "Sep 23" not in details
    assert "card changed" not in details
    dialog.close()
    parent.deleteLater()


def test_dialog_pages_older_records_without_reordering(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    events = tuple(cleanup_event(source_id, index=index) for index in range(3))
    for event in events:
        store.append(event)
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store, page_size=2)

    assert [record.event for record in dialog._records] == [events[2], events[1]]
    assert dialog.load_older_button.isEnabled()

    dialog._load_older()

    assert [record.event for record in dialog._records] == [events[2], events[1], events[0]]
    assert not dialog.load_older_button.isEnabled()
    dialog.close()
    parent.deleteLater()


def test_history_columns_and_controls_have_deliberate_layout(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, automatic=True))
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store)
    dialog.show()
    QApplication.processEvents()

    metrics = dialog.table.fontMetrics()
    assert dialog.table.columnWidth(0) >= metrics.horizontalAdvance("Sep 23, 2026 19:50") + 20
    assert dialog.table.columnWidth(1) >= metrics.horizontalAdvance("Automatic") + 20
    assert dialog.table.columnWidth(2) >= 120
    assert dialog.table.columnWidth(3) >= 120
    assert dialog.table.columnWidth(4) >= metrics.horizontalAdvance("Cards") + 20
    assert dialog.table.columnWidth(5) >= metrics.horizontalAdvance("Policies") + 20
    assert dialog.table.columnWidth(0) < dialog.table.columnWidth(2) * 2

    assert dialog.intro_layout.indexOf(dialog.delete_button) < dialog.intro_layout.indexOf(
        dialog.export_button
    )
    assert dialog.footer_layout.itemAt(0).widget() is dialog.load_older_button
    footer_buttons = dialog.footer_layout.itemAt(dialog.footer_layout.count() - 1).widget()
    assert isinstance(footer_buttons, QDialogButtonBox)
    assert footer_buttons.standardButtons() == QDialogButtonBox.StandardButton.Close
    dialog.close()
    parent.deleteLater()


def test_trigger_uses_policy_activation_not_all_invocation_events(tmp_path: Path) -> None:
    source_id = str(uuid4())
    event = cleanup_event(
        source_id,
        automatic=True,
        invocation_events=("on_open", "on_sync"),
        activation_triggers=("on_sync",),
    )
    assert event.invocation.events == ("on_open", "on_sync")
    assert event.policy_results[0].activation.triggers == ("on_sync",)
    store = HistoryStore(source_id, root=tmp_path)
    store.append(event)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)

    assert dialog.table.item(0, 1).text() == "Automatic"
    assert dialog.table.item(0, 2).text() == "On sync"
    assert "On open" not in dialog.table.item(0, 2).text()
    dialog.close()
    parent.deleteLater()


def test_simple_success_omits_schema_inspection_details(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    event = cleanup_event(source_id, alternatives=False)
    store.append(event)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    details = dialog.details.toPlainText()

    assert details.startswith("Changes made\n")
    assert "Interval ≥ 1d" in details
    assert "Suspended" in details
    assert "1 card" in details
    assert "Matched alternatives" not in details
    assert "Not applied" not in details
    assert "Execution" not in details
    assert "Policy ID" not in details
    assert "history schema" not in details
    changes = dialog.details.findChildren(history_dialog._DetailsTable)[0]
    assert [changes.horizontalHeaderItem(column).text() for column in range(4)] == [
        "Policy",
        "Change",
        "Affected",
        "Why",
    ]
    assert changes.item(0, 0).text() == "Audit <Policy>"
    assert "Scope:\nMining" in changes.item(0, 0).toolTip()
    assert "Conditions:\nInterval ≥ 1 days" in changes.item(0, 0).toolTip()
    dialog.show()
    QApplication.processEvents()
    QApplication.processEvents()
    assert changes.columnWidth(3) > changes.columnWidth(0)
    assert changes.columnWidth(3) > changes.columnWidth(1)
    assert changes.columnWidth(2) >= 90
    assert changes.item(0, 0).textAlignment() & Qt.AlignmentFlag.AlignTop.value
    assert dialog.table.horizontalHeader().font() == changes.horizontalHeader().font()
    assert not changes.horizontalHeader().font().bold()
    assert all(
        not changes.horizontalHeaderItem(column).font().bold()
        for column in range(changes.columnCount())
    )
    dialog.close()
    parent.deleteLater()


def test_selection_changes_are_deferred_and_coalesced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, index=1))
    store.append(cleanup_event(source_id, index=2))
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store)
    selected = []
    monkeypatch.setattr(dialog.details, "set_record", selected.append)

    dialog._selection_changed(1, 0, 0, 0)
    dialog._selection_changed(0, 0, 1, 0)

    assert selected == []
    QApplication.processEvents()
    assert selected == [dialog._records[0]]
    dialog.close()
    parent.deleteLater()


def test_multiple_changes_share_one_policy_and_why_group(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, alternatives=False, multiple_actions=True))
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    changes = dialog.details.findChildren(history_dialog._DetailsTable)[0]

    assert changes.rowCount() == 2
    assert changes.rowSpan(0, 0) == 2
    assert changes.rowSpan(0, 3) == 2
    assert changes.item(0, 0).text() == "Audit <Policy>"
    assert {changes.item(row, 1).text() for row in range(2)} == {
        "Added tag 'history-marked'",
        "Suspended",
    }
    assert changes.item(0, 3).text() == "Interval ≥ 1d"
    dialog.show()
    QApplication.processEvents()
    QApplication.processEvents()
    assert max(changes.rowHeight(row) for row in range(2)) <= changes.fontMetrics().height() + 8
    dialog.close()
    parent.deleteLater()


@pytest.mark.parametrize(
    "change",
    [
        "Added tag 'cj_test_learning'",
        "Replaced tag 'a_very_long_source_tag' with 'a_very_long_replacement_tag'",
        "Moved to 'Mining::Languages::Japanese::Long-term study material'",
    ],
)
def test_wrapped_change_rows_use_width_aware_height_without_expanding_siblings(
    tmp_path: Path,
    change: str,
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, alternatives=False, multiple_actions=True))
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store)
    dialog.resize(560, 650)
    dialog.show()
    QApplication.processEvents()
    QApplication.processEvents()
    changes = dialog.details.findChildren(history_dialog._DetailsTable)[0]
    changes.item(0, 1).setText(change)
    changes._refit()
    QApplication.processEvents()
    option = QStyleOptionViewItem()
    option.initFrom(changes)
    hint = changes.itemDelegateForColumn(1).sizeHint(option, changes.model().index(0, 1))

    assert changes.rowHeight(0) >= hint.height()
    dialog.close()
    parent.deleteLater()


def test_closing_history_reactivates_parent_after_child_dialogs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store)
    focus_events = []
    monkeypatch.setattr(parent, "raise_", lambda: focus_events.append("raised"))
    monkeypatch.setattr(parent, "activateWindow", lambda: focus_events.append("activated"))

    dialog.done(QDialog.DialogCode.Rejected)
    QApplication.processEvents()

    assert focus_events == ["raised", "activated"]
    parent.deleteLater()


def test_logical_group_shading_keeps_effect_rows_together() -> None:
    parent = QWidget()
    table = history_dialog._DetailsTable(("Policy", "Change"), parent)
    table.setRowCount(3)
    for row in range(3):
        for column in range(2):
            table.setItem(row, column, history_dialog._table_item(f"{row}:{column}"))

    table.shade_group(0, 2, 0)
    table.shade_group(2, 1, 1)

    assert table.item(0, 0).background() == table.item(1, 0).background()
    assert table.item(1, 1).background() != table.item(2, 1).background()
    parent.deleteLater()


def test_nested_conditions_stay_compact_and_understandable_in_why(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, nested_conditions=True))
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    changes = dialog.details.findChildren(history_dialog._DetailsTable)[0]
    why = changes.item(0, 3)

    assert "Interval ≥ 1d + (Interval ≥ 100d OR Tags contain any of 'matched')" in why.text()
    assert "Conditions:" in why.toolTip()
    assert "OR Tags contain any of 'matched'" in why.toolTip()
    dialog.close()
    parent.deleteLater()


def test_grouped_alternative_uses_the_same_concise_language_and_parentheses() -> None:
    grouped = AllConditions(
        (
            CardStateCondition(("learning", "relearning")),
            ReviewHistoryCondition("exists"),
        )
    )
    condition = AnyConditions((ReviewHistoryCondition("not_exists"), grouped))

    assert history_dialog._condition_summary(condition) == (
        "Never studied OR (State: Learning, Relearning + Studied)"
    )
    assert history_dialog._alternative_label(grouped) == ("(State: Learning, Relearning + Studied)")
    assert "( " not in history_dialog._condition_summary(condition)
    assert " )" not in history_dialog._condition_summary(condition)


def test_or_provenance_is_grouped_in_condition_language(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    event = cleanup_event(source_id, mixed_alternatives=True)
    assert len(event.effects) == 3
    store.append(event)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    details = dialog.details.toPlainText()

    assert "    ↳ 1 · Interval only" in details
    assert "    ↳ 1 · Tags only" in details
    assert "    ↳ 1 · Interval + Tags" in details
    changes = dialog.details.findChildren(history_dialog._DetailsTable)[0]
    why_lines = changes.item(0, 3).text().splitlines()
    assert all(line.startswith("    ↳") for line in why_lines[1:])
    assert "Within" not in changes.item(0, 3).text()
    assert details.count("Suspended") == 1
    assert "3 cards" in details
    assert "m0" not in details
    assert "/conditions" not in details
    dialog.close()
    parent.deleteLater()


def test_overlapping_policies_are_named_without_repeating_the_shared_effect(
    tmp_path: Path,
) -> None:
    source_id = str(uuid4())
    policies = tuple(
        Policy(
            id=policy_id,
            name=name,
            triggers=(Trigger(trigger),),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=IntervalCondition(1, "gte"),
            actions=(SuspendAction(),),
        )
        for policy_id, name, trigger in (
            ("first", "First policy", "on_open"),
            ("second", "Second policy", "on_sync"),
        )
    )
    item = CardFacts(1, 10, 1, 0, 2, 2, 100, 1_000, 2_000, frozenset())
    items = [item, replace(item, card_id=2, note_id=20, queue=-1)]
    reports = tuple(
        evaluate_facts(
            policy,
            items,
            {1},
            (ResolvedAction(policy.actions[0]),),
            now_ms=3_000,
            collect_provenance=True,
        )
        for policy in policies
    )
    plan = build_execution_plan(reports, collect_history=True)
    ledger = ExecutionLedger(
        "complete",
        (
            ExecutionLedgerStep(
                0,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, (1,)),),
                "completed",
            ),
        ),
        effects_complete=True,
        unknown_effects_possible=False,
    )
    started = datetime(2026, 9, 23, 12, tzinfo=UTC)
    context = CleanupEventContext(
        str(uuid4()),
        source_id,
        Producer("0.8.0", "26.8.1"),
        started,
        started,
        Invocation("automatic", ("on_open", "on_sync")),
        tuple(
            PolicyActivation(policy.id, "automatic", (policy.triggers[0].type,))
            for policy in policies
        ),
    )
    event = build_success_event(
        context=context,
        policies=policies,
        result=ExecutionResult(OpChanges(), 1, 0, plan.semantics, ledger),
    )
    store = HistoryStore(source_id, root=tmp_path)
    store.append(event)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    details = dialog.details.toPlainText()

    assert "First policy" in details
    assert "Second policy" in details
    assert details.count("Suspended") == 1
    assert details.count("First policy + Second policy") == 1
    assert "First policy: Interval ≥ 1d" in details
    assert "Second policy: Interval ≥ 1d" in details
    assert "Already suspended" not in details
    assert event.non_applied
    assert "first @" not in details
    assert "second @" not in details
    assert dialog.table.item(0, 2).text() == "On open and On sync"
    dialog.close()
    parent.deleteLater()


def test_dialog_labels_successful_no_op_without_claiming_changes(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, changed=False))
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)

    assert dialog.table.item(0, 2).text() == "—"
    assert dialog.table.item(0, 3).text() == "No change"
    assert dialog.table.item(0, 4).text() == "0"
    details = dialog.details.toPlainText()
    assert "No changes were needed" in details
    assert "Policies evaluated" in details
    assert "Already suspended" not in details
    assert "Not applied" not in details
    dialog.show()
    QApplication.processEvents()
    QApplication.processEvents()
    fallback = dialog.details.findChildren(history_dialog._DetailsTable)[0]
    assert fallback.columnWidth(2) >= 160
    dialog.close()
    parent.deleteLater()


def test_non_applied_conflict_remains_in_event_but_is_omitted_from_viewer(
    tmp_path: Path,
) -> None:
    source_id = str(uuid4())
    policies = (
        Policy(
            id="suspend",
            name="Suspend policy",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=IntervalCondition(1, "gte"),
            actions=(SuspendAction(),),
        ),
        Policy(
            id="unsuspend",
            name="Unsuspend policy",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=IntervalCondition(1, "gte"),
            actions=(UnsuspendAction(),),
        ),
    )
    card = CardFacts(1, 10, 1, 0, 2, 2, 100, 1_000, 2_000, frozenset())
    reports = tuple(
        evaluate_facts(
            policy,
            [card],
            {1},
            (ResolvedAction(policy.actions[0]),),
            now_ms=3_000,
            collect_provenance=True,
        )
        for policy in policies
    )
    plan = build_execution_plan(reports, collect_history=True)
    started = datetime(2026, 9, 23, 12, tzinfo=UTC)
    context = CleanupEventContext(
        str(uuid4()),
        source_id,
        Producer("0.8.0", "26.8.1"),
        started,
        started,
        Invocation("manual"),
        tuple(PolicyActivation(policy.id, "manual") for policy in policies),
    )
    event = build_success_event(
        context=context,
        policies=policies,
        result=ExecutionResult(
            OpChanges(),
            0,
            1,
            plan.semantics,
            ExecutionLedger(
                "complete",
                (),
                effects_complete=True,
                unknown_effects_possible=False,
            ),
        ),
    )
    store = HistoryStore(source_id, root=tmp_path)
    store.append(event)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    tables = dialog.details.findChildren(history_dialog._DetailsTable)

    assert event.non_applied
    assert dialog._records[0].event is not None
    assert dialog._records[0].event.non_applied == event.non_applied
    assert len(tables) == 1
    assert [tables[0].horizontalHeaderItem(column).text() for column in range(3)] == [
        "Policy",
        "Why",
        "Intended action",
    ]
    assert "Conflicting actions" not in dialog.details.toPlainText()
    assert "Not applied" not in dialog.details.toPlainText()
    assert "Cleanup failed" not in dialog.details.toPlainText()
    dialog.close()
    parent.deleteLater()


def test_non_applied_provenance_remains_recorded_but_is_not_displayed(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    event = cleanup_event(source_id, changed=False, mixed_alternatives=True)
    assert len(event.non_applied) == 3
    store.append(event)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    details = dialog.details.toPlainText()

    assert "Already suspended" not in details
    assert "Not applied" not in details
    assert "Policies evaluated" in details
    assert "    ↳ 1 · Interval only" in details
    assert len(event.non_applied) == 3
    dialog.close()
    parent.deleteLater()


def test_zero_match_no_op_uses_policies_evaluated_fallback(tmp_path: Path) -> None:
    source_id = str(uuid4())
    policy = Policy(
        id="no-match",
        name="No matching cards",
        triggers=(),
        scope=Scope((DeckSelector("Mining"),)),
        conditions=IntervalCondition(1_000, "gte"),
        actions=(SuspendAction(),),
    )
    card = CardFacts(1, 10, 1, 0, 2, 2, 100, 1_000, 2_000, frozenset())
    report = evaluate_facts(
        policy,
        [card],
        {1},
        (ResolvedAction(policy.actions[0]),),
        now_ms=3_000,
        collect_provenance=True,
    )
    plan = build_execution_plan((report,), collect_history=True)
    started = datetime(2026, 9, 23, 12, tzinfo=UTC)
    context = CleanupEventContext(
        str(uuid4()),
        source_id,
        Producer("0.8.0", "26.8.1"),
        started,
        started,
        Invocation("manual"),
        (PolicyActivation(policy.id, "manual"),),
    )
    event = build_success_event(
        context=context,
        policies=(policy,),
        result=ExecutionResult(
            OpChanges(),
            0,
            0,
            plan.semantics,
            ExecutionLedger(
                "complete",
                (),
                effects_complete=True,
                unknown_effects_possible=False,
            ),
        ),
    )
    store = HistoryStore(source_id, root=tmp_path)
    store.append(event)
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    fallback = dialog.details.findChildren(history_dialog._DetailsTable)[0]

    assert "Policies evaluated" in dialog.details.toPlainText()
    assert [fallback.horizontalHeaderItem(column).text() for column in range(3)] == [
        "Policy",
        "Why",
        "Intended action",
    ]
    assert fallback.item(0, 1).text() == "Interval ≥ 1000d"
    assert fallback.item(0, 2).text() == "Suspend cards"
    dialog.close()
    parent.deleteLater()


def test_note_wide_sibling_effect_explains_trigger_and_consequence(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, alternatives=False, note_wide_sibling=True))
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    details = dialog.details.toPlainText()

    assert "Suspended" in details
    assert "1 sibling card" in details
    assert "Already suspended" not in details
    dialog.close()
    parent.deleteLater()


def test_mixed_note_wide_effect_keeps_sibling_causality_visible(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(
        cleanup_event(
            source_id,
            alternatives=False,
            note_wide_sibling=True,
            mixed_siblings=True,
        )
    )
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    changes = dialog.details.findChildren(history_dialog._DetailsTable)[0]

    assert changes.item(0, 2).text() == "2 cards (1 sibling)"
    assert "different card of the same note matched" in changes.item(0, 2).toolTip()
    dialog.close()
    parent.deleteLater()


def test_display_coalescing_keeps_different_sibling_shapes_separate() -> None:
    source_id = str(uuid4())
    event = cleanup_event(source_id, alternatives=False, note_wide_sibling=True)
    sibling_effect = event.effects[0]
    matching_effect = replace(
        sibling_effect,
        counts=EntityCounts(
            cards=1,
            notes=1,
            matching_trigger_cards=1,
            consequential_sibling_cards=0,
        ),
    )

    displayed = _coalesced_effects(replace(event, effects=(sibling_effect, matching_effect)))

    assert len(displayed) == 2


def test_display_coalescing_keeps_different_action_parameters_separate() -> None:
    source_id = str(uuid4())
    event = cleanup_event(source_id, alternatives=False)
    first = replace(event.effects[0], action=EventAction("add_tag", tag="first"))
    second = replace(event.effects[0], action=EventAction("add_tag", tag="second"))

    displayed = _coalesced_effects(replace(event, effects=(first, second)))

    assert len(displayed) == 2


def test_note_and_card_counts_use_parenthetical_affected_wording() -> None:
    counts = history_dialog._DisplayedCounts(4, 2, 2, 0)

    assert history_dialog._affected_text(EventAction("delete_note"), counts) == (
        "2 notes (4 cards)"
    )


def test_unavailable_provenance_is_shown_as_an_exception_not_internal_codes(
    tmp_path: Path,
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, unavailable_provenance=True))
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    details = dialog.details.toPlainText()

    assert "Detailed match reasons unavailable" in details
    assert "trace_evaluation_failed" not in details
    assert "m0" not in details
    changes = dialog.details.findChildren(history_dialog._DetailsTable)[0]
    assert "Complete match provenance could not be collected" in changes.item(0, 3).toolTip()
    dialog.close()
    parent.deleteLater()


def test_failed_execution_explains_uncertainty_without_dumping_ledger(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id, alternatives=False, failed=True))
    parent = QWidget()

    dialog = CleanupHistoryDialog({}, parent, store=store)
    details = dialog.details.toPlainText()

    assert "Changes may have occurred, but their number is unknown" in details
    assert "Cleanup failed while applying changes" in details
    assert "Anki could not apply the requested change" in details
    assert "Suspending cards failed for 1 card; whether it changed is unknown" in details
    assert "Policies evaluated" in details
    assert "Execution ledger" not in details
    assert "failed_unknown" not in details
    dialog.close()
    parent.deleteLater()


def test_dialog_exposes_corrupt_and_unsupported_records(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("{broken\n")
        handle.write(
            json.dumps(
                {
                    "schema": {"name": "card_janitor.cleanup_history", "version": 999},
                    "event_id": str(uuid4()),
                }
            )
            + "\n"
        )
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store)

    assert dialog.table.item(0, 3).text() == "Unsupported"
    assert dialog.table.item(1, 3).text() == "Corrupt"
    dialog.table.setCurrentCell(0, 0)
    assert "cannot interpret" in dialog.details.toPlainText()
    dialog.table.setCurrentCell(1, 0)
    QApplication.processEvents()
    assert "could not be decoded" in dialog.details.toPlainText()
    dialog.close()
    parent.deleteLater()


def test_export_copies_full_file_not_only_loaded_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path / "history")
    store.append(cleanup_event(source_id, index=1))
    store.append(cleanup_event(source_id, index=2))
    destination = tmp_path / "export.jsonl"
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store, page_size=1)
    chooser_arguments = []

    def choose_export(*args: object, **_kwargs: object) -> tuple[str, str]:
        chooser_arguments.append(args)
        return str(destination), "JSON Lines (*.jsonl)"

    monkeypatch.setattr(
        history_dialog.QFileDialog,
        "getSaveFileName",
        choose_export,
    )
    monkeypatch.setattr(history_dialog, "tooltip", lambda *_args, **_kwargs: None)
    focus_events = []
    monkeypatch.setattr(dialog, "raise_", lambda: focus_events.append("raised"))
    monkeypatch.setattr(dialog, "activateWindow", lambda: focus_events.append("activated"))
    original = store.path.read_bytes()

    dialog._export()

    assert dialog.table.rowCount() == 1
    assert destination.read_bytes() == original
    assert store.path.read_bytes() == original
    assert Path(chooser_arguments[0][2]).parent == Path(QDir.homePath())
    assert focus_events == ["raised", "activated"]
    dialog.close()
    parent.deleteLater()


def test_default_export_name_uses_local_export_minute() -> None:
    local_zone = datetime.now().astimezone().tzinfo
    exported_at = datetime(2026, 9, 24, 2, 5, 49, tzinfo=local_zone)

    path = Path(history_dialog._default_export_path(exported_at))

    assert path.parent == Path(QDir.homePath())
    assert path.name == "card-janitor-cleanup-history-2026-09-24-0205.jsonl"


def test_cancelled_export_restores_history_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store)
    monkeypatch.setattr(
        history_dialog.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("", ""),
    )
    focus_events = []
    monkeypatch.setattr(dialog, "raise_", lambda: focus_events.append("raised"))
    monkeypatch.setattr(dialog, "activateWindow", lambda: focus_events.append("activated"))

    dialog._export()

    assert focus_events == ["raised", "activated"]
    dialog.close()
    parent.deleteLater()


def test_delete_is_permanent_and_removes_legacy_last_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = str(uuid4())
    profile: dict[str, object] = {
        SOURCE_ID_PROFILE_KEY: source_id,
        LAST_CLEANUP_KEY: {"summary": "stale"},
    }
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))
    refreshed = []
    parent = QWidget()
    dialog = CleanupHistoryDialog(
        profile,
        parent,
        store=store,
        on_cleared=lambda: refreshed.append(True),
    )
    confirmations = []

    def confirm(message: str, **kwargs: object) -> bool:
        confirmations.append((message, kwargs))
        return True

    monkeypatch.setattr(history_dialog, "askUser", confirm)
    focus_events = []
    monkeypatch.setattr(dialog, "raise_", lambda: focus_events.append("raised"))
    monkeypatch.setattr(dialog, "activateWindow", lambda: focus_events.append("activated"))

    assert dialog.delete_button.text() == "Delete…"
    dialog._delete_history()

    assert "complete cleanup history" in confirmations[0][0]
    assert "cannot be undone" in confirmations[0][0]
    assert confirmations[0][1]["title"] == "Delete Cleanup History"
    assert not store.directory.exists()
    assert LAST_CLEANUP_KEY not in profile
    assert dialog.table.rowCount() == 0
    assert "No cleanup history" in dialog.details.toPlainText()
    assert not dialog.export_button.isEnabled()
    assert not dialog.delete_button.isEnabled()
    assert refreshed == [True]
    assert focus_events == ["raised", "activated"]
    dialog.close()
    parent.deleteLater()


def test_cancelled_delete_restores_history_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))
    parent = QWidget()
    dialog = CleanupHistoryDialog({}, parent, store=store)
    monkeypatch.setattr(history_dialog, "askUser", lambda *_args, **_kwargs: False)
    focus_events = []
    monkeypatch.setattr(dialog, "raise_", lambda: focus_events.append("raised"))
    monkeypatch.setattr(dialog, "activateWindow", lambda: focus_events.append("activated"))

    dialog._delete_history()

    assert store.path.is_file()
    assert focus_events == ["raised", "activated"]
    dialog.close()
    parent.deleteLater()


def test_opening_empty_history_does_not_create_profile_identity() -> None:
    profile: dict[str, object] = {}
    parent = QWidget()

    dialog = CleanupHistoryDialog(profile, parent)

    assert profile == {}
    assert dialog.table.rowCount() == 0
    assert not dialog.export_button.isEnabled()
    assert not dialog.delete_button.isEnabled()
    dialog.close()
    parent.deleteLater()


def test_delete_failure_keeps_history_and_legacy_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_id = str(uuid4())
    profile: dict[str, object] = {LAST_CLEANUP_KEY: {"summary": "keep"}}
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))
    parent = QWidget()
    dialog = CleanupHistoryDialog(profile, parent, store=store)
    warnings = []
    monkeypatch.setattr(history_dialog, "askUser", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        history_dialog, "showWarning", lambda message, **_kwargs: warnings.append(message)
    )
    monkeypatch.setattr(store, "clear", lambda: (_ for _ in ()).throw(OSError("denied")))

    dialog._delete_history()

    assert store.path.is_file()
    assert LAST_CLEANUP_KEY in profile
    assert dialog.table.rowCount() == 1
    assert "denied" in warnings[0]
    dialog.close()
    parent.deleteLater()


def test_unreadable_record_display_is_bounded_but_explicit() -> None:
    record = HistoryRecord(7, "x" * 9_000, error="invalid JSON")

    details = _history_record_fallback(record)

    assert "Corrupt history record" in details
    assert "truncated for display" in details
    assert len(details) < 9_000
