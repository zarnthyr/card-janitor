# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from aqt.qt import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSignalBlocker,
    QSpinBox,
    QStackedWidget,
    QStandardItem,
    QStyle,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    pyqtSignal,
    qconnect,
)

from .editor_utils import (
    PopupCheckBox,
    _split_tags,
    configure_policy_row_layout,
    guard_popup_anchor,
    ignore_policy_row_size_hints,
    pad_text_field,
    show_text_from_start,
)
from .models import (
    FLAG_NAMES,
    MAX_COUNT,
    MAX_DAYS,
    MAX_EASE_PERCENT,
    AgeCondition,
    AllConditions,
    AnswerCountCondition,
    AnyConditions,
    CardFlagCondition,
    CardStateCondition,
    ConditionExpression,
    CorrectAnswerCountCondition,
    CorrectAnswerRateCondition,
    FsrsDifficultyCondition,
    FsrsRetrievabilityCondition,
    FsrsStabilityCondition,
    IntervalCondition,
    LapseCountCondition,
    OverdueCondition,
    ReviewHistoryCondition,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    Sm2EaseCondition,
    SuspensionCondition,
    TagCondition,
)
from .presentation import (
    CARD_STATES,
    CONDITION_HELP,
    NUMERIC_OPERATOR_LABELS,
)


