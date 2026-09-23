# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from anki.collection import OpChanges
from card_janitor.actions import ExecutionResult, build_execution_plan
from card_janitor.engine import CardFacts, PolicyReport, ResolvedAction, evaluate_facts
from card_janitor.history_events import (
    CleanupEvent,
    CleanupEventContext,
    FailureRecord,
    HistoryEventValidationError,
    Invocation,
    PolicyActivation,
    Producer,
    TerminalState,
    build_cleanup_event,
    build_success_event,
    event_from_json,
    event_to_dict,
    event_to_json,
    make_policy_snapshot,
    validate_cleanup_event,
)
from card_janitor.history_semantics import (
    EMPTY_EXECUTION_LEDGER,
    NOT_COLLECTED_EXECUTION_LEDGER,
    ExecutionLedger,
    ExecutionLedgerStep,
    ExecutionLedgerTarget,
    PlanSemantics,
    PolicyMatchProvenance,
)
from card_janitor.models import (
    AnyConditions,
    DeckSelector,
    IntervalCondition,
    MoveAction,
    Policy,
    RemoveTagAction,
    Scope,
    SetFlagAction,
    SuspendAction,
    TagAction,
    TagCondition,
    Trigger,
)

EVENT_ID = "ec0f58fc-9846-46f0-9cd7-ca03de157cd9"
SOURCE_ID = "fc02ab54-d5e5-4568-b82e-72de33444eef"


def card(
    card_id: int = 1,
    note_id: int = 10,
    *,
    interval: int = 100,
    tags: frozenset[str] = frozenset({"marked"}),
    queue: int = 2,
) -> CardFacts:
    return CardFacts(
        card_id,
        note_id,
        1,
        0,
        queue,
        2,
        interval,
        1_000,
        2_000,
        tags,
    )


def policy(
    policy_id: str = "policy-x",
    name: str = "Policy X",
    *,
    actions: tuple[object, ...] = (SuspendAction(),),
    triggers: tuple[Trigger, ...] = (),
) -> Policy:
    return Policy(
        id=policy_id,
        name=name,
        triggers=triggers,
        scope=Scope((DeckSelector("Mining"),)),
        conditions=AnyConditions(
            (
                IntervalCondition(50, "gte"),
                TagCondition(("marked",), "contains_any"),
            )
        ),
        actions=actions,
    )


def context(
    policies: tuple[Policy, ...],
    *,
    automatic: bool = False,
) -> CleanupEventContext:
    started = datetime(2026, 9, 23, 14, 32, 9, tzinfo=UTC)
    kind = "automatic" if automatic else "manual"
    events = ("daily",) if automatic else ()
    return CleanupEventContext(
        event_id=EVENT_ID,
        source_id=SOURCE_ID,
        producer=Producer("0.8.0", "26.8.1"),
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        invocation=Invocation(kind, events),
        activations=tuple(
            PolicyActivation(item.id, kind, events if automatic else ()) for item in policies
        ),
    )


def evaluated_report(value: Policy, item: CardFacts) -> PolicyReport:
    return evaluate_facts(
        value,
        [item],
        {1},
        tuple(ResolvedAction(action) for action in value.actions),
        now_ms=3_000,
        collect_provenance=True,
    )


def successful_event(
    policies: tuple[Policy, ...],
    reports: tuple[PolicyReport, ...],
    ledger: ExecutionLedger,
) -> CleanupEvent:
    plan = build_execution_plan(reports, collect_history=True)
    result = ExecutionResult(
        OpChanges(), plan.card_count, len(plan.conflicted_card_ids), plan.semantics, ledger
    )
    return build_success_event(context=context(policies), policies=policies, result=result)


def test_policy_snapshot_hashes_exact_canonical_definition() -> None:
    original = policy()
    reordered = replace(
        original,
        conditions=AnyConditions(tuple(reversed(original.conditions.conditions))),
    )
    renamed = replace(original, name="Renamed")

    snapshot = make_policy_snapshot(original)

    assert snapshot.definition_hash.startswith("sha256:")
    assert snapshot.definition_hash != make_policy_snapshot(reordered).definition_hash
    assert snapshot.definition_hash != make_policy_snapshot(renamed).definition_hash
    mutable_copy = snapshot.definition
    mutable_copy["name"] = "Changed outside the snapshot"
    assert snapshot.definition["name"] == original.name


