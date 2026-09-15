# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .evaluator import evaluate_policies

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
    approved_reports = tuple(
        replace(
            report,
            actionable=tuple(
                card
                for card in report.actionable
                if card.card_id in approved_card_ids[report.policy.id]
            ),
        )
        for report in fresh_reports
    )
    return execute_plan(col, build_execution_plan(approved_reports), undo_name)
