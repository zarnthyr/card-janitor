# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from aqt.qt import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSignalBlocker,
    QSpinBox,
    QStackedWidget,
    QStandardItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    qconnect,
)

from .editor_utils import _split_tags
from .models import (
    MAX_DAYS,
    AgeCondition,
    CardStateCondition,
    ConditionExpression,
    IntervalCondition,
    ReviewHistoryCondition,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    SuspensionCondition,
    TagCondition,
)
from .presentation import (
    CARD_STATES,
    CONDITION_HELP,
    NUMERIC_OPERATOR_LABELS,
)


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
        self.setToolTip(f"Match cards whose current state is any of: {summary}")

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


def _add_combo_group(combo: QComboBox, title: str, choices: tuple[tuple[str, str], ...]) -> None:
    header = QStandardItem(title)
    header.setEnabled(False)
    header.setSelectable(False)
    font = header.font()
    font.setBold(True)
    header.setFont(font)
    combo.model().appendRow(header)
    for label, value in choices:
        combo.addItem(label, value)


class ConditionRow(QWidget):
    def __init__(
        self, condition: ConditionExpression | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.number_label = QLabel(self)
        self.number_label.setMinimumWidth(20)
        self.kind = QComboBox(self)
        for group, choices in (
            (
                "Cards",
                (
                    ("Age since first review", "age_first_review"),
                    ("Age since creation", "age_card_created"),
                    ("Current interval", "interval"),
                    ("Card state", "card_state"),
                    ("Review history", "review_history"),
                    ("Suspension state", "suspension"),
                ),
            ),
            (
                "Notes",
                (
                    ("Tags", "tags"),
                    ("Sibling suspension", "sibling_suspension"),
                    ("Sibling review history", "sibling_review_history"),
                ),
            ),
        ):
            _add_combo_group(self.kind, group, choices)
        self.kind.setMinimumWidth(165)
        self.operator = QComboBox(self)
        self.fixed_operator = QLabel("is", self)
        self.operator_stack = QStackedWidget(self)
        self.operator_stack.setMinimumWidth(115)
        self.operator_stack.addWidget(self.operator)
        self.operator_stack.addWidget(self.fixed_operator)
        self.days = QSpinBox(self)
        self.days.setRange(0, MAX_DAYS)
        self.days.setSuffix(" days")
        self.days.setMinimumWidth(140)
        self.states = CardStatePicker(self)
        self.states.setMinimumWidth(140)
        self.tags = QLineEdit(self)
        self.tags.setPlaceholderText("tag1, tag2")
        self.tags.setMinimumWidth(140)
        self.value_stack = QStackedWidget(self)
        self.value_stack.setMinimumWidth(140)
        self.value_stack.addWidget(self.days)
        self.value_stack.addWidget(self.states)
        self.value_stack.addWidget(self.tags)
        self.no_value = QWidget(self)
        self.value_stack.addWidget(self.no_value)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setMinimumWidth(75)
        self.remove_button.setToolTip("Remove this condition")
        for widget in (
            self.number_label,
            self.kind,
            self.operator_stack,
            self.value_stack,
            self.remove_button,
        ):
            layout.addWidget(widget)
        selected_states: tuple[str, ...] | None = None
        selected_tags: tuple[str, ...] | None = None
        selected_operator: str | None = None
        if isinstance(condition, AgeCondition):
            kind = "age_first_review" if condition.source == "first_review" else "age_card_created"
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self.days.setValue(condition.days)
            selected_operator = condition.operator
        elif isinstance(condition, IntervalCondition):
            self.kind.setCurrentIndex(self.kind.findData("interval"))
            self.days.setValue(condition.days)
            selected_operator = condition.operator
        elif isinstance(condition, CardStateCondition):
            self.kind.setCurrentIndex(self.kind.findData("card_state"))
            selected_states = condition.states
        elif isinstance(condition, ReviewHistoryCondition):
            self.kind.setCurrentIndex(self.kind.findData("review_history"))
            selected_operator = condition.operator
        elif isinstance(condition, TagCondition):
            self.kind.setCurrentIndex(self.kind.findData("tags"))
            selected_tags = condition.tags
            selected_operator = condition.operator
        elif isinstance(
            condition,
            (SuspensionCondition, SiblingSuspensionCondition, SiblingReviewHistoryCondition),
        ):
            self.kind.setCurrentIndex(
                self.kind.findData(
                    {
                        SuspensionCondition: "suspension",
                        SiblingSuspensionCondition: "sibling_suspension",
                        SiblingReviewHistoryCondition: "sibling_review_history",
                    }[type(condition)]
                )
            )
            selected_operator = condition.operator
        else:
            self.kind.setCurrentIndex(self.kind.findData("age_first_review"))
            self.days.setValue(365)
        qconnect(self.kind.currentIndexChanged, self._update_controls)
        self._update_controls()
        if selected_operator is not None:
            self.operator.setCurrentIndex(self.operator.findData(selected_operator))
        if selected_states is not None:
            self.states.set_states(selected_states)
        if selected_tags is not None:
            self.tags.setText(" ".join(selected_tags))

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
        elif kind == "tags":
            self.operator_stack.setCurrentWidget(self.operator)
            self.operator.addItem("contains any", "contains_any")
            self.operator.addItem("contains all", "contains_all")
            self.operator.addItem("contains none", "contains_none")
            self.value_stack.setCurrentWidget(self.tags)
        elif kind == "suspension":
            self.operator_stack.setCurrentWidget(self.operator)
            self.operator.addItem("is suspended", "is_suspended")
            self.operator.addItem("is not suspended", "is_not_suspended")
            self.value_stack.setCurrentWidget(self.no_value)
        elif kind in {"sibling_suspension", "sibling_review_history"}:
            self.operator_stack.setCurrentWidget(self.operator)
            labels = (
                (("all suspended", "all"), ("any suspended", "any"), ("none suspended", "none"))
                if kind == "sibling_suspension"
                else (("none studied", "none"), ("any studied", "any"), ("all studied", "all"))
            )
            for label, value in labels:
                self.operator.addItem(label, value)
            self.value_stack.setCurrentWidget(self.no_value)
        else:
            self.operator_stack.setCurrentWidget(self.operator)
            for label, value in NUMERIC_OPERATOR_LABELS:
                self.operator.addItem(label, value)
            self.operator.setCurrentIndex(self.operator.findData("gte"))
            self.value_stack.setCurrentWidget(self.days)
        self.operator.setToolTip("Choose how this condition should match")
        self.fixed_operator.setToolTip("A card matches when its state is one of those selected")
        self.days.setToolTip(help_text)
        self.tags.setToolTip(help_text)

    def condition(  # noqa: PLR0911
        self,
    ) -> (
        AgeCondition
        | IntervalCondition
        | CardStateCondition
        | ReviewHistoryCondition
        | TagCondition
        | SuspensionCondition
        | SiblingSuspensionCondition
        | SiblingReviewHistoryCondition
    ):
        kind = self.kind.currentData()
        if kind == "age_first_review":
            return AgeCondition(self.days.value(), "first_review", self.operator.currentData())
        if kind == "age_card_created":
            return AgeCondition(self.days.value(), "card_created", self.operator.currentData())
        if kind == "interval":
            return IntervalCondition(self.days.value(), self.operator.currentData())
        if kind == "card_state":
            return CardStateCondition(self.states.states())
        if kind == "tags":
            return TagCondition(_split_tags(self.tags.text()), self.operator.currentData())
        if kind == "suspension":
            return SuspensionCondition(self.operator.currentData())
        if kind == "sibling_suspension":
            return SiblingSuspensionCondition(self.operator.currentData())
        if kind == "sibling_review_history":
            return SiblingReviewHistoryCondition(self.operator.currentData())
        return ReviewHistoryCondition(self.operator.currentData())

    def focus_widgets(self) -> tuple[QWidget, ...]:
        return (
            self.kind,
            self.operator,
            self.days,
            self.states,
            self.tags,
            self.remove_button,
        )