def test_success_event_is_self_contained_and_preserves_complete_provenance() -> None:
    value = policy()
    report = evaluated_report(value, card())
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

    event = successful_event((value,), (report,), ledger)
    serialized = event_to_dict(event)

    assert event.outcome.result == "changed"
    assert event.summary.distinct_cards_changed == 1
    assert event.policies[0].definition == make_policy_snapshot(value).definition
    provenance = event.policy_results[0].match_provenance
    assert provenance.status == "complete"
    assert provenance.signatures[0].any_nodes[0].path == ""
    assert provenance.signatures[0].any_nodes[0].matched_children == (
        "/conditions/0",
        "/conditions/1",
    )
    assert event.effects[0].contributors[0].match_signatures == ("m0",)
    assert event_to_json(event) == event_to_json(event)
    assert event_from_json(event_to_json(event)) == event

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return set(value).union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    assert not keys(serialized) & {
        "card_id",
        "note_id",
        "target_id",
        "target_ids",
        "target_deck_id",
    }
    assert serialized["execution"]["steps"][0]["targets"] == [{"count": 1}]


def test_overlapping_policies_are_contributors_to_one_physical_effect() -> None:
    first = policy("first", "First")
    second = policy("second", "Second")
    item = card()
    reports = (evaluated_report(first, item), evaluated_report(second, item))
    ledger = ExecutionLedger(
        "complete",
        (
            ExecutionLedgerStep(
                0,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, (item.card_id,)),),
                "completed",
            ),
        ),
        effects_complete=True,
        unknown_effects_possible=False,
    )

    event = successful_event((first, second), reports, ledger)

    assert len(event.effects) == 1
    assert event.effects[0].counts.cards == 1
    assert tuple(item.policy.id for item in event.effects[0].contributors) == (
        "first",
        "second",
    )
    assert event.summary.distinct_cards_changed == 1


def test_note_wide_effect_separates_trigger_from_consequential_sibling() -> None:
    value = policy(actions=(SuspendAction("note"),))
    trigger = card(1, 10)
    sibling = card(2, 10)
    report = evaluated_report(value, trigger)
    resolved = report.resolved_actions
    report = replace(
        report,
        actionable=(trigger, sibling),
        card_actions=((trigger, resolved), (sibling, resolved)),
        evaluation_actionable_cards=2,
    )
    ledger = ExecutionLedger(
        "complete",
        (
            ExecutionLedgerStep(
                0,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, (1, 2)),),
                "completed",
            ),
        ),
        effects_complete=True,
        unknown_effects_possible=False,
    )

    event = successful_event((value,), (report,), ledger)
    counts = event.effects[0].counts

    assert event.effects[0].action.type == "suspend_note"
    assert counts.cards == 2
    assert counts.notes == 1
    assert counts.matching_trigger_cards == 1
    assert counts.consequential_sibling_cards == 1
    assert event.effects[0].contributors[0].trigger_cards == 1


def test_note_wide_effect_allows_only_consequential_sibling_to_change() -> None:
    value = policy(actions=(SuspendAction("note"),))
    trigger = card(1, 10, queue=-1)
    sibling = card(2, 10)
    report = evaluated_report(value, trigger)
    resolved = report.resolved_actions
    report = replace(
        report,
        actionable=(sibling,),
        card_actions=((trigger, resolved), (sibling, resolved)),
        evaluation_actionable_cards=1,
    )
    ledger = ExecutionLedger(
        "complete",
        (
            ExecutionLedgerStep(
                0,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, (sibling.card_id,)),),
                "completed",
            ),
        ),
        effects_complete=True,
        unknown_effects_possible=False,
    )

    event = successful_event((value,), (report,), ledger)
    effect = event.effects[0]

    assert effect.counts.cards == 1
    assert effect.counts.matching_trigger_cards == 0
    assert effect.counts.consequential_sibling_cards == 1
    assert effect.contributors[0].trigger_cards == 1
    assert event_from_json(event_to_json(event)) == event
    already_satisfied = next(
        item for item in event.non_applied if item.disposition == "already_satisfied"
    )
    assert already_satisfied.counts.cards == 1
    assert already_satisfied.counts.matching_trigger_cards == 1
    assert already_satisfied.counts.consequential_sibling_cards == 0


