# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from .actions import (
    CleanupError,
    ExecutionResult,
    build_execution_plan,
    execute_plan,
    history_action_target_kind,
    policy_evaluation_facts,
)
from .configuration import COLLECTION_POLICIES_KEY
from .engine import action_is_satisfied
from .evaluator import evaluate_policies
from .history_semantics import (
    NOT_COLLECTED_EXECUTION_LEDGER,
    BoundaryDisposition,
    PlanSemantics,
)
from .log import exception
from .models import (
    Policy,
    action_expands_to_siblings,
    parse_policy,
    policy_to_dict,
)

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import CardFacts, PolicyReport, ResolvedAction
    from .history_runtime import HistorySession


class StalePolicyDefinitionsError(RuntimeError):
    """The policies approved for cleanup no longer match their saved definitions."""


_STALE_POLICIES_MESSAGE = (
    "Cleanup cancelled because one or more selected policies changed. "
    "Refresh Card Janitor and try again."
)


def _append_boundary_dispositions(
    boundary: list[BoundaryDisposition],
    *,
    code: Literal[
        "preview_no_longer_matching",
        "preview_newly_matching",
        "preview_note_boundary",
    ],
    card: CardFacts,
    actions: tuple[ResolvedAction, ...],
    qualifying_by_note: dict[int, set[int]],
) -> None:
    grouped: dict[tuple[int, ...], list[ResolvedAction]] = {}
    for resolved in actions:
        trigger_ids = (
            tuple(sorted(qualifying_by_note.get(card.note_id, ())))
            if history_action_target_kind(resolved.action) == "note"
            else (card.card_id,)
        )
        grouped.setdefault(trigger_ids, []).append(resolved)
    for trigger_ids, grouped_actions in grouped.items():
        boundary.append(
            BoundaryDisposition(
                code,
                card.card_id,
                card.note_id,
                tuple(grouped_actions),
                trigger_ids,
            )
        )


def _history_boundary_dispositions(
    preview: PolicyReport,
    fresh: PolicyReport,
    approved_ids: set[int],
    blocked_notes: set[int],
) -> tuple[BoundaryDisposition, ...]:
    preview_cards = {card.card_id: card for card in (*preview.qualifying, *preview.actionable)}
    preview_actions = {card.card_id: actions for card, actions in preview.card_actions}
    fresh_actions = {card.card_id: actions for card, actions in fresh.card_actions}
    preview_qualifying_by_note: dict[int, set[int]] = {}
    fresh_qualifying_by_note: dict[int, set[int]] = {}
    for card in preview.qualifying:
        preview_qualifying_by_note.setdefault(card.note_id, set()).add(card.card_id)
    for card in fresh.qualifying:
        fresh_qualifying_by_note.setdefault(card.note_id, set()).add(card.card_id)

    boundary: list[BoundaryDisposition] = []
    for card_id in sorted(approved_ids):
        card = preview_cards[card_id]
        current_actions = fresh_actions.get(card_id, ())
        actions = tuple(
            action
            for action in preview_actions.get(card_id, ())
            if not action_is_satisfied(action, card)
            and not any(current.action == action.action for current in current_actions)
        )
        _append_boundary_dispositions(
            boundary,
            code="preview_no_longer_matching",
            card=card,
            actions=actions,
            qualifying_by_note=preview_qualifying_by_note,
        )

    for card in fresh.actionable:
        if card.card_id in approved_ids and card.note_id not in blocked_notes:
            continue
        actions = tuple(
            action
            for action in fresh_actions.get(card.card_id, ())
            if not action_is_satisfied(action, card)
        )
        _append_boundary_dispositions(
            boundary,
            code=(
                "preview_note_boundary"
                if card.note_id in blocked_notes
                else "preview_newly_matching"
            ),
            card=card,
            actions=actions,
            qualifying_by_note=fresh_qualifying_by_note,
        )
    return tuple(boundary)


def _ensure_policy_definitions_current(col: Collection, policies: tuple[Policy, ...]) -> None:
    expected = {policy.id.casefold(): policy for policy in policies}
    if len(expected) != len(policies):
        raise StalePolicyDefinitionsError(_STALE_POLICIES_MESSAGE)
    raw_policies = col.get_config(COLLECTION_POLICIES_KEY, [])
    current: dict[str, Policy] = {}
    if isinstance(raw_policies, list):
        for index, raw in enumerate(raw_policies):
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                continue
            normalized_id = raw["id"].strip().casefold()
            if normalized_id not in expected:
                continue
            try:
                policy = parse_policy(raw, index)
            except (TypeError, ValueError):
                break
            if normalized_id in current:
                break
            current[normalized_id] = policy
    if set(current) != set(expected) or any(
        policy_to_dict(current[policy_id]) != policy_to_dict(policy)
        for policy_id, policy in expected.items()
    ):
        raise StalePolicyDefinitionsError(_STALE_POLICIES_MESSAGE)


