# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
from collections import Counter
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import aqt
from anki.collection import SearchNode
from aqt import mw
from aqt.addons import ConfigEditor
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import (
    QAbstractItemView,
    QAction,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QSignalBlocker,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    qconnect,
)
from aqt.utils import showInfo, showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .configuration import ADDON_MODULE, load_config, load_raw_config
from .evaluator import evaluate_policies
from .log import configure as configure_logging
from .log import debug, error, exception
from .models import (
    Action,
    AgeRule,
    AllRule,
    AnyRule,
    DeleteCardAction,
    IntervalRule,
    MoveAction,
    NewRule,
    Rule,
    Scope,
    SuspendAction,
    TagAction,
)

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport
    from .models import AutomaticSchedule, ParsedConfig

AutomaticTrigger = Literal["profile_open", "day_change"]

MENU_ATTR = "_card_janitor_action"
CONFIG_EDITOR_ATTR = "_card_janitor_config_editor"
MANUAL_DIALOG_ATTR = "_card_janitor_dialog"
LAST_AUTOMATIC_DAY_PROFILE_KEY = "card_janitor_last_automatic_day"


def _issues_text(parsed: ParsedConfig) -> str:
    details = "\n".join(f"• {issue}" for issue in parsed.issues)
    return f"Card Janitor configuration has errors:\n\n{details}\n\nNo actions were applied."


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
    message = f"unknown cleanup action: {action!r}"
    raise AssertionError(message)


def _describe_actions(actions: tuple[Action, ...]) -> str:
    return " + ".join(_describe_action(action) for action in actions)


def _describe_scope(scope: Scope) -> str:
    decks = ", ".join(scope.decks)
    return f"{decks} + subdecks" if scope.include_subdecks else decks


def _scope_tooltip(scope: Scope) -> str:
    return "\n".join(
        (
            f"Decks: {', '.join(scope.decks)}",
            f"Include subdecks: {'Yes' if scope.include_subdecks else 'No'}",
            f"Include suspended cards: {'Yes' if scope.include_suspended else 'No'}",
            f"Include filtered decks: {'Yes' if scope.include_filtered_decks else 'No'}",
        )
    )


def _describe_rule(rule: Rule, *, nested: bool = False) -> str:
    if isinstance(rule, AgeRule):
        source = "First studied" if rule.source == "first_review" else "Created"
        return f"{source} ≥ {rule.days} days ago"
    if isinstance(rule, IntervalRule):
        return f"Interval ≥ {rule.days} days"
    if isinstance(rule, NewRule):
        return "Still new"
    if isinstance(rule, (AllRule, AnyRule)):
        operator = " AND " if isinstance(rule, AllRule) else " OR "
        description = operator.join(_describe_rule(child, nested=True) for child in rule.rules)
        return f"({description})" if nested else description
    message = f"unknown cleanup rule: {rule!r}"
    raise AssertionError(message)


