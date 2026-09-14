# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
import re
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
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSignalBlocker,
    QSpinBox,
    QStackedWidget,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    qconnect,
)
from aqt.utils import showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan, execute_plan
from .configuration import (
    ADDON_MODULE,
    ConfigWriteError,
    load_config,
    load_raw_config,
    save_policy,
    save_settings,
)
from .evaluator import evaluate_policies
from .log import configure as configure_logging
from .log import debug, error, exception
from .models import (
    Action,
    AgeRule,
    AllRule,
    AnyRule,
    CardStateRule,
    DeleteCardAction,
    IntervalRule,
    MoveAction,
    Policy,
    PolicyRecord,
    ReviewHistoryRule,
    Rule,
    Scope,
    SuspendAction,
    TagAction,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from anki.collection import Collection

    from .engine import PolicyReport
    from .models import AddonConfig, ParsedConfig

AutomaticTrigger = Literal["profile_open", "day_change"]

NUMERIC_OPERATOR_LABELS = (
    ("is greater than", "gt"),
    ("is at least", "gte"),
    ("is exactly", "eq"),
    ("is at most", "lte"),
    ("is less than", "lt"),
)
NUMERIC_OPERATOR_SYMBOLS = {"gt": ">", "gte": "≥", "eq": "=", "lte": "≤", "lt": "<"}
CARD_STATES = (
    ("New", "new"),
    ("Learning", "learning"),
    ("Review", "review"),
    ("Relearning", "relearning"),
)

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
        source = "Age since first review" if rule.source == "first_review" else "Age since creation"
        return f"{source} {NUMERIC_OPERATOR_SYMBOLS[rule.operator]} {rule.days} days"
    if isinstance(rule, IntervalRule):
        return f"Interval {NUMERIC_OPERATOR_SYMBOLS[rule.operator]} {rule.days} days"
    if isinstance(rule, CardStateRule):
        operator = "is any of" if rule.operator == "in" else "is none of"
        states = ", ".join(state.capitalize() for state in rule.states)
        return f"Card state {operator} {states}"
    if isinstance(rule, ReviewHistoryRule):
        operator = "exists" if rule.operator == "exists" else "does not exist"
        return f"Review history {operator}"
    if isinstance(rule, (AllRule, AnyRule)):
        operator = " AND " if isinstance(rule, AllRule) else " OR "
        description = operator.join(_describe_rule(child, nested=True) for child in rule.rules)
        return f"({description})" if nested else description
    message = f"unknown cleanup rule: {rule!r}"
    raise AssertionError(message)


def _configured_use(state: str) -> str:
    if state == "disabled":
        return "Off"
    if state == "manual":
        return "On demand"
    return "Automatic"


@dataclass(frozen=True)
class DashboardRow:
    record: PolicyRecord
    report: PolicyReport | None


class CardStatePicker(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._states = ("new",)
        self._checkboxes: dict[str, QCheckBox] = {}
        menu = QMenu(self)
        container = QWidget(menu)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)
        for label, value in CARD_STATES:
            checkbox = QCheckBox(label, container)
            self._checkboxes[value] = checkbox
            qconnect(
                checkbox.toggled,
                lambda checked, selected=value: self._state_toggled(selected, checked),
            )
            layout.addWidget(checkbox)
        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        self.setMenu(menu)
        self.set_states(self._states)

    def states(self) -> tuple[str, ...]:
        return self._states

    def set_states(self, states: tuple[str, ...]) -> None:
        self._states = tuple(value for _label, value in CARD_STATES if value in states)
        blockers = [QSignalBlocker(checkbox) for checkbox in self._checkboxes.values()]
        for value, checkbox in self._checkboxes.items():
            checkbox.setChecked(value in self._states)
        del blockers
        self._update_text()

    def _update_text(self) -> None:
        selected = [label for label, value in CARD_STATES if value in self._states]
        self.setText(", ".join(selected) if selected else "Choose states")

    def _state_toggled(self, state: str, _checked: bool) -> None:
        selected = tuple(
            value for _label, value in CARD_STATES if self._checkboxes[value].isChecked()
        )
        if not selected:
            blocker = QSignalBlocker(self._checkboxes[state])
            self._checkboxes[state].setChecked(True)
            del blocker
            return
        self._states = selected
        self._update_text()


class RuleConditionRow(QWidget):
    def __init__(self, rule: Rule | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.number_label = QLabel(self)
        self.number_label.setMinimumWidth(20)
        self.kind = QComboBox(self)
        self.kind.addItem("Age since first review", "age_first_review")
        self.kind.addItem("Age since creation", "age_card_created")
        self.kind.addItem("Current interval", "interval")
        self.kind.addItem("Card state", "card_state")
        self.kind.addItem("Review history", "review_history")
        self.kind.setMinimumWidth(190)
        self.operator = QComboBox(self)
        self.operator.setMinimumWidth(125)
        self.days = QSpinBox(self)
        self.days.setRange(0, 100000)
        self.days.setSuffix(" days")
        self.days.setMinimumWidth(220)
        self.states = CardStatePicker(self)
        self.states.setMinimumWidth(220)
        self.value_stack = QStackedWidget(self)
        self.value_stack.setMinimumWidth(220)
        self.value_stack.addWidget(self.days)
        self.value_stack.addWidget(self.states)
        self.no_value = QWidget(self)
        self.value_stack.addWidget(self.no_value)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setMinimumWidth(80)
        for widget in (
            self.number_label,
            self.kind,
            self.operator,
            self.value_stack,
            self.remove_button,
        ):
            layout.addWidget(widget)
        selected_states: tuple[str, ...] | None = None
        selected_operator: str | None = None
        if isinstance(rule, AgeRule):
            kind = "age_first_review" if rule.source == "first_review" else "age_card_created"
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self.days.setValue(rule.days)
            selected_operator = rule.operator
        elif isinstance(rule, IntervalRule):
            self.kind.setCurrentIndex(self.kind.findData("interval"))
            self.days.setValue(rule.days)
            selected_operator = rule.operator
        elif isinstance(rule, CardStateRule):
            self.kind.setCurrentIndex(self.kind.findData("card_state"))
            selected_states = rule.states
            selected_operator = rule.operator
        elif isinstance(rule, ReviewHistoryRule):
            self.kind.setCurrentIndex(self.kind.findData("review_history"))
            selected_operator = rule.operator
        else:
            self.days.setValue(365)
        qconnect(self.kind.currentIndexChanged, self._update_controls)
        self._update_controls()
        if selected_operator is not None:
            self.operator.setCurrentIndex(self.operator.findData(selected_operator))
        if selected_states is not None:
            self.states.set_states(selected_states)

    def _update_controls(self, _index: int = 0) -> None:
        kind = self.kind.currentData()
        self.operator.clear()
        if kind == "card_state":
            self.operator.addItem("is any of", "in")
            self.operator.addItem("is none of", "not_in")
            self.value_stack.setCurrentWidget(self.states)
        elif kind == "review_history":
            self.operator.addItem("exists", "exists")
            self.operator.addItem("does not exist", "not_exists")
            self.value_stack.setCurrentWidget(self.no_value)
        else:
            for label, value in NUMERIC_OPERATOR_LABELS:
                self.operator.addItem(label, value)
            self.operator.setCurrentIndex(self.operator.findData("gte"))
            self.value_stack.setCurrentWidget(self.days)

    def rule(self) -> AgeRule | IntervalRule | CardStateRule | ReviewHistoryRule:
        kind = self.kind.currentData()
        if kind == "age_first_review":
            return AgeRule(self.days.value(), "first_review", self.operator.currentData())
        if kind == "age_card_created":
            return AgeRule(self.days.value(), "card_created", self.operator.currentData())
        if kind == "interval":
            return IntervalRule(self.days.value(), self.operator.currentData())
        if kind == "card_state":
            return CardStateRule(self.states.states(), self.operator.currentData())
        return ReviewHistoryRule(self.operator.currentData())


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
        self.state.addItem("On demand", "manual")
        self.state.addItem("Automatic", "automatic")
        state = policy.state if policy else raw.get("state", "manual")
        index = self.state.findData(state)
        self.state.setCurrentIndex(index if index >= 0 else self.state.findData("manual"))
        form.addRow("Mode", self.state)
        layout.addWidget(general_group)

        scope_group = QGroupBox("Scope", self)
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
        self.include_subdecks.setToolTip(
            "Include every child of each checked deck, even when those child decks are not "
            "checked separately in the list."
        )
        self.include_suspended = QCheckBox("Include suspended cards", self)
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
        self.creation_age_warning = QLabel(
            "⚠ Imported cards retain their original creation dates and may qualify "
            "immediately. Review the matching cards before using Automatic mode.",
            self,
        )
        self.creation_age_warning.setWordWrap(True)
        self.creation_age_warning.setStyleSheet(
            "background-color: rgba(230, 160, 0, 35);"
            "border: 1px solid rgba(230, 160, 0, 120);"
            "border-radius: 5px;"
            "padding: 8px;"
        )
        conditions_group_layout.addWidget(self.creation_age_warning)
        self.conditions_layout = QVBoxLayout()
        conditions_group_layout.addLayout(self.conditions_layout)
        source_rule = policy.rule if policy else _best_effort_rule(raw.get("rule"))
        if isinstance(source_rule, (AllRule, AnyRule)):
            self.match.setCurrentIndex(
                self.match.findData("all" if isinstance(source_rule, AllRule) else "any")
            )
            rules = source_rule.rules
        else:
            rules = (
                (source_rule,)
                if source_rule is not None
                else (AgeRule(365, "first_review", "gte"),)
            )
        for rule in rules:
            self._add_condition(rule)
        qconnect(self.add_condition_button.clicked, lambda: self._add_condition(None))
        self._update_condition_warning()
        layout.addWidget(conditions_group)

        actions_group = QGroupBox("Actions", self)
        actions_group_layout = QVBoxLayout(actions_group)
        actions_grid = QGridLayout()
        actions_grid.setHorizontalSpacing(16)
        actions_grid.setVerticalSpacing(8)
        actions_grid.setColumnStretch(1, 1)
        source_actions = policy.actions if policy else _best_effort_actions(raw.get("actions"))
        self.tag_enabled = QCheckBox("Add tags", self)
        self.tag = QLineEdit(self)
        self.tag.setPlaceholderText("Separate tags with spaces or commas")
        self.suspend = QCheckBox("Suspend cards", self)
        self.move_enabled = QCheckBox("Move to deck", self)
        self.move_deck = QComboBox(self)
        self.move_deck.setEditable(True)
        self.move_deck.addItems(deck_names)
        self.delete = QCheckBox("Delete cards", self)
        tags = [action.tag for action in source_actions if isinstance(action, TagAction)]
        if tags:
            self.tag_enabled.setChecked(True)
            self.tag.setText(" ".join(tags))
        for action in source_actions:
            if isinstance(action, TagAction):
                continue
            if isinstance(action, SuspendAction):
                self.suspend.setChecked(True)
            elif isinstance(action, MoveAction):
                self.move_enabled.setChecked(True)
                self.move_deck.setCurrentText(action.deck)
            elif isinstance(action, DeleteCardAction):
                self.delete.setChecked(True)
        actions_grid.addWidget(self.tag_enabled, 0, 0)
        actions_grid.addWidget(self.tag, 0, 1)
        actions_grid.addWidget(self.suspend, 1, 0)
        actions_grid.addWidget(self.move_enabled, 2, 0)
        actions_grid.addWidget(self.move_deck, 2, 1)
        actions_grid.addWidget(self.delete, 3, 0)
        actions_group_layout.addLayout(actions_grid)
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
        qconnect(row.kind.currentIndexChanged, self._update_condition_warning)
        self._renumber_conditions()
        self._update_condition_warning()

    def _remove_condition(self, row: RuleConditionRow) -> None:
        if len(self._conditions) == 1:
            showWarning("A policy must have at least one condition.", parent=self)
            return
        self._conditions.remove(row)
        row.deleteLater()
        self._renumber_conditions()
        self._update_condition_warning()

    def _renumber_conditions(self) -> None:
        for index, row in enumerate(self._conditions, start=1):
            row.number_label.setText(f"{index}.")

    def _update_condition_warning(self, _value: object = None) -> None:
        show_creation_warning = any(
            row.kind.currentData() == "age_card_created" for row in self._conditions
        )
        self.creation_age_warning.setVisible(show_creation_warning)

    def _update_action_controls(self, _value: object = None) -> None:
        deleting = self.delete.isChecked()
        if deleting:
            self.tag_enabled.setChecked(False)
            self.suspend.setChecked(False)
            self.move_enabled.setChecked(False)
        for widget in (self.tag_enabled, self.tag, self.suspend, self.move_enabled, self.move_deck):
            widget.setEnabled(not deleting)
        self.tag.setEnabled(not deleting and self.tag_enabled.isChecked())
        self.move_deck.setEnabled(not deleting and self.move_enabled.isChecked())

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
                tags = tuple(
                    value for value in re.split(r"[\s,]+", self.tag.text().strip()) if value
                )
                if not tags:
                    showWarning("Enter one or more tags or disable the tag action.", parent=self)
                    return
                actions.extend(TagAction(tag) for tag in tags)
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


def _best_effort_rule(raw: object) -> Rule | None:  # noqa: PLR0911
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind in {"all", "any"} and isinstance(raw.get("rules"), list):
        children = tuple(filter(None, (_best_effort_rule(child) for child in raw["rules"])))
        simple = tuple(
            child
            for child in children
            if isinstance(
                child,
                (AgeRule, IntervalRule, CardStateRule, ReviewHistoryRule),
            )
        )
        if simple:
            return AllRule(simple) if kind == "all" else AnyRule(simple)
    days = raw.get("days")
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        days = 365
    if kind == "age":
        source = raw.get("from")
        return AgeRule(
            days,
            source if source in {"first_review", "card_created"} else "first_review",
            raw.get("operator") if raw.get("operator") in NUMERIC_OPERATOR_SYMBOLS else "gte",
        )
    if kind == "interval":
        operator = raw.get("operator")
        return IntervalRule(days, operator if operator in NUMERIC_OPERATOR_SYMBOLS else "gte")
    if kind == "card_state":
        raw_states = raw.get("states")
        states = tuple(
            value
            for _label, value in CARD_STATES
            if isinstance(raw_states, list) and value in raw_states
        )
        if states:
            operator = raw.get("operator")
            return CardStateRule(states, operator if operator in {"in", "not_in"} else "in")
    if kind == "review_history" and raw.get("operator") in {"exists", "not_exists"}:
        return ReviewHistoryRule(raw["operator"])
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


class SettingsDialog(QDialog):
    def __init__(self, config: AddonConfig, parent: QWidget) -> None:
        super().__init__(parent)
        self._edit_json_requested = False
        self.setWindowTitle("Card Janitor Settings")

        layout = QVBoxLayout(self)

        automatic_group = QGroupBox("Automatic Cleanup", self)
        automatic_layout = QVBoxLayout(automatic_group)
        self.notify = QCheckBox(
            "Show a notification after cards are cleaned up automatically",
            automatic_group,
        )
        self.notify.setChecked(config.notify_after_automatic_run)
        automatic_layout.addWidget(self.notify)
        layout.addWidget(automatic_group)

        troubleshooting_group = QGroupBox("Troubleshooting", self)
        troubleshooting_layout = QVBoxLayout(troubleshooting_group)
        self.debug_logging = QCheckBox("Enable debug logging", troubleshooting_group)
        self.debug_logging.setChecked(config.debug_logging)
        self.debug_logging.setToolTip("Print policy evaluation details to Anki's terminal output.")
        troubleshooting_layout.addWidget(self.debug_logging)
        layout.addWidget(troubleshooting_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.json_button = buttons.addButton(
            "Edit JSON…",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        qconnect(self.json_button.clicked, self._request_json_editor)
        qconnect(buttons.accepted, self._save)
        qconnect(buttons.rejected, self.reject)
        layout.addWidget(buttons)

    @property
    def edit_json_requested(self) -> bool:
        return self._edit_json_requested

    def _request_json_editor(self) -> None:
        self._edit_json_requested = True
        self.reject()

    def _save(self) -> None:
        try:
            save_settings(
                notify_after_automatic_run=self.notify.isChecked(),
                debug_logging=self.debug_logging.isChecked(),
            )
        except ConfigWriteError as exc:
            showWarning(str(exc), parent=self)
            return
        configure_logging(debug_logging=self.debug_logging.isChecked())
        self.accept()


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
        self._running = False
        self.setWindowTitle("Card Janitor")
        self.resize(1050, 420)

        layout = QVBoxLayout(self)
        intro = QHBoxLayout()
        intro.addWidget(QLabel("Choose which policies to run.", self))
        intro.addStretch()
        self.add_button = QPushButton("Add…", self)
        self.edit_button = QPushButton("Edit…", self)
        intro.addWidget(self.add_button)
        intro.addWidget(self.edit_button)
        layout.addLayout(intro)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ("", "Policy", "Mode", "Scope", "Conditions", "Actions", "Cards")
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
        qconnect(self.table.itemSelectionChanged, self._update_buttons)
        qconnect(self.add_button.clicked, self._add_policy)
        qconnect(self.edit_button.clicked, self._edit_policy)
        layout.addWidget(self.table)

        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self.settings_button = buttons.addButton(
            "Settings…",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.refresh_button = buttons.addButton(
            "Refresh",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.view_button = buttons.addButton(
            "Browse",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.run_button = buttons.addButton(
            "Clean Up",
            QDialogButtonBox.ButtonRole.AcceptRole,
        )
        if isinstance(self.run_button, QPushButton):
            self.run_button.setDefault(True)
        qconnect(self.settings_button.clicked, self._open_settings)
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
                    _configured_use(policy.state),
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
        messages = [f"{_card_count_text(plan.card_count).capitalize()} would be cleaned up."]
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

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._parsed.config, self)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            self._refresh()
        elif dialog.edit_json_requested:
            open_json_settings(parent=self, on_close=self._refresh)

    def _run(self) -> None:
        if self._running:
            return
        self._running = True
        self.run_button.setEnabled(False)
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
    return f"Card Janitor cleaned up {_card_count_text(count)}."


def _open_cards_in_browser(card_ids: set[int]) -> None:
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in sorted(card_ids)))
    browser = aqt.dialogs.open("Browser", mw, search=(node,))
    dialog = getattr(mw, MANUAL_DIALOG_ATTR, None)
    if isinstance(dialog, CardJanitorDialog):
        qconnect(browser.destroyed, lambda _object=None: _restore_dashboard(dialog))


def _restore_dashboard(dialog: CardJanitorDialog) -> None:
    if getattr(mw, MANUAL_DIALOG_ATTR, None) is dialog and dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()


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
            else "No cards needed cleanup."
        )
        if result.conflicts:
            message += f" {result.conflicts} conflicting cards were skipped."
        tooltip(message, parent=mw)

    CollectionOp(parent=mw, op=execute_fresh).success(on_applied).run_in_background()


def open_json_settings(*, parent: QWidget, on_close: Callable[[], None] | None = None) -> None:
    config = load_raw_config()
    if not isinstance(config, dict):
        showWarning("The add-on configuration is not a JSON object.", parent=parent)
        return
    editor_parent = QDialog(parent)
    editor_parent.mgr = mw.addonManager
    editor = ConfigEditor(editor_parent, ADDON_MODULE, config)
    setattr(mw, CONFIG_EDITOR_ATTR, (editor_parent, editor))

    def editor_closed(_result: int) -> None:
        setattr(mw, CONFIG_EDITOR_ATTR, None)
        if callable(on_close):
            on_close()

    qconnect(editor.finished, editor_closed)


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
