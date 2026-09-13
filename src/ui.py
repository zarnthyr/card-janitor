# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
import time
import traceback
from dataclasses import replace
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
from .models import DeleteCardAction, Policy

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport
    from .models import ParsedConfig

MENU_ATTR = "_card_retirement_menu"
CONFIG_EDITOR_ATTR = "_card_retirement_config_editor"
LAST_CHECK_PROFILE_KEY = "card_retirement_last_automatic_check"


def _issues_text(parsed: ParsedConfig) -> str:
    details = "\n".join(f"• {issue}" for issue in parsed.issues)
    return f"Card Retirement configuration has errors:\n\n{details}\n\nNo policy was run."


def _load_for_operation() -> ParsedConfig | None:
    parsed = load_config()
    if parsed.issues:
        showWarning(_issues_text(parsed), parent=mw)
        return None
    return parsed


def _choose_policy(title: str) -> Policy | None:
    parsed = _load_for_operation()
    if parsed is None:
        return None
    policies = [policy for policy in parsed.config.policies if policy.enabled]
    if not policies:
        showInfo("There are no enabled retirement policies.", parent=mw)
        return None
    if len(policies) == 1:
        return policies[0]
    labels = [f"{policy.name} ({policy.id})" for policy in policies]
    selected, accepted = QInputDialog.getItem(
        mw, title, "Policy:", labels, current=0, editable=False
    )
    if not accepted:
        return None
    return policies[labels.index(selected)]


def _report_text(report: PolicyReport) -> str:
    lines = [
        f"Policy: {report.policy.name}",
        "",
        f"Cards satisfying the rule: {len(report.qualifying)}",
        f"Cards requiring an action: {len(report.actionable)}",
        f"Already fully acted upon: {len(report.qualifying) - len(report.actionable)}",
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
    details = "\n".join(f"• {error}" for error in report.errors)
    showWarning(f"Policy {report.policy.name!r} cannot be evaluated:\n\n{details}", parent=mw)
    return True


def _open_cards_in_browser(report: PolicyReport) -> None:
    card_ids = [card.card_id for card in report.actionable]
    if not card_ids:
        return
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in card_ids))
    aqt.dialogs.open("Browser", mw, search=(node,))


def preview_policy() -> None:
    policy = _choose_policy("Preview Retirement Policy")
    if policy is None:
        return

    def on_success(report: PolicyReport) -> None:
        if _show_report_error(report):
            return
        text = _report_text(report)
        if report.actionable and askUser(
            text + "\n\nOpen the cards requiring action in Browse?", parent=mw
        ):
            _open_cards_in_browser(report)
        elif not report.actionable:
            showInfo(text, parent=mw)

    QueryOp(parent=mw, op=lambda col: evaluate_policy(col, policy)).success(
        on_success
    ).run_in_background()


def _execute_single_report(report: PolicyReport) -> None:
    plan = build_execution_plan((report,))
    if plan.is_empty:
        showInfo("No cards currently require an action.", parent=mw)
        return
    destructive = any(isinstance(action, DeleteCardAction) for action in report.policy.actions)
    warning = "\n\nThis deletes cards and any notes left without cards." if destructive else ""
    if not askUser(
        _report_text(report)
        + warning
        + f"\n\nRun {report.policy.name!r} on {plan.card_count} cards?",
        parent=mw,
        title="Confirm Card Retirement",
    ):
        return

    def on_success(result: ExecutionResult) -> None:
        message = f"Card Retirement acted on {result.affected_cards} cards."
        if result.conflicts:
            message += f" {result.conflicts} conflicting cards were skipped."
        tooltip(message, parent=mw)

    approved_ids = {card.card_id for card in report.actionable}

    def execute_fresh(col: Collection) -> ExecutionResult:
        fresh = evaluate_policy(col, report.policy)
        if fresh.errors:
            raise RuntimeError("; ".join(fresh.errors))
        fresh = replace(
            fresh,
            actionable=tuple(card for card in fresh.actionable if card.card_id in approved_ids),
        )
        fresh_plan = build_execution_plan((fresh,))
        return execute_plan(col, fresh_plan, f"Card Retirement: {report.policy.name}")

    CollectionOp(parent=mw, op=execute_fresh).success(on_success).run_in_background()


