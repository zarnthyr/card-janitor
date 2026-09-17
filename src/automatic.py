# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from aqt import mw
from aqt import utils as aqt_utils
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import QTimer
from aqt.utils import showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan
from .configuration import load_config
from .evaluator import evaluate_policies
from .execution import execute_approved_reports
from .log import configure as configure_logging
from .log import debug, error
from .presentation import TRIGGER_LABELS, applied_message, configuration_error_text, record_cleanup

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport
    from .models import ParsedConfig

AutomaticTrigger = Literal["on_open", "day_change", "on_sync"]
LAST_AUTOMATIC_DAYS_PROFILE_KEY = "card_janitor_automatic_days"


@dataclass
class _RunState:
    token: object | None = None
    pending: set[AutomaticTrigger] = field(default_factory=set)
    notification_generation: int = 0


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
    _run_state.pending.clear()
    _run_state.notification_generation += 1


def _notify_automatic(message: str) -> None:
    profile = mw.pm.profile
    collection = mw.col
    generation = _run_state.notification_generation

    def show_when_available() -> None:
        if (
            mw.pm.profile is not profile
            or mw.col is not collection
            or _run_state.notification_generation != generation
        ):
            return
        # Anki has no public tooltip queue. Only inspect its current label;
        # never replace or modify Anki's tooltip/timer implementation.
        label = getattr(aqt_utils, "_tooltipLabel", None)
        try:
            visible = label is not None and label.isVisible()
        except RuntimeError:
            visible = False  # Its parent may already have deleted the label.
        if visible:
            QTimer.singleShot(200, show_when_available)
        else:
            tooltip(message, parent=mw)

    show_when_available()


def _automatic_completion_message(*, notify: bool, affected_cards: int, conflicts: int) -> str:
    messages: list[str] = []
    if notify and affected_cards:
        messages.append(applied_message(affected_cards))
    if conflicts:
        messages.append(f"{conflicts} conflicting cards were skipped")
    if len(messages) <= 1:
        return "".join(messages)
    return ". ".join(messages) + "."


def run_automatic_policies(
    *,
    trigger: AutomaticTrigger = "on_open",
    events: frozenset[AutomaticTrigger] | None = None,
) -> None:
    if mw is None or mw.col is None or mw.pm.profile is None:
        return
    events = events if events is not None else frozenset({trigger})
    if _run_state.token is not None:
        _run_state.pending.update(events)
        return
    parsed = _load_configured()
    if not parsed.config.automatic_cleanup_enabled:
        debug("automatic cleanup skipped", reason="disabled in settings")
        return
    if parsed.issues:
        error("invalid configuration", issues=tuple(str(issue) for issue in parsed.issues))
        record_cleanup(mw.pm.profile, failure=configuration_error_text(parsed.issues))
        showWarning(configuration_error_text(parsed.issues), parent=mw)
        return
    today = int(mw.col.sched.today)
    profile = mw.pm.profile
    collection = mw.col
    last_days = profile.get(LAST_AUTOMATIC_DAYS_PROFILE_KEY, {})
    if not isinstance(last_days, dict):
        last_days = {}
    policies = tuple(
        policy
        for policy in parsed.config.policies
        if any(
            (item.type in events)
            or (
                item.type == "daily"
                and bool(events & {"on_open", "day_change"})
                and automatic_run_is_due(today=today, last_automatic_day=last_days.get(policy.id))
            )
            for item in policy.triggers
        )
    )
    if not policies:
        debug("automatic run skipped", reason="no eligible policies", events=sorted(events))
        return

    policy_names = tuple(policy.name for policy in policies)
    policy_ids = tuple(policy.id for policy in policies)
    matched_triggers = {
        item.type
        for policy in policies
        for item in policy.triggers
        if item.type in events
        or (
            item.type == "daily"
            and bool(events & {"on_open", "day_change"})
            and automatic_run_is_due(today=today, last_automatic_day=last_days.get(policy.id))
        )
    }
    trigger_names = tuple(
        label for kind, label in TRIGGER_LABELS.items() if kind in matched_triggers
    )

    def record_result(
        *, affected_cards: int | None = 0, conflicts: int = 0, failure: str = ""
    ) -> None:
        record_cleanup(
            profile,
            policies=policy_names,
            policy_ids=policy_ids,
            triggers=trigger_names,
            affected_cards=affected_cards,
            conflicts=conflicts,
            failure=failure,
        )

    token = object()
    _run_state.token = token

    def is_current() -> bool:
        return _run_state.token is token and mw.col is collection and mw.pm.profile is profile

    def finish(*, complete: bool = False) -> None:
        current = is_current()
        if complete and current:
            stored_days = profile.get(LAST_AUTOMATIC_DAYS_PROFILE_KEY, {})
            days = dict(stored_days) if isinstance(stored_days, dict) else {}
            days.update(
                (policy.id, today)
                for policy in policies
                if any(item.type == "daily" for item in policy.triggers)
            )
            if days:
                profile[LAST_AUTOMATIC_DAYS_PROFILE_KEY] = days
        if _run_state.token is token:
            _run_state.token = None
            if complete and current and int(collection.sched.today) != today:
                _run_state.pending.add("day_change")
            pending = frozenset(_run_state.pending)
            _run_state.pending.clear()
            if complete and current and pending:
                try:
                    run_automatic_policies(events=pending)
                except Exception:
                    error("queued automatic cleanup failed to start")

    def on_failure(exc: Exception) -> None:
        current = is_current()
        try:
            if current and not isinstance(exc, CancelledAutomaticRunError):
                record_result(affected_cards=None, failure=str(exc))
                error("automatic cleanup failed", reason=str(exc))
                showWarning(str(exc), parent=mw)
        finally:
            finish()

    def evaluate_current(col: Collection) -> tuple[PolicyReport, ...]:
        if col is not collection or not is_current():
            raise CancelledAutomaticRunError
        return evaluate_policies(col, policies)

    debug(
        "automatic run started",
        policy_count=len(policies),
        events=sorted(events),
    )

    def apply_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        if not is_current():
            finish()
            return
        errors = [f"{report.policy.name}: {item}" for report in reports for item in report.errors]
        if errors:
            record_result(failure="\n".join(errors))
            finish()
            error("automatic run evaluation failed", errors=tuple(errors))
            _notify_automatic(
                "Card Janitor: an automatic policy has errors; open Card Janitor to repair it",
            )
            return
        plan = build_execution_plan(reports, collection)
        if plan.is_empty:
            record_result(conflicts=len(plan.conflicted_card_ids))
            finish(complete=True)
            if plan.conflicted_card_ids:
                _notify_automatic(
                    f"Card Janitor: {len(plan.conflicted_card_ids)} conflicting cards were skipped",
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
                "Card Janitor: Automatic Clean Up",
            )

        def on_applied(result: ExecutionResult) -> None:
            if not is_current():
                finish()
                return
            record_result(affected_cards=result.affected_cards, conflicts=result.conflicts)
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
                _notify_automatic(message)

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
    except Exception as exc:
        if is_current():
            record_result(failure=str(exc))
        finish()
        raise
