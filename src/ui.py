# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
from collections import Counter
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

import aqt
from anki.collection import SearchNode
from aqt import mw
from aqt.addons import ConfigEditor
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import (
    QAbstractItemView,
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSignalBlocker,
    QSpinBox,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .configuration import ADDON_MODULE, load_config, load_raw_config, save_policy
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
    Policy,
    PolicyRecord,
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


def _configured_use(state: str, schedule: AutomaticSchedule) -> str:
    if state == "disabled":
        return "Off"
    if state == "manual":
        return "On request"
    schedules = {
        "profile_open": "when profile opens",
        "daily": "daily",
        "profile_open_and_daily": "when opened + daily",
    }
    return f"On request + {schedules[schedule]}"


@dataclass(frozen=True)
class DashboardRow:
    record: PolicyRecord
    report: PolicyReport | None


class RuleConditionRow(QWidget):
    def __init__(self, rule: Rule | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox(self)
        self.kind.addItem("Age", "age")
        self.kind.addItem("Current interval", "interval")
        self.kind.addItem("Still new", "new")
        self.kind.setMinimumWidth(160)
        self.days = QSpinBox(self)
        self.days.setRange(1, 100000)
        self.days.setSuffix(" days")
        self.days.setMinimumWidth(110)
        self.source = QComboBox(self)
        self.source.addItem("since first review", "first_review")
        self.source.addItem("since card creation", "card_created")
        self.source.setMinimumWidth(180)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setMinimumWidth(80)
        for widget in (self.kind, self.days, self.source, self.remove_button):
            layout.addWidget(widget)
        if isinstance(rule, AgeRule):
            self.kind.setCurrentIndex(self.kind.findData("age"))
            self.days.setValue(rule.days)
            self.source.setCurrentIndex(self.source.findData(rule.source))
        elif isinstance(rule, IntervalRule):
            self.kind.setCurrentIndex(self.kind.findData("interval"))
            self.days.setValue(rule.days)
        elif isinstance(rule, NewRule):
            self.kind.setCurrentIndex(self.kind.findData("new"))
            self.days.setValue(365)
        else:
            self.days.setValue(365)
        qconnect(self.kind.currentIndexChanged, self._update_controls)
        self._update_controls()

    def _update_controls(self, _index: int = 0) -> None:
        kind = self.kind.currentData()
        self.days.setEnabled(kind != "new")
        self.source.setEnabled(kind == "age")

    def rule(self) -> AgeRule | IntervalRule | NewRule:
        kind = self.kind.currentData()
        if kind == "age":
            return AgeRule(self.days.value(), self.source.currentData())
        if kind == "interval":
            return IntervalRule(self.days.value())
        return NewRule()


class PolicyEditorDialog(QDialog):
    def __init__(self, record: PolicyRecord | None, existing_ids: set[str]) -> None:
        super().__init__(mw)
        self._record = record
        self._existing_ids = existing_ids
        self.result_policy: Policy | None = None
        self._conditions: list[RuleConditionRow] = []
        self.setWindowTitle("Add Policy" if record is None else "Edit Policy")
        self.resize(650, 680)
        raw = record.raw if record is not None and isinstance(record.raw, dict) else {}
        policy = record.policy if record is not None else None
        deck_names = [
            item.name
            for item in mw.col.decks.all_names_and_ids(
                skip_empty_default=True,
                include_filtered=False,
            )
        ]

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        general_group = QGroupBox("General", self)
        form = QFormLayout(general_group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name = QLineEdit(policy.name if policy else _raw_string(raw, "name"), self)
        form.addRow("Name", self.name)
        self.state = QComboBox(self)
        self.state.addItem("Off", "disabled")
        self.state.addItem("On request", "manual")
        self.state.addItem("Automatic + on request", "automatic")
        state = policy.state if policy else raw.get("state", "manual")
        index = self.state.findData(state)
        self.state.setCurrentIndex(index if index >= 0 else self.state.findData("manual"))
        form.addRow("Use", self.state)
        layout.addWidget(general_group)

        scope_group = QGroupBox("Decks", self)
        scope_layout = QVBoxLayout(scope_group)
        scope_layout.addWidget(QLabel("Apply this policy to one or more decks:", self))
        self.decks = QListWidget(self)
        self.decks.setAlternatingRowColors(True)
        self.decks.setMinimumHeight(130)
        raw_scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        deck_values = policy.scope.decks if policy else _raw_string_list(raw_scope, "decks")
        for deck_name in (*deck_names, *(name for name in deck_values if name not in deck_names)):
            item = QListWidgetItem(deck_name, self.decks)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if deck_name in deck_values else Qt.CheckState.Unchecked
            )
        scope_layout.addWidget(self.decks)
        self.include_subdecks = QCheckBox("Include subdecks", self)
        self.include_suspended = QCheckBox("Include already suspended cards", self)
        self.include_subdecks.setChecked(
            policy.scope.include_subdecks
            if policy
            else _raw_bool(raw_scope, "include_subdecks", default=True)
        )
        self.include_suspended.setChecked(
            policy.scope.include_suspended
            if policy
            else _raw_bool(raw_scope, "include_suspended", default=False)
        )
        for checkbox in (self.include_subdecks, self.include_suspended):
            scope_layout.addWidget(checkbox)
        layout.addWidget(scope_group)

        conditions_group = QGroupBox("Conditions", self)
        conditions_group_layout = QVBoxLayout(conditions_group)
        match_row = QHBoxLayout()
        match_row.addWidget(QLabel("Match", self))
        self.match = QComboBox(self)
        self.match.addItem("All conditions (AND)", "all")
        self.match.addItem("Any condition (OR)", "any")
        match_row.addWidget(self.match)
        match_row.addStretch()
        self.add_condition_button = QPushButton("Add Condition", self)
        match_row.addWidget(self.add_condition_button)
        conditions_group_layout.addLayout(match_row)
        self.conditions_layout = QVBoxLayout()
        conditions_group_layout.addLayout(self.conditions_layout)
        source_rule = policy.rule if policy else _best_effort_rule(raw.get("rule"))
        if isinstance(source_rule, (AllRule, AnyRule)):
            self.match.setCurrentIndex(
                self.match.findData("all" if isinstance(source_rule, AllRule) else "any")
            )
            rules = source_rule.rules
        else:
            rules = (source_rule,) if source_rule is not None else (AgeRule(365, "first_review"),)
        for rule in rules:
            self._add_condition(rule)
        qconnect(self.add_condition_button.clicked, lambda: self._add_condition(None))
        layout.addWidget(conditions_group)

        actions_group = QGroupBox("Actions", self)
        actions_group_layout = QVBoxLayout(actions_group)
        actions_form = QFormLayout()
        source_actions = policy.actions if policy else _best_effort_actions(raw.get("actions"))
        self.tag_enabled = QCheckBox("Add tag", self)
        self.tag = QLineEdit(self)
        self.suspend = QCheckBox("Suspend cards", self)
        self.move_enabled = QCheckBox("Move to deck", self)
        self.move_deck = QComboBox(self)
        self.move_deck.setEditable(True)
        self.move_deck.addItems(deck_names)
        self.delete = QCheckBox("Delete cards (and notes left without cards)", self)
        for action in source_actions:
            if isinstance(action, TagAction):
                self.tag_enabled.setChecked(True)
                self.tag.setText(action.tag)
            elif isinstance(action, SuspendAction):
                self.suspend.setChecked(True)
            elif isinstance(action, MoveAction):
                self.move_enabled.setChecked(True)
                self.move_deck.setCurrentText(action.deck)
            elif isinstance(action, DeleteCardAction):
                self.delete.setChecked(True)
        actions_form.addRow(self.tag_enabled, self.tag)
        actions_form.addRow(self.suspend)
        actions_form.addRow(self.move_enabled, self.move_deck)
        actions_form.addRow(self.delete)
        actions_group_layout.addLayout(actions_form)
        self.delete_warning = QLabel(
            "Automatic deletion is destructive. Anki undo is available only until later changes replace it.",
            self,
        )
        self.delete_warning.setWordWrap(True)
        actions_group_layout.addWidget(self.delete_warning)
        layout.addWidget(actions_group)
        qconnect(self.delete.toggled, self._update_action_controls)
        qconnect(self.state.currentIndexChanged, self._update_action_controls)
        qconnect(self.tag_enabled.toggled, self._update_action_controls)
        qconnect(self.move_enabled.toggled, self._update_action_controls)
        self._update_action_controls()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        qconnect(buttons.accepted, self._accept)
        qconnect(buttons.rejected, self.reject)
        layout.addWidget(buttons)

    def _add_condition(self, rule: Rule | None) -> None:
        row = RuleConditionRow(rule, self)
        self._conditions.append(row)
        self.conditions_layout.addWidget(row)
        qconnect(row.remove_button.clicked, lambda: self._remove_condition(row))

    def _remove_condition(self, row: RuleConditionRow) -> None:
        if len(self._conditions) == 1:
            showWarning("A policy must have at least one condition.", parent=self)
            return
        self._conditions.remove(row)
        row.deleteLater()

    def _update_action_controls(self, _value: object = None) -> None:
        deleting = self.delete.isChecked()
        for widget in (self.tag_enabled, self.tag, self.suspend, self.move_enabled, self.move_deck):
            widget.setEnabled(not deleting)
        self.tag.setEnabled(not deleting and self.tag_enabled.isChecked())
        self.move_deck.setEnabled(not deleting and self.move_enabled.isChecked())
        self.delete_warning.setVisible(deleting)

    def _accept(self) -> None:  # noqa: PLR0912
        name = self.name.text().strip()
        decks = tuple(
            self.decks.item(index).text()
            for index in range(self.decks.count())
            if self.decks.item(index).checkState() == Qt.CheckState.Checked
        )
        if not name:
            showWarning("Enter a policy name.", parent=self)
            return
        if not decks:
            showWarning("Enter at least one deck.", parent=self)
            return
        simple_rules = tuple(row.rule() for row in self._conditions)
        rule: Rule
        if len(simple_rules) == 1:
            rule = simple_rules[0]
        elif self.match.currentData() == "all":
            rule = AllRule(simple_rules)
        else:
            rule = AnyRule(simple_rules)
        actions: list[Action] = []
        if self.delete.isChecked():
            actions.append(DeleteCardAction())
        else:
            if self.tag_enabled.isChecked():
                tag = self.tag.text().strip()
                if not tag:
                    showWarning("Enter a tag or disable the tag action.", parent=self)
                    return
                actions.append(TagAction(tag))
            if self.suspend.isChecked():
                actions.append(SuspendAction())
            if self.move_enabled.isChecked():
                deck = self.move_deck.currentText().strip()
                if not deck:
                    showWarning("Enter a destination deck or disable the move action.", parent=self)
                    return
                actions.append(MoveAction(deck))
        if not actions:
            showWarning("Choose at least one action.", parent=self)
            return
        raw = (
            self._record.raw
            if self._record is not None and isinstance(self._record.raw, dict)
            else {}
        )
        existing_id = _raw_string(raw, "id")
        policy_id = self._record.policy.id if self._record and self._record.policy else existing_id
        if not policy_id:
            policy_id = uuid4().hex
        normalized_id = policy_id.casefold()
        if normalized_id in self._existing_ids:
            if self._record is not None and self._record.policy is None:
                policy_id = uuid4().hex
            else:
                showWarning("Another policy has the same internal ID.", parent=self)
                return
        self.result_policy = Policy(
            id=policy_id,
            name=name,
            state=self.state.currentData(),
            scope=Scope(
                decks=decks,
                include_subdecks=self.include_subdecks.isChecked(),
                include_suspended=self.include_suspended.isChecked(),
            ),
            rule=rule,
            actions=tuple(actions),
        )
        self.accept()


def _raw_string(raw: object, key: str) -> str:
    if isinstance(raw, dict) and isinstance(raw.get(key), str):
        return raw[key]
    return ""


def _raw_string_list(raw: object, key: str) -> tuple[str, ...]:
    value = raw.get(key) if isinstance(raw, dict) else None
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _raw_bool(raw: object, key: str, *, default: bool) -> bool:
    value = raw.get(key, default) if isinstance(raw, dict) else default
    return value if isinstance(value, bool) else default


def _best_effort_rule(raw: object) -> Rule | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind in {"all", "any"} and isinstance(raw.get("rules"), list):
        children = tuple(filter(None, (_best_effort_rule(child) for child in raw["rules"])))
        simple = tuple(
            child for child in children if isinstance(child, (AgeRule, IntervalRule, NewRule))
        )
        if simple:
            return AllRule(simple) if kind == "all" else AnyRule(simple)
    days = raw.get("days")
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        days = 365
    if kind == "age":
        source = raw.get("from")
        return AgeRule(
            days, source if source in {"first_review", "card_created"} else "first_review"
        )
    if kind == "interval":
        return IntervalRule(days)
    if kind == "new":
        return NewRule()
    return None


def _best_effort_actions(raw: object) -> tuple[Action, ...]:
    if not isinstance(raw, list):
        return ()
    actions: list[Action] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "tag" and isinstance(item.get("tag"), str):
            actions.append(TagAction(item["tag"]))
        elif kind == "suspend":
            actions.append(SuspendAction())
        elif kind == "move" and isinstance(item.get("deck"), str):
            actions.append(MoveAction(item["deck"]))
        elif kind == "delete_card":
            actions.append(DeleteCardAction())
    return tuple(actions)


class CardJanitorDialog(QDialog):
    COLUMN_RUN = 0
    COLUMN_POLICY = 1
    COLUMN_STATE = 2
    COLUMN_SCOPE = 3
    COLUMN_RULE = 4
    COLUMN_ACTIONS = 5
    COLUMN_AFFECTED = 6

    def __init__(self, parsed: ParsedConfig, reports: tuple[PolicyReport, ...]) -> None:
        super().__init__(mw)
        self._rows: tuple[DashboardRow, ...] = ()
        self._parsed = parsed
        self.setWindowTitle("Card Janitor")
        self.resize(1050, 420)

        layout = QVBoxLayout(self)
        intro = QHBoxLayout()
        intro.addWidget(QLabel("Choose which policies to include in this run.", self))
        intro.addStretch()
        self.add_button = QPushButton("Add…", self)
        self.edit_button = QPushButton("Edit…", self)
        intro.addWidget(self.add_button)
        intro.addWidget(self.edit_button)
        layout.addLayout(intro)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ("Run", "Policy", "Configured use", "Scope", "Rule", "Actions", "Affected")
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
        qconnect(self.table.cellDoubleClicked, lambda _row, _column: self._edit_policy())
        qconnect(self.table.itemSelectionChanged, self._update_buttons)
        qconnect(self.add_button.clicked, self._add_policy)
        qconnect(self.edit_button.clicked, self._edit_policy)
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

        self.set_dashboard(parsed, reports)

    def set_dashboard(
        self,
        parsed: ParsedConfig,
        reports: tuple[PolicyReport, ...],
        checked_keys: set[str] | None = None,
        known_keys: set[str] | None = None,
    ) -> None:
        self._parsed = parsed
        report_by_id = {report.policy.id: report for report in reports}
        self._rows = tuple(
            DashboardRow(
                record,
                report_by_id.get(record.policy.id) if record.policy is not None else None,
            )
            for record in parsed.policy_records
        )
        signal_blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(self._rows))
        for row, dashboard_row in enumerate(self._rows):
            record = dashboard_row.record
            policy = record.policy
            use_config_default = checked_keys is None or (
                known_keys is not None and record.key not in known_keys
            )
            included = (
                policy is not None and policy.state != "disabled"
                if use_config_default
                else record.key in checked_keys
            )
            run_item = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if policy is not None:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            run_item.setFlags(flags)
            run_item.setCheckState(Qt.CheckState.Checked if included else Qt.CheckState.Unchecked)
            self.table.setItem(row, self.COLUMN_RUN, run_item)
            if policy is None:
                raw = record.raw if isinstance(record.raw, dict) else {}
                values = (
                    _raw_string(raw, "name") or f"Invalid policy {record.index + 1}",
                    "Invalid",
                    "—",
                    "—",
                    "—",
                    "Error",
                )
            else:
                report = dashboard_row.report
                values = (
                    policy.name,
                    _configured_use(policy.state, parsed.config.automatic_schedule),
                    _describe_scope(policy.scope),
                    _describe_rule(policy.rule),
                    _describe_actions(policy.actions),
                    "Error" if report is None or report.errors else str(len(report.actionable)),
                )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                self.table.setItem(row, column, item)
            if policy is None:
                error_text = "\n".join(str(issue) for issue in record.issues)
                self.table.item(row, self.COLUMN_AFFECTED).setToolTip(error_text)
                self.table.item(row, self.COLUMN_POLICY).setToolTip(error_text)
            else:
                self.table.item(row, self.COLUMN_POLICY).setToolTip(f"Policy ID: {policy.id}")
                self.table.item(row, self.COLUMN_SCOPE).setToolTip(_scope_tooltip(policy.scope))
                if dashboard_row.report and dashboard_row.report.errors:
                    error_text = "\n".join(dashboard_row.report.errors)
                    self.table.item(row, self.COLUMN_AFFECTED).setToolTip(error_text)
        del signal_blocker
        self.table.resizeRowsToContents()
        if self._rows:
            self.table.selectRow(0)
        self._update_summary()
        self._update_buttons()

    def checked_keys(self) -> set[str]:
        return {
            dashboard_row.record.key
            for row, dashboard_row in enumerate(self._rows)
            if self.table.item(row, self.COLUMN_RUN).checkState() == Qt.CheckState.Checked
        }

    def row_keys(self) -> set[str]:
        return {row.record.key for row in self._rows}

    def checked_reports(self) -> tuple[PolicyReport, ...]:
        checked = self.checked_keys()
        return tuple(
            row.report for row in self._rows if row.record.key in checked and row.report is not None
        )

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == self.COLUMN_RUN:
            self._update_summary()

    def _update_summary(self) -> None:
        reports = self.checked_reports()
        global_issues = [
            issue for issue in self._parsed.issues if not issue.path.startswith("policies[")
        ]
        if global_issues:
            self.summary.setText(
                "Configuration settings need repair:\n"
                + "\n".join(f"• {issue}" for issue in global_issues)
            )
            self.view_button.setEnabled(False)
            self.run_button.setEnabled(False)
            return
        if not reports:
            message = "No policies are included in this run."
            if any(row.record.policy is None for row in self._rows):
                message += " Edit policies marked Invalid to repair them."
            self.summary.setText(message)
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

    def _refresh(self) -> None:
        refresh_manual_dialog(self)

    def _run(self) -> None:
        execute_manual_reports(self, self.checked_reports())

    def _selected_record(self) -> PolicyRecord | None:
        row = self.table.currentRow()
        return self._rows[row].record if 0 <= row < len(self._rows) else None

    def _update_buttons(self) -> None:
        self.edit_button.setEnabled(self._selected_record() is not None)

    def _add_policy(self) -> None:
        self._open_editor(None)

    def _edit_policy(self) -> None:
        record = self._selected_record()
        if record is not None:
            self._open_editor(record)

    def _open_editor(self, record: PolicyRecord | None) -> None:
        excluded_id = record.policy.id.casefold() if record and record.policy else None
        existing_ids = {
            item.policy.id.casefold()
            for item in self._parsed.policy_records
            if item.policy is not None and item.policy.id.casefold() != excluded_id
        }
        editor = PolicyEditorDialog(record, existing_ids)
        if editor.exec() != QDialog.DialogCode.Accepted or editor.result_policy is None:
            return
        try:
            save_policy(editor.result_policy, index=record.index if record is not None else None)
        except ValueError as exc:
            showWarning(str(exc), parent=self)
            return
        self._refresh()


def _card_count_text(count: int) -> str:
    return "1 card" if count == 1 else f"{count} cards"


def _applied_message(count: int) -> str:
    return f"Applied policies to {_card_count_text(count)}."


def _open_cards_in_browser(card_ids: set[int]) -> None:
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in sorted(card_ids)))
    aqt.dialogs.open("Browser", mw, search=(node,))


