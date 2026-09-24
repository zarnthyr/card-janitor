# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from anki.collection import OpChanges

from .engine import action_is_satisfied
from .history_semantics import (
    EMPTY_EXECUTION_LEDGER,
    NOT_COLLECTED_EXECUTION_LEDGER,
    NOT_COLLECTED_PLAN_SEMANTICS,
    EffectContributor,
    ExecutionLedger,
    ExecutionLedgerStep,
    ExecutionLedgerTarget,
    LogicalEffect,
    LogicalIntention,
    PlanSemantics,
    PolicyEvaluationFacts,
)
from .log import exception
from .models import (
    AllCardsCondition,
    AllConditions,
    AnyConditions,
    ClearFlagAction,
    DeleteCardAction,
    DeleteNoteAction,
    MoveAction,
    RemoveTagAction,
    ReplaceTagsAction,
    SetFlagAction,
    SuspendAction,
    TagAction,
    UnsuspendAction,
    action_expands_to_siblings,
)
from .presentation import describe_action

if TYPE_CHECKING:
    from collections.abc import Callable

    from anki.collection import Collection

    from .engine import PolicyReport, ResolvedAction


@dataclass(frozen=True)
class ConflictDetail:
    card_id: int
    policies: tuple[str, ...]
    reasons: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionPlan:
    tags: tuple[tuple[str, tuple[int, ...]], ...]
    remove_tags: tuple[tuple[str, tuple[int, ...]], ...]
    replace_tags: tuple[tuple[tuple[str, ...], tuple[int, ...]], ...]
    suspend_card_ids: tuple[int, ...]
    unsuspend_card_ids: tuple[int, ...]
    moves: tuple[tuple[int, tuple[int, ...]], ...]
    flags: tuple[tuple[int, tuple[int, ...]], ...]
    delete_card_ids: tuple[int, ...]
    delete_note_ids: tuple[int, ...]
    conflicted_card_ids: tuple[int, ...]
    planned_card_ids: tuple[int, ...]
    conflict_details: tuple[ConflictDetail, ...]
    semantics: PlanSemantics = field(
        default=NOT_COLLECTED_PLAN_SEMANTICS,
        compare=False,
    )

    @property
    def card_count(self) -> int:
        return len(self.planned_card_ids)

    @property
    def is_empty(self) -> bool:
        return not (
            self.tags
            or self.remove_tags
            or self.replace_tags
            or self.suspend_card_ids
            or self.unsuspend_card_ids
            or self.moves
            or self.flags
            or self.delete_card_ids
            or self.delete_note_ids
        )


@dataclass(frozen=True)
class ExecutionResult:
    changes: OpChanges
    affected_cards: int
    conflicts: int
    semantics: PlanSemantics = NOT_COLLECTED_PLAN_SEMANTICS
    execution_ledger: ExecutionLedger = NOT_COLLECTED_EXECUTION_LEDGER