class CardStatePicker(QComboBox):
    changed = pyqtSignal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        choices: tuple[tuple[str, str], ...] = CARD_STATES,
        noun: str = "states",
    ) -> None:
        super().__init__(parent)
        self._choices = choices
        self._noun = noun
        self._states = (choices[0][1],)
        self._checkboxes: dict[str, QCheckBox] = {}
        self._menu = QMenu(self)
        container = QWidget(self._menu)
        self._container = container
        self._anchor_guard = guard_popup_anchor(self, self._menu)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(4)
        for label, value in self._choices:
            checkbox = PopupCheckBox(label, container)
            self._checkboxes[value] = checkbox
            qconnect(
                checkbox.toggled,
                lambda checked, selected=value: self._state_toggled(selected, checked),
            )
            layout.addWidget(checkbox)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(container)
        self._menu.addAction(action)
        qconnect(self._menu.aboutToShow, self._resize_popup)
        self.addItem("")
        self.set_states(self._states)

    def showPopup(self) -> None:  # noqa: N802 - Qt virtual method
        self._menu.popup(self.mapToGlobal(self.rect().bottomLeft()))

    def _resize_popup(self) -> None:
        width = min(self.width(), self.screen().availableGeometry().width())
        panel_width = self._menu.style().pixelMetric(QStyle.PixelMetric.PM_MenuPanelWidth)
        self._menu.setFixedWidth(width)
        self._container.setFixedWidth(max(1, width - 2 * panel_width))

    def states(self) -> tuple[str, ...]:
        return self._states

    def set_states(self, states: tuple[str, ...]) -> None:
        self._states = tuple(value for _label, value in self._choices if value in states)
        blockers = [QSignalBlocker(checkbox) for checkbox in self._checkboxes.values()]
        for value, checkbox in self._checkboxes.items():
            checkbox.setChecked(value in self._states)
        del blockers
        self._update_text()

    def _update_text(self) -> None:
        selected = [label for label, value in self._choices if value in self._states]
        summary = ", ".join(selected) if selected else f"Choose {self._noun}"
        self.setItemText(0, summary)
        self.setToolTip(f"Match cards whose current {self._noun[:-1]} is any of: {summary}")

    def _state_toggled(self, state: str, _checked: bool) -> None:
        selected = tuple(
            value for _label, value in self._choices if self._checkboxes[value].isChecked()
        )
        if not selected:
            blocker = QSignalBlocker(self._checkboxes[state])
            self._checkboxes[state].setChecked(True)
            del blocker
            return
        self._states = selected
        self._update_text()
        self.changed.emit()


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
    # Each supported condition has a small initialization branch by design.

    def __init__(  # noqa: PLR0912
        self, condition: ConditionExpression | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        configure_policy_row_layout(layout)
        self.number_label = QLabel(self)
        self.number_label.setMinimumWidth(20)
        self.kind = QComboBox(self)
        for group, choices in (
            (
                "Cards",
                (
                    ("Age since first review", "age_first_review"),
                    ("Age since last review", "age_last_review"),
                    ("Age since creation", "age_card_created"),
                    ("Current interval", "interval"),
                    ("Days overdue", "overdue"),
                    ("Card state", "card_state"),
                    ("Card flag", "card_flag"),
                    ("Review history", "review_history"),
                    ("Suspension state", "suspension"),
                ),
            ),
            (
                "Review history",
                (
                    ("Answer count", "answer_count"),
                    ("Correct-answer count", "correct_answer_count"),
                    ("Correct-answer rate", "correct_answer_rate"),
                    ("Lapse count", "lapse_count"),
                ),
            ),
            ("SM-2", (("Ease", "sm2_ease"),)),
            (
                "FSRS",
                (
                    ("Stability", "fsrs_stability"),
                    ("Difficulty", "fsrs_difficulty"),
                    ("Retrievability", "fsrs_retrievability"),
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
        pad_text_field(self.days.lineEdit())
        self.days.setRange(0, MAX_DAYS)
        self.days.setSuffix(" days")
        self.days.setMinimumWidth(140)
        self.states = CardStatePicker(self)
        self.states.setMinimumWidth(140)
        flag_choices = tuple(
            ("No flag" if name == "none" else name.title(), name) for name in FLAG_NAMES
        )
        self.flags = CardStatePicker(self, choices=flag_choices, noun="flags")
        self.flags.setMinimumWidth(140)
        self.count = QSpinBox(self)
        pad_text_field(self.count.lineEdit())
        self.count.setRange(0, MAX_COUNT)
        self.count.setMinimumWidth(140)
        self.percent = QSpinBox(self)
        pad_text_field(self.percent.lineEdit())
        self.percent.setRange(0, 100)
        self.percent.setSuffix(" %")
        self.percent.setMinimumWidth(140)
        self.ease = QSpinBox(self)
        pad_text_field(self.ease.lineEdit())
        self.ease.setRange(0, MAX_EASE_PERCENT)
        self.ease.setSuffix(" %")
        self.ease.setMinimumWidth(140)
        self.tags = QLineEdit(self)
        pad_text_field(self.tags)
        self.tags.setPlaceholderText("tag1, tag2")
        self.tags.setMinimumWidth(140)
        self.value_stack = QStackedWidget(self)
        self.value_stack.setMinimumWidth(140)
        self.value_stack.addWidget(self.days)
        self.value_stack.addWidget(self.states)
        self.value_stack.addWidget(self.flags)
        self.value_stack.addWidget(self.count)
        self.value_stack.addWidget(self.percent)
        self.value_stack.addWidget(self.ease)
        self.value_stack.addWidget(self.tags)
        self.no_value = QWidget(self)
        self.value_stack.addWidget(self.no_value)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setMinimumWidth(75)
        self.remove_button.setToolTip("Remove this condition")
        ignore_policy_row_size_hints(self.kind, self.operator_stack, self.value_stack)
        layout.addWidget(self.number_label, 0, 0)
        layout.addWidget(self.kind, 0, 1)
        layout.addWidget(self.operator_stack, 0, 2)
        layout.addWidget(self.value_stack, 0, 3)
        layout.addWidget(self.remove_button, 0, 4)
        selected_states: tuple[str, ...] | None = None
        selected_flags: tuple[str, ...] | None = None
        selected_tags: tuple[str, ...] | None = None
        selected_operator: str | None = None
        if isinstance(condition, AgeCondition):
            kind = {
                "first_review": "age_first_review",
                "last_review": "age_last_review",
                "card_created": "age_card_created",
            }[condition.source]
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
        elif isinstance(condition, CardFlagCondition):
            self.kind.setCurrentIndex(self.kind.findData("card_flag"))
            selected_flags = condition.flags
        elif isinstance(
            condition,
            (AnswerCountCondition, CorrectAnswerCountCondition, LapseCountCondition),
        ):
            kind = {
                AnswerCountCondition: "answer_count",
                CorrectAnswerCountCondition: "correct_answer_count",
                LapseCountCondition: "lapse_count",
            }[type(condition)]
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self.count.setValue(condition.count)
            selected_operator = condition.operator
        elif isinstance(condition, CorrectAnswerRateCondition):
            self.kind.setCurrentIndex(self.kind.findData("correct_answer_rate"))
            self.percent.setValue(condition.percent)
            selected_operator = condition.operator
        elif isinstance(condition, OverdueCondition):
            self.kind.setCurrentIndex(self.kind.findData("overdue"))
            self.days.setValue(condition.days)
            selected_operator = condition.operator
        elif isinstance(condition, FsrsStabilityCondition):
            self.kind.setCurrentIndex(self.kind.findData("fsrs_stability"))
            self.days.setValue(condition.days)
            selected_operator = condition.operator
        elif isinstance(condition, (FsrsDifficultyCondition, FsrsRetrievabilityCondition)):
            kind = (
                "fsrs_difficulty"
                if isinstance(condition, FsrsDifficultyCondition)
                else "fsrs_retrievability"
            )
            self.kind.setCurrentIndex(self.kind.findData(kind))
            self.percent.setValue(condition.percent)
            selected_operator = condition.operator
        elif isinstance(condition, Sm2EaseCondition):
            self.kind.setCurrentIndex(self.kind.findData("sm2_ease"))
            self.ease.setValue(condition.percent)
            selected_operator = condition.operator
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
        if selected_flags is not None:
            self.flags.set_states(selected_flags)
        if selected_tags is not None:
            self.tags.setText(" ".join(selected_tags))
            show_text_from_start(self.tags)
        self.rendered_size_hint_height()

    def rendered_size_hint_height(self) -> int:
        """Return the polished natural one-line row height."""
        self.ensurePolished()
        return max(1, self.sizeHint().height())

    def _update_controls(self, _index: int = 0) -> None:
        kind = self.kind.currentData()
        help_text = CONDITION_HELP[kind]
        self.kind.setToolTip(help_text)
        self.operator.clear()
        if kind in {"card_state", "card_flag"}:
            self.operator_stack.setCurrentWidget(self.fixed_operator)
            self.value_stack.setCurrentWidget(self.states if kind == "card_state" else self.flags)
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
            operators = (
                tuple(item for item in NUMERIC_OPERATOR_LABELS if item[1] != "eq")
                if kind.startswith("fsrs_")
                else NUMERIC_OPERATOR_LABELS
            )
            for label, value in operators:
                self.operator.addItem(label, value)
            self.operator.setCurrentIndex(self.operator.findData("gte"))
            if kind in {"answer_count", "correct_answer_count", "lapse_count"}:
                self.value_stack.setCurrentWidget(self.count)
            elif kind in {
                "correct_answer_rate",
                "fsrs_difficulty",
                "fsrs_retrievability",
            }:
                self.value_stack.setCurrentWidget(self.percent)
            elif kind == "sm2_ease":
                self.value_stack.setCurrentWidget(self.ease)
            else:
                self.value_stack.setCurrentWidget(self.days)
        self.operator.setToolTip("Choose how this condition should match")
        self.fixed_operator.setToolTip("A card matches when its state is one of those selected")
        self.days.setToolTip(help_text)
        self.count.setToolTip(help_text)
        self.percent.setToolTip(help_text)
        self.ease.setToolTip(help_text)
        self.tags.setToolTip(help_text)

    def condition(  # noqa: PLR0911, PLR0912
        self,
    ) -> (
        AgeCondition
        | IntervalCondition
        | CardStateCondition
        | CardFlagCondition
        | AnswerCountCondition
        | CorrectAnswerCountCondition
        | LapseCountCondition
        | CorrectAnswerRateCondition
        | OverdueCondition
        | FsrsStabilityCondition
        | FsrsDifficultyCondition
        | FsrsRetrievabilityCondition
        | Sm2EaseCondition
        | ReviewHistoryCondition
        | TagCondition
        | SuspensionCondition
        | SiblingSuspensionCondition
        | SiblingReviewHistoryCondition
    ):
        kind = self.kind.currentData()
        if kind == "age_first_review":
            return AgeCondition(self.days.value(), "first_review", self.operator.currentData())
        if kind == "age_last_review":
            return AgeCondition(self.days.value(), "last_review", self.operator.currentData())
        if kind == "age_card_created":
            return AgeCondition(self.days.value(), "card_created", self.operator.currentData())
        if kind == "interval":
            return IntervalCondition(self.days.value(), self.operator.currentData())
        if kind == "card_state":
            return CardStateCondition(self.states.states())
        if kind == "card_flag":
            return CardFlagCondition(self.flags.states())
        if kind == "answer_count":
            return AnswerCountCondition(self.count.value(), self.operator.currentData())
        if kind == "correct_answer_count":
            return CorrectAnswerCountCondition(self.count.value(), self.operator.currentData())
        if kind == "lapse_count":
            return LapseCountCondition(self.count.value(), self.operator.currentData())
        if kind == "correct_answer_rate":
            return CorrectAnswerRateCondition(self.percent.value(), self.operator.currentData())
        if kind == "overdue":
            return OverdueCondition(self.days.value(), self.operator.currentData())
        if kind == "fsrs_stability":
            return FsrsStabilityCondition(self.days.value(), self.operator.currentData())
        if kind == "fsrs_difficulty":
            return FsrsDifficultyCondition(self.percent.value(), self.operator.currentData())
        if kind == "fsrs_retrievability":
            return FsrsRetrievabilityCondition(self.percent.value(), self.operator.currentData())
        if kind == "sm2_ease":
            return Sm2EaseCondition(self.ease.value(), self.operator.currentData())
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
            self.flags,
            self.count,
            self.percent,
            self.ease,
            self.tags,
            self.remove_button,
        )


class ConditionGroup(QWidget):
    def __init__(self, match: str = "any", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rows: list[ConditionRow] = []
        self.setObjectName("conditionGroup")
        self.setStyleSheet(
            "QWidget#conditionGroup { border: 1px solid palette(mid); border-radius: 5px;}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.match_label = QLabel("Match", self)
        header.addWidget(self.match_label)
        self.match = QComboBox(self)
        self.match.addItem("All conditions (AND)", "all")
        self.match.addItem("Any condition (OR)", "any")
        self.match.setCurrentIndex(self.match.findData(match))
        self.match.setToolTip("Require every condition in this group or allow any condition")
        header.addWidget(self.match)
        header.addStretch()
        self.add_condition_button = QPushButton("Add Condition", self)
        self.add_condition_button.setToolTip("Add another condition to this group")
        header.addWidget(self.add_condition_button)
        self.remove_button = QPushButton("Remove Group", self)
        self.remove_button.setToolTip("Remove this group and all of its conditions")
        header.addWidget(self.remove_button)
        layout.addLayout(header)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(8, 4, 0, 0)
        self.rows_layout.setSpacing(6)
        layout.addLayout(self.rows_layout)

    def expression(self) -> AllConditions | AnyConditions:
        conditions = tuple(row.condition() for row in self.rows)
        return (
            AllConditions(conditions)
            if self.match.currentData() == "all"
            else AnyConditions(conditions)
        )

    def bounded_size_hint_height(self, row_limit: int) -> int:
        """Return this group's rendered height with at most ``row_limit`` rows."""
        height = self.rendered_size_hint_height()
        hidden_rows = self.rows[row_limit:]
        if hidden_rows:
            height -= sum(row.rendered_size_hint_height() for row in hidden_rows)
            height -= self.rows_layout.spacing() * len(hidden_rows)
        return max(1, height)

    def rendered_size_hint_height(self) -> int:
        """Return a current height hint after nested row-layout changes."""
        self.ensurePolished()
        for row in self.rows:
            row.rendered_size_hint_height()
        self.rows_layout.invalidate()
        self.rows_layout.activate()
        layout = self.layout()
        layout.invalidate()
        layout.activate()
        self.updateGeometry()
        return max(1, self.sizeHint().height())

    def focus_widgets(self) -> tuple[QWidget, ...]:
        widgets: list[QWidget] = [
            self.match,
            self.add_condition_button,
            self.remove_button,
        ]
        for row in self.rows:
            widgets.extend(row.focus_widgets())
        return tuple(widgets)