def run_policy() -> None:
    policy = _choose_policy("Run Retirement Policy")
    if policy is None:
        return

    def on_success(report: PolicyReport) -> None:
        if not _show_report_error(report):
            _execute_single_report(report)

    QueryOp(parent=mw, op=lambda col: evaluate_policy(col, policy)).success(
        on_success
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


def _automatic_summary(
    reports: tuple[PolicyReport, ...], applied: int = 0, conflicts: int = 0
) -> str:
    notified = sum(len(report.actionable) for report in reports if report.policy.mode == "notify")
    pieces: list[str] = []
    if applied:
        pieces.append(f"acted on {applied} cards")
    if notified:
        pieces.append(f"found {notified} cards awaiting manual action")
    if conflicts:
        pieces.append(f"skipped {conflicts} conflicts")
    return "Card Retirement: " + "; ".join(pieces) + "." if pieces else ""


def run_automatic_policies(*, ignore_interval: bool = False) -> None:
    parsed = load_config()
    if parsed.issues:
        tooltip("Card Retirement: configuration errors; automatic check skipped.", parent=mw)
        return
    policies = tuple(
        policy for policy in parsed.config.policies if policy.enabled and policy.mode != "manual"
    )
    if not policies:
        if ignore_interval:
            showInfo("There are no enabled automatic retirement policies.", parent=mw)
        return

    now = time.time()
    profile = mw.pm.profile
    last_check = profile.get(LAST_CHECK_PROFILE_KEY, 0) if profile else 0
    minimum_seconds = parsed.config.automatic_check_interval_hours * 3600
    if (
        not ignore_interval
        and isinstance(last_check, (int, float))
        and now - last_check < minimum_seconds
    ):
        return

    def on_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        if profile is not None:
            profile[LAST_CHECK_PROFILE_KEY] = now
        errors = [f"{report.policy.name}: {error}" for report in reports for error in report.errors]
        if errors:
            tooltip(
                "Card Retirement: a policy has runtime errors; see Tools → Preview Policy.",
                parent=mw,
            )
            return
        apply_reports = tuple(report for report in reports if report.policy.mode == "automatic")
        plan = build_execution_plan(apply_reports)
        if plan.is_empty:
            summary = _automatic_summary(reports, conflicts=len(plan.conflicted_card_ids))
            if summary:
                tooltip(summary, parent=mw)
            elif ignore_interval:
                showInfo("No automatic retirement policy requires action.", parent=mw)
            return

        def on_applied(result: ExecutionResult) -> None:
            summary = _automatic_summary(
                reports,
                applied=result.affected_cards,
                conflicts=result.conflicts,
            )
            if summary:
                tooltip(summary, parent=mw)

        approved = {
            report.policy.id: {card.card_id for card in report.actionable}
            for report in apply_reports
        }

        def execute_fresh(col: Collection) -> ExecutionResult:
            fresh_reports = evaluate_policies(col, tuple(report.policy for report in apply_reports))
            runtime_errors = [error for item in fresh_reports for error in item.errors]
            if runtime_errors:
                raise RuntimeError("; ".join(runtime_errors))
            filtered = tuple(
                replace(
                    item,
                    actionable=tuple(
                        card for card in item.actionable if card.card_id in approved[item.policy.id]
                    ),
                )
                for item in fresh_reports
            )
            return execute_plan(
                col,
                build_execution_plan(filtered),
                "Automatic Card Retirement",
            )

        CollectionOp(parent=mw, op=execute_fresh).success(on_applied).run_in_background()

    QueryOp(parent=mw, op=lambda col: evaluate_policies(col, policies)).success(
        on_evaluated
    ).run_in_background()


def install_menu() -> None:
    existing = getattr(mw, MENU_ATTR, None)
    if existing is not None:
        with contextlib.suppress(RuntimeError):
            mw.form.menuTools.removeAction(existing.menuAction())

    menu = QMenu("Card Retirement", mw)
    preview_action = QAction("Preview Policy…", mw)
    run_action = QAction("Run Policy…", mw)
    auto_action = QAction("Check Automatic Policies Now", mw)
    settings_action = QAction("Settings…", mw)
    qconnect(preview_action.triggered, preview_policy)
    qconnect(run_action.triggered, run_policy)
    qconnect(auto_action.triggered, lambda: run_automatic_policies(ignore_interval=True))
    qconnect(settings_action.triggered, open_settings)
    menu.addAction(preview_action)
    menu.addAction(run_action)
    menu.addSeparator()
    menu.addAction(auto_action)
    menu.addSeparator()
    menu.addAction(settings_action)
    mw.form.menuTools.addMenu(menu)
    setattr(mw, MENU_ATTR, menu)


def safe_install_menu() -> None:
    try:
        install_menu()
    except Exception:
        traceback.print_exc()
