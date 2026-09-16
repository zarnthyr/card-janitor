# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import itertools
import re
from typing import TYPE_CHECKING
from uuid import uuid4

from aqt import mw
from aqt.operations import QueryOp
from aqt.qt import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSignalBlocker,
    QSpinBox,
    QStackedWidget,
    QStandardItem,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    qconnect,
)
from aqt.utils import showWarning, tooltip

from .browsing import open_cards_in_browser
from .configuration import ConfigWriteError, save_settings
from .deck_picker import DeckPicker
from .evaluator import evaluate_policy
from .log import configure as configure_logging
from .log import debug, error
from .models import (
    Action,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnyConditions,
    CardStateCondition,
    ConditionExpression,
    DeckSelector,
    DeleteCardAction,
    DeleteNoteAction,
    IntervalCondition,
    MoveAction,
    Policy,
    PolicyRecord,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    Scope,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    SuspendAction,
    SuspensionCondition,
    TagAction,
    TagCondition,
    UnsuspendAction,
)
from .note_type_picker import NoteTypePicker
from .presentation import (
    CARD_STATES,
    CONDITION_HELP,
    NUMERIC_OPERATOR_LABELS,
    NUMERIC_OPERATOR_SYMBOLS,
    mode_tooltip,
    warning_panel,
)

if TYPE_CHECKING:
    from .engine import PolicyReport
    from .models import AddonConfig


def _split_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[\s,]+", value.strip()):
        normalized = item.casefold()
        if item and normalized not in seen:
            tags.append(item)
            seen.add(normalized)
    return tuple(tags)


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
        self.days.setRange(0, 100000)
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


