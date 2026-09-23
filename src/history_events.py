# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from .models import (
    ClearFlagAction,
    DeleteCardAction,
    DeleteNoteAction,
    MoveAction,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    SetFlagAction,
    SuspendAction,
    TagAction,
    UnsuspendAction,
    parse_policy,
    policy_to_dict,
)

if TYPE_CHECKING:
    from .actions import CleanupError, ExecutionResult
    from .engine import ResolvedAction
    from .history_semantics import (
        CardMatchProvenance,
        EffectContributor,
        ExecutionLedger,
        ExecutionLedgerStep,
        LogicalEffect,
        LogicalIntention,
        PlanSemantics,
        PolicyEvaluationFacts,
    )

HISTORY_SCHEMA_NAME = "card_janitor.cleanup_history"
HISTORY_SCHEMA_VERSION = 1
POLICY_SNAPSHOT_SCHEMA_VERSION = 1

InvocationKind = Literal["manual", "automatic"]
AutomaticEvent = Literal["daily", "on_open", "on_sync"]
AUTOMATIC_EVENTS = frozenset({"daily", "on_open", "on_sync"})
AUTOMATIC_EVENT_ORDER = ("daily", "on_open", "on_sync")
_RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
OutcomeStatus = Literal["succeeded", "failed", "cancelled"]
OutcomeStage = Literal[
    "validation",
    "evaluation",
    "planning",
    "execution",
    "undo_merge",
    "complete",
]
EventActionType = Literal[
    "add_tag",
    "remove_tag",
    "replace_tags",
    "suspend_card",
    "suspend_note",
    "unsuspend_card",
    "unsuspend_note",
    "move_card",
    "move_note",
    "set_flag",
    "clear_flag",
    "delete_card",
    "delete_note",
]
NonAppliedDisposition = Literal[
    "already_satisfied",
    "conflict",
    "preview_no_longer_matching",
    "preview_newly_matching",
    "preview_note_boundary",
]
ExecutionOperation = Literal[
    "add_tag",
    "remove_tag",
    "replace_tags",
    "move",
    "set_flag",
    "suspend",
    "unsuspend",
    "delete_card",
    "delete_note",
]
ExecutionStepStatus = Literal[
    "completed",
    "completed_undo_merge_failed",
    "failed_before_mutation",
    "failed_unknown",
    "not_attempted_due_to_failure",
]


class HistoryEventValidationError(ValueError):
    """An immutable history event is internally inconsistent."""


@dataclass(frozen=True)
class SchemaVersion:
    name: str = HISTORY_SCHEMA_NAME
    version: int = HISTORY_SCHEMA_VERSION


@dataclass(frozen=True)
class Producer:
    card_janitor_version: str
    anki_version: str


@dataclass(frozen=True)
class EventTime:
    started_at: str
    finished_at: str


@dataclass(frozen=True)
class Invocation:
    kind: InvocationKind
    events: tuple[AutomaticEvent, ...] = ()


@dataclass(frozen=True)
class PolicyActivation:
    policy_id: str
    kind: InvocationKind
    triggers: tuple[AutomaticEvent, ...] = ()


@dataclass(frozen=True)
class PolicyReference:
    id: str
    definition_hash: str


