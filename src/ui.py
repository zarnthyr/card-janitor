# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import aqt
from anki.collection import SearchNode
from aqt import mw
from aqt.addons import ConfigEditor
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import QAction, QDialog, QInputDialog, QMenu, qconnect
from aqt.utils import askUser, showInfo, showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .configuration import ADDON_MODULE, load_config, load_raw_config
from .evaluator import evaluate_policies, evaluate_policy
from .log import configure as configure_logging
from .log import debug, error, exception
from .models import DeleteCardAction, Policy

if TYPE_CHECKING:
    from anki.collection import Collection
    from aqt.browser import Browser

    from .engine import PolicyReport
    from .models import ParsedConfig

MENU_ATTR = "_card_retirement_menu"
CONFIG_EDITOR_ATTR = "_card_retirement_config_editor"
BROWSER_ACTION_ATTR = "_card_retirement_action"
BROWSER_PREVIEW_ATTR = "_card_retirement_preview"


@dataclass(frozen=True)
class BrowserRetirementPreview:
    report: PolicyReport


def _issues_text(parsed: ParsedConfig) -> str:
    details = "\n".join(f"• {issue}" for issue in parsed.issues)
    return f"Card Retirement configuration has errors:\n\n{details}\n\nNo cards were retired."


def _load_configured() -> ParsedConfig:
    parsed = load_config()
    configure_logging(debug_logging=parsed.config.debug_logging)
    return parsed


def _load_for_operation() -> ParsedConfig | None:
    parsed = _load_configured()
    if parsed.issues:
        error("invalid configuration", issues=tuple(str(issue) for issue in parsed.issues))
        showWarning(_issues_text(parsed), parent=mw)
        return None
    return parsed


def _choose_configuration(title: str) -> Policy | None:
    parsed = _load_for_operation()
    if parsed is None:
        return None
    policies = [policy for policy in parsed.config.policies if policy.enabled]
    if not policies:
        showInfo("There are no enabled retirement configurations.", parent=mw)
        return None
    if len(policies) == 1:
        return policies[0]
    labels = [policy.name for policy in policies]
    selected, accepted = QInputDialog.getItem(
        mw,
        title,
        "Retirement configuration:",
        labels,
        current=0,
        editable=False,
    )
    if not accepted:
        return None
    return policies[labels.index(selected)]


def _report_text(report: PolicyReport) -> str:
    lines = [
        f"Retirement configuration: {report.policy.name}",
        "",
        f"Cards satisfying the rule: {len(report.qualifying)}",
        f"Cards ready to retire: {len(report.actionable)}",
        f"Already retired: {len(report.qualifying) - len(report.actionable)}",
    ]
    if report.missing_first_review:
        lines.extend(
            [
                "",
                f"In-scope cards with no first-review history: {report.missing_first_review}",
            ]
        )
    return "\n".join(lines)


def _show_report_error(report: PolicyReport) -> bool:
    if not report.errors:
        return False
    details = "\n".join(f"• {item}" for item in report.errors)
    showWarning(
        f"Retirement configuration {report.policy.name!r} cannot be evaluated:\n\n{details}",
        parent=mw,
    )
    return True


def _browser_preview(browser: Browser) -> BrowserRetirementPreview | None:
    value = getattr(browser, BROWSER_PREVIEW_ATTR, None)
    return value if isinstance(value, BrowserRetirementPreview) else None


def _update_browser_action(browser: Browser) -> None:
    action = getattr(browser, BROWSER_ACTION_ATTR, None)
    if not isinstance(action, QAction):
        return
    preview = _browser_preview(browser)
    action.setEnabled(preview is not None)
    count = len(preview.report.actionable) if preview else 0
    action.setText(f"Retire {count} Eligible Cards…" if count else "Retire Eligible Cards…")


def _clear_browser_preview(browser: Browser) -> None:
    setattr(browser, BROWSER_PREVIEW_ATTR, None)
    _update_browser_action(browser)


