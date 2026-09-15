# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import itertools
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from aqt import mw
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSignalBlocker,
    QSpinBox,
    QStackedWidget,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    qconnect,
)
from aqt.utils import showWarning

from .configuration import ConfigWriteError, save_settings
from .log import configure as configure_logging
from .log import debug, error
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
from .presentation import (
    CARD_STATES,
    CONDITION_HELP,
    NUMERIC_OPERATOR_LABELS,
    NUMERIC_OPERATOR_SYMBOLS,
    mode_tooltip,
    warning_panel,
)

if TYPE_CHECKING:
    from .models import AddonConfig


class CardStatePicker(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._states = ("new",)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._menu = QMenu(self)
        container = QWidget(self._menu)
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
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(container)
        self._menu.addAction(action)
        self.addItem("")
        self.set_states(self._states)

    def showPopup(self) -> None:  # noqa: N802 - Qt virtual method
        self._menu.popup(self.mapToGlobal(self.rect().bottomLeft()))

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
        summary = ", ".join(selected) if selected else "Choose states"
        self.setItemText(0, summary)
        self.setToolTip(f"Match cards whose current state is any of: {summary}.")

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
        self.kind.setMinimumWidth(165)
        self.operator = QComboBox(self)
        self.fixed_operator = QLabel("is", self)
        self.operator_stack = QStackedWidget(self)
        self.operator_stack.setMinimumWidth(115)
        self.operator_stack.addWidget(self.operator)
        self.operator_stack.addWidget(self.fixed_operator)
        self.days = QSpinBox(self)
        self.days.setRange(0, 100000)
        self.days.setSuffix(" days")
        self.days.setMinimumWidth(140)
        self.states = CardStatePicker(self)
        self.states.setMinimumWidth(140)
        self.value_stack = QStackedWidget(self)
        self.value_stack.setMinimumWidth(140)
        self.value_stack.addWidget(self.days)
        self.value_stack.addWidget(self.states)
        self.no_value = QWidget(self)
        self.value_stack.addWidget(self.no_value)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setMinimumWidth(75)
        self.remove_button.setToolTip("Remove this condition.")
        for widget in (
            self.number_label,
            self.kind,
            self.operator_stack,
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
        help_text = CONDITION_HELP[kind]
        self.kind.setToolTip(help_text)
        self.operator.clear()
        if kind == "card_state":
            self.operator_stack.setCurrentWidget(self.fixed_operator)
            self.value_stack.setCurrentWidget(self.states)
        elif kind == "review_history":
            self.operator_stack.setCurrentWidget(self.operator)
            self.operator.addItem("exists", "exists")
            self.operator.addItem("does not exist", "not_exists")
            self.value_stack.setCurrentWidget(self.no_value)
        else:
            self.operator_stack.setCurrentWidget(self.operator)
            for label, value in NUMERIC_OPERATOR_LABELS:
                self.operator.addItem(label, value)
            self.operator.setCurrentIndex(self.operator.findData("gte"))
            self.value_stack.setCurrentWidget(self.days)
        self.operator.setToolTip("Choose how this condition compares the card value.")
        self.fixed_operator.setToolTip("A card matches when its state is one of those selected.")
        self.days.setToolTip(help_text)

    def rule(self) -> AgeRule | IntervalRule | CardStateRule | ReviewHistoryRule:
        kind = self.kind.currentData()
        if kind == "age_first_review":
            return AgeRule(self.days.value(), "first_review", self.operator.currentData())
        if kind == "age_card_created":
            return AgeRule(self.days.value(), "card_created", self.operator.currentData())
        if kind == "interval":
            return IntervalRule(self.days.value(), self.operator.currentData())
        if kind == "card_state":
            return CardStateRule(self.states.states())
        return ReviewHistoryRule(self.operator.currentData())

    def focus_widgets(self) -> tuple[QWidget, ...]:
        return (self.kind, self.operator, self.days, self.states, self.remove_button)


class PolicyEditorDialog(QDialog):
    def __init__(
        self,
        record: PolicyRecord | None,
        existing_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
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
        self.mode = QComboBox(self)
        self.mode.addItem("On demand", "on_demand")
        self.mode.addItem("Automatic", "automatic")
        mode = policy.mode if policy else raw.get("mode", "on_demand")
        index = self.mode.findData(mode)
        self.mode.setCurrentIndex(index if index >= 0 else self.mode.findData("on_demand"))
        self.automatic_warning = warning_panel(
            "This policy runs once per day <b>without confirmation</b>.",
            self,
        )
        form.insertRow(0, self.automatic_warning)
        self.mode_label = QLabel("Mode", self)
        form.addRow(self.mode_label, self.mode)
        layout.addWidget(general_group)

        scope_group = QGroupBox("Scope", self)
        scope_layout = QVBoxLayout(scope_group)
        scope_layout.addWidget(QLabel("Select one or more decks", self))
        self.decks = QListWidget(self)
        self.decks.setAlternatingRowColors(True)
        self.decks.setMinimumHeight(130)
        self.decks.setTabKeyNavigation(False)
        self.decks.setToolTip("Select the decks whose cards this policy may clean up.")
        raw_scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        deck_values = policy.scope.decks if policy else _raw_string_list(raw_scope, "decks")
        for deck_name in (*deck_names, *(name for name in deck_values if name not in deck_names)):
            item = QListWidgetItem(deck_name, self.decks)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if deck_name in deck_values else Qt.CheckState.Unchecked
            )
        scope_layout.addWidget(self.decks)
        scope_layout.addSpacing(6)
        self.include_subdecks = QCheckBox("Include subdecks", self)
        self.include_subdecks.setToolTip(
            "Also include cards in every child deck of each selected deck."
        )
        self.include_suspended = QCheckBox("Include suspended cards", self)
        self.include_suspended.setToolTip(
            "Allow this policy to match cards that are already suspended."
        )
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
        self.match.setToolTip("Require every condition to match, or allow any one to match.")
        match_row.addWidget(self.match)
        match_row.addStretch()
        self.add_condition_button = QPushButton("Add Condition", self)
        self.add_condition_button.setToolTip("Add another condition to this policy.")
        match_row.addWidget(self.add_condition_button)
        self.creation_age_warning = warning_panel(
            "Imported cards retain their original creation dates and "
            "<b>may qualify immediately</b>.",
            self,
        )
        conditions_group_layout.addWidget(self.creation_age_warning)
        conditions_group_layout.addLayout(match_row)
        self.conditions_scroll = QScrollArea(self)
        self.conditions_scroll.setWidgetResizable(True)
        self.conditions_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.conditions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.conditions_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.conditions_scroll.setMinimumHeight(100)
        self.conditions_scroll.setMaximumHeight(230)
        self.conditions_container = QWidget(self.conditions_scroll)
        self.conditions_layout = QVBoxLayout()
        self.conditions_layout.setContentsMargins(8, 8, 8, 8)
        self.conditions_container.setLayout(self.conditions_layout)
        self.conditions_scroll.setWidget(self.conditions_container)
        conditions_group_layout.addWidget(self.conditions_scroll)
        source_rule = policy.rule if policy else None
        if isinstance(source_rule, (AllRule, AnyRule)):
            self.match.setCurrentIndex(
                self.match.findData("all" if isinstance(source_rule, AllRule) else "any")
            )
            rules = source_rule.rules
        else:
            raw_match = raw.get("match")
            self.match.setCurrentIndex(
                self.match.findData(raw_match if raw_match in {"all", "any"} else "all")
            )
            raw_conditions = raw.get("conditions")
            condition_values = raw_conditions if isinstance(raw_conditions, list) else ()
            rules = tuple(
                condition
                for condition in (_best_effort_rule(item) for item in condition_values)
                if condition is not None
            )
            if not rules:
                rules = (AgeRule(365, "first_review", "gte"),)
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
        self.tag_enabled.setToolTip("Add tags to the notes of matching cards.")
        self.tag = QLineEdit(self)
        self.tag.setPlaceholderText("Separate tags with spaces or commas")
        self.tag.setToolTip("Enter one or more note tags, separated by spaces or commas.")
        self.suspend = QCheckBox("Suspend cards", self)
        self.suspend.setToolTip("Suspend matching cards so Anki no longer schedules them.")
        self.move_enabled = QCheckBox("Move to deck", self)
        self.move_enabled.setToolTip("Move matching cards to another deck.")
        self.move_deck = QComboBox(self)
        self.move_deck.setEditable(True)
        self.move_deck.addItems(deck_names)
        self.move_deck.setToolTip("Choose or enter the destination deck.")
        self.delete = QCheckBox("Delete cards", self)
        self.delete.setToolTip("Delete matching cards from the collection.")
        self.delete_warning = warning_panel(
            "Matching cards will be <b>DELETED</b> from your collection.",
            self,
            destructive=True,
        )
        actions_group_layout.addWidget(self.delete_warning)
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
        qconnect(self.mode.currentIndexChanged, self._update_action_controls)
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
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        qconnect(self.mode.currentIndexChanged, self._update_mode_tooltip)
        self._updatemode_tooltip()
        self._update_tab_order()
        QTimer.singleShot(0, self._focus_initial)

    def _focus_initial(self) -> None:
        self.name.setFocus()
        if self._record is not None:
            self.name.selectAll()

    def _updatemode_tooltip(self, _index: int = 0) -> None:
        help_text = mode_tooltip(self.mode.currentData())
        self.mode_label.setToolTip(help_text)
        self.mode.setToolTip(help_text)

    def _update_tab_order(self) -> None:
        widgets: list[QWidget] = [
            self.name,
            self.mode,
            self.decks,
            self.include_subdecks,
            self.include_suspended,
            self.match,
            self.add_condition_button,
        ]
        for row in self._conditions:
            widgets.extend(row.focus_widgets())
        widgets.extend(
            (
                self.tag_enabled,
                self.tag,
                self.suspend,
                self.move_enabled,
                self.move_deck,
                self.delete,
                self.save_button,
                self.cancel_button,
            )
        )
        for current, following in itertools.pairwise(widgets):
            QWidget.setTabOrder(current, following)

    def _add_condition(self, rule: Rule | None) -> None:
        row = RuleConditionRow(rule, self)
        self._conditions.append(row)
        self.conditions_layout.addWidget(row, alignment=Qt.AlignmentFlag.AlignTop)
        qconnect(row.remove_button.clicked, lambda: self._remove_condition(row))
        qconnect(row.kind.currentIndexChanged, self._update_condition_warning)
        self._renumber_conditions()
        self._update_condition_warning()
        self._update_conditions_extent()
        if hasattr(self, "save_button"):
            self._update_tab_order()
            row.kind.setFocus()

    def _remove_condition(self, row: RuleConditionRow) -> None:
        if len(self._conditions) == 1:
            showWarning("A policy must have at least one condition.", parent=self)
            return
        index = self._conditions.index(row)
        self._conditions.remove(row)
        self.conditions_layout.removeWidget(row)
        row.deleteLater()
        self._renumber_conditions()
        self._update_condition_warning()
        self._update_conditions_extent()
        self._update_tab_order()
        self._conditions[min(index, len(self._conditions) - 1)].kind.setFocus()

    def _renumber_conditions(self) -> None:
        for index, row in enumerate(self._conditions, start=1):
            row.number_label.setText(f"{index}.")

    def _update_conditions_extent(self) -> None:
        spacing = max(0, self.conditions_layout.spacing())
        row_heights = [max(1, row.sizeHint().height()) for row in self._conditions]
        height = sum(row_heights)
        height += spacing * max(0, len(self._conditions) - 1)
        margins = self.conditions_layout.contentsMargins()
        height += margins.top() + margins.bottom()
        self.conditions_container.setMinimumHeight(height)
        two_rows_height = max(row_heights, default=40) * 2 + spacing
        two_rows_height += margins.top() + margins.bottom()
        self.conditions_scroll.setMinimumHeight(two_rows_height)

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
        self._update_warning_panels()

    def _update_warning_panels(self) -> None:
        self.automatic_warning.setVisible(self.mode.currentData() == "automatic")
        self.delete_warning.setVisible(self.delete.isChecked())

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
        rule: Rule = (
            AllRule(simple_rules) if self.match.currentData() == "all" else AnyRule(simple_rules)
        )
        actions: list[Action] = []
        if self.delete.isChecked():
            actions.append(DeleteCardAction())
        else:
            if self.tag_enabled.isChecked():
                tags = tuple(
                    dict.fromkeys(
                        value for value in re.split(r"[\s,]+", self.tag.text().strip()) if value
                    )
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
            mode=self.mode.currentData(),
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
    days = raw.get("days")
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        days = 365
    if kind == "age":
        source = raw.get("source")
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
            return CardStateRule(states)
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
        if kind == "tag" and isinstance(item.get("tags"), list):
            actions.extend(TagAction(tag) for tag in item["tags"] if isinstance(tag, str))
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
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        QWidget.setTabOrder(self.notify, self.debug_logging)
        QWidget.setTabOrder(self.debug_logging, self.json_button)
        QWidget.setTabOrder(self.json_button, save_button)
        QWidget.setTabOrder(save_button, cancel_button)
        QTimer.singleShot(0, self.notify.setFocus)

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
            error("failed to save settings", reason=str(exc))
            showWarning(str(exc), parent=self)
            return
        configure_logging(debug_logging=self.debug_logging.isChecked())
        debug(
            "settings saved",
            notify_after_automatic_run=self.notify.isChecked(),
            debug_logging=self.debug_logging.isChecked(),
        )
        self.accept()