def _show_manual_dialog(parsed: ParsedConfig, reports: tuple[PolicyReport, ...]) -> None:
    dialog = CardJanitorDialog(parsed, reports)
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
    parsed = _load_configured()
    policies = parsed.config.policies
    collection = mw.col

    def on_success(reports: tuple[PolicyReport, ...]) -> None:
        if mw.col is collection:
            _show_manual_dialog(parsed, reports)

    QueryOp(
        parent=mw,
        op=lambda col: evaluate_policies(col, policies),
        success=on_success,
    ).run_in_background()


def refresh_manual_dialog(dialog: CardJanitorDialog) -> None:
    parsed = _load_configured()
    checked = dialog.checked_keys()
    known = dialog.row_keys()
    policies = parsed.config.policies

    def on_success(reports: tuple[PolicyReport, ...]) -> None:
        if getattr(mw, MANUAL_DIALOG_ATTR, None) is dialog and dialog.isVisible():
            dialog.set_dashboard(parsed, reports, checked, known)

    QueryOp(
        parent=dialog,
        op=lambda col: evaluate_policies(col, policies),
        success=on_success,
    ).run_in_background()


def close_card_janitor() -> None:
    dialog = getattr(mw, MANUAL_DIALOG_ATTR, None)
    if isinstance(dialog, CardJanitorDialog):
        dialog.close()
    setattr(mw, MANUAL_DIALOG_ATTR, None)


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