def test_no_op_and_narrow_non_applied_outcome_are_complete() -> None:
    value = policy(actions=(SuspendAction(),))
    report = evaluated_report(value, card(queue=-1))
    event = successful_event((value,), (report,), EMPTY_EXECUTION_LEDGER)

    assert event.outcome.result == "no_change"
    assert event.outcome.effects_complete
    assert event.effects == ()
    assert len(event.non_applied) == 1
    assert event.non_applied[0].disposition == "already_satisfied"


def test_event_keeps_fresh_evaluation_counts_after_approval_filtering() -> None:
    value = policy()
    report = evaluated_report(value, card())
    approved_report = replace(report, qualifying=(), actionable=(), card_actions=())
    event = successful_event((value,), (approved_report,), EMPTY_EXECUTION_LEDGER)

    evaluation = event.policy_results[0].evaluation
    assert evaluation.qualifying_trigger_cards == 1
    assert evaluation.actionable_cards_after_expansion == 1
    assert event.effects == ()


def test_unavailable_provenance_is_explicit_and_never_invented_for_contributors() -> None:
    value = policy()
    report = replace(
        evaluated_report(value, card()),
        match_provenance=PolicyMatchProvenance(
            "unavailable",
            reason_code="trace_evaluation_failed",
        ),
    )
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

    event = successful_event((value,), (report,), ledger)

    provenance = event.policy_results[0].match_provenance
    assert provenance.status == "unavailable"
    assert provenance.reason_code == "trace_evaluation_failed"
    assert event.effects[0].contributors[0].match_signatures == ()


def test_conflicting_intentions_remain_non_applied_not_execution_failures() -> None:
    add = policy("add", "Add", actions=(TagAction("leech"),))
    remove = policy("remove", "Remove", actions=(RemoveTagAction("leech"),))
    item = card(tags=frozenset({"leech"}))
    reports = (evaluated_report(add, item), evaluated_report(remove, item))
    event = successful_event((add, remove), reports, EMPTY_EXECUTION_LEDGER)

    assert event.execution.status == "complete"
    assert event.effects == ()
    assert {record.disposition for record in event.non_applied} == {"conflict"}
    assert all(record.reason_codes for record in event.non_applied)


def test_partial_failure_records_only_known_completed_effects() -> None:
    value = policy(actions=(SetFlagAction("red"), SuspendAction()))
    report = evaluated_report(value, card())
    plan = build_execution_plan((report,), collect_history=True)
    ledger = ExecutionLedger(
        "partial",
        (
            ExecutionLedgerStep(
                0,
                "set_flag",
                "card",
                (ExecutionLedgerTarget(1, (1,)),),
                "completed",
            ),
            ExecutionLedgerStep(
                1,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, (1,)),),
                "failed_unknown",
            ),
        ),
        effects_complete=False,
        unknown_effects_possible=True,
    )

    event = build_cleanup_event(
        context=context((value,)),
        policies=(value,),
        semantics=plan.semantics,
        ledger=ledger,
        terminal=TerminalState(
            "failed",
            "execution",
            FailureRecord("backend_error", "The cleanup failed"),
        ),
    )

    assert tuple(effect.action.type for effect in event.effects) == ("set_flag",)
    assert event.outcome.result == "unknown"
    assert not event.outcome.effects_complete
    assert event.outcome.unknown_effects_possible
    assert event.execution.status == "partial"
    assert event.execution.steps[0].targets[0].parameter == "red"


def test_execution_ledger_translates_move_ids_to_historical_deck_names() -> None:
    value = policy(actions=(MoveAction("Archive"),))
    item = card()
    report = evaluate_facts(
        value,
        [item],
        {1},
        (ResolvedAction(value.actions[0], target_deck_id=2),),
        now_ms=3_000,
        collect_provenance=True,
    )
    plan = build_execution_plan((report,), collect_history=True)
    ledger = ExecutionLedger(
        "partial",
        (
            ExecutionLedgerStep(
                0,
                "move",
                "card",
                (ExecutionLedgerTarget(2, (1,)),),
                "not_attempted_due_to_failure",
            ),
        ),
        effects_complete=True,
        unknown_effects_possible=False,
    )

    event = build_cleanup_event(
        context=context((value,)),
        policies=(value,),
        semantics=plan.semantics,
        ledger=ledger,
        terminal=TerminalState(
            "failed",
            "execution",
            FailureRecord("earlier_step_failed", "Execution stopped"),
        ),
    )

    target = event.execution.steps[0].targets[0]
    assert target.parameter == "Archive"
    assert event_to_dict(event)["execution"]["steps"][0]["targets"] == [
        {"parameter": "Archive", "count": 1}
    ]