def execute_approved_reports(  # noqa: PLR0912
    col: Collection,
    reports: tuple[PolicyReport, ...],
    approved_card_ids: dict[str, set[int]],
    undo_name: str,
    *,
    collect_history: bool = False,
    history: HistorySession | None = None,
) -> ExecutionResult:
    policies = tuple(report.policy for report in reports)
    collect_history = collect_history or bool(history and history.collect_history)
    try:
        _ensure_policy_definitions_current(col, policies)
    except StalePolicyDefinitionsError as exc:
        if history is not None:
            history.record_terminal(
                semantics=PlanSemantics("not_collected"),
                status="cancelled",
                stage="validation",
                code="stale_policy_definitions",
                message=str(exc),
            )
        raise
    except Exception as exc:
        if history is not None:
            history.record_terminal(
                semantics=PlanSemantics("not_collected"),
                status="failed",
                stage="validation",
                code="validation_failed",
                message=str(exc),
            )
        raise
    try:
        fresh_reports = evaluate_policies(
            col,
            policies,
            collect_provenance=collect_history,
        )
    except Exception as exc:
        if history is not None:
            history.record_terminal(
                semantics=PlanSemantics("unavailable", reason_code="policy_evaluation_failed"),
                status="failed",
                stage="evaluation",
                code="evaluation_failed",
                message=str(exc),
            )
        raise
    runtime_errors = [item for report in fresh_reports for item in report.errors]
    if runtime_errors:
        message = "; ".join(runtime_errors)
        if history is not None:
            history.record_terminal(
                semantics=PlanSemantics("unavailable", reason_code="policy_evaluation_failed"),
                status="failed",
                stage="evaluation",
                code="policy_evaluation_failed",
                message=message,
            )
        raise RuntimeError(message)
    preview_by_id = {report.policy.id: report for report in reports}
    approved_reports = []
    boundary_history_available = True
    for report in fresh_reports:
        approved_ids = approved_card_ids[report.policy.id]
        blocked_notes = (
            {card.note_id for card in report.actionable if card.card_id not in approved_ids}
            if any(action_expands_to_siblings(action.action) for action in report.resolved_actions)
            else set()
        )
        boundary_dispositions: tuple[BoundaryDisposition, ...] = ()
        if collect_history:
            preview = preview_by_id[report.policy.id]
            try:
                boundary_dispositions = _history_boundary_dispositions(
                    preview,
                    report,
                    approved_ids,
                    blocked_notes,
                )
            except Exception:
                boundary_history_available = False
                with suppress(Exception):
                    exception("history preview-boundary collection failed")
        approved_reports.append(
            replace(
                report,
                qualifying=tuple(
                    card for card in report.qualifying if card.note_id not in blocked_notes
                ),
                card_actions=tuple(
                    (card, actions)
                    for card, actions in report.card_actions
                    if card.note_id not in blocked_notes
                ),
                actionable=tuple(
                    card
                    for card in report.actionable
                    if card.card_id in approved_ids and card.note_id not in blocked_notes
                ),
                boundary_dispositions=boundary_dispositions,
            )
        )
    try:
        plan = build_execution_plan(
            tuple(approved_reports),
            col,
            collect_history=collect_history and boundary_history_available,
        )
        if collect_history and not boundary_history_available:
            plan = replace(
                plan,
                semantics=PlanSemantics(
                    "unavailable",
                    policy_evaluations=policy_evaluation_facts(tuple(approved_reports)),
                    reason_code="preview_boundary_trace_failed",
                ),
            )
    except CleanupError as exc:
        if history is not None:
            history.record_terminal(
                semantics=PlanSemantics(
                    "unavailable",
                    policy_evaluations=policy_evaluation_facts(tuple(approved_reports)),
                    reason_code="planning_failed",
                ),
                status="cancelled",
                stage="planning",
                code="safety_check_cancelled",
                message=str(exc),
            )
        raise
    except Exception as exc:
        if history is not None:
            history.record_terminal(
                semantics=PlanSemantics(
                    "unavailable",
                    policy_evaluations=policy_evaluation_facts(tuple(approved_reports)),
                    reason_code="planning_failed",
                ),
                status="failed",
                stage="planning",
                code="planning_failed",
                message=str(exc),
            )
        raise
    try:
        result = execute_plan(col, plan, undo_name)
    except CleanupError as exc:
        if history is not None:
            history.record_cleanup_error(
                exc,
                code=("undo_merge_failed" if exc.stage == "undo_merge" else "execution_failed"),
                recovery=exc.recovery,
            )
        raise
    except Exception as exc:
        if history is not None:
            history.record_terminal(
                semantics=plan.semantics,
                ledger=NOT_COLLECTED_EXECUTION_LEDGER,
                status="failed",
                stage="execution",
                code="execution_failed",
                message=str(exc),
            )
        raise
    if history is not None:
        history.record_success(result)
    return result