class ActionRow(QWidget):
    def __init__(
        self,
        deck_names: list[str],
        *,
        kind: str = "add_tags",
        tags: tuple[str, ...] = (),
        deck: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.number_label = QLabel(self)
        self.number_label.setMinimumWidth(20)
        self.kind = QComboBox(self)
        self.kind.addItem("Tags", "tags")
        self.kind.addItem("Cards", "cards")
        self.kind.addItem("Notes", "notes")
        self.kind.setMinimumWidth(105)
        self.operator = QComboBox(self)
        self.operator.setMinimumWidth(125)
        self.tags = QLineEdit(self)
        self.tags.setPlaceholderText("Separate tags with spaces or commas")
        self.tags.setToolTip("Enter one or more tags, separated by spaces or commas")
        self.deck = QComboBox(self)
        self.deck.setEditable(True)
        self.deck.addItems(deck_names)
        self.deck.setToolTip("Choose or enter the destination deck")
        self.no_value = QWidget(self)
        self.value_stack = QStackedWidget(self)
        self.value_stack.setMinimumWidth(150)
        self.value_stack.addWidget(self.tags)
        self.value_stack.addWidget(self.deck)
        self.value_stack.addWidget(self.no_value)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setMinimumWidth(75)
        self.remove_button.setToolTip("Remove this action")
        layout.addWidget(self.number_label)
        layout.addWidget(self.kind, 2)
        layout.addWidget(self.operator, 2)
        layout.addWidget(self.value_stack, 3)
        layout.addWidget(self.remove_button)
        category, operator = {
            "add_tags": ("tags", "add"),
            "remove_tags": ("tags", "remove"),
            "replace_tags": ("tags", "replace"),
            "suspend": ("cards", "suspend"),
            "unsuspend": ("cards", "unsuspend"),
            "move": ("cards", "move"),
            "delete_card": ("cards", "delete"),
            "delete_note": ("notes", "delete"),
            "suspend_note": ("notes", "suspend"),
            "unsuspend_note": ("notes", "unsuspend"),
            "move_note": ("notes", "move"),
        }.get(kind, ("tags", "add"))
        self.kind.setCurrentIndex(max(self.kind.findData(category), 0))
        self.tags.setText(" ".join(tags))
        self.deck.setCurrentText(deck)
        qconnect(self.kind.currentIndexChanged, self._update_controls)
        self._update_controls()
        self.operator.setCurrentIndex(max(self.operator.findData(operator), 0))
        qconnect(self.operator.currentIndexChanged, self._update_value)
        self._update_value()

    def _update_controls(self, _index: int = 0) -> None:
        kind = self.kind.currentData()
        self.operator.clear()
        if kind == "tags":
            self.operator.addItem("add", "add")
            self.operator.addItem("remove", "remove")
            self.operator.addItem("replace", "replace")
        else:
            self.operator.addItem("suspend", "suspend")
            self.operator.addItem("unsuspend", "unsuspend")
            self.operator.addItem("move to deck", "move")
            self.operator.addItem("delete", "delete")
        self._update_value()

    def _update_value(self, _index: int = 0) -> None:
        kind = self.action_kind()
        if kind in {"add_tags", "remove_tags", "replace_tags"}:
            self.value_stack.setCurrentWidget(self.tags)
            self.tags.setPlaceholderText(
                "Empty clears all tags" if kind == "replace_tags" else "tag1, tag2"
            )
        elif kind in {"move", "move_note"}:
            self.value_stack.setCurrentWidget(self.deck)
        else:
            self.value_stack.setCurrentWidget(self.no_value)

    def action_kind(self) -> str:
        category = self.kind.currentData()
        operator = self.operator.currentData()
        if category == "tags":
            return {"add": "add_tags", "remove": "remove_tags"}.get(operator, "replace_tags")
        if category == "cards":
            return {
                "unsuspend": "unsuspend",
                "move": "move",
                "delete": "delete_card",
            }.get(operator, "suspend")
        return {
            "unsuspend": "unsuspend_note",
            "move": "move_note",
            "delete": "delete_note",
        }.get(operator, "suspend_note")

    def actions(self) -> tuple[Action, ...]:  # noqa: PLR0911
        kind = self.action_kind()
        if kind == "add_tags":
            return tuple(TagAction(tag) for tag in _split_tags(self.tags.text()))
        if kind == "remove_tags":
            return tuple(RemoveTagAction(tag) for tag in _split_tags(self.tags.text()))
        if kind == "replace_tags":
            return (ReplaceTagsAction(_split_tags(self.tags.text())),)
        if kind in {"suspend", "suspend_note"}:
            return (SuspendAction("note" if kind == "suspend_note" else "card"),)
        if kind in {"unsuspend", "unsuspend_note"}:
            return (UnsuspendAction("note" if kind == "unsuspend_note" else "card"),)
        if kind in {"move", "move_note"}:
            return (
                MoveAction(
                    self.deck.currentText().strip(), "note" if kind == "move_note" else "card"
                ),
            )
        if kind == "delete_card":
            return (DeleteCardAction(),)
        return (DeleteNoteAction(),)

    def focus_widgets(self) -> tuple[QWidget, ...]:
        return (self.kind, self.operator, self.tags, self.deck, self.remove_button)


class PolicyEditorDialog(QDialog):
    def __init__(  # noqa: PLR0912
        self,
        record: PolicyRecord | None,
        existing_ids: set[str],
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._record = record
        self._existing_ids = existing_ids
        self.result_policy: Policy | None = None
        self._conditions: list[ConditionRow] = []
        self._actions: list[ActionRow] = []
        self.setWindowTitle("Add Policy" if record is None else "Edit Policy")
        self.resize(650, 580)
        raw = record.raw if record is not None and isinstance(record.raw, dict) else {}
        policy = record.policy if record is not None else None
        deck_names = [
            item.name
            for item in mw.col.decks.all_names_and_ids(
                skip_empty_default=True,
                include_filtered=False,
            )
        ]
        self._deck_names = deck_names

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
            "This policy runs once per day <b>without confirmation</b>",
            self,
        )
        form.insertRow(0, self.automatic_warning)
        self.mode_label = QLabel("Mode", self)
        form.addRow(self.mode_label, self.mode)
        layout.addWidget(general_group, alignment=Qt.AlignmentFlag.AlignTop)

        scope_group = QGroupBox("Scope", self)
        scope_layout = QVBoxLayout(scope_group)
        scope_layout.addWidget(QLabel("Choose where this policy applies", self))
        raw_scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        deck_values = policy.scope.selectors if policy else _raw_deck_selectors(raw_scope)
        self.decks = DeckPicker(
            deck_names,
            deck_values,
            self,
            all_decks=policy.scope.all_decks if policy else raw_scope.get("all_decks") is True,
        )
        scope_form = QFormLayout()
        scope_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scope_form.addRow("Decks", self.decks)
        self.note_types = NoteTypePicker(
            [item.name for item in mw.col.models.all_names_and_ids()],
            policy.scope.note_types
            if policy
            else (_raw_string_list(raw_scope, "note_types") if "note_types" in raw_scope else None),
            self,
        )
        scope_form.addRow("Note types", self.note_types)
        scope_layout.addLayout(scope_form)
        self.include_suspended = QCheckBox("Include suspended cards", self)
        self.include_suspended.setToolTip(
            "Allow this policy to match cards that are already suspended"
        )
        self.include_suspended.setChecked(
            policy.scope.include_suspended
            if policy
            else _raw_bool(raw_scope, "include_suspended", default=False)
        )
        scope_layout.addWidget(self.include_suspended)
        layout.addWidget(scope_group, alignment=Qt.AlignmentFlag.AlignTop)

        conditions_group = QGroupBox("Conditions", self)
        conditions_group_layout = QVBoxLayout(conditions_group)
        conditions_group_layout.addWidget(QLabel("Choose what this policy matches", self))
        match_row = QHBoxLayout()
        match_row.addWidget(QLabel("Match", self))
        self.match = QComboBox(self)
        self.match.addItem("All cards", "all_cards")
        self.match.addItem("All conditions (AND)", "all")
        self.match.addItem("Any condition (OR)", "any")
        self.match.setToolTip("Match all cards, require every condition, or allow any condition")
        match_row.addWidget(self.match)
        match_row.addStretch()
        self.add_condition_button = QPushButton("Add Condition", self)
        self.add_condition_button.setToolTip("Add another condition to this policy")
        match_row.addWidget(self.add_condition_button)
        self.creation_age_warning = warning_panel(
            "Imported cards retain their original creation dates and "
            "<b>may qualify immediately</b>",
            self,
        )
        conditions_group_layout.addWidget(self.creation_age_warning)
        self.sibling_condition_warning = warning_panel(
            "Checks <b>all sibling cards</b>, even outside scope",
            self,
        )
        conditions_group_layout.addWidget(self.sibling_condition_warning)
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
        source_conditions = policy.conditions if policy else None
        if isinstance(source_conditions, (AllConditions, AnyConditions)):
            self.match.setCurrentIndex(
                self.match.findData(
                    "all" if isinstance(source_conditions, AllConditions) else "any"
                )
            )
            conditions = source_conditions.conditions
        else:
            raw_match = raw.get("match")
            self.match.setCurrentIndex(
                self.match.findData(raw_match if raw_match in {"all", "any"} else "all")
            )
            raw_conditions = raw.get("conditions")
            condition_values = raw_conditions if isinstance(raw_conditions, list) else ()
            conditions = tuple(
                condition
                for condition in (_best_effort_condition(item) for item in condition_values)
                if condition is not None
            )
            if not conditions:
                conditions = (AgeCondition(365, "first_review", "gte"),)
        if isinstance(source_conditions, AllCardsCondition) or any(
            isinstance(condition, AllCardsCondition) for condition in conditions
        ):
            self.match.setCurrentIndex(self.match.findData("all_cards"))
        conditions = tuple(
            condition for condition in conditions if not isinstance(condition, AllCardsCondition)
        ) or (AgeCondition(365, "first_review", "gte"),)
        for condition in conditions:
            self._add_condition(condition)
        qconnect(self.add_condition_button.clicked, lambda: self._add_condition(None))
        qconnect(self.match.currentIndexChanged, self._update_condition_warning)
        self._update_condition_warning()
        layout.addWidget(conditions_group, alignment=Qt.AlignmentFlag.AlignTop)

        actions_group = QGroupBox("Actions", self)
        actions_group_layout = QVBoxLayout(actions_group)
        action_header = QHBoxLayout()
        action_header.addWidget(QLabel("Choose what happens to matches", self))
        action_header.addStretch()
        self.add_action_button = QPushButton("Add Action", self)
        self.add_action_button.setToolTip("Add another action to this policy")
        action_header.addWidget(self.add_action_button)
        actions_group_layout.addLayout(action_header)
        self.delete_warning = warning_panel(
            "Matching cards will be <b>DELETED</b> from your collection",
            self,
            destructive=True,
        )
        self.replace_tags_warning = warning_panel(
            "<b>All tags</b> on matching notes will be replaced",
            self,
            destructive=True,
        )
        self.delete_note_warning = warning_panel(
            "Matching notes and all their cards will be <b>DELETED</b> from your collection",
            self,
            destructive=True,
        )
        actions_group_layout.addWidget(self.delete_warning)
        actions_group_layout.addWidget(self.delete_note_warning)
        actions_group_layout.addWidget(self.replace_tags_warning)
        self.note_action_warning = warning_panel(
            "Note actions can affect <b>all sibling cards</b>, even outside scope",
            self,
        )
        actions_group_layout.addWidget(self.note_action_warning)
        self.actions_scroll = QScrollArea(self)
        self.actions_scroll.setWidgetResizable(True)
        self.actions_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.actions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.actions_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.actions_scroll.setMinimumHeight(60)
        self.actions_scroll.setMaximumHeight(190)
        self.actions_container = QWidget(self.actions_scroll)
        self.actions_layout = QVBoxLayout()
        self.actions_layout.setContentsMargins(8, 8, 8, 8)
        self.actions_container.setLayout(self.actions_layout)
        self.actions_scroll.setWidget(self.actions_container)
        actions_group_layout.addWidget(self.actions_scroll)
        source_actions = policy.actions if policy else _best_effort_actions(raw.get("actions"))
        added_tags = tuple(action.tag for action in source_actions if isinstance(action, TagAction))
        removed_tags = tuple(
            action.tag for action in source_actions if isinstance(action, RemoveTagAction)
        )
        if added_tags:
            self._add_action("add_tags", tags=added_tags)
        if removed_tags:
            self._add_action("remove_tags", tags=removed_tags)
        for action in source_actions:
            if isinstance(action, (TagAction, RemoveTagAction)):
                continue
            if isinstance(action, ReplaceTagsAction):
                self._add_action("replace_tags", tags=action.tags)
            elif isinstance(action, SuspendAction):
                self._add_action("suspend_note" if action.target == "note" else "suspend")
            elif isinstance(action, UnsuspendAction):
                self._add_action("unsuspend_note" if action.target == "note" else "unsuspend")
            elif isinstance(action, MoveAction):
                self._add_action(
                    "move_note" if action.target == "note" else "move", deck=action.deck
                )
            elif isinstance(action, DeleteCardAction):
                self._add_action("delete_card")
            elif isinstance(action, DeleteNoteAction):
                self._add_action("delete_note")
        if not self._actions:
            self._add_action("add_tags")
        qconnect(self.add_action_button.clicked, self._add_default_action)
        self._update_action_warnings()
        layout.addWidget(actions_group, alignment=Qt.AlignmentFlag.AlignTop)
        qconnect(self.mode.currentIndexChanged, self._update_warning_panels)

        layout.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.browse_button = buttons.addButton(
            "Browse",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.browse_button.setToolTip(
            "Open cards this policy would clean up using the current unsaved settings"
        )
        qconnect(buttons.accepted, self._accept)
        qconnect(buttons.rejected, self.reject)
        qconnect(self.browse_button.clicked, self._browse)
        layout.addWidget(buttons)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        qconnect(self.mode.currentIndexChanged, self._update_mode_tooltip)
        self._update_mode_tooltip()
        self._update_warning_panels()
        self._update_tab_order()
        QTimer.singleShot(0, self._focus_initial)

    def _focus_initial(self) -> None:
        self.name.setFocus()
        if self._record is not None:
            self.name.selectAll()

    def _update_mode_tooltip(self, _index: int = 0) -> None:
        help_text = mode_tooltip(self.mode.currentData())
        self.mode_label.setToolTip(help_text)
        self.mode.setToolTip(help_text)

    def _update_tab_order(self) -> None:
        widgets: list[QWidget] = [
            self.name,
            self.mode,
            self.decks,
            self.note_types,
            self.include_suspended,
            self.match,
            self.add_condition_button,
        ]
        for row in self._conditions:
            widgets.extend(row.focus_widgets())
        widgets.append(self.add_action_button)
        for row in self._actions:
            widgets.extend(row.focus_widgets())
        widgets.extend((self.browse_button, self.save_button, self.cancel_button))
        for current, following in itertools.pairwise(widgets):
            QWidget.setTabOrder(current, following)

    def _add_condition(self, condition: ConditionExpression | None) -> None:
        row = ConditionRow(condition, self)
        self._conditions.append(row)
        self.conditions_layout.addWidget(row, alignment=Qt.AlignmentFlag.AlignTop)
        qconnect(row.remove_button.clicked, lambda: self._remove_condition(row))
        qconnect(row.kind.currentIndexChanged, self._update_condition_warning)
        qconnect(row.operator.currentIndexChanged, self._update_condition_warning)
        self._renumber_conditions()
        self._update_condition_warning()
        self._update_conditions_extent()
        if hasattr(self, "save_button"):
            self._update_tab_order()
            row.kind.setFocus()

    def _remove_condition(self, row: ConditionRow) -> None:
        if len(self._conditions) == 1:
            showWarning("A policy must have at least one condition", parent=self)
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
        visible_height = min(height, two_rows_height)
        self.conditions_scroll.setMinimumHeight(visible_height)
        self.conditions_scroll.setMaximumHeight(visible_height)

    def _update_condition_warning(self, _value: object = None) -> None:
        all_cards = self.match.currentData() == "all_cards"
        self.add_condition_button.setVisible(not all_cards)
        self.conditions_scroll.setVisible(not all_cards)
        show_creation_warning = not all_cards and any(
            row.kind.currentData() == "age_card_created" for row in self._conditions
        )
        self.creation_age_warning.setVisible(show_creation_warning)
        self.sibling_condition_warning.setVisible(
            not all_cards
            and any(
                row.kind.currentData() in {"sibling_suspension", "sibling_review_history"}
                for row in self._conditions
            )
        )
        if not all_cards and any(
            (
                row.kind.currentData() == "suspension"
                and row.operator.currentData() == "is_suspended"
            )
            or (
                row.kind.currentData() == "sibling_suspension"
                and row.operator.currentData() == "all"
            )
            for row in self._conditions
        ):
            self.include_suspended.setChecked(True)

    def _add_action(
        self,
        kind: str,
        *,
        tags: tuple[str, ...] = (),
        deck: str = "",
    ) -> None:
        row = ActionRow(
            self._deck_names,
            kind=kind,
            tags=tags,
            deck=deck,
            parent=self,
        )
        self._actions.append(row)
        self.actions_layout.addWidget(row, alignment=Qt.AlignmentFlag.AlignTop)
        qconnect(row.remove_button.clicked, lambda: self._remove_action(row))
        qconnect(row.kind.currentIndexChanged, self._update_action_warnings)
        qconnect(row.operator.currentIndexChanged, self._update_action_warnings)
        self._renumber_actions()
        self._update_action_warnings()
        self._update_actions_extent()
        if hasattr(self, "save_button"):
            self._update_tab_order()
            row.kind.setFocus()

    def _add_default_action(self, _checked: object = None) -> None:
        used = {row.action_kind() for row in self._actions}
        candidates = [
            "add_tags",
            "remove_tags",
            "suspend",
            "move",
            "replace_tags",
            "unsuspend",
            "delete_card",
            "delete_note",
            "suspend_note",
            "unsuspend_note",
            "move_note",
        ]
        if "replace_tags" in used:
            candidates = [kind for kind in candidates if kind not in {"add_tags", "remove_tags"}]
        if {"add_tags", "remove_tags"} & used:
            candidates = [kind for kind in candidates if kind != "replace_tags"]
        if {"suspend", "suspend_note"} & used:
            candidates = [
                kind for kind in candidates if kind not in {"unsuspend", "unsuspend_note"}
            ]
        if {"unsuspend", "unsuspend_note"} & used:
            candidates = [kind for kind in candidates if kind not in {"suspend", "suspend_note"}]
        kind = next((candidate for candidate in candidates if candidate not in used), "add_tags")
        self._add_action(kind)

    def _remove_action(self, row: ActionRow) -> None:
        if len(self._actions) == 1:
            showWarning("A policy must have at least one action", parent=self)
            return
        index = self._actions.index(row)
        self._actions.remove(row)
        self.actions_layout.removeWidget(row)
        row.deleteLater()
        self._renumber_actions()
        self._update_action_warnings()
        self._update_actions_extent()
        self._update_tab_order()
        self._actions[min(index, len(self._actions) - 1)].kind.setFocus()

    def _renumber_actions(self) -> None:
        for index, row in enumerate(self._actions, start=1):
            row.number_label.setText(f"{index}.")

    def _update_actions_extent(self) -> None:
        spacing = max(0, self.actions_layout.spacing())
        row_heights = [max(1, row.sizeHint().height()) for row in self._actions]
        height = sum(row_heights) + spacing * max(0, len(row_heights) - 1)
        margins = self.actions_layout.contentsMargins()
        height += margins.top() + margins.bottom()
        self.actions_container.setMinimumHeight(height)
        two_rows_height = max(row_heights, default=40) * 2 + spacing
        two_rows_height += margins.top() + margins.bottom()
        visible_height = min(height, two_rows_height)
        self.actions_scroll.setMinimumHeight(visible_height)
        self.actions_scroll.setMaximumHeight(visible_height)

    def _update_action_warnings(self, _value: object = None) -> None:
        kinds = {row.action_kind() for row in self._actions}
        self.delete_warning.setVisible("delete_card" in kinds)
        self.delete_note_warning.setVisible("delete_note" in kinds)
        self.replace_tags_warning.setVisible("replace_tags" in kinds)
        self.note_action_warning.setVisible(
            bool({"suspend_note", "unsuspend_note", "move_note"} & kinds)
        )
        self.add_action_button.setEnabled(not bool({"delete_card", "delete_note"} & kinds))
        if "unsuspend" in kinds:
            self.include_suspended.setChecked(True)

    def _update_warning_panels(self, _value: object = None) -> None:
        self.automatic_warning.setVisible(self.mode.currentData() == "automatic")

    def _policy_from_form(self) -> Policy | None:  # noqa: PLR0911, PLR0912
        name = self.name.text().strip()
        decks = self.decks.selectors()
        if not name:
            showWarning("Enter a policy name", parent=self)
            return None
        if not decks and not self.decks.all_decks:
            showWarning("Enter at least one deck", parent=self)
            return None
        if self.note_types.selected() == ():
            showWarning("Choose at least one note type", parent=self)
            return None
        simple_conditions = (
            (AllCardsCondition(),)
            if self.match.currentData() == "all_cards"
            else tuple(row.condition() for row in self._conditions)
        )
        if any(isinstance(item, TagCondition) and not item.tags for item in simple_conditions):
            showWarning("Enter one or more tags for every note-tags condition", parent=self)
            return None
        condition: ConditionExpression = (
            AllConditions(simple_conditions)
            if self.match.currentData() in {"all", "all_cards"}
            else AnyConditions(simple_conditions)
        )
        kinds = [row.action_kind() for row in self._actions]
        if len(kinds) != len(set(kinds)):
            showWarning("Each action type can only be selected once", parent=self)
            return None
        if {"delete_card", "delete_note"} & set(kinds) and len(kinds) != 1:
            showWarning("Deletion must be the only action", parent=self)
            return None
        if {"suspend", "suspend_note"} & set(kinds) and {"unsuspend", "unsuspend_note"} & set(
            kinds
        ):
            showWarning("Suspend and unsuspend cannot be combined", parent=self)
            return None
        if "replace_tags" in kinds and {
            "add_tags",
            "remove_tags",
        } & set(kinds):
            showWarning(
                "Replacing tags cannot be combined with adding or removing tags", parent=self
            )
            return None
        for row in self._actions:
            kind = row.action_kind()
            if kind in {"add_tags", "remove_tags"} and not _split_tags(row.tags.text()):
                showWarning(
                    f"Enter one or more tags for action {row.number_label.text()}", parent=self
                )
                return None
            if kind in {"move", "move_note"} and not row.deck.currentText().strip():
                showWarning("Choose a destination deck for the move action", parent=self)
                return None
        actions = [action for row in self._actions for action in row.actions()]
        if (
            len({action.deck.casefold() for action in actions if isinstance(action, MoveAction)})
            > 1
        ):
            showWarning("A policy cannot have multiple move destinations", parent=self)
            return None
        added = {action.tag.casefold() for action in actions if isinstance(action, TagAction)}
        removed = {
            action.tag.casefold() for action in actions if isinstance(action, RemoveTagAction)
        }
        if added & removed:
            showWarning("The same tag cannot be added and removed", parent=self)
            return None
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
                showWarning("Another policy has the same internal ID", parent=self)
                return None
        return Policy(
            id=policy_id,
            name=name,
            mode=self.mode.currentData(),
            scope=Scope(
                decks=decks,
                all_decks=self.decks.all_decks,
                note_types=self.note_types.selected(),
                include_suspended=self.include_suspended.isChecked(),
            ),
            conditions=condition,
            actions=tuple(actions),
        )

    def _accept(self) -> None:
        policy = self._policy_from_form()
        if policy is None:
            return
        self.result_policy = policy
        self.accept()

    def _browse(self, _checked: object = None) -> None:
        policy = self._policy_from_form()
        if policy is None:
            return
        collection = mw.col
        self.browse_button.setEnabled(False)

        def restore_button() -> None:
            if self.isVisible():
                self.browse_button.setEnabled(True)

        def on_success(report: PolicyReport) -> None:
            restore_button()
            if mw.col is not collection or not self.isVisible():
                return
            if report.errors:
                showWarning("\n".join(report.errors), parent=self)
                return
            card_ids = {card.card_id for card in report.actionable}
            if not card_ids:
                tooltip("No cards would be cleaned up by this policy", parent=self)
                return
            browser = open_cards_in_browser(card_ids)
            qconnect(browser.destroyed, lambda _object=None: self._restore_after_browse())

        def on_failure(exception: Exception) -> None:
            restore_button()
            error("policy preview failed", reason=str(exception))
            if self.isVisible():
                showWarning(f"Could not preview this policy:\n\n{exception}", parent=self)

        QueryOp(
            parent=self,
            op=lambda col: evaluate_policy(col, policy),
            success=on_success,
        ).failure(on_failure).with_progress("Finding cards…").run_in_background()

    def _restore_after_browse(self) -> None:
        if self.isVisible():
            self.raise_()
            self.activateWindow()


def _raw_deck_selectors(raw: dict) -> tuple[DeckSelector, ...]:
    values = raw.get("decks", [])
    if not isinstance(values, list):
        return ()
    return tuple(
        DeckSelector(value["deck"].strip(), _raw_bool(value, "include_subdecks", default=False))
        for value in values
        if isinstance(value, dict) and isinstance(value.get("deck"), str) and value["deck"].strip()
    )


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


def _best_effort_condition(raw: object) -> ConditionExpression | None:  # noqa: PLR0911
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    days = raw.get("days")
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        days = 365
    if kind == "all_cards":
        return AllCardsCondition()
    if kind == "age":
        source = raw.get("source")
        return AgeCondition(
            days,
            source if source in {"first_review", "card_created"} else "first_review",
            raw.get("operator") if raw.get("operator") in NUMERIC_OPERATOR_SYMBOLS else "gte",
        )
    if kind == "interval":
        operator = raw.get("operator")
        return IntervalCondition(days, operator if operator in NUMERIC_OPERATOR_SYMBOLS else "gte")
    if kind == "card_state":
        raw_states = raw.get("states")
        states = tuple(
            value
            for _label, value in CARD_STATES
            if isinstance(raw_states, list) and value in raw_states
        )
        if states:
            return CardStateCondition(states)
    if kind == "review_history" and raw.get("operator") in {"exists", "not_exists"}:
        return ReviewHistoryCondition(raw["operator"])
    if kind == "tags" and raw.get("operator") in {
        "contains_any",
        "contains_all",
        "contains_none",
    }:
        tags = _raw_string_list(raw, "tags")
        if tags:
            return TagCondition(tags, raw["operator"])
    if kind == "suspension" and raw.get("operator") in {
        "is_suspended",
        "is_not_suspended",
    }:
        return SuspensionCondition(raw["operator"])
    if kind in {"sibling_suspension", "sibling_review_history"} and raw.get("operator") in {
        "all",
        "any",
        "none",
    }:
        return (
            SiblingSuspensionCondition(raw["operator"])
            if kind == "sibling_suspension"
            else SiblingReviewHistoryCondition(raw["operator"])
        )
    return None


def _best_effort_actions(raw: object) -> tuple[Action, ...]:
    if not isinstance(raw, list):
        return ()
    actions: list[Action] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind in {"tag", "add_tags"} and isinstance(item.get("tags"), list):
            actions.extend(TagAction(tag) for tag in item["tags"] if isinstance(tag, str))
        elif kind == "remove_tags" and isinstance(item.get("tags"), list):
            actions.extend(RemoveTagAction(tag) for tag in item["tags"] if isinstance(tag, str))
        elif kind == "replace_tags" and isinstance(item.get("tags"), list):
            actions.append(
                ReplaceTagsAction(tuple(tag for tag in item["tags"] if isinstance(tag, str)))
            )
        elif kind in {"suspend", "suspend_note"}:
            actions.append(SuspendAction("note" if kind == "suspend_note" else "card"))
        elif kind in {"unsuspend", "unsuspend_note"}:
            actions.append(UnsuspendAction("note" if kind == "unsuspend_note" else "card"))
        elif kind in {"move", "move_note"} and isinstance(item.get("deck"), str):
            actions.append(MoveAction(item["deck"], "note" if kind == "move_note" else "card"))
        elif kind == "delete_card":
            actions.append(DeleteCardAction())
        elif kind == "delete_note":
            actions.append(DeleteNoteAction())
    return tuple(actions)


class SettingsDialog(QDialog):
    def __init__(self, config: AddonConfig, parent: QWidget) -> None:
        super().__init__(parent)
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
        self.debug_logging.setToolTip("Print policy evaluation details to Anki's terminal output")
        troubleshooting_layout.addWidget(self.debug_logging)
        layout.addWidget(troubleshooting_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        qconnect(buttons.accepted, self._save)
        qconnect(buttons.rejected, self.reject)
        layout.addWidget(buttons)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        QWidget.setTabOrder(self.notify, self.debug_logging)
        QWidget.setTabOrder(self.debug_logging, save_button)
        QWidget.setTabOrder(save_button, cancel_button)
        QTimer.singleShot(0, self.notify.setFocus)

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
