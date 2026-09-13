# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import aqt
from anki.collection import SearchNode
from aqt import mw
from aqt.addons import ConfigEditor
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import showInfo, showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .configuration import ADDON_MODULE, load_config, load_raw_config
from .evaluator import evaluate_policies
from .log import configure as configure_logging
from .log import debug, error, exception
from .models import DeleteCardAction, MoveAction, SuspendAction, TagAction

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport
    from .models import Action, AutomaticSchedule, ParsedConfig

AutomaticTrigger = Literal["profile_open", "day_change"]

MENU_ATTR = "_card_retirement_menu"
CONFIG_EDITOR_ATTR = "_card_retirement_config_editor"
LAST_AUTOMATIC_DAY_PROFILE_KEY = "card_retirement_last_automatic_day"


def _issues_text(parsed: ParsedConfig) -> str:
    details = "\n".join(f"• {issue}" for issue in parsed.issues)
    return f"Card Retirement configuration has errors:\n\n{details}\n\nNo cards were retired."


def _load_configured() -> ParsedConfig:
    parsed = load_config()
    configure_logging(debug_logging=parsed.config.debug_logging)
    return parsed


def _load_for_operation(parent: object = mw) -> ParsedConfig | None:
    parsed = _load_configured()
    if parsed.issues:
        error("invalid configuration", issues=tuple(str(issue) for issue in parsed.issues))
        showWarning(_issues_text(parsed), parent=parent)
        return None
    return parsed


def _describe_action(action: Action) -> str:
    if isinstance(action, TagAction):
        return f"Add the tag {action.tag!r} to notes"
    if isinstance(action, SuspendAction):
        return "Suspend cards"
    if isinstance(action, MoveAction):
        return f"Move cards to the {action.deck!r} deck"
    if isinstance(action, DeleteCardAction):
        return "Delete cards and any notes left without cards"
    message = f"unknown retirement action: {action!r}"
    raise AssertionError(message)