def _open_cards_in_browser(report: PolicyReport) -> None:
    card_ids = [card.card_id for card in report.actionable]
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in card_ids))
    browser = aqt.dialogs.open("Browser", mw, search=(node,))
    setattr(browser, BROWSER_PREVIEW_ATTR, BrowserRetirementPreview(report))
    _update_browser_action(browser)
    tooltip(
        f"Card Retirement: {len(card_ids)} cards are ready. "
        "Review them, then use Cards → Retire Eligible Cards.",
        period=7000,
        parent=browser,
    )


def retire_cards() -> None:
    policy = _choose_configuration("Retire Cards")
    if policy is None:
        return

    def on_success(report: PolicyReport) -> None:
        if _show_report_error(report):
            return
        if not report.actionable:
            showInfo(
                f"No cards currently need retirement for {report.policy.name!r}.",
                parent=mw,
            )
            return
        _open_cards_in_browser(report)

    QueryOp(
        parent=mw,
        op=lambda col: evaluate_policy(col, policy),
        success=on_success,
    ).run_in_background()


def _retire_browser_preview(browser: Browser) -> None:
    preview = _browser_preview(browser)
    if preview is None:
        showInfo("Open eligible cards from Tools → Card Retirement → Retire Cards first.")
        return
    report = preview.report
    plan = build_execution_plan((report,))
    if plan.is_empty:
        showInfo("No cards currently need retirement.", parent=browser)
        _clear_browser_preview(browser)
        return

    destructive = any(isinstance(action, DeleteCardAction) for action in report.policy.actions)
    warning = "\n\nThis deletes cards and any notes left without cards." if destructive else ""
    if not askUser(
        _report_text(report)
        + warning
        + f"\n\nRetire {plan.card_count} cards using {report.policy.name!r}?",
        parent=browser,
        title="Confirm Card Retirement",
    ):
        return

    approved_ids = {card.card_id for card in report.actionable}

    def execute_fresh(col: Collection) -> ExecutionResult:
        fresh = evaluate_policy(col, report.policy)
        if fresh.errors:
            raise RuntimeError("; ".join(fresh.errors))
        fresh = replace(
            fresh,
            actionable=tuple(card for card in fresh.actionable if card.card_id in approved_ids),
        )
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
        _clear_browser_preview(browser)
        message = f"Card Retirement: {result.affected_cards} cards retired."
        if result.conflicts:
            message += f" {result.conflicts} conflicting cards were skipped."
        tooltip(message, parent=browser)

    CollectionOp(parent=browser, op=execute_fresh).success(on_success).run_in_background()


def install_browser_menu(browser: Browser) -> None:
    if isinstance(getattr(browser, BROWSER_ACTION_ATTR, None), QAction):
        _update_browser_action(browser)
        return
    action = QAction("Retire Eligible Cards…", browser)
    action.setEnabled(False)
    qconnect(action.triggered, lambda: _retire_browser_preview(browser))
    browser.form.menu_Cards.addSeparator()
    browser.form.menu_Cards.addAction(action)
    setattr(browser, BROWSER_ACTION_ATTR, action)


def add_browser_context_action(browser: Browser, menu: QMenu) -> None:
    action = getattr(browser, BROWSER_ACTION_ATTR, None)
    if isinstance(action, QAction) and action.isEnabled():
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


def _automatic_completion_message(*, notify: bool, affected_cards: int, conflicts: int) -> str:
    messages: list[str] = []
    if notify and affected_cards:
        messages.append(f"{affected_cards} cards retired automatically")
    if conflicts:
        messages.append(f"{conflicts} conflicting cards were skipped")
    return "Card Retirement: " + "; ".join(messages) + "." if messages else ""


def run_automatic_policies() -> None:
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

    debug("automatic retirement started", configuration_count=len(policies))

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
    qconnect(retire_action.triggered, retire_cards)
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
