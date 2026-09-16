# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class _RunState:
    token: object | None = None


class CancelledAutomaticRunError(RuntimeError):
    """The collection or profile changed before cleanup could execute."""


_run_state = _RunState()


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


def cancel_automatic_run() -> None:
    _run_state.token = None


def _automatic_completion_message(*, notify: bool, affected_cards: int, conflicts: int) -> str:
    messages: list[str] = []
    if notify and affected_cards:
        messages.append(applied_message(affected_cards))
    if conflicts:
        messages.append(f"{conflicts} conflicting cards were skipped")
    if len(messages) <= 1:
        return "".join(messages)
    return ". ".join(messages) + "."


def run_automatic_policies(*, trigger: AutomaticTrigger = "profile_open") -> None:
    if mw is None or mw.col is None or mw.pm.profile is None or _run_state.token is not None:
        return
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
    collection = mw.col
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

    token = object()
    _run_state.token = token

    def is_current() -> bool:
        return _run_state.token is token and mw.col is collection and mw.pm.profile is profile

    def finish(*, complete: bool = False) -> None:
        current = is_current()
        if complete and current:
            profile[LAST_AUTOMATIC_DAY_PROFILE_KEY] = today
        if _run_state.token is token:
            _run_state.token = None
        if complete and current and int(collection.sched.today) != today:
            run_automatic_policies(trigger="day_change")

    def on_failure(exc: Exception) -> None:
        current = is_current()
        finish()
        if current and not isinstance(exc, CancelledAutomaticRunError):
            error("automatic cleanup failed", reason=str(exc))
            showWarning(str(exc), parent=mw)

    def evaluate_current(col: Collection) -> tuple[PolicyReport, ...]:
        if col is not collection or not is_current():
            raise CancelledAutomaticRunError
        return evaluate_policies(col, policies)

    debug(
        "automatic run started",
        policy_count=len(policies),
        trigger=trigger,
    )

    def apply_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        if not is_current():
            finish()
            return
        errors = [f"{report.policy.name}: {item}" for report in reports for item in report.errors]
        if errors:
            finish()
            error("automatic run evaluation failed", errors=tuple(errors))
            tooltip(
                "Card Janitor: an automatic policy has errors; open Card Janitor to repair it",
                parent=mw,
            )
            return
        plan = build_execution_plan(reports, collection)
        if plan.is_empty:
            finish(complete=True)
            if plan.conflicted_card_ids:
                tooltip(
                    f"Card Janitor: {len(plan.conflicted_card_ids)} conflicting cards were skipped",
                    parent=mw,
                )
            return

        approved = {
            report.policy.id: {card.card_id for card in report.actionable} for report in reports
        }

        def execute_fresh(col: Collection) -> ExecutionResult:
            if col is not collection or not is_current():
                raise CancelledAutomaticRunError
            return execute_approved_reports(
                col,
                reports,
                approved,
                "Card Janitor: Automatic Run",
            )

        def on_applied(result: ExecutionResult) -> None:
            if not is_current():
                finish()
                return
            finish(complete=True)
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

        CollectionOp(parent=mw, op=execute_fresh).success(on_applied).failure(
            on_failure
        ).run_in_background()

    def on_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        try:
            apply_evaluated(reports)
        except Exception as exc:
            on_failure(exc)

    try:
        QueryOp(parent=mw, op=evaluate_current, success=on_evaluated).failure(
            on_failure
        ).run_in_background()
    except Exception:
        finish()
        raise
