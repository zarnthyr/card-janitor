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
from aqt.qt import QAction, QDialog, QInputDialog, QMenu, qconnect
from aqt.utils import showInfo, showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .configuration import ADDON_MODULE, load_config, load_raw_config
from .evaluator import evaluate_policies, evaluate_policy, evaluate_selected_cards
from .log import configure as configure_logging
from .log import debug, error, exception

if TYPE_CHECKING:
    from anki.collection import Collection
    from aqt.browser import Browser

    from .engine import PolicyReport
    from .models import AutomaticSchedule, ParsedConfig, Policy

AutomaticTrigger = Literal["profile_open", "day_change"]

MENU_ATTR = "_card_retirement_menu"
CONFIG_EDITOR_ATTR = "_card_retirement_config_editor"
BROWSER_FIND_ACTION_ATTR = "_card_retirement_find_action"
BROWSER_RETIRE_ACTION_ATTR = "_card_retirement_retire_action"
BROWSER_CONFIGURATION_ATTR = "_card_retirement_configuration_id"
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


def _choose_configuration(title: str, parent: object = mw) -> Policy | None:
    parsed = _load_for_operation(parent)
    if parsed is None:
        return None
    policies = [policy for policy in parsed.config.policies if policy.enabled]
    if not policies:
        showInfo("There are no enabled retirement configurations.", parent=parent)
        return None
    if len(policies) == 1:
        return policies[0]
    labels = [policy.name for policy in policies]
    selected, accepted = QInputDialog.getItem(
        parent,
        title,
        "Retirement configuration:",
        labels,
        current=0,
        editable=False,
    )
    if not accepted:
        return None
    return policies[labels.index(selected)]


def _configuration_for_browser(browser: Browser) -> Policy | None:
    configuration_id = getattr(browser, BROWSER_CONFIGURATION_ATTR, None)
    if isinstance(configuration_id, str):
        parsed = _load_for_operation(browser)
        if parsed is None:
            return None
        for policy in parsed.config.policies:
            if policy.enabled and policy.id == configuration_id:
                return policy
    return _choose_configuration("Retire Selected Cards", browser)


def _show_report_error(report: PolicyReport, parent: object = mw) -> bool:
    if not report.errors:
        return False
    details = "\n".join(f"• {item}" for item in report.errors)
    showWarning(
        f"Retirement configuration {report.policy.name!r} cannot be evaluated:\n\n{details}",
        parent=parent,
    )
    return True


def _card_count_text(count: int) -> str:
    return "1 card" if count == 1 else f"{count} cards"


def _retired_message(count: int) -> str:
    return f"Retired {_card_count_text(count)}."


def _open_and_select_cards(report: PolicyReport) -> None:
    card_ids = [card.card_id for card in report.actionable]
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in card_ids))
    browser = aqt.dialogs.open("Browser", mw, search=(node,))
    setattr(browser, BROWSER_CONFIGURATION_ATTR, report.policy.id)
    browser.table.select_all()
    browser.onCardList()


def find_cards_to_retire(browser: Browser | None = None) -> None:
    parent = browser or mw
    policy = _choose_configuration("Find Cards to Retire", parent)
    if policy is None:
        return

    def on_success(report: PolicyReport) -> None:
        if _show_report_error(report, parent):
            return
        if not report.actionable:
            showInfo("No cards were eligible for retirement.", parent=parent)
            return
        _open_and_select_cards(report)

    QueryOp(
        parent=parent,
        op=lambda col: evaluate_policy(col, policy),
        success=on_success,
    ).run_in_background()


def _execute_selected_report(browser: Browser, report: PolicyReport) -> None:
    selected_ids = {card.card_id for card in report.actionable}

    def execute_fresh(col: Collection) -> ExecutionResult:
        fresh = evaluate_selected_cards(col, report.policy, selected_ids)
        if fresh.errors:
            raise RuntimeError("; ".join(fresh.errors))
        return execute_plan(
            col,
            build_execution_plan((fresh,)),
            f"Card Retirement: {report.policy.name}",
        )

    def on_success(result: ExecutionResult) -> None:
        debug(
            "manual retirement complete",
            configuration_id=report.policy.id,
            affected_cards=result.affected_cards,
            conflicts=result.conflicts,
        )
        browser.onCardList()
        message = _retired_message(result.affected_cards)
        if result.conflicts:
            message += f" {result.conflicts} conflicting cards were skipped."
        tooltip(message, parent=browser)

    CollectionOp(parent=browser, op=execute_fresh).success(on_success).run_in_background()


def retire_selected_cards(browser: Browser) -> None:
    card_ids = {int(card_id) for card_id in browser.selected_cards()}
    if not card_ids:
        showInfo("No cards are selected.", parent=browser)
        return
    policy = _configuration_for_browser(browser)
    if policy is None:
        return

    def on_success(report: PolicyReport) -> None:
        if _show_report_error(report, browser):
            return
        if not report.actionable:
            showInfo("The selected cards are already retired.", parent=browser)
            return
        _execute_selected_report(browser, report)

    QueryOp(
        parent=browser,
        op=lambda col: evaluate_selected_cards(col, policy, card_ids),
        success=on_success,
    ).run_in_background()


def install_browser_menu(browser: Browser) -> None:
    if isinstance(getattr(browser, BROWSER_FIND_ACTION_ATTR, None), QAction):
        return
    find_action = QAction("Find Cards to Retire…", browser)
    retire_action = QAction("Retire Selected Cards…", browser)
    qconnect(find_action.triggered, lambda: find_cards_to_retire(browser))
    qconnect(retire_action.triggered, lambda: retire_selected_cards(browser))
    browser.form.menu_Cards.addSeparator()
    browser.form.menu_Cards.addAction(find_action)
    browser.form.menu_Cards.addAction(retire_action)
    setattr(browser, BROWSER_FIND_ACTION_ATTR, find_action)
    setattr(browser, BROWSER_RETIRE_ACTION_ATTR, retire_action)


def add_browser_context_action(browser: Browser, menu: QMenu) -> None:
    action = getattr(browser, BROWSER_RETIRE_ACTION_ATTR, None)
    if isinstance(action, QAction):
        menu.addSeparator()
        menu.addAction(action)


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
        debug("automatic retirement skipped", reason="no enabled automatic configurations")
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
        configuration_count=len(policies),
        schedule=parsed.config.automatic_schedule,
        trigger=trigger,
    )

    def on_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        errors = [f"{report.policy.name}: {item}" for report in reports for item in report.errors]
        if errors:
            error("automatic retirement evaluation failed", errors=tuple(errors))
            tooltip(
                "Card Retirement: an automatic configuration has errors; see Settings.",
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
    find_action = QAction("Find Cards to Retire…", mw)
    settings_action = QAction("Settings…", mw)
    qconnect(find_action.triggered, find_cards_to_retire)
    qconnect(settings_action.triggered, open_settings)
    menu.addAction(find_action)
    menu.addSeparator()
    menu.addAction(settings_action)
    mw.form.menuTools.addMenu(menu)
    setattr(mw, MENU_ATTR, menu)


def safe_install_menu() -> None:
    try:
        install_menu()
    except Exception:
        exception("failed to install Tools menu")