class CardJanitorDialog(QDialog):
    COLUMN_RUN = 0
    COLUMN_POLICY = 1
    COLUMN_STATE = 2
    COLUMN_SCOPE = 3
    COLUMN_RULE = 4
    COLUMN_ACTIONS = 5
    COLUMN_AFFECTED = 6

    def __init__(self, reports: tuple[PolicyReport, ...]) -> None:
        super().__init__(mw)
        self._reports: tuple[PolicyReport, ...] = ()
        self.setWindowTitle("Card Janitor")
        self.resize(1050, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose which policies to include in this run.", self))

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ("Run", "Policy", "State", "Scope", "Rule", "Actions", "Affected")
        )
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        for column in (self.COLUMN_RUN, self.COLUMN_STATE, self.COLUMN_AFFECTED):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (
            self.COLUMN_POLICY,
            self.COLUMN_SCOPE,
            self.COLUMN_RULE,
            self.COLUMN_ACTIONS,
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        qconnect(self.table.itemChanged, self._on_item_changed)
        qconnect(self.table.cellDoubleClicked, self._view_policy)
        layout.addWidget(self.table)

        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=self)
        self.settings_button = buttons.addButton(
            "Settings…",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.refresh_button = buttons.addButton(
            "Refresh",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.view_button = buttons.addButton(
            "View Included Cards",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.run_button = buttons.addButton(
            "Run",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        if isinstance(self.run_button, QPushButton):
            self.run_button.setDefault(True)
        qconnect(self.settings_button.clicked, open_settings)
        qconnect(self.refresh_button.clicked, self._refresh)
        qconnect(self.view_button.clicked, self._view_included)
        qconnect(self.run_button.clicked, self._run)
        qconnect(buttons.rejected, self.close)
        layout.addWidget(buttons)

        self.set_reports(reports)

    def set_reports(
        self,
        reports: tuple[PolicyReport, ...],
        checked_policy_ids: set[str] | None = None,
        known_policy_ids: set[str] | None = None,
    ) -> None:
        self._reports = reports
        signal_blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(reports))
        for row, report in enumerate(reports):
            policy = report.policy
            use_config_default = checked_policy_ids is None or (
                known_policy_ids is not None and policy.id not in known_policy_ids
            )
            included = (
                policy.state != "disabled"
                if use_config_default
                else policy.id in checked_policy_ids
            )
            run_item = QTableWidgetItem()
            run_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            run_item.setCheckState(Qt.CheckState.Checked if included else Qt.CheckState.Unchecked)
            self.table.setItem(row, self.COLUMN_RUN, run_item)

            values = (
                policy.name,
                policy.state.capitalize(),
                _describe_scope(policy.scope),
                _describe_rule(policy.rule),
                _describe_actions(policy.actions),
                "Error" if report.errors else str(len(report.actionable)),
            )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                self.table.setItem(row, column, item)
            self.table.item(row, self.COLUMN_POLICY).setToolTip(f"Policy ID: {policy.id}")
            self.table.item(row, self.COLUMN_SCOPE).setToolTip(_scope_tooltip(policy.scope))
            if report.errors:
                error_text = "\n".join(report.errors)
                self.table.item(row, self.COLUMN_AFFECTED).setToolTip(error_text)
        del signal_blocker
        self.table.resizeRowsToContents()
        if reports:
            self.table.selectRow(0)
        self._update_summary()

    def checked_policy_ids(self) -> set[str]:
        return {
            report.policy.id
            for row, report in enumerate(self._reports)
            if self.table.item(row, self.COLUMN_RUN).checkState() == Qt.CheckState.Checked
        }

    def policy_ids(self) -> set[str]:
        return {report.policy.id for report in self._reports}

    def checked_reports(self) -> tuple[PolicyReport, ...]:
        checked = self.checked_policy_ids()
        return tuple(report for report in self._reports if report.policy.id in checked)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == self.COLUMN_RUN:
            self._update_summary()

    def _update_summary(self) -> None:
        reports = self.checked_reports()
        if not reports:
            self.summary.setText("No policies are included in this run.")
            self.view_button.setEnabled(False)
            self.run_button.setEnabled(False)
            return
        errors = [error for report in reports for error in report.errors]
        match_counts = Counter(card.card_id for report in reports for card in report.actionable)
        candidate_ids = set(match_counts)
        overlap_count = sum(count > 1 for count in match_counts.values())
        plan = build_execution_plan(reports)
        messages = [f"{_card_count_text(plan.card_count).capitalize()} would be affected."]
        if overlap_count:
            overlap_verb = "matches" if overlap_count == 1 else "match"
            messages.append(
                f"{_card_count_text(overlap_count).capitalize()} {overlap_verb} "
                "more than one policy."
            )
        if plan.conflicted_card_ids:
            conflict_count = len(plan.conflicted_card_ids)
            conflict_verb = "has" if conflict_count == 1 else "have"
            messages.append(
                f"{_card_count_text(conflict_count).capitalize()} {conflict_verb} "
                "conflicting actions and would be skipped."
            )
        if errors:
            messages.append(
                "A checked policy has an error. Uncheck it or fix the configuration before running."
            )
        self.summary.setText("\n".join(messages))
        self.view_button.setEnabled(bool(candidate_ids))
        self.run_button.setEnabled(plan.card_count > 0 and not errors)

    def _view_included(self) -> None:
        card_ids = {card.card_id for report in self.checked_reports() for card in report.actionable}
        if card_ids:
            _open_cards_in_browser(card_ids)

    def _view_policy(self, row: int, _column: int) -> None:
        if _column == self.COLUMN_RUN:
            return
        card_ids = {card.card_id for card in self._reports[row].actionable}
        if card_ids:
            _open_cards_in_browser(card_ids)

    def _refresh(self) -> None:
        refresh_manual_dialog(self)

    def _run(self) -> None:
        execute_manual_reports(self, self.checked_reports())


def _card_count_text(count: int) -> str:
    return "1 card" if count == 1 else f"{count} cards"


def _applied_message(count: int) -> str:
    return f"Applied policies to {_card_count_text(count)}."


def _open_cards_in_browser(card_ids: set[int]) -> None:
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in sorted(card_ids)))
    aqt.dialogs.open("Browser", mw, search=(node,))


def _show_manual_dialog(reports: tuple[PolicyReport, ...]) -> None:
    dialog = CardJanitorDialog(reports)
    setattr(mw, MANUAL_DIALOG_ATTR, dialog)

    def clear_reference(_result: int) -> None:
        if getattr(mw, MANUAL_DIALOG_ATTR, None) is dialog:
            setattr(mw, MANUAL_DIALOG_ATTR, None)

    qconnect(dialog.finished, clear_reference)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


def open_card_janitor() -> None:
    existing = getattr(mw, MANUAL_DIALOG_ATTR, None)
    if isinstance(existing, CardJanitorDialog) and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return
    parsed = _load_for_operation()
    if parsed is None:
        return
    policies = parsed.config.policies
    if not policies:
        showInfo("There are no cleanup policies configured.", parent=mw)
        return
    QueryOp(
        parent=mw,
        op=lambda col: evaluate_policies(col, policies),
        success=_show_manual_dialog,
    ).run_in_background()


def refresh_manual_dialog(dialog: CardJanitorDialog) -> None:
    parsed = _load_for_operation(dialog)
    if parsed is None:
        return
    checked = dialog.checked_policy_ids()
    known = dialog.policy_ids()
    policies = parsed.config.policies
    if not policies:
        dialog.close()
        showInfo("There are no cleanup policies configured.", parent=mw)
        return
    QueryOp(
        parent=dialog,
        op=lambda col: evaluate_policies(col, policies),
        success=lambda reports: dialog.set_reports(reports, checked, known),
    ).run_in_background()


def execute_manual_reports(
    dialog: CardJanitorDialog,
    reports: tuple[PolicyReport, ...],
) -> None:
    if not reports or any(report.errors for report in reports):
        return
    approved = {
        report.policy.id: {card.card_id for card in report.actionable} for report in reports
    }
    policies = tuple(report.policy for report in reports)
    dialog.close()

    def execute_fresh(col: Collection) -> ExecutionResult:
        fresh_reports = evaluate_policies(col, policies)
        runtime_errors = [item for report in fresh_reports for item in report.errors]
        if runtime_errors:
            raise RuntimeError("; ".join(runtime_errors))
        filtered = tuple(
            replace(
                report,
                actionable=tuple(
                    card for card in report.actionable if card.card_id in approved[report.policy.id]
                ),
            )
            for report in fresh_reports
        )
        return execute_plan(col, build_execution_plan(filtered), "Card Janitor: Manual Run")

    def on_applied(result: ExecutionResult) -> None:
        debug(
            "manual run complete",
            affected_cards=result.affected_cards,
            conflicts=result.conflicts,
        )
        message = (
            _applied_message(result.affected_cards)
            if result.affected_cards
            else "No cards required an action."
        )
        if result.conflicts:
            message += f" {result.conflicts} conflicting cards were skipped."
        tooltip(message, parent=mw)

    CollectionOp(parent=mw, op=execute_fresh).success(on_applied).run_in_background()


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
        messages.append(_applied_message(affected_cards).removesuffix("."))
    if conflicts:
        messages.append(f"{conflicts} conflicting cards were skipped")
    return " ".join(f"{message}." for message in messages)


def run_automatic_policies(*, trigger: AutomaticTrigger = "profile_open") -> None:
    parsed = _load_configured()
    if parsed.issues:
        error("invalid configuration", issues=tuple(str(issue) for issue in parsed.issues))
        showWarning(_issues_text(parsed), parent=mw)
        return
    policies = tuple(policy for policy in parsed.config.policies if policy.state == "automatic")
    if not policies:
        debug("automatic run skipped", reason="no automatic policies")
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
            "automatic run skipped",
            reason="schedule is not due",
            schedule=parsed.config.automatic_schedule,
            trigger=trigger,
            today=today,
            last_automatic_day=last_day,
        )
        return

    debug(
        "automatic run started",
        policy_count=len(policies),
        schedule=parsed.config.automatic_schedule,
        trigger=trigger,
    )

    def on_evaluated(reports: tuple[PolicyReport, ...]) -> None:
        errors = [f"{report.policy.name}: {item}" for report in reports for item in report.errors]
        if errors:
            error("automatic run evaluation failed", errors=tuple(errors))
            tooltip(
                "Card Janitor: an automatic policy has errors; see Settings.",
                parent=mw,
            )
            return
        plan = build_execution_plan(reports)
        if plan.is_empty:
            _mark_daily_run(parsed.config.automatic_schedule, today)
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
            return execute_plan(col, build_execution_plan(filtered), "Card Janitor: Automatic Run")

        def on_applied(result: ExecutionResult) -> None:
            _mark_daily_run(parsed.config.automatic_schedule, today)
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


def install_menu() -> None:
    existing = getattr(mw, MENU_ATTR, None)
    if existing is not None:
        with contextlib.suppress(RuntimeError):
            mw.form.menuTools.removeAction(existing)

    action = QAction("Card Janitor…", mw)
    qconnect(action.triggered, open_card_janitor)
    mw.form.menuTools.addAction(action)
    setattr(mw, MENU_ATTR, action)


def safe_install_menu() -> None:
    try:
        install_menu()
    except Exception:
        exception("failed to install Tools menu")