def test_undo_merge_failure_keeps_the_completed_effect_authoritative() -> None:
    value = policy(actions=(SetFlagAction("red"),))
    report = evaluated_report(value, card())
    plan = build_execution_plan((report,), collect_history=True)
    ledger = ExecutionLedger(
        "partial",
        (
            ExecutionLedgerStep(
                0,
                "set_flag",
                "card",
                (ExecutionLedgerTarget(1, (1,)),),
                "completed_undo_merge_failed",
            ),
        ),
        effects_complete=True,
        unknown_effects_possible=False,
    )

    event = build_cleanup_event(
        context=context((value,)),
        policies=(value,),
        semantics=plan.semantics,
        ledger=ledger,
        terminal=TerminalState(
            "failed",
            "undo_merge",
            FailureRecord("undo_merge_failed", "Undo preservation failed"),
        ),
    )

    assert event.outcome.result == "changed"
    assert event.outcome.effects_complete
    assert tuple(effect.action.type for effect in event.effects) == ("set_flag",)


def test_pre_mutation_failure_has_complete_empty_effects_and_unreached_execution() -> None:
    value = policy()
    event = build_cleanup_event(
        context=context((value,)),
        policies=(value,),
        semantics=PlanSemantics("not_collected"),
        ledger=NOT_COLLECTED_EXECUTION_LEDGER,
        terminal=TerminalState(
            "failed",
            "validation",
            FailureRecord("invalid_policy", "Policy validation failed"),
        ),
    )

    assert event.outcome.result == "no_change"
    assert event.outcome.effects_complete
    assert event.execution.status == "not_reached"
    assert event.policy_results[0].evaluation.status == "not_reached"


def test_automatic_activation_must_be_supported_by_invocation_and_snapshot() -> None:
    value = policy(triggers=(Trigger("daily"),))
    report = evaluated_report(value, card())
    plan = build_execution_plan((report,), collect_history=True)
    bad_context = replace(
        context((value,), automatic=True),
        activations=(PolicyActivation(value.id, "automatic", ("on_sync",)),),
    )

    with pytest.raises(HistoryEventValidationError, match="not invoked"):
        build_cleanup_event(
            context=bad_context,
            policies=(value,),
            semantics=plan.semantics,
            ledger=EMPTY_EXECUTION_LEDGER,
            terminal=TerminalState("succeeded", "complete"),
        )


def test_validation_rejects_broken_snapshot_hash_and_provenance_pointer() -> None:
    value = policy()
    report = evaluated_report(value, card())
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
    event = successful_event((value,), (report,), ledger)

    bad_snapshot = replace(event.policies[0], definition_hash="sha256:" + "0" * 64)
    with pytest.raises(HistoryEventValidationError, match="hash"):
        validate_cleanup_event(replace(event, policies=(bad_snapshot,)))

    signature = event.policy_results[0].match_provenance.signatures[0]
    any_node = replace(signature.any_nodes[0], matched_children=("/conditions/0/type",))
    bad_signature = replace(signature, any_nodes=(any_node,))
    provenance = replace(
        event.policy_results[0].match_provenance,
        signatures=(bad_signature,),
    )
    result = replace(event.policy_results[0], match_provenance=provenance)
    with pytest.raises(HistoryEventValidationError, match="outside"):
        validate_cleanup_event(replace(event, policy_results=(result,)))


def test_validation_rejects_naive_timestamps() -> None:
    value = policy()
    report = evaluated_report(value, card(interval=1, tags=frozenset()))
    event = successful_event((value,), (report,), EMPTY_EXECUTION_LEDGER)

    with pytest.raises(HistoryEventValidationError, match="RFC 3339"):
        validate_cleanup_event(
            replace(event, time=replace(event.time, started_at="2026-09-23T14:32:09"))
        )
