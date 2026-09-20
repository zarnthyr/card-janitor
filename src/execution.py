# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .configuration import COLLECTION_POLICIES_KEY
from .evaluator import evaluate_policies
from .models import Policy, action_expands_to_siblings, parse_policy, policy_to_dict

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport


class StalePolicyDefinitionsError(RuntimeError):
    """The policies approved for cleanup no longer match their saved definitions."""


_STALE_POLICIES_MESSAGE = (
    "Cleanup cancelled because one or more selected policies changed. "
    "Refresh Card Janitor and try again."
)


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


def execute_approved_reports(
    col: Collection,
    reports: tuple[PolicyReport, ...],
    approved_card_ids: dict[str, set[int]],
    undo_name: str,
) -> ExecutionResult:
    policies = tuple(report.policy for report in reports)
    _ensure_policy_definitions_current(col, policies)
    fresh_reports = evaluate_policies(col, policies)
    runtime_errors = [item for report in fresh_reports for item in report.errors]
    if runtime_errors:
        raise RuntimeError("; ".join(runtime_errors))
    approved_reports = []
    for report in fresh_reports:
        approved_ids = approved_card_ids[report.policy.id]
        blocked_notes = (
            {card.note_id for card in report.actionable if card.card_id not in approved_ids}
            if any(action_expands_to_siblings(action.action) for action in report.resolved_actions)
            else set()
        )
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
            )
        )
    return execute_plan(col, build_execution_plan(tuple(approved_reports), col), undo_name)
