# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from aqt import mw
from aqt.operations import CollectionOp, QueryOp
from aqt.utils import showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan
from .configuration import load_config
from .evaluator import evaluate_policies
from .execution import execute_approved_reports
from .log import configure as configure_logging
from .log import debug, error
from .presentation import applied_message, configuration_error_text

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport
    from .models import ParsedConfig

AutomaticTrigger = Literal["profile_open", "day_change"]
LAST_AUTOMATIC_DAY_PROFILE_KEY = "card_janitor_last_automatic_day"


def _load_configured() -> ParsedConfig:
    parsed = load_config()
    configure_logging(debug_logging=parsed.config.debug_logging)
    return parsed


def automatic_run_is_due(
    *,
    today: int,
    last_automatic_day: object,
) -> bool:
    return last_automatic_day != today


def _mark_daily_run(today: int) -> None:
    if mw.pm.profile is None:
        return
    mw.pm.profile[LAST_AUTOMATIC_DAY_PROFILE_KEY] = today


def _automatic_completion_message(*, notify: bool, affected_cards: int, conflicts: int) -> str:
    messages: list[str] = []
    if notify and affected_cards:
        messages.append(applied_message(affected_cards).removesuffix("."))
    if conflicts:
        messages.append(f"{conflicts} conflicting cards were skipped")
    return " ".join(f"{message}." for message in messages)


def run_automatic_policies(*, trigger: AutomaticTrigger = "profile_open") -> None:
    parsed = _load_configured()
    if parsed.issues:
        error("invalid configuration", issues=tuple(str(issue) for issue in parsed.issues))
        showWarning(configuration_error_text(parsed.issues), parent=mw)
        return
    policies = tuple(policy for policy in parsed.config.policies if policy.mode == "automatic")
    if not policies:
        debug("automatic run skipped", reason="no automatic policies")
        return

    today = int(mw.col.sched.today)
    profile = mw.pm.profile
    last_day = profile.get(LAST_AUTOMATIC_DAY_PROFILE_KEY) if profile else None
    if not automatic_run_is_due(
        today=today,
        last_automatic_day=last_day,
    ):
        debug(
            "automatic run skipped",
            reason="automatic cleanup already ran today",
            trigger=trigger,
            today=today,
            last_automatic_day=last_day,
        )
        return

    debug(
        "automatic run started",
        policy_count=len(policies),
        trigger=trigger,
    )

    def on_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        errors = [f"{report.policy.name}: {item}" for report in reports for item in report.errors]
        if errors:
            error("automatic run evaluation failed", errors=tuple(errors))
            tooltip(
                "Card Janitor: an automatic policy has errors; open Card Janitor to repair it.",
                parent=mw,
            )
            return
        plan = build_execution_plan(reports)
        if plan.is_empty:
            _mark_daily_run(today)
            if plan.conflicted_card_ids:
                tooltip(
                    f"Card Janitor: {len(plan.conflicted_card_ids)} conflicting cards "
                    "were skipped.",
                    parent=mw,
                )
            return

        approved = {
            report.policy.id: {card.card_id for card in report.actionable} for report in reports
        }

        def execute_fresh(col: Collection) -> ExecutionResult:
            return execute_approved_reports(
                col,
                reports,
                approved,
                "Card Janitor: Automatic Run",
            )

        def on_applied(result: ExecutionResult) -> None:
            _mark_daily_run(today)
            debug(
                "automatic run complete",
                affected_cards=result.affected_cards,
                conflicts=result.conflicts,
            )
            message = _automatic_completion_message(
                notify=parsed.config.notify_after_automatic_run,
                affected_cards=result.affected_cards,
                conflicts=result.conflicts,
            )
            if message:
                tooltip(message, parent=mw)

        CollectionOp(parent=mw, op=execute_fresh).success(on_applied).run_in_background()

    QueryOp(
        parent=mw,
        op=lambda col: evaluate_policies(col, policies),
        success=on_evaluated,
    ).run_in_background()