class ManualRetirementDialog(QDialog):
    def __init__(
        self,
        reports: tuple[PolicyReport, ...],
        affected_cards: int,
        conflicts: int,
    ) -> None:
        super().__init__(mw)
        self.choice: Literal["browse", "retire"] | None = None
        self.setWindowTitle("Retire Cards")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{_card_count_text(affected_cards).capitalize()} would be retired.",
            self,
        )
        layout.addWidget(summary)

        details: list[str] = []
        for report in reports:
            details.append(f"{report.policy.name}: {_card_count_text(len(report.actionable))}")
            details.extend(f"  • {_describe_action(action)}" for action in report.policy.actions)
        if conflicts:
            conflict_subject = _card_count_text(conflicts).capitalize()
            conflict_verb = "has" if conflicts == 1 else "have"
            conflict_message = (
                f"{conflict_subject} {conflict_verb} conflicting actions and would be skipped."
            )
            details.extend(
                (
                    "",
                    conflict_message,
                )
            )
        detail_label = QLabel("\n".join(details), self)
        detail_label.setWordWrap(True)
        layout.addWidget(detail_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        browse_button = buttons.addButton(
            "View in Browser",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        retire_button = buttons.addButton("Retire", QDialogButtonBox.ButtonRole.AcceptRole)
        browse_button.setEnabled(any(report.actionable for report in reports))
        retire_button.setEnabled(affected_cards > 0)
        if isinstance(retire_button, QPushButton):
            retire_button.setDefault(True)
        qconnect(browse_button.clicked, self._browse)
        qconnect(retire_button.clicked, self._retire)
        qconnect(buttons.rejected, self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        self.choice = "browse"
        self.accept()

    def _retire(self) -> None:
        self.choice = "retire"
        self.accept()


def _card_count_text(count: int) -> str:
    return "1 card" if count == 1 else f"{count} cards"


def _retired_message(count: int) -> str:
    return f"Retired {_card_count_text(count)}."


def _open_cards_in_browser(card_ids: set[int]) -> None:
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in sorted(card_ids)))
    aqt.dialogs.open("Browser", mw, search=(node,))


def retire_cards_manually() -> None:
    parsed = _load_for_operation()
    if parsed is None:
        return
    policies = tuple(
        policy for policy in parsed.config.policies if policy.enabled and policy.mode == "manual"
    )
    if not policies:
        showInfo("There are no enabled manual retirement policies.", parent=mw)
        return

    def on_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        errors = [f"{report.policy.name}: {item}" for report in reports for item in report.errors]
        if errors:
            showWarning(
                "Manual retirement could not be evaluated:\n\n"
                + "\n".join(f"• {item}" for item in errors),
                parent=mw,
            )
            return
        plan = build_execution_plan(reports)
        if plan.is_empty and not plan.conflicted_card_ids:
            showInfo("No cards were eligible for retirement.", parent=mw)
            return
        dialog = ManualRetirementDialog(
            reports,
            plan.card_count,
            len(plan.conflicted_card_ids),
        )
        dialog.exec()
        if dialog.choice == "browse":
            candidate_ids = {card.card_id for report in reports for card in report.actionable}
            _open_cards_in_browser(candidate_ids)
            return
        if dialog.choice != "retire":
            return

        approved = {
            report.policy.id: {card.card_id for card in report.actionable} for report in reports
        }

        def execute_fresh(col: Collection) -> ExecutionResult:
            fresh_reports = evaluate_policies(col, policies)
            runtime_errors = [item for report in fresh_reports for item in report.errors]
            if runtime_errors:
                raise RuntimeError("; ".join(runtime_errors))
            filtered = tuple(
                replace(
                    report,
                    actionable=tuple(
                        card
                        for card in report.actionable
                        if card.card_id in approved[report.policy.id]
                    ),
                )
                for report in fresh_reports
            )
            return execute_plan(col, build_execution_plan(filtered), "Manual Card Retirement")

        def on_applied(result: ExecutionResult) -> None:
            debug(
                "manual retirement complete",
                affected_cards=result.affected_cards,
                conflicts=result.conflicts,
            )
            message = (
                _retired_message(result.affected_cards)
                if result.affected_cards
                else "No cards were eligible for retirement."
            )
            if result.conflicts:
                message += f" {result.conflicts} conflicting cards were skipped."
            tooltip(message, parent=mw)

        CollectionOp(parent=mw, op=execute_fresh).success(on_applied).run_in_background()

    QueryOp(
        parent=mw,
        op=lambda col: evaluate_policies(col, policies),
        success=on_evaluated,
    ).run_in_background()


def open_settings() -> None:
    config = load_raw_config()
    if not isinstance(config, dict):
        showWarning("The add-on configuration is not a JSON object.", parent=mw)
        return
    parent = QDialog(mw)
    parent.mgr = mw.addonManager
    editor = ConfigEditor(parent, ADDON_MODULE, config)
    setattr(mw, CONFIG_EDITOR_ATTR, (parent, editor))


def automatic_run_is_due(
    schedule: AutomaticSchedule,
    trigger: AutomaticTrigger,
    *,
    today: int,
    last_automatic_day: object,
) -> bool:
    if schedule == "profile_open":
        return trigger == "profile_open"
    if schedule == "profile_open_and_daily":
        return True
    return last_automatic_day != today


def _mark_daily_run(schedule: AutomaticSchedule, today: int) -> None:
    if schedule != "daily" or mw.pm.profile is None:
        return
    mw.pm.profile[LAST_AUTOMATIC_DAY_PROFILE_KEY] = today


def _automatic_completion_message(*, notify: bool, affected_cards: int, conflicts: int) -> str:
    messages: list[str] = []
    if notify and affected_cards:
        messages.append(_retired_message(affected_cards).removesuffix("."))
    if conflicts:
        messages.append(f"{conflicts} conflicting cards were skipped")
    return " ".join(f"{message}." for message in messages)


def run_automatic_policies(*, trigger: AutomaticTrigger = "profile_open") -> None:
    parsed = _load_configured()
    if parsed.issues:
        error("invalid configuration", issues=tuple(str(issue) for issue in parsed.issues))
        showWarning(_issues_text(parsed), parent=mw)
        return
    policies = tuple(
        policy for policy in parsed.config.policies if policy.enabled and policy.mode == "automatic"
    )
    if not policies:
        debug("automatic retirement skipped", reason="no enabled automatic policies")
        return

    today = int(mw.col.sched.today)
    profile = mw.pm.profile
    last_day = profile.get(LAST_AUTOMATIC_DAY_PROFILE_KEY) if profile else None
    if not automatic_run_is_due(
        parsed.config.automatic_schedule,
        trigger,
        today=today,
        last_automatic_day=last_day,
    ):
        debug(
            "automatic retirement skipped",
            reason="schedule is not due",
            schedule=parsed.config.automatic_schedule,
            trigger=trigger,
            today=today,
            last_automatic_day=last_day,
        )
        return

    debug(
        "automatic retirement started",
        policy_count=len(policies),
        schedule=parsed.config.automatic_schedule,
        trigger=trigger,
    )

    def on_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        errors = [f"{report.policy.name}: {item}" for report in reports for item in report.errors]
        if errors:
            error("automatic retirement evaluation failed", errors=tuple(errors))
            tooltip(
                "Card Retirement: an automatic policy has errors; see Settings.",
                parent=mw,
            )
            return
        plan = build_execution_plan(reports)
        if plan.is_empty:
            _mark_daily_run(parsed.config.automatic_schedule, today)
            if plan.conflicted_card_ids:
                tooltip(
                    f"Card Retirement: {len(plan.conflicted_card_ids)} conflicting cards "
                    "could not be retired.",
                    parent=mw,
                )
            return

        approved = {
            report.policy.id: {card.card_id for card in report.actionable} for report in reports
        }

        def execute_fresh(col: Collection) -> ExecutionResult:
            fresh_reports = evaluate_policies(col, tuple(report.policy for report in reports))
            runtime_errors = [item for report in fresh_reports for item in report.errors]
            if runtime_errors:
                raise RuntimeError("; ".join(runtime_errors))
            filtered = tuple(
                replace(
                    report,
                    actionable=tuple(
                        card
                        for card in report.actionable
                        if card.card_id in approved[report.policy.id]
                    ),
                )
                for report in fresh_reports
            )
            return execute_plan(col, build_execution_plan(filtered), "Automatic Card Retirement")

        def on_applied(result: ExecutionResult) -> None:
            _mark_daily_run(parsed.config.automatic_schedule, today)
            debug(
                "automatic retirement complete",
                affected_cards=result.affected_cards,
                conflicts=result.conflicts,
            )
            message = _automatic_completion_message(
                notify=parsed.config.notify_after_automatic_retirement,
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


def install_menu() -> None:
    existing = getattr(mw, MENU_ATTR, None)
    if existing is not None:
        with contextlib.suppress(RuntimeError):
            mw.form.menuTools.removeAction(existing.menuAction())

    menu = QMenu("Card Retirement", mw)
    retire_action = QAction("Retire Cards…", mw)
    settings_action = QAction("Settings…", mw)
    qconnect(retire_action.triggered, retire_cards_manually)
    qconnect(settings_action.triggered, open_settings)
    menu.addAction(retire_action)
    menu.addSeparator()
    menu.addAction(settings_action)
    mw.form.menuTools.addMenu(menu)
    setattr(mw, MENU_ATTR, menu)


def safe_install_menu() -> None:
    try:
        install_menu()
    except Exception:
        exception("failed to install Tools menu")
