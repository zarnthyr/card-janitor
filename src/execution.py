# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .evaluator import evaluate_policies
from .models import action_expands_to_siblings

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport


def execute_approved_reports(
    col: Collection,
    reports: tuple[PolicyReport, ...],
    approved_card_ids: dict[str, set[int]],
    undo_name: str,
) -> ExecutionResult:
    fresh_reports = evaluate_policies(col, tuple(report.policy for report in reports))
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