class CleanupError(RuntimeError):
    """Cleanup failed; earlier backend operations may have completed."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "execution",
        semantics: PlanSemantics = NOT_COLLECTED_PLAN_SEMANTICS,
        execution_ledger: ExecutionLedger = NOT_COLLECTED_EXECUTION_LEDGER,
        recovery: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.semantics = semantics
        self.execution_ledger = execution_ledger
        self.recovery = recovery


class _UndoMergeError(RuntimeError):
    """A completed backend operation could not be merged into the cleanup entry."""


def _conditions_are_unrestricted(condition: object) -> bool:
    if isinstance(condition, AllCardsCondition):
        return True
    if isinstance(condition, AllConditions):
        return all(_conditions_are_unrestricted(child) for child in condition.conditions)
    if isinstance(condition, AnyConditions):
        return any(_conditions_are_unrestricted(child) for child in condition.conditions)
    return False


def _ensure_execution_policies_are_safe(reports: tuple[PolicyReport, ...]) -> None:
    for report in reports:
        policy = report.policy
        if (
            any(
                isinstance(action, (DeleteCardAction, DeleteNoteAction))
                for action in policy.actions
            )
            and policy.scope.all_decks
            and policy.scope.note_types is None
            and _conditions_are_unrestricted(policy.conditions)
        ):
            message = (
                f"Cleanup refused: policy {policy.name!r} would delete cards or notes "
                "without a scope or condition restriction."
            )
            raise CleanupError(message)


def build_execution_plan(  # noqa: PLR0912
    reports: tuple[PolicyReport, ...],
    col: Collection | None = None,
    *,
    collect_history: bool = False,
) -> ExecutionPlan:
    _ensure_execution_policies_are_safe(reports)
    card_actions: dict[int, set[ResolvedAction]] = {}
    desired_card_actions: dict[int, set[ResolvedAction]] = {}
    card_notes: dict[int, int] = {}
    note_wide_ids: set[int] = set()
    card_policies: dict[int, set[str]] = {}
    note_policies: dict[int, set[str]] = {}
    action_sources: dict[int, dict[ResolvedAction, set[str]]] = {}
    card_action_sources: dict[int, dict[ResolvedAction, set[str]]] = {}
    for report in reports:
        desired = report.card_actions
        per_card = {card.card_id: actions for card, actions in desired}
        for card, actions in desired:
            for action in actions:
                card_action_sources.setdefault(card.card_id, {}).setdefault(action, set()).add(
                    report.policy.name
                )
                action_sources.setdefault(card.note_id, {}).setdefault(action, set()).add(
                    report.policy.name
                )
            card_policies.setdefault(card.card_id, set()).add(report.policy.name)
            note_policies.setdefault(card.note_id, set()).add(report.policy.name)
            card_notes[card.card_id] = card.note_id
            desired_card_actions.setdefault(card.card_id, set()).update(actions)
            if any(action_expands_to_siblings(action.action) for action in actions):
                note_wide_ids.add(card.note_id)
        for card in report.actionable:
            card_notes[card.card_id] = card.note_id
            card_actions.setdefault(card.card_id, set()).update(
                action for action in per_card[card.card_id] if not action_is_satisfied(action, card)
            )

    conflicts: set[int] = set()
    reasons: dict[int, set[str]] = {}
    reason_codes: dict[int, set[str]] = {}

    def mark_conflict(
        ids: set[int] | tuple[int, ...],
        code: str,
        reason: str,
        policies: set[str] | None = None,
    ) -> None:
        for card_id in ids:
            conflicts.add(card_id)
            reasons.setdefault(card_id, set()).add(reason)
            reason_codes.setdefault(card_id, set()).add(code)
            if policies is not None:
                card_policies[card_id].update(policies)

    for card_id, actions in desired_card_actions.items():
        move_targets = {
            action.target_deck_id for action in actions if isinstance(action.action, MoveAction)
        }
        has_delete = any(
            isinstance(action.action, (DeleteCardAction, DeleteNoteAction)) for action in actions
        )
        has_suspend = any(isinstance(action.action, SuspendAction) for action in actions)
        has_unsuspend = any(isinstance(action.action, UnsuspendAction) for action in actions)
        flag_values = {
            0 if isinstance(action.action, ClearFlagAction) else _flag_number(action.action.flag)
            for action in actions
            if isinstance(action.action, (SetFlagAction, ClearFlagAction))
        }
        if card_id in card_actions:
            if len(move_targets) > 1:
                mark_conflict(
                    (card_id,), "different_move_destinations", "Different move destinations"
                )
            if has_delete and len(actions) > 1:
                mark_conflict(
                    (card_id,),
                    "deletion_with_other_actions",
                    "Deletion combined with other actions",
                )
            if has_suspend and has_unsuspend:
                mark_conflict((card_id,), "suspend_unsuspend", "Suspend and unsuspend conflict")
            if len(flag_values) > 1:
                mark_conflict((card_id,), "different_flags", "Different card flags")

    note_actions: dict[int, set[ResolvedAction]] = {}
    all_note_actions: dict[int, set[ResolvedAction]] = {}
    note_cards: dict[int, set[int]] = {}
    for card_id, actions in desired_card_actions.items():
        note_id = card_notes[card_id]
        if card_id in card_actions:
            note_cards.setdefault(note_id, set()).add(card_id)
        all_note_actions.setdefault(note_id, set()).update(actions)
        note_actions.setdefault(note_id, set()).update(
            action
            for action in actions
            if isinstance(action.action, (TagAction, RemoveTagAction, ReplaceTagsAction))
        )
    for note_id, actions in note_actions.items():
        added = {
            action.action.tag.casefold()
            for action in actions
            if isinstance(action.action, TagAction)
        }
        removed = {
            action.action.tag.casefold()
            for action in actions
            if isinstance(action.action, RemoveTagAction)
        }
        replacements = {
            frozenset(tag.casefold() for tag in action.action.tags)
            for action in actions
            if isinstance(action.action, ReplaceTagsAction)
        }
        ids = note_cards.get(note_id, set())
        if added & removed:
            mark_conflict(
                ids,
                "tag_add_remove",
                "Tags are both added and removed: " + ", ".join(sorted(added & removed)),
                note_policies[note_id],
            )
        if len(replacements) > 1:
            mark_conflict(
                ids,
                "different_tag_replacements",
                "Different tag replacements",
                note_policies[note_id],
            )
        if replacements and (added or removed):
            mark_conflict(
                ids,
                "replace_tags_with_incremental",
                "Replacing tags combined with adding or removing tags",
                note_policies[note_id],
            )
    for note_id, actions in all_note_actions.items():
        if any(isinstance(action.action, DeleteNoteAction) for action in actions) and any(
            not isinstance(action.action, DeleteNoteAction) for action in actions
        ):
            mark_conflict(
                note_cards.get(note_id, ()),
                "note_deletion_with_other_actions",
                "Note deletion combined with other actions",
                note_policies[note_id],
            )
    # A note-wide operation is atomic: never apply it to only some siblings.
    for note_id in note_wide_ids:
        if conflicts.intersection(note_cards.get(note_id, ())):
            inherited_reasons = {
                reason for card_id in note_cards[note_id] for reason in reasons.get(card_id, ())
            }
            inherited_codes = {
                code for card_id in note_cards[note_id] for code in reason_codes.get(card_id, ())
            }
            for card_id in note_cards[note_id]:
                conflicts.add(card_id)
                reasons.setdefault(card_id, set()).update(inherited_reasons)
                reason_codes.setdefault(card_id, set()).update(inherited_codes)
                card_policies[card_id].update(note_policies[note_id])
            mark_conflict(
                note_cards[note_id],
                "note_wide_conflict_propagation",
                "All affected cards of the note are skipped together",
                note_policies[note_id],
            )

    tags: dict[str, set[int]] = {}
    remove_tags: dict[str, set[int]] = {}
    replace_tags: dict[tuple[str, ...], set[int]] = {}
    replacement_values: dict[tuple[str, ...], tuple[str, ...]] = {}
    suspend: set[int] = set()
    unsuspend: set[int] = set()
    moves: dict[int, set[int]] = {}
    flags: dict[int, set[int]] = {}
    delete: set[int] = set()
    delete_notes: set[int] = set()
    for card_id, actions in card_actions.items():
        if card_id in conflicts:
            continue
        for resolved in actions:
            action = resolved.action
            if isinstance(action, TagAction):
                tags.setdefault(action.tag, set()).add(card_notes[card_id])
            elif isinstance(action, RemoveTagAction):
                remove_tags.setdefault(action.tag, set()).add(card_notes[card_id])
            elif isinstance(action, ReplaceTagsAction):
                key = tuple(sorted(tag.casefold() for tag in action.tags))
                replace_tags.setdefault(key, set()).add(card_notes[card_id])
                replacement_values[key] = min(replacement_values.get(key, action.tags), action.tags)
            elif isinstance(action, SuspendAction):
                suspend.add(card_id)
            elif isinstance(action, UnsuspendAction):
                unsuspend.add(card_id)
            elif isinstance(action, MoveAction):
                if resolved.target_deck_id is not None:
                    moves.setdefault(resolved.target_deck_id, set()).add(card_id)
            elif isinstance(action, SetFlagAction):
                flags.setdefault(_flag_number(action.flag), set()).add(card_id)
            elif isinstance(action, ClearFlagAction):
                flags.setdefault(0, set()).add(card_id)
            elif isinstance(action, DeleteCardAction):
                delete.add(card_id)
            elif isinstance(action, DeleteNoteAction):
                delete_notes.add(card_notes[card_id])

    planned_card_ids = set(card_actions) - conflicts
    if col is not None:
        for note_id in delete_notes:
            planned_card_ids.update(int(card_id) for card_id in col.card_ids_of_note(note_id))

    def explain(card_id: int, reason: str) -> str:
        if reason == "All affected cards of the note are skipped together":
            return reason
        types = (
            (MoveAction,)
            if reason.startswith("Different move")
            else (SuspendAction, UnsuspendAction)
            if reason.startswith("Suspend and unsuspend")
            else (TagAction, RemoveTagAction, ReplaceTagsAction)
            if reason.startswith(("Tags are", "Different tag replacements", "Replacing tags"))
            else (SetFlagAction, ClearFlagAction)
            if reason.startswith("Different card flags")
            else None
        )
        descriptions: set[str] = set()
        note_id = card_notes[card_id]
        sources = action_sources[note_id]
        if note_id not in note_wide_ids and reason.startswith(
            ("Different move", "Suspend and unsuspend", "Deletion combined")
        ):
            sources = card_action_sources[card_id]
        for resolved, policies in sources.items():
            if types is not None and not isinstance(resolved.action, types):
                continue
            if reason.startswith("Different tag replacements") and not isinstance(
                resolved.action, ReplaceTagsAction
            ):
                continue
            if reason.startswith("Tags are"):
                opposing_type = (
                    RemoveTagAction if isinstance(resolved.action, TagAction) else TagAction
                )
                if not isinstance(resolved.action, (TagAction, RemoveTagAction)) or not any(
                    isinstance(other.action, opposing_type)
                    and other.action.tag.casefold() == resolved.action.tag.casefold()
                    for other in sources
                ):
                    continue
            descriptions.update(
                f"{policy}: {describe_action(resolved.action)}" for policy in policies
            )
        return reason + "\n" + "\n".join(sorted(descriptions))

    plan = ExecutionPlan(
        tags=tuple((tag, tuple(sorted(ids))) for tag, ids in sorted(tags.items())),
        remove_tags=tuple((tag, tuple(sorted(ids))) for tag, ids in sorted(remove_tags.items())),
        replace_tags=tuple(
            (replacement_values[key], tuple(sorted(ids)))
            for key, ids in sorted(replace_tags.items())
        ),
        suspend_card_ids=tuple(sorted(suspend)),
        unsuspend_card_ids=tuple(sorted(unsuspend)),
        moves=tuple((deck_id, tuple(sorted(ids))) for deck_id, ids in sorted(moves.items())),
        flags=tuple((flag, tuple(sorted(ids))) for flag, ids in sorted(flags.items())),
        delete_card_ids=tuple(sorted(delete)),
        delete_note_ids=tuple(sorted(delete_notes)),
        conflicted_card_ids=tuple(sorted(conflicts)),
        planned_card_ids=tuple(sorted(planned_card_ids)),
        conflict_details=tuple(
            ConflictDetail(
                card_id,
                tuple(sorted(card_policies[card_id])),
                tuple(explain(card_id, reason) for reason in sorted(reasons[card_id])),
                tuple(sorted(reason_codes[card_id])),
            )
            for card_id in sorted(conflicts)
        ),
    )
    if not collect_history:
        return plan
    try:
        return replace(plan, semantics=_build_plan_semantics(reports, plan, col))
    except Exception:
        _log_history_exception("history planning semantics collection failed")
        return replace(
            plan,
            semantics=PlanSemantics("unavailable", reason_code="planning_trace_failed"),
        )


def history_action_target_kind(action: object) -> str:
    if isinstance(action, (TagAction, RemoveTagAction, ReplaceTagsAction)):
        return "note"
    return "note" if action_expands_to_siblings(action) else "card"


def _log_history_exception(message: str) -> None:
    with suppress(Exception):
        exception(message)


def _tag_note_card_ids(
    col: Collection | None,
    note_ids: set[int],
) -> tuple[dict[int, set[int]], bool]:
    if col is None:
        return {}, False
    grouped: dict[int, set[int]] = {note_id: set() for note_id in note_ids}
    ordered = sorted(note_ids)
    for offset in range(0, len(ordered), 900):
        batch = ordered[offset : offset + 900]
        placeholders = ",".join("?" for _value in batch)
        for note_id, card_id in col.db.all(
            f"select nid, id from cards where nid in ({placeholders})",  # noqa: S608
            *batch,
        ):
            grouped[int(note_id)].add(int(card_id))
    return grouped, True


def policy_evaluation_facts(
    reports: tuple[PolicyReport, ...],
) -> tuple[PolicyEvaluationFacts, ...]:
    return tuple(
        PolicyEvaluationFacts(
            report.policy.id,
            (
                report.evaluation_qualifying_cards
                if report.evaluation_qualifying_cards is not None
                else len(report.qualifying)
            ),
            (
                report.evaluation_actionable_cards
                if report.evaluation_actionable_cards is not None
                else len(report.actionable)
            ),
            report.match_provenance,
        )
        for report in reports
    )


def _build_plan_semantics(
    reports: tuple[PolicyReport, ...],
    plan: ExecutionPlan,
    col: Collection | None,
) -> PlanSemantics:
    conflict_codes = {detail.card_id: detail.reason_codes for detail in plan.conflict_details}
    intentions: list[LogicalIntention] = []
    policy_evaluations = policy_evaluation_facts(reports)
    provenance_by_card = {
        report.policy.id: {match.card_id: match for match in report.match_provenance.cards}
        for report in reports
        if report.match_provenance.status == "complete"
    }

    for report in reports:
        qualifying_by_note: dict[int, set[int]] = {}
        qualifying_ids = {card.card_id for card in report.qualifying}
        actionable_ids = {card.card_id for card in report.actionable}
        for card in report.qualifying:
            qualifying_by_note.setdefault(card.note_id, set()).add(card.card_id)
        for card, actions in report.card_actions:
            for resolved in actions:
                target_kind = history_action_target_kind(resolved.action)
                trigger_ids = (
                    qualifying_by_note.get(card.note_id, set())
                    if target_kind == "note"
                    else ({card.card_id} if card.card_id in qualifying_ids else set())
                )
                satisfied = action_is_satisfied(resolved, card)
                disposition = (
                    "conflict"
                    if card.card_id in conflict_codes
                    else "already_satisfied"
                    if satisfied
                    else "planned"
                    if card.card_id in actionable_ids
                    else None
                )
                if disposition is None:
                    continue
                intentions.append(
                    LogicalIntention(
                        policy_id=report.policy.id,
                        action=resolved,
                        target_kind=target_kind,
                        target_id=card.note_id if target_kind == "note" else card.card_id,
                        target_card_id=card.card_id,
                        target_note_id=card.note_id,
                        trigger_card_ids=tuple(sorted(trigger_ids)),
                        disposition=disposition,
                        reason_codes=(
                            conflict_codes.get(card.card_id, ())
                            if disposition == "conflict"
                            else ()
                        ),
                    )
                )
        for boundary in report.boundary_dispositions:
            for resolved in boundary.actions:
                target_kind = history_action_target_kind(resolved.action)
                intentions.append(
                    LogicalIntention(
                        policy_id=report.policy.id,
                        action=resolved,
                        target_kind=target_kind,
                        target_id=(boundary.note_id if target_kind == "note" else boundary.card_id),
                        target_card_id=boundary.card_id,
                        target_note_id=boundary.note_id,
                        trigger_card_ids=boundary.trigger_card_ids,
                        disposition=boundary.code,
                    )
                )

    effect_groups: dict[tuple[ResolvedAction, str, int], list[LogicalIntention]] = {}
    for intention in intentions:
        if intention.disposition == "planned":
            effect_groups.setdefault(
                (intention.action, intention.target_kind, intention.target_id), []
            ).append(intention)

    tag_note_ids = {
        target_id
        for (action, target_kind, target_id) in effect_groups
        if target_kind == "note"
        and isinstance(action.action, (TagAction, RemoveTagAction, ReplaceTagsAction))
    }
    tag_cards, tag_cards_complete = _tag_note_card_ids(col, tag_note_ids)

    effects = []
    for (action, target_kind, target_id), grouped in effect_groups.items():
        contributors = []
        for policy_id in sorted({item.policy_id for item in grouped}):
            trigger_ids = tuple(
                sorted(
                    {
                        card_id
                        for item in grouped
                        if item.policy_id == policy_id
                        for card_id in item.trigger_card_ids
                    }
                )
            )
            match_map = provenance_by_card.get(policy_id, {})
            contributors.append(
                EffectContributor(
                    policy_id,
                    trigger_ids,
                    tuple(match_map[card_id] for card_id in trigger_ids if card_id in match_map),
                )
            )
        trigger_card_ids = tuple(
            sorted(
                {
                    card_id
                    for contributor in contributors
                    for card_id in contributor.trigger_card_ids
                }
            )
        )
        is_tag_effect = target_kind == "note" and isinstance(
            action.action,
            (TagAction, RemoveTagAction, ReplaceTagsAction),
        )
        affected_card_ids = tuple(
            sorted(
                tag_cards.get(target_id, set())
                if is_tag_effect and tag_cards_complete
                else {item.target_card_id for item in grouped}
            )
        )
        affected_matching_trigger_ids = tuple(
            sorted(set(affected_card_ids).intersection(trigger_card_ids))
        )
        effects.append(
            LogicalEffect(
                action=action,
                target_kind=target_kind,
                target_id=target_id,
                affected_card_ids=affected_card_ids,
                affected_note_ids=tuple(sorted({item.target_note_id for item in grouped})),
                affected_card_ids_complete=not is_tag_effect or tag_cards_complete,
                matching_trigger_card_ids=affected_matching_trigger_ids,
                consequential_sibling_card_ids=tuple(
                    sorted(set(affected_card_ids) - set(trigger_card_ids))
                ),
                contributors=tuple(contributors),
            )
        )

    intentions.sort(
        key=lambda item: (
            item.policy_id,
            item.target_kind,
            item.target_id,
            item.target_card_id,
            repr(item.action),
            item.disposition,
        )
    )
    effects.sort(
        key=lambda item: (
            item.target_kind,
            item.target_id,
            repr(item.action),
        )
    )
    return PlanSemantics(
        status="complete",
        intentions=tuple(intentions),
        effects=tuple(effects),
        policy_evaluations=policy_evaluations,
    )


def execute_plan(col: Collection, plan: ExecutionPlan, undo_name: str) -> ExecutionResult:
    if plan.is_empty:
        return ExecutionResult(
            changes=OpChanges(),
            affected_cards=0,
            conflicts=len(plan.conflicted_card_ids),
            semantics=plan.semantics,
            execution_ledger=(
                EMPTY_EXECUTION_LEDGER
                if plan.semantics.status != "not_collected"
                else NOT_COLLECTED_EXECUTION_LEDGER
            ),
        )
    undo_target = col.add_custom_undo_entry(undo_name)
    ledger = _new_execution_ledger(plan)
    affected_card_ids = set(plan.planned_card_ids)
    for note_id in plan.delete_note_ids:
        affected_card_ids.update(int(card_id) for card_id in col.card_ids_of_note(note_id))
    try:
        changes = _apply_plan(col, plan, undo_target, ledger)
    except _UndoMergeError as exc:
        message = (
            "Cleanup stopped after applying changes because Anki could not preserve them as "
            f"one Undo entry: {exc}\n\nSome changes may be recoverable through Anki Undo, "
            "but complete recovery cannot be guaranteed."
        )
        raise CleanupError(
            message,
            stage="undo_merge",
            semantics=plan.semantics,
            execution_ledger=ledger.snapshot(succeeded=False),
            recovery=(
                "Some changes may be recoverable through Anki Undo, but complete recovery "
                "cannot be guaranteed."
            ),
        ) from exc
    except Exception as exc:
        if _undo_entry_is_current(col, undo_target, undo_name):
            recovery = f"Use Anki's Undo entry {undo_name!r} to revert any completed changes."
        else:
            recovery = (
                "Card Janitor could not verify a complete cleanup Undo entry. Some changes may "
                "be recoverable through Anki Undo, but complete recovery cannot be guaranteed."
            )
        message = f"Cleanup failed: {exc}\n\nEarlier changes may have been applied. {recovery}"
        raise CleanupError(
            message,
            semantics=plan.semantics,
            execution_ledger=ledger.snapshot(succeeded=False),
            recovery=recovery,
        ) from exc
    return ExecutionResult(
        changes=changes,
        affected_cards=len(affected_card_ids),
        conflicts=len(plan.conflicted_card_ids),
        semantics=plan.semantics,
        execution_ledger=ledger.snapshot(succeeded=True),
    )


def _undo_entry_is_current(col: Collection, undo_target: int, undo_name: str) -> bool:
    try:
        status = col.undo_status()
    except Exception:
        exception("failed to inspect undo state after cleanup failure")
        return False
    return status.last_step == undo_target and status.undo == undo_name


def _merge_completed_operation(col: Collection, undo_target: int) -> OpChanges:
    try:
        return col.merge_undo_entries(undo_target)
    except Exception as exc:
        raise _UndoMergeError(str(exc)) from exc


class _ExecutionLedgerRecorder:
    def __init__(
        self,
        steps: tuple[ExecutionLedgerStep, ...],
        *,
        enabled: bool,
    ) -> None:
        self._steps = list(steps)
        self._enabled = enabled
        self._available = enabled

    def set_status(self, sequence: int, status: str) -> None:
        if not self._available:
            return
        try:
            current = self._steps[sequence]
            self._steps[sequence] = replace(current, status=status)
        except Exception:
            self._available = False
            _log_history_exception("execution history ledger update failed")

    def snapshot(self, *, succeeded: bool) -> ExecutionLedger:
        if not self._enabled:
            return NOT_COLLECTED_EXECUTION_LEDGER
        if not self._available:
            return ExecutionLedger(
                "unavailable",
                (),
                effects_complete=False,
                unknown_effects_possible=True,
                reason_code="execution_ledger_failed",
            )
        unknown = any(step.status == "failed_unknown" for step in self._steps)
        return ExecutionLedger(
            "complete" if succeeded else "partial",
            tuple(self._steps),
            effects_complete=not unknown,
            unknown_effects_possible=unknown,
        )


def _new_execution_ledger(plan: ExecutionPlan) -> _ExecutionLedgerRecorder:
    enabled = plan.semantics.status != "not_collected"
    if not enabled:
        return _ExecutionLedgerRecorder((), enabled=False)
    try:
        return _ExecutionLedgerRecorder(
            _planned_ledger_steps(plan),
            enabled=enabled,
        )
    except Exception:
        _log_history_exception("execution history ledger creation failed")
        recorder = _ExecutionLedgerRecorder((), enabled=enabled)
        recorder._available = False
        return recorder


def _planned_ledger_steps(plan: ExecutionPlan) -> tuple[ExecutionLedgerStep, ...]:
    pending: list[tuple[str, str, tuple[ExecutionLedgerTarget, ...]]] = []
    pending.extend(
        ("add_tag", "note", (ExecutionLedgerTarget(tag, ids),)) for tag, ids in plan.tags
    )
    pending.extend(
        ("remove_tag", "note", (ExecutionLedgerTarget(tag, ids),)) for tag, ids in plan.remove_tags
    )
    if plan.replace_tags:
        pending.append(
            (
                "replace_tags",
                "note",
                tuple(
                    ExecutionLedgerTarget(tags, note_ids) for tags, note_ids in plan.replace_tags
                ),
            )
        )
    pending.extend(
        ("move", "card", (ExecutionLedgerTarget(deck_id, ids),)) for deck_id, ids in plan.moves
    )
    pending.extend(
        ("set_flag", "card", (ExecutionLedgerTarget(flag, ids),)) for flag, ids in plan.flags
    )
    if plan.suspend_card_ids:
        pending.append(
            (
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, plan.suspend_card_ids),),
            )
        )
    if plan.unsuspend_card_ids:
        pending.append(
            (
                "unsuspend",
                "card",
                (ExecutionLedgerTarget(None, plan.unsuspend_card_ids),),
            )
        )
    if plan.delete_card_ids:
        pending.append(
            (
                "delete_card",
                "card",
                (ExecutionLedgerTarget(None, plan.delete_card_ids),),
            )
        )
    if plan.delete_note_ids:
        pending.append(
            (
                "delete_note",
                "note",
                (ExecutionLedgerTarget(None, plan.delete_note_ids),),
            )
        )
    return tuple(
        ExecutionLedgerStep(
            sequence,
            operation,
            target_kind,
            targets,
            "not_attempted_due_to_failure",
        )
        for sequence, (operation, target_kind, targets) in enumerate(pending)
    )


def _apply_mutation(
    col: Collection,
    undo_target: int,
    ledger: _ExecutionLedgerRecorder,
    sequence: int,
    mutation: Callable[[], object],
) -> OpChanges:
    try:
        mutation()
    except Exception:
        ledger.set_status(sequence, "failed_unknown")
        raise
    try:
        changes = _merge_completed_operation(col, undo_target)
    except _UndoMergeError:
        ledger.set_status(sequence, "completed_undo_merge_failed")
        raise
    ledger.set_status(sequence, "completed")
    return changes


def _apply_plan(
    col: Collection,
    plan: ExecutionPlan,
    undo_target: int,
    ledger: _ExecutionLedgerRecorder,
) -> OpChanges:
    changes = OpChanges()
    sequence = 0
    for tag, note_ids in plan.tags:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda tag=tag, note_ids=note_ids: col.tags.bulk_add(note_ids, tag),
        )
        sequence += 1
    for tag, note_ids in plan.remove_tags:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda tag=tag, note_ids=note_ids: col.tags.bulk_remove(note_ids, tag),
        )
        sequence += 1
    replacement_notes = []
    try:
        for tags, note_ids in plan.replace_tags:
            for note_id in note_ids:
                note = col.get_note(note_id)
                note.tags = list(tags)
                replacement_notes.append(note)
    except Exception:
        ledger.set_status(sequence, "failed_before_mutation")
        raise
    if replacement_notes:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda: col.update_notes(replacement_notes),
        )
        sequence += 1
    for deck_id, card_ids in plan.moves:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda deck_id=deck_id, card_ids=card_ids: col.set_deck(card_ids, deck_id),
        )
        sequence += 1
    for flag, card_ids in plan.flags:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda flag=flag, card_ids=card_ids: col.set_user_flag_for_cards(flag, card_ids),
        )
        sequence += 1
    if plan.suspend_card_ids:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda: col.sched.suspend_cards(plan.suspend_card_ids),
        )
        sequence += 1
    if plan.unsuspend_card_ids:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda: col.sched.unsuspend_cards(plan.unsuspend_card_ids),
        )
        sequence += 1
    if plan.delete_card_ids:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda: col.remove_cards_and_orphaned_notes(plan.delete_card_ids),
        )
        sequence += 1
    if plan.delete_note_ids:
        changes = _apply_mutation(
            col,
            undo_target,
            ledger,
            sequence,
            lambda: col.remove_notes(plan.delete_note_ids),
        )
    return changes


def _flag_number(flag: str) -> int:
    return {
        "red": 1,
        "orange": 2,
        "green": 3,
        "blue": 4,
        "pink": 5,
        "turquoise": 6,
        "purple": 7,
    }[flag]