@dataclass(frozen=True)
class PolicySnapshot:
    snapshot_schema_version: int
    definition_hash: str
    canonical_definition_json: str

    @property
    def definition(self) -> dict[str, object]:
        try:
            value = json.loads(self.canonical_definition_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise HistoryEventValidationError("policy definition is not valid JSON") from exc
        if not isinstance(value, dict):
            raise HistoryEventValidationError("policy definition must be an object")
        return value

    @property
    def reference(self) -> PolicyReference:
        policy_id = self.definition.get("id")
        if not isinstance(policy_id, str):
            raise HistoryEventValidationError("policy definition has no valid id")
        return PolicyReference(policy_id, self.definition_hash)


@dataclass(frozen=True)
class AnyNodeRecord:
    path: str
    matched_children: tuple[str, ...]


@dataclass(frozen=True)
class MatchSignature:
    id: str
    trigger_cards: int
    any_nodes: tuple[AnyNodeRecord, ...]


@dataclass(frozen=True)
class ProvenanceRecord:
    status: Literal["complete", "unavailable"]
    signatures: tuple[MatchSignature, ...] = ()
    reason_code: str | None = None
    reason_message: str | None = None


@dataclass(frozen=True)
class EvaluationRecord:
    status: Literal["succeeded", "unavailable", "not_reached"]
    qualifying_trigger_cards: int | None = None
    actionable_cards_after_expansion: int | None = None
    reason_code: str | None = None


@dataclass(frozen=True)
class PolicyResultRecord:
    policy: PolicyReference
    activation: PolicyActivation
    evaluation: EvaluationRecord
    match_provenance: ProvenanceRecord


@dataclass(frozen=True)
class FailureRecord:
    code: str
    message: str
    recovery: str | None = None


@dataclass(frozen=True)
class OutcomeRecord:
    status: OutcomeStatus
    stage: OutcomeStage
    result: Literal["changed", "no_change", "unknown"]
    effects_complete: bool
    unknown_effects_possible: bool
    failure: FailureRecord | None = None


@dataclass(frozen=True)
class EventAction:
    type: EventActionType
    tag: str | None = None
    tags: tuple[str, ...] = ()
    target_deck: str | None = None
    flag: str | None = None


@dataclass(frozen=True)
class EntityCounts:
    cards: int
    notes: int
    matching_trigger_cards: int
    consequential_sibling_cards: int


@dataclass(frozen=True)
class ContributorRecord:
    policy: PolicyReference
    match_signatures: tuple[str, ...]
    trigger_cards: int


@dataclass(frozen=True)
class EffectRecord:
    action: EventAction
    counts: EntityCounts
    contributors: tuple[ContributorRecord, ...]


@dataclass(frozen=True)
class NonAppliedRecord:
    disposition: NonAppliedDisposition
    action: EventAction
    counts: EntityCounts
    contributors: tuple[ContributorRecord, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionTargetRecord:
    parameter: str | tuple[str, ...] | None
    targets: int


@dataclass(frozen=True)
class ExecutionStepRecord:
    sequence: int
    operation: ExecutionOperation
    target_kind: Literal["card", "note"]
    targets: tuple[ExecutionTargetRecord, ...]
    status: ExecutionStepStatus


@dataclass(frozen=True)
class ExecutionRecord:
    status: Literal["complete", "partial", "not_reached", "unavailable"]
    steps: tuple[ExecutionStepRecord, ...]
    reason_code: str | None = None


@dataclass(frozen=True)
class SummaryRecord:
    policies: int
    distinct_cards_changed: int
    distinct_notes_changed: int
    effect_groups: int
    non_applied_groups: int


@dataclass(frozen=True)
class CleanupEvent:
    schema: SchemaVersion
    event_type: Literal["cleanup_run"]
    event_id: str
    source_id: str
    producer: Producer
    time: EventTime
    invocation: Invocation
    policies: tuple[PolicySnapshot, ...]
    policy_results: tuple[PolicyResultRecord, ...]
    outcome: OutcomeRecord
    summary: SummaryRecord
    effects: tuple[EffectRecord, ...]
    non_applied: tuple[NonAppliedRecord, ...]
    execution: ExecutionRecord


@dataclass(frozen=True)
class CleanupEventContext:
    event_id: str
    source_id: str
    producer: Producer
    started_at: datetime
    finished_at: datetime
    invocation: Invocation
    activations: tuple[PolicyActivation, ...]


@dataclass(frozen=True)
class TerminalState:
    status: OutcomeStatus
    stage: OutcomeStage
    failure: FailureRecord | None = None


@dataclass
class _Aggregate:
    card_ids: set[int]
    note_ids: set[int]
    trigger_card_ids: set[int]
    sibling_card_ids: set[int]
    contributor_cards: dict[str, set[int]]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _canonical_events(events: tuple[AutomaticEvent, ...]) -> tuple[AutomaticEvent, ...]:
    if len(events) != len(set(events)) or not set(events) <= AUTOMATIC_EVENTS:
        raise HistoryEventValidationError("invalid or duplicate automatic event")
    return tuple(item for item in AUTOMATIC_EVENT_ORDER if item in events)


def make_policy_snapshot(policy: Policy) -> PolicySnapshot:
    definition = policy_to_dict(policy)
    canonical_definition = _canonical_json(definition)
    digest_input = {
        "snapshot_schema_version": POLICY_SNAPSHOT_SCHEMA_VERSION,
        "definition": definition,
    }
    digest = hashlib.sha256(_canonical_json(digest_input).encode("utf-8")).hexdigest()
    return PolicySnapshot(
        POLICY_SNAPSHOT_SCHEMA_VERSION,
        f"sha256:{digest}",
        canonical_definition,
    )


def _reason_message(code: str) -> str:
    return {
        "trace_evaluation_failed": "Complete match provenance could not be collected",
        "trace_mismatch": "Match provenance did not agree with authoritative evaluation",
        "policy_evaluation_failed": "Policy evaluation did not complete",
        "resource_limit": "Match provenance exceeded its resource budget",
        "unsupported_condition": "Match provenance does not support this condition",
        "not_recorded": "Match provenance was not recorded",
        "not_recorded_by_older_version": "This version did not record match provenance",
    }.get(code, "Match provenance is unavailable")


def _signature_key(match: CardMatchProvenance) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple((node.path, node.matched_children) for node in match.any_nodes)


def _build_provenance(
    facts: PolicyEvaluationFacts,
) -> tuple[ProvenanceRecord, dict[int, str]]:
    provenance = facts.provenance
    if provenance.status != "complete":
        code = provenance.reason_code or "not_recorded"
        return (
            ProvenanceRecord(
                "unavailable",
                reason_code=code,
                reason_message=_reason_message(code),
            ),
            {},
        )
    card_ids = tuple(match.card_id for match in provenance.cards)
    if len(card_ids) != len(set(card_ids)) or len(card_ids) != facts.qualifying_trigger_cards:
        raise HistoryEventValidationError(
            "complete provenance does not identify every qualifying trigger card exactly once"
        )
    grouped: dict[tuple[tuple[str, tuple[str, ...]], ...], list[int]] = {}
    for match in provenance.cards:
        grouped.setdefault(_signature_key(match), []).append(match.card_id)
    signatures = []
    card_signatures: dict[int, str] = {}
    for index, key in enumerate(sorted(grouped)):
        signature_id = f"m{index}"
        card_ids = grouped[key]
        for card_id in card_ids:
            card_signatures[card_id] = signature_id
        signatures.append(
            MatchSignature(
                signature_id,
                len(card_ids),
                tuple(AnyNodeRecord(path, children) for path, children in key),
            )
        )
    return ProvenanceRecord("complete", tuple(signatures)), card_signatures


def _build_policy_results(
    snapshots: tuple[PolicySnapshot, ...],
    semantics: PlanSemantics,
    activations: tuple[PolicyActivation, ...],
    terminal: TerminalState,
) -> tuple[tuple[PolicyResultRecord, ...], dict[str, dict[int, str]]]:
    refs = {snapshot.reference.id: snapshot.reference for snapshot in snapshots}
    activation_by_id = {activation.policy_id: activation for activation in activations}
    facts_by_id = {facts.policy_id: facts for facts in semantics.policy_evaluations}
    if len(refs) != len(snapshots):
        raise HistoryEventValidationError("policy snapshots have duplicate ids")
    if len(activation_by_id) != len(activations) or set(refs) != set(activation_by_id):
        raise HistoryEventValidationError("policy activations do not match policy snapshots")
    if len(facts_by_id) != len(semantics.policy_evaluations) or not set(facts_by_id) <= set(refs):
        raise HistoryEventValidationError("policy evaluation facts do not match policy snapshots")
    signature_maps: dict[str, dict[int, str]] = {}
    results = []
    for snapshot in snapshots:
        policy_ref = snapshot.reference
        facts = facts_by_id.get(policy_ref.id)
        if facts is None:
            provenance = ProvenanceRecord(
                "unavailable",
                reason_code="not_recorded",
                reason_message=_reason_message("not_recorded"),
            )
            evaluation = EvaluationRecord(
                "not_reached" if terminal.stage == "validation" else "unavailable",
                reason_code=(
                    None
                    if terminal.stage == "validation"
                    else semantics.reason_code or "history_semantics_unavailable"
                ),
            )
            signature_maps[policy_ref.id] = {}
        else:
            provenance, signature_map = _build_provenance(facts)
            signature_maps[policy_ref.id] = signature_map
            evaluation = EvaluationRecord(
                "succeeded",
                facts.qualifying_trigger_cards,
                facts.actionable_cards_after_expansion,
            )
        results.append(
            PolicyResultRecord(
                policy_ref,
                activation_by_id[policy_ref.id],
                evaluation,
                provenance,
            )
        )
    return tuple(results), signature_maps


def _event_action(resolved: ResolvedAction) -> EventAction:
    action = resolved.action
    if isinstance(action, TagAction):
        return EventAction("add_tag", tag=action.tag)
    if isinstance(action, RemoveTagAction):
        return EventAction("remove_tag", tag=action.tag)
    if isinstance(action, ReplaceTagsAction):
        return EventAction("replace_tags", tags=action.tags)
    if isinstance(action, SuspendAction):
        return EventAction("suspend_note" if action.target == "note" else "suspend_card")
    if isinstance(action, UnsuspendAction):
        return EventAction("unsuspend_note" if action.target == "note" else "unsuspend_card")
    if isinstance(action, MoveAction):
        return EventAction(
            "move_note" if action.target == "note" else "move_card",
            target_deck=action.deck,
        )
    if isinstance(action, DeleteCardAction):
        return EventAction("delete_card")
    if isinstance(action, DeleteNoteAction):
        return EventAction("delete_note")
    if isinstance(action, SetFlagAction):
        return EventAction("set_flag", flag=action.flag)
    if isinstance(action, ClearFlagAction):
        return EventAction("clear_flag")
    raise HistoryEventValidationError(f"unsupported action: {action!r}")


def _step_matches_effect(step: ExecutionLedgerStep, effect: LogicalEffect) -> bool:
    action = effect.action.action
    for target in step.targets:
        ids = set(target.target_ids)
        if isinstance(action, TagAction):
            if (
                step.operation == "add_tag"
                and target.value == action.tag
                and effect.target_id in ids
            ):
                return True
            continue
        if isinstance(action, RemoveTagAction):
            if (
                step.operation == "remove_tag"
                and target.value == action.tag
                and effect.target_id in ids
            ):
                return True
            continue
        if isinstance(action, ReplaceTagsAction):
            value = target.value
            if (
                step.operation == "replace_tags"
                and isinstance(value, tuple)
                and {tag.casefold() for tag in value} == {tag.casefold() for tag in action.tags}
                and effect.target_id in ids
            ):
                return True
            continue
        if isinstance(action, MoveAction):
            if (
                step.operation == "move"
                and target.value == effect.action.target_deck_id
                and set(effect.affected_card_ids) <= ids
            ):
                return True
            continue
        if isinstance(action, SetFlagAction):
            flag = {
                "red": 1,
                "orange": 2,
                "green": 3,
                "blue": 4,
                "pink": 5,
                "turquoise": 6,
                "purple": 7,
            }[action.flag]
            if (
                step.operation == "set_flag"
                and target.value == flag
                and set(effect.affected_card_ids) <= ids
            ):
                return True
            continue
        if isinstance(action, ClearFlagAction):
            if (
                step.operation == "set_flag"
                and target.value == 0
                and set(effect.affected_card_ids) <= ids
            ):
                return True
            continue
        operation = (
            "suspend"
            if isinstance(action, SuspendAction)
            else "unsuspend"
            if isinstance(action, UnsuspendAction)
            else "delete_card"
            if isinstance(action, DeleteCardAction)
            else "delete_note"
            if isinstance(action, DeleteNoteAction)
            else ""
        )
        target_ids = (
            {effect.target_id}
            if isinstance(action, DeleteNoteAction)
            else set(effect.affected_card_ids)
        )
        if step.operation == operation and target_ids <= ids:
            return True
    return False


def _known_applied_effects(
    semantics: PlanSemantics,
    ledger: ExecutionLedger,
    status: OutcomeStatus,
) -> tuple[LogicalEffect, ...]:
    if semantics.status != "complete":
        return ()
    if status == "succeeded":
        return semantics.effects
    completed = tuple(
        step for step in ledger.steps if step.status in {"completed", "completed_undo_merge_failed"}
    )
    return tuple(
        effect
        for effect in semantics.effects
        if any(_step_matches_effect(step, effect) for step in completed)
    )


def _contributor_descriptor(
    contributor: EffectContributor,
    signature_maps: dict[str, dict[int, str]],
) -> tuple[str, tuple[str, ...]]:
    signatures = signature_maps.get(contributor.policy_id, {})
    return contributor.policy_id, tuple(
        sorted(
            {
                signatures[card_id]
                for card_id in contributor.trigger_card_ids
                if card_id in signatures
            }
        )
    )


def _aggregate_effects(
    effects: tuple[LogicalEffect, ...],
    refs: dict[str, PolicyReference],
    signature_maps: dict[str, dict[int, str]],
) -> tuple[EffectRecord, ...]:
    grouped: dict[tuple[EventAction, tuple[tuple[str, tuple[str, ...]], ...]], _Aggregate] = {}
    for effect in effects:
        descriptors = tuple(
            sorted(_contributor_descriptor(item, signature_maps) for item in effect.contributors)
        )
        key = (_event_action(effect.action), descriptors)
        aggregate = grouped.setdefault(key, _Aggregate(set(), set(), set(), set(), {}))
        aggregate.card_ids.update(effect.affected_card_ids)
        aggregate.note_ids.update(effect.affected_note_ids)
        aggregate.trigger_card_ids.update(effect.matching_trigger_card_ids)
        aggregate.sibling_card_ids.update(effect.consequential_sibling_card_ids)
        for contributor in effect.contributors:
            aggregate.contributor_cards.setdefault(contributor.policy_id, set()).update(
                contributor.trigger_card_ids
            )
    records = []
    for (action, descriptors), aggregate in sorted(
        grouped.items(), key=lambda item: (_canonical_json(_action_to_dict(item[0][0])), item[0][1])
    ):
        contributors = tuple(
            ContributorRecord(
                refs[policy_id], signatures, len(aggregate.contributor_cards[policy_id])
            )
            for policy_id, signatures in descriptors
        )
        records.append(
            EffectRecord(
                action,
                EntityCounts(
                    len(aggregate.card_ids),
                    len(aggregate.note_ids),
                    len(aggregate.trigger_card_ids),
                    len(aggregate.sibling_card_ids),
                ),
                contributors,
            )
        )
    return tuple(records)


def _intention_descriptor(
    intention: LogicalIntention,
    signature_maps: dict[str, dict[int, str]],
) -> tuple[str, tuple[str, ...]]:
    signatures = signature_maps.get(intention.policy_id, {})
    return intention.policy_id, tuple(
        sorted(
            {signatures[card_id] for card_id in intention.trigger_card_ids if card_id in signatures}
        )
    )


def _aggregate_non_applied(
    intentions: tuple[LogicalIntention, ...],
    refs: dict[str, PolicyReference],
    signature_maps: dict[str, dict[int, str]],
) -> tuple[NonAppliedRecord, ...]:
    by_target: dict[tuple[str, EventAction, tuple[str, ...], str, int], list[LogicalIntention]] = {}
    for intention in intentions:
        if intention.disposition == "planned":
            continue
        key = (
            intention.disposition,
            _event_action(intention.action),
            intention.reason_codes,
            intention.target_kind,
            intention.target_id,
        )
        by_target.setdefault(key, []).append(intention)

    grouped: dict[
        tuple[str, EventAction, tuple[str, ...], tuple[tuple[str, tuple[str, ...]], ...]],
        _Aggregate,
    ] = {}
    for (disposition, action, reasons, _target_kind, _target_id), target_items in by_target.items():
        descriptors = tuple(
            sorted({_intention_descriptor(item, signature_maps) for item in target_items})
        )
        key = disposition, action, reasons, descriptors
        aggregate = grouped.setdefault(key, _Aggregate(set(), set(), set(), set(), {}))
        target_cards = {item.target_card_id for item in target_items}
        trigger_cards = {card_id for item in target_items for card_id in item.trigger_card_ids}
        aggregate.card_ids.update(target_cards)
        aggregate.note_ids.update(item.target_note_id for item in target_items)
        aggregate.trigger_card_ids.update(trigger_cards)
        aggregate.sibling_card_ids.update(target_cards - trigger_cards)
        for item in target_items:
            aggregate.contributor_cards.setdefault(item.policy_id, set()).update(
                item.trigger_card_ids
            )

    records = []
    for (disposition, action, reasons, descriptors), aggregate in sorted(
        grouped.items(),
        key=lambda item: (
            item[0][0],
            _canonical_json(_action_to_dict(item[0][1])),
            item[0][2],
            item[0][3],
        ),
    ):
        records.append(
            NonAppliedRecord(
                disposition,
                action,
                EntityCounts(
                    len(aggregate.card_ids),
                    len(aggregate.note_ids),
                    len(aggregate.trigger_card_ids),
                    len(aggregate.sibling_card_ids),
                ),
                tuple(
                    ContributorRecord(
                        refs[policy_id],
                        signatures,
                        len(aggregate.contributor_cards[policy_id]),
                    )
                    for policy_id, signatures in descriptors
                ),
                reasons,
            )
        )
    return tuple(records)


def _execution_parameter(
    step: ExecutionLedgerStep,
    value: str | int | tuple[str, ...] | None,
    semantics: PlanSemantics,
) -> str | tuple[str, ...] | None:
    if step.operation in {"add_tag", "remove_tag"} and isinstance(value, str):
        return value
    if step.operation == "replace_tags" and isinstance(value, tuple):
        return value
    if step.operation == "set_flag" and isinstance(value, int):
        return {
            0: "none",
            1: "red",
            2: "orange",
            3: "green",
            4: "blue",
            5: "pink",
            6: "turquoise",
            7: "purple",
        }.get(value)
    if step.operation == "move" and isinstance(value, int):
        deck_names = {
            effect.action.action.deck
            for effect in semantics.effects
            if isinstance(effect.action.action, MoveAction)
            and effect.action.target_deck_id == value
        }
        if len(deck_names) == 1:
            return next(iter(deck_names))
    return None


def _execution_record(
    ledger: ExecutionLedger,
    terminal: TerminalState,
    semantics: PlanSemantics,
) -> ExecutionRecord:
    if terminal.status != "succeeded" and terminal.stage in {
        "validation",
        "evaluation",
        "planning",
    }:
        return ExecutionRecord("not_reached", ())
    status = ledger.status if ledger.status in {"complete", "partial"} else "unavailable"
    return ExecutionRecord(
        status,
        tuple(
            ExecutionStepRecord(
                step.sequence,
                step.operation,
                step.target_kind,
                tuple(
                    ExecutionTargetRecord(
                        _execution_parameter(step, target.value, semantics),
                        len(target.target_ids),
                    )
                    for target in step.targets
                ),
                step.status,
            )
            for step in ledger.steps
        ),
        (ledger.reason_code or "not_recorded") if status == "unavailable" else None,
    )


def build_cleanup_event(
    *,
    context: CleanupEventContext,
    policies: tuple[Policy, ...],
    semantics: PlanSemantics,
    ledger: ExecutionLedger,
    terminal: TerminalState,
) -> CleanupEvent:
    invocation = Invocation(
        context.invocation.kind,
        _canonical_events(context.invocation.events),
    )
    activations = tuple(
        PolicyActivation(
            activation.policy_id,
            activation.kind,
            _canonical_events(activation.triggers),
        )
        for activation in context.activations
    )
    snapshots = tuple(make_policy_snapshot(policy) for policy in policies)
    refs = {snapshot.reference.id: snapshot.reference for snapshot in snapshots}
    policy_results, signature_maps = _build_policy_results(
        snapshots,
        semantics,
        activations,
        terminal,
    )
    pre_mutation_terminal = terminal.status != "succeeded" and terminal.stage in {
        "validation",
        "evaluation",
        "planning",
    }
    applied = (
        () if pre_mutation_terminal else _known_applied_effects(semantics, ledger, terminal.status)
    )
    effects = _aggregate_effects(applied, refs, signature_maps)
    non_applied = _aggregate_non_applied(semantics.non_applied, refs, signature_maps)
    changed_cards = {card_id for effect in applied for card_id in effect.affected_card_ids}
    changed_notes = {note_id for effect in applied for note_id in effect.affected_note_ids}
    effects_complete = pre_mutation_terminal or (
        semantics.status == "complete"
        and all(effect.affected_card_ids_complete for effect in applied)
        and (terminal.status == "succeeded" or ledger.effects_complete)
    )
    unknown_effects = (
        False
        if pre_mutation_terminal
        else (
            not effects_complete
            or ledger.unknown_effects_possible
            or semantics.status != "complete"
        )
    )
    result = "unknown" if unknown_effects else "changed" if effects else "no_change"
    outcome = OutcomeRecord(
        terminal.status,
        terminal.stage,
        result,
        effects_complete,
        unknown_effects,
        terminal.failure,
    )
    event = CleanupEvent(
        SchemaVersion(),
        "cleanup_run",
        context.event_id,
        context.source_id,
        context.producer,
        EventTime(context.started_at.isoformat(), context.finished_at.isoformat()),
        invocation,
        snapshots,
        policy_results,
        outcome,
        SummaryRecord(
            len(snapshots),
            len(changed_cards),
            len(changed_notes),
            len(effects),
            len(non_applied),
        ),
        effects,
        non_applied,
        _execution_record(ledger, terminal, semantics),
    )
    validate_cleanup_event(event)
    return event


def build_success_event(
    *,
    context: CleanupEventContext,
    policies: tuple[Policy, ...],
    result: ExecutionResult,
) -> CleanupEvent:
    return build_cleanup_event(
        context=context,
        policies=policies,
        semantics=result.semantics,
        ledger=result.execution_ledger,
        terminal=TerminalState("succeeded", "complete"),
    )


def build_failure_event(
    *,
    context: CleanupEventContext,
    policies: tuple[Policy, ...],
    error: CleanupError,
    code: str,
    recovery: str | None = None,
) -> CleanupEvent:
    stage: OutcomeStage = "undo_merge" if error.stage == "undo_merge" else "execution"
    return build_cleanup_event(
        context=context,
        policies=policies,
        semantics=error.semantics,
        ledger=error.execution_ledger,
        terminal=TerminalState(
            "failed",
            stage,
            FailureRecord(code, str(error), recovery),
        ),
    )


def _action_to_dict(action: EventAction) -> dict[str, object]:
    value: dict[str, object] = {"type": action.type}
    if action.tag is not None:
        value["tag"] = action.tag
    if action.tags:
        value["tags"] = list(action.tags)
    if action.target_deck is not None:
        value["target_deck"] = action.target_deck
    if action.flag is not None:
        value["flag"] = action.flag
    return value


def _policy_ref_to_dict(value: PolicyReference) -> dict[str, object]:
    return {"id": value.id, "definition_hash": value.definition_hash}


def _contributor_to_dict(value: ContributorRecord) -> dict[str, object]:
    return {
        "policy": _policy_ref_to_dict(value.policy),
        "match_signatures": list(value.match_signatures),
        "trigger_cards": value.trigger_cards,
    }


def _counts_to_dict(value: EntityCounts) -> dict[str, object]:
    return {
        "cards": value.cards,
        "notes": value.notes,
        "matching_trigger_cards": value.matching_trigger_cards,
        "consequential_sibling_cards": value.consequential_sibling_cards,
    }


def event_to_dict(event: CleanupEvent) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": {"name": event.schema.name, "version": event.schema.version},
        "event_type": event.event_type,
        "event_id": event.event_id,
        "source_id": event.source_id,
        "producer": {
            "card_janitor_version": event.producer.card_janitor_version,
            "anki_version": event.producer.anki_version,
        },
        "time": {
            "started_at": event.time.started_at,
            "finished_at": event.time.finished_at,
        },
        "invocation": {
            "kind": event.invocation.kind,
            **({"events": list(event.invocation.events)} if event.invocation.events else {}),
        },
        "policies": [
            {
                "snapshot_schema_version": snapshot.snapshot_schema_version,
                "definition_hash": snapshot.definition_hash,
                "definition": snapshot.definition,
            }
            for snapshot in event.policies
        ],
        "policy_results": [
            {
                "policy": _policy_ref_to_dict(item.policy),
                "activation": {
                    "kind": item.activation.kind,
                    **(
                        {"triggers": list(item.activation.triggers)}
                        if item.activation.triggers
                        else {}
                    ),
                },
                "evaluation": {
                    "status": item.evaluation.status,
                    **(
                        {
                            "qualifying_trigger_cards": item.evaluation.qualifying_trigger_cards,
                            "actionable_cards_after_expansion": item.evaluation.actionable_cards_after_expansion,
                        }
                        if item.evaluation.qualifying_trigger_cards is not None
                        else {}
                    ),
                    **(
                        {"reason_code": item.evaluation.reason_code}
                        if item.evaluation.reason_code
                        else {}
                    ),
                },
                "match_provenance": {
                    "status": item.match_provenance.status,
                    **(
                        {
                            "signatures": [
                                {
                                    "id": signature.id,
                                    "trigger_cards": signature.trigger_cards,
                                    "any_nodes": [
                                        {
                                            "path": node.path,
                                            "matched_children": list(node.matched_children),
                                        }
                                        for node in signature.any_nodes
                                    ],
                                }
                                for signature in item.match_provenance.signatures
                            ]
                        }
                        if item.match_provenance.status == "complete"
                        else {
                            "reason": {
                                "code": item.match_provenance.reason_code,
                                "message": item.match_provenance.reason_message,
                            }
                        }
                    ),
                },
            }
            for item in event.policy_results
        ],
        "outcome": {
            "status": event.outcome.status,
            "stage": event.outcome.stage,
            "result": event.outcome.result,
            "effects_complete": event.outcome.effects_complete,
            "unknown_effects_possible": event.outcome.unknown_effects_possible,
            **(
                {
                    "failure": {
                        "code": event.outcome.failure.code,
                        "message": event.outcome.failure.message,
                        **(
                            {"recovery": event.outcome.failure.recovery}
                            if event.outcome.failure.recovery
                            else {}
                        ),
                    }
                }
                if event.outcome.failure
                else {}
            ),
        },
        "summary": {
            "policies": event.summary.policies,
            "distinct_cards_changed": event.summary.distinct_cards_changed,
            "distinct_notes_changed": event.summary.distinct_notes_changed,
            "effect_groups": event.summary.effect_groups,
            "non_applied_groups": event.summary.non_applied_groups,
        },
        "effects": [
            {
                "action": _action_to_dict(item.action),
                "counts": _counts_to_dict(item.counts),
                "contributors": [_contributor_to_dict(value) for value in item.contributors],
            }
            for item in event.effects
        ],
        "non_applied": [
            {
                "disposition": item.disposition,
                "action": _action_to_dict(item.action),
                "counts": _counts_to_dict(item.counts),
                "contributors": [_contributor_to_dict(value) for value in item.contributors],
                **({"reason_codes": list(item.reason_codes)} if item.reason_codes else {}),
            }
            for item in event.non_applied
        ],
        "execution": {
            "status": event.execution.status,
            "steps": [
                {
                    "sequence": step.sequence,
                    "operation": step.operation,
                    "target_kind": step.target_kind,
                    "targets": [
                        {
                            **(
                                {
                                    "parameter": (
                                        list(target.parameter)
                                        if isinstance(target.parameter, tuple)
                                        else target.parameter
                                    )
                                }
                                if target.parameter is not None
                                else {}
                            ),
                            "count": target.targets,
                        }
                        for target in step.targets
                    ],
                    "status": step.status,
                }
                for step in event.execution.steps
            ],
            **({"reason_code": event.execution.reason_code} if event.execution.reason_code else {}),
        },
    }
    return value


def event_to_json(event: CleanupEvent) -> str:
    validate_cleanup_event(event)
    return _canonical_json(event_to_dict(event))


def _parse_aware_timestamp(value: str, field: str) -> datetime:
    if not isinstance(value, str) or _RFC3339_PATTERN.fullmatch(value) is None:
        raise HistoryEventValidationError(f"{field} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HistoryEventValidationError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HistoryEventValidationError(f"{field} must include an offset")
    return parsed


def _resolve_pointer(value: object, pointer: str) -> object:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise HistoryEventValidationError(f"invalid JSON Pointer: {pointer!r}")
    current = value
    for raw in pointer[1:].split("/"):
        if "~" in raw.replace("~0", "").replace("~1", ""):
            raise HistoryEventValidationError(f"invalid JSON Pointer escape: {pointer!r}")
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise HistoryEventValidationError(f"JSON Pointer does not resolve: {pointer!r}")
    return current


def _validate_policy_snapshot(snapshot: PolicySnapshot) -> None:
    if snapshot.snapshot_schema_version != POLICY_SNAPSHOT_SCHEMA_VERSION:
        raise HistoryEventValidationError("unsupported policy snapshot schema")
    definition = snapshot.definition
    try:
        policy = parse_policy(definition)
    except (TypeError, ValueError) as exc:
        raise HistoryEventValidationError("invalid embedded policy definition") from exc
    expected = make_policy_snapshot(policy)
    if expected.canonical_definition_json != snapshot.canonical_definition_json:
        raise HistoryEventValidationError("policy definition is not canonical")
    if expected.definition_hash != snapshot.definition_hash:
        raise HistoryEventValidationError("policy definition hash does not match snapshot")


def _validate_action(action: EventAction) -> None:
    fields = {
        "add_tag": (
            action.tag is not None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "remove_tag": (
            action.tag is not None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "replace_tags": (
            action.tag is None
            and bool(action.tags)
            and action.target_deck is None
            and action.flag is None
        ),
        "move_card": (
            action.tag is None
            and not action.tags
            and action.target_deck is not None
            and action.flag is None
        ),
        "move_note": (
            action.tag is None
            and not action.tags
            and action.target_deck is not None
            and action.flag is None
        ),
        "set_flag": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is not None
        ),
        "suspend_card": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "suspend_note": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "unsuspend_card": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "unsuspend_note": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "clear_flag": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "delete_card": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
        "delete_note": (
            action.tag is None
            and not action.tags
            and action.target_deck is None
            and action.flag is None
        ),
    }
    if not fields.get(action.type, False):
        raise HistoryEventValidationError(f"invalid history action: {action.type!r}")
    if action.tag is not None and not action.tag:
        raise HistoryEventValidationError("history tag must not be empty")
    if action.tags and (
        any(not tag for tag in action.tags)
        or len({tag.casefold() for tag in action.tags}) != len(action.tags)
    ):
        raise HistoryEventValidationError("replacement tags must be non-empty and unique")
    if action.target_deck is not None and not action.target_deck:
        raise HistoryEventValidationError("history target deck must not be empty")
    if action.flag is not None and action.flag not in {
        "red",
        "orange",
        "green",
        "blue",
        "pink",
        "turquoise",
        "purple",
    }:
        raise HistoryEventValidationError("invalid history flag")


def validate_cleanup_event(event: CleanupEvent) -> None:
    if event.schema != SchemaVersion() or event.event_type != "cleanup_run":
        raise HistoryEventValidationError("unsupported cleanup-history schema")
    for field, value in (("event_id", event.event_id), ("source_id", event.source_id)):
        try:
            UUID(value)
        except (AttributeError, TypeError, ValueError) as exc:
            raise HistoryEventValidationError(f"{field} must be a UUID") from exc
    if not event.producer.card_janitor_version or not event.producer.anki_version:
        raise HistoryEventValidationError("producer versions must not be empty")
    started = _parse_aware_timestamp(event.time.started_at, "time.started_at")
    finished = _parse_aware_timestamp(event.time.finished_at, "time.finished_at")
    if finished < started:
        raise HistoryEventValidationError("event finish precedes its start")
    if event.invocation.kind not in {"manual", "automatic"}:
        raise HistoryEventValidationError("invalid invocation kind")
    if not set(event.invocation.events) <= AUTOMATIC_EVENTS:
        raise HistoryEventValidationError("invalid automatic invocation event")
    if len(set(event.invocation.events)) != len(event.invocation.events):
        raise HistoryEventValidationError("duplicate automatic invocation event")
    if event.invocation.events != _canonical_events(event.invocation.events):
        raise HistoryEventValidationError("automatic invocation events are not canonical")
    if event.invocation.kind == "manual" and event.invocation.events:
        raise HistoryEventValidationError("manual invocation cannot contain automatic events")
    if event.invocation.kind == "automatic" and not event.invocation.events:
        raise HistoryEventValidationError("automatic invocation requires events")
    if not event.policies:
        raise HistoryEventValidationError("cleanup event requires at least one policy")

    refs: dict[tuple[str, str], PolicySnapshot] = {}
    for snapshot in event.policies:
        _validate_policy_snapshot(snapshot)
        key = snapshot.reference.id, snapshot.reference.definition_hash
        if key in refs:
            raise HistoryEventValidationError("duplicate policy snapshot")
        refs[key] = snapshot
    if len({snapshot.reference.id.casefold() for snapshot in event.policies}) != len(
        event.policies
    ):
        raise HistoryEventValidationError("duplicate policy id")

    if len(event.policy_results) != len(event.policies):
        raise HistoryEventValidationError("every policy must have exactly one result")
    signatures_by_policy: dict[tuple[str, str], set[str]] = {}
    result_keys: set[tuple[str, str]] = set()
    for result in event.policy_results:
        key = result.policy.id, result.policy.definition_hash
        snapshot = refs.get(key)
        if snapshot is None:
            raise HistoryEventValidationError("policy result references a missing snapshot")
        if key in result_keys:
            raise HistoryEventValidationError("duplicate policy result")
        result_keys.add(key)
        if result.activation.policy_id != result.policy.id:
            raise HistoryEventValidationError("activation references a different policy")
        if result.activation.kind != event.invocation.kind:
            raise HistoryEventValidationError("policy activation conflicts with invocation")
        if not set(result.activation.triggers) <= AUTOMATIC_EVENTS:
            raise HistoryEventValidationError("invalid policy activation trigger")
        if len(set(result.activation.triggers)) != len(result.activation.triggers):
            raise HistoryEventValidationError("duplicate policy activation trigger")
        if result.activation.triggers != _canonical_events(result.activation.triggers):
            raise HistoryEventValidationError("policy activation triggers are not canonical")
        if result.activation.kind == "manual" and result.activation.triggers:
            raise HistoryEventValidationError("manual activation cannot contain triggers")
        if result.activation.kind == "automatic" and not result.activation.triggers:
            raise HistoryEventValidationError("automatic activation requires triggers")
        if not set(result.activation.triggers) <= set(event.invocation.events):
            raise HistoryEventValidationError("policy activation trigger was not invoked")
        configured_triggers = {
            trigger.get("type")
            for trigger in snapshot.definition.get("triggers", [])
            if isinstance(trigger, dict)
        }
        if not set(result.activation.triggers) <= configured_triggers:
            raise HistoryEventValidationError("policy activation trigger is not in its snapshot")
        if result.evaluation.status not in {"succeeded", "unavailable", "not_reached"}:
            raise HistoryEventValidationError("invalid policy evaluation status")
        if result.evaluation.status == "succeeded":
            if (
                result.evaluation.qualifying_trigger_cards is None
                or result.evaluation.actionable_cards_after_expansion is None
                or result.evaluation.qualifying_trigger_cards < 0
                or result.evaluation.actionable_cards_after_expansion < 0
            ):
                raise HistoryEventValidationError("successful evaluation requires counts")
            if result.evaluation.reason_code is not None:
                raise HistoryEventValidationError("successful evaluation cannot have a reason")
        elif (
            result.evaluation.qualifying_trigger_cards is not None
            or result.evaluation.actionable_cards_after_expansion is not None
        ):
            raise HistoryEventValidationError("incomplete evaluation cannot contain counts")
        elif result.evaluation.status == "unavailable" and not result.evaluation.reason_code:
            raise HistoryEventValidationError("unavailable evaluation requires a reason")
        elif result.evaluation.status == "not_reached" and result.evaluation.reason_code:
            raise HistoryEventValidationError("unreached evaluation cannot contain a reason")
        provenance = result.match_provenance
        if provenance.status not in {"complete", "unavailable"}:
            raise HistoryEventValidationError("invalid match provenance status")
        if provenance.status == "complete":
            if provenance.reason_code is not None or provenance.reason_message is not None:
                raise HistoryEventValidationError("complete provenance cannot contain a reason")
            signature_ids = {signature.id for signature in provenance.signatures}
            if len(signature_ids) != len(provenance.signatures):
                raise HistoryEventValidationError("duplicate match signature")
            if tuple(signature.id for signature in provenance.signatures) != tuple(
                f"m{index}" for index in range(len(provenance.signatures))
            ):
                raise HistoryEventValidationError("match signatures are not canonical")
            if (
                result.evaluation.status == "succeeded"
                and sum(signature.trigger_cards for signature in provenance.signatures)
                != result.evaluation.qualifying_trigger_cards
            ):
                raise HistoryEventValidationError("provenance counts do not match evaluation")
            definition = snapshot.definition
            for signature in provenance.signatures:
                if signature.trigger_cards <= 0:
                    raise HistoryEventValidationError("empty match signature")
                node_paths = tuple(node.path for node in signature.any_nodes)
                if len(node_paths) != len(set(node_paths)) or node_paths != tuple(
                    sorted(node_paths)
                ):
                    raise HistoryEventValidationError("any-node provenance is not canonical")
                for node in signature.any_nodes:
                    resolved = _resolve_pointer(definition, node.path)
                    if not isinstance(resolved, dict) or resolved.get("match") != "any":
                        raise HistoryEventValidationError("provenance path is not an any node")
                    conditions = resolved.get("conditions")
                    if not isinstance(conditions, list):
                        raise HistoryEventValidationError("any node has no conditions")
                    if not node.matched_children:
                        raise HistoryEventValidationError("matched any node has no alternatives")
                    if node.matched_children != tuple(sorted(set(node.matched_children))):
                        raise HistoryEventValidationError("matched alternatives are not canonical")
                    expected_children = {
                        f"{node.path}/conditions/{index}" for index in range(len(conditions))
                    }
                    for child in node.matched_children:
                        if child not in expected_children:
                            raise HistoryEventValidationError(
                                "matched child is outside its any node"
                            )
                        _resolve_pointer(definition, child)
            signatures_by_policy[key] = signature_ids
        else:
            if provenance.signatures or not provenance.reason_code or not provenance.reason_message:
                raise HistoryEventValidationError("unavailable provenance requires a reason")
            signatures_by_policy[key] = set()
    if result_keys != set(refs):
        raise HistoryEventValidationError("every policy must have exactly one result")

    if event.outcome.status == "succeeded":
        if event.outcome.stage != "complete" or event.outcome.failure is not None:
            raise HistoryEventValidationError("successful outcome must be complete")
    elif event.outcome.failure is None:
        raise HistoryEventValidationError("non-success outcome requires failure details")
    elif event.outcome.stage == "complete":
        raise HistoryEventValidationError("non-success outcome cannot have a complete stage")
    if event.outcome.status == "cancelled" and event.outcome.stage not in {
        "validation",
        "evaluation",
        "planning",
    }:
        raise HistoryEventValidationError("cancelled outcome must precede execution")
    if event.outcome.status not in {
        "succeeded",
        "failed",
        "cancelled",
    } or event.outcome.stage not in {
        "validation",
        "evaluation",
        "planning",
        "execution",
        "undo_merge",
        "complete",
    }:
        raise HistoryEventValidationError("invalid terminal outcome")
    if event.outcome.stage == "validation" and any(
        result.evaluation.status != "not_reached" for result in event.policy_results
    ):
        raise HistoryEventValidationError("validation-stage outcome contains evaluation results")
    if event.outcome.effects_complete == event.outcome.unknown_effects_possible:
        raise HistoryEventValidationError("effect completeness fields disagree")
    expected_result = (
        "unknown"
        if event.outcome.unknown_effects_possible
        else "changed"
        if event.effects
        else "no_change"
    )
    if event.outcome.result != expected_result:
        raise HistoryEventValidationError("outcome result does not match recorded effects")
    if event.outcome.failure is not None and (
        not event.outcome.failure.code or not event.outcome.failure.message
    ):
        raise HistoryEventValidationError("failure details must include a code and message")
    if (
        event.outcome.status != "succeeded"
        and event.outcome.stage
        in {
            "validation",
            "evaluation",
            "planning",
        }
        and (event.effects or not event.outcome.effects_complete)
    ):
        raise HistoryEventValidationError("pre-mutation outcome must have complete empty effects")

    for record in (*event.effects, *event.non_applied):
        _validate_action(record.action)
        counts = record.counts
        if (
            min(
                counts.cards,
                counts.notes,
                counts.matching_trigger_cards,
                counts.consequential_sibling_cards,
            )
            < 0
        ):
            raise HistoryEventValidationError("negative history count")
        if (
            counts.cards <= 0
            or counts.notes <= 0
            or counts.matching_trigger_cards <= 0
            or counts.notes > counts.cards
            or counts.matching_trigger_cards + counts.consequential_sibling_cards != counts.cards
        ):
            raise HistoryEventValidationError("history entity counts are inconsistent")
        if not record.contributors:
            raise HistoryEventValidationError("effect/disposition requires contributors")
        contributor_keys = tuple(
            (item.policy.id, item.policy.definition_hash) for item in record.contributors
        )
        if len(contributor_keys) != len(set(contributor_keys)) or contributor_keys != tuple(
            sorted(contributor_keys)
        ):
            raise HistoryEventValidationError("contributors are not canonical")
        for contributor in record.contributors:
            key = contributor.policy.id, contributor.policy.definition_hash
            if key not in refs:
                raise HistoryEventValidationError("contributor references a missing policy")
            if not set(contributor.match_signatures) <= signatures_by_policy[key]:
                raise HistoryEventValidationError("contributor references a missing signature")
            if contributor.trigger_cards <= 0:
                raise HistoryEventValidationError("contributor count must be positive")
            if contributor.match_signatures != tuple(sorted(set(contributor.match_signatures))):
                raise HistoryEventValidationError("contributor signatures are not canonical")
    admitted_dispositions = {
        "already_satisfied",
        "conflict",
        "preview_no_longer_matching",
        "preview_newly_matching",
        "preview_note_boundary",
    }
    for record in event.non_applied:
        if record.disposition not in admitted_dispositions:
            raise HistoryEventValidationError("non-applied disposition is not admitted")
        if record.disposition == "conflict" and not record.reason_codes:
            raise HistoryEventValidationError("conflict disposition requires reason codes")
        if record.disposition != "conflict" and record.reason_codes:
            raise HistoryEventValidationError("only conflicts may contain reason codes")
        if record.reason_codes != tuple(sorted(set(record.reason_codes))):
            raise HistoryEventValidationError("non-applied reason codes are not canonical")

    if event.summary != SummaryRecord(
        len(event.policies),
        event.summary.distinct_cards_changed,
        event.summary.distinct_notes_changed,
        len(event.effects),
        len(event.non_applied),
    ):
        raise HistoryEventValidationError("event summary group counts are inconsistent")
    if event.summary.distinct_cards_changed < max(
        (effect.counts.cards for effect in event.effects), default=0
    ) or event.summary.distinct_notes_changed < max(
        (effect.counts.notes for effect in event.effects), default=0
    ):
        raise HistoryEventValidationError("event summary distinct counts are inconsistent")
    if event.summary.distinct_cards_changed > sum(
        effect.counts.cards for effect in event.effects
    ) or event.summary.distinct_notes_changed > sum(
        effect.counts.notes for effect in event.effects
    ):
        raise HistoryEventValidationError("event summary distinct counts are inconsistent")
    if event.execution.status == "complete":
        if event.execution.reason_code is not None or any(
            step.status != "completed" for step in event.execution.steps
        ):
            raise HistoryEventValidationError("complete execution contains an incomplete step")
    elif event.execution.status == "not_reached":
        if event.execution.steps or event.execution.reason_code is not None:
            raise HistoryEventValidationError("unreached execution cannot contain details")
        if event.outcome.status == "succeeded" or event.outcome.stage not in {
            "validation",
            "evaluation",
            "planning",
        }:
            raise HistoryEventValidationError("execution status conflicts with outcome")
    elif event.execution.status == "unavailable":
        if event.execution.steps or not event.execution.reason_code:
            raise HistoryEventValidationError("unavailable execution requires only a reason")
    elif event.execution.status == "partial":
        if event.execution.reason_code is not None or event.outcome.status == "succeeded":
            raise HistoryEventValidationError("partial execution conflicts with outcome")
        if not any(step.status != "completed" for step in event.execution.steps):
            raise HistoryEventValidationError("partial execution has no incomplete step")
    else:
        raise HistoryEventValidationError("invalid execution status")
    if tuple(step.sequence for step in event.execution.steps) != tuple(
        range(len(event.execution.steps))
    ):
        raise HistoryEventValidationError("execution steps are not contiguous")
    allowed_step_statuses = {
        "completed",
        "completed_undo_merge_failed",
        "failed_before_mutation",
        "failed_unknown",
        "not_attempted_due_to_failure",
    }
    for step in event.execution.steps:
        if step.operation not in {
            "add_tag",
            "remove_tag",
            "replace_tags",
            "move",
            "set_flag",
            "suspend",
            "unsuspend",
            "delete_card",
            "delete_note",
        }:
            raise HistoryEventValidationError("invalid execution operation")
        if step.target_kind not in {"card", "note"} or step.status not in allowed_step_statuses:
            raise HistoryEventValidationError("invalid execution step")
        if not step.targets or any(target.targets <= 0 for target in step.targets):
            raise HistoryEventValidationError("execution target counts must be positive")
        for target in step.targets:
            parameter = target.parameter
            if step.operation in {"suspend", "unsuspend", "delete_card", "delete_note"}:
                valid_parameter = parameter is None
            elif parameter is None:
                valid_parameter = True
            elif step.operation == "replace_tags":
                valid_parameter = (
                    isinstance(parameter, tuple)
                    and bool(parameter)
                    and all(isinstance(item, str) and item for item in parameter)
                    and len({item.casefold() for item in parameter}) == len(parameter)
                )
            elif step.operation == "set_flag":
                valid_parameter = parameter in {
                    "none",
                    "red",
                    "orange",
                    "green",
                    "blue",
                    "pink",
                    "turquoise",
                    "purple",
                }
            else:
                valid_parameter = isinstance(parameter, str) and bool(parameter)
            if not valid_parameter:
                raise HistoryEventValidationError("invalid execution target parameter")
