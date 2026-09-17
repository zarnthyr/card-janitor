# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import itertools
import json
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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    Qt,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import askUser, showWarning, tooltip

from .action_row import ActionRow
from .actions import build_execution_plan
from .browsing import open_cards_in_browser
from .cleanup_preview import CleanupPreviewDialog, build_preview_rows
from .condition_row import ConditionRow
from .deck_picker import DeckPicker
from .editor_utils import _split_tags, pad_text_field
from .evaluator import evaluate_policy
from .line_numbers import LineNumberArea
from .log import error
from .models import (
    MAX_DAYS,
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
    Trigger,
    UnsuspendAction,
    parse_policy,
    policy_to_dict,
)
from .note_type_picker import NoteTypePicker
from .presentation import (
    CARD_STATES,
    NUMERIC_OPERATOR_SYMBOLS,
    TRIGGER_LABELS,
    warning_panel,
)
from .trigger_picker import TriggerPicker

if TYPE_CHECKING:
    from anki.collection import Collection


class PolicyEditorDialog(QDialog):
    def __init__(
        self,
        record: PolicyRecord | None,
        existing_ids: set[str],
        parent: QWidget,
        *,
        initial_policy: Policy | None = None,
    ) -> None:
        super().__init__(parent)
        self._manual_size = False
        self._adjusting_size = False
        self._size_ready = False
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        qconnect(self._fit_timer.timeout, self._fit_form_height)
        self._record = record
        self._existing_ids = existing_ids
        self.result_policy: Policy | None = None
        self._conditions: list[ConditionRow] = []
        self._actions: list[ActionRow] = []
        self.setWindowTitle("Add Policy" if record is None else "Edit Policy")
        self.resize(650, 580)
        raw = record.raw if record is not None and isinstance(record.raw, dict) else {}
        policy = initial_policy or (record.policy if record is not None else None)
        self._policy_id = policy.id if policy else _raw_string(raw, "id") or uuid4().hex
        if record and record.policy is None and self._policy_id.casefold() in existing_ids:
            self._policy_id = uuid4().hex
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
        pad_text_field(self.name)
        form.addRow("Name", self.name)
        self.automatic_warning = warning_panel(
            "Triggers apply this policy <b>without confirmation</b>",
            self,
        )
        form.insertRow(0, self.automatic_warning)
        raw_triggers = raw.get("triggers", [])
        selected_triggers = (
            policy.triggers
            if policy
            else tuple(
                Trigger(trigger["type"])
                for trigger in (raw_triggers if isinstance(raw_triggers, list) else [])
                if isinstance(trigger, dict)
                and isinstance(trigger.get("type"), str)
                and trigger["type"] in TRIGGER_LABELS
            )
        )
        self.triggers = TriggerPicker(selected_triggers, self)
        form.addRow("Trigger", self.triggers)
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
        self._scope_form = scope_form
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
            raw_match = _raw_string(raw, "match")
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
        if isinstance(source_conditions, AllCardsCondition) or any(
            isinstance(condition, AllCardsCondition) for condition in conditions
        ):
            self.match.setCurrentIndex(self.match.findData("all_cards"))
        conditions = tuple(
            condition for condition in conditions if not isinstance(condition, AllCardsCondition)
        )
        for condition in conditions:
            self._add_condition(condition)
        self._update_conditions_extent()
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
        self._load_actions(source_actions)
        self._update_actions_extent()
        qconnect(self.add_action_button.clicked, self._add_default_action)
        self._update_action_warnings()
        layout.addWidget(actions_group, alignment=Qt.AlignmentFlag.AlignTop)
        qconnect(self.triggers.changed, self._update_warning_panels)
        qconnect(
            self.triggers.menu().aboutToHide,
            lambda: QTimer.singleShot(0, self._update_warning_panels),
        )

        layout.addStretch()
        self._form_spacer = layout.itemAt(layout.count() - 1).spacerItem()
        self._form_groups = (general_group, scope_group, conditions_group, actions_group)
        self.json_text = QPlainTextEdit(self)
        self._line_numbers = LineNumberArea(self.json_text)
        self.json_text.setToolTip(
            "Edit one policy. Its internal ID is managed automatically and cannot be changed here."
        )
        self.json_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.json_text.hide()
        layout.addWidget(self.json_text, stretch=1)
        self._json_mode = False
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.browse_button = buttons.addButton(
            "Browse",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.browse_button.setToolTip(
            "Open cards this policy would clean up using the current settings"
        )
        self._preview_dialog: CleanupPreviewDialog | None = None
        self.preview_button = QPushButton("Preview…", self)
        self.preview_button.setAutoDefault(False)
        self.preview_button.setToolTip("Preview changes from this policy alone")
        qconnect(self.preview_button.clicked, self._preview)
        qconnect(self.finished, lambda _result: self._close_preview())
        self.json_button = buttons.addButton(
            "Edit as JSON…", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.json_button.setToolTip("Edit this policy's settings as JSON")
        qconnect(self.json_button.clicked, self._toggle_json)
        qconnect(buttons.accepted, self._accept)
        qconnect(buttons.rejected, self.reject)
        qconnect(self.browse_button.clicked, self._browse)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        buttons.removeButton(self.save_button)
        button_row = QHBoxLayout()
        button_row.addWidget(buttons, stretch=1)
        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)
        self.save_button.setDefault(True)
        qconnect(self.save_button.clicked, self._accept)
        self.cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self._update_warning_panels()
        self._update_tab_order()
        self._initial_snapshot = self._form_payload()
        if record is None and initial_policy is None:
            layout.activate()
            self.resize(self.width(), self.minimumSizeHint().height())
        QTimer.singleShot(0, self._focus_initial)
        self._size_ready = True

    def resizeEvent(self, event: object) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        if (
            self._size_ready
            and self.isVisible()
            and not self._adjusting_size
            and not self._fit_timer.isActive()
            and not self._json_mode
            and self.height() > self.minimumSizeHint().height()
        ):
            self._manual_size = True

    def _schedule_form_fit(self) -> None:
        if self._size_ready and not self._json_mode:
            self._fit_timer.start(0)

    def _fit_form_height(self) -> None:
        if self._manual_size or self.isMaximized() or self._json_mode:
            return
        self._adjusting_size = True
        try:
            self.layout().activate()
            self.resize(self.width(), self.minimumSizeHint().height())
        finally:
            self._adjusting_size = False

    def _focus_initial(self) -> None:
        self.name.setFocus()
        if self._record is not None:
            self.name.selectAll()

    def _update_tab_order(self) -> None:
        widgets: list[QWidget] = [
            self.name,
            self.triggers,
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
        widgets.extend(
            (
                self.browse_button,
                self.json_button,
                self.cancel_button,
                self.preview_button,
                self.save_button,
            )
        )
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
        index = self._conditions.index(row)
        self._conditions.remove(row)
        self.conditions_layout.removeWidget(row)
        row.deleteLater()
        self._renumber_conditions()
        self._update_condition_warning()
        self._update_conditions_extent()
        self._update_tab_order()
        if self._conditions:
            self._conditions[min(index, len(self._conditions) - 1)].kind.setFocus()
        else:
            self.add_condition_button.setFocus()

    def _renumber_conditions(self) -> None:
        for index, row in enumerate(self._conditions, start=1):
            row.number_label.setText(f"{index}.")

    def _update_conditions_extent(self) -> None:
        self.conditions_scroll.setVisible(
            bool(self._conditions) and self.match.currentData() != "all_cards"
        )
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
        self._schedule_form_fit()

    def _update_condition_warning(self, _value: object = None) -> None:
        all_cards = self.match.currentData() == "all_cards"
        self.add_condition_button.setVisible(not all_cards)
        self.conditions_scroll.setVisible(not all_cards and bool(self._conditions))
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

    def _load_actions(self, actions: tuple[Action, ...]) -> None:
        added_tags = tuple(action.tag for action in actions if isinstance(action, TagAction))
        removed_tags = tuple(
            action.tag for action in actions if isinstance(action, RemoveTagAction)
        )
        if added_tags:
            self._add_action("add_tags", tags=added_tags)
        if removed_tags:
            self._add_action("remove_tags", tags=removed_tags)
        for action in actions:
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
        index = self._actions.index(row)
        self._actions.remove(row)
        self.actions_layout.removeWidget(row)
        row.deleteLater()
        self._renumber_actions()
        self._update_action_warnings()
        self._update_actions_extent()
        self._update_tab_order()
        if self._actions:
            self._actions[min(index, len(self._actions) - 1)].kind.setFocus()
        else:
            self.add_action_button.setFocus()

    def _renumber_actions(self) -> None:
        for index, row in enumerate(self._actions, start=1):
            row.number_label.setText(f"{index}.")

    def _update_actions_extent(self) -> None:
        self.actions_scroll.setVisible(bool(self._actions))
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
        self._schedule_form_fit()

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
        if self.triggers.menu().isVisible():
            return
        self.automatic_warning.setVisible(bool(self.triggers.selected()))

    def _policy_from_form(self, *, preview: bool = False) -> Policy | None:  # noqa: PLR0911, PLR0912
        if self._json_mode:
            return self._policy_from_json(preview=preview)
        name = self.name.text().strip()
        if preview and not name:
            name = "Unnamed policy"
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
        if not simple_conditions:
            showWarning("Add a condition or choose All cards", parent=self)
            return None
        if any(isinstance(item, TagCondition) and not item.tags for item in simple_conditions):
            showWarning("Enter one or more tags for every note-tags condition", parent=self)
            return None
        condition: ConditionExpression = (
            AllConditions(simple_conditions)
            if self.match.currentData() in {"all", "all_cards"}
            else AnyConditions(simple_conditions)
        )
        kinds = [row.action_kind() for row in self._actions]
        if not kinds:
            showWarning("Add at least one action", parent=self)
            return None
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
        policy_id = self._policy_id
        normalized_id = policy_id.casefold()
        if normalized_id in self._existing_ids:
            if self._record is not None and self._record.policy is None:
                policy_id = uuid4().hex
                self._policy_id = policy_id
            else:
                showWarning("Another policy has the same internal ID", parent=self)
                return None
        policy = Policy(
            id=policy_id,
            name=name,
            triggers=self.triggers.selected(),
            scope=Scope(
                decks=decks,
                all_decks=self.decks.all_decks,
                note_types=self.note_types.selected(),
                include_suspended=self.include_suspended.isChecked(),
            ),
            conditions=condition,
            actions=tuple(actions),
        )
        try:
            return parse_policy(policy_to_dict(policy))
        except ValueError as exc:
            showWarning(str(exc), parent=self)
            return None

    def _form_payload(self) -> dict:
        conditions = (
            (AllCardsCondition(),)
            if self.match.currentData() == "all_cards"
            else tuple(row.condition() for row in self._conditions)
        )
        expression = (
            AnyConditions(conditions)
            if self.match.currentData() == "any"
            else AllConditions(conditions)
        )
        return policy_to_dict(
            Policy(
                id=self._policy_id,
                name=self.name.text(),
                triggers=self.triggers.selected(),
                scope=Scope(
                    decks=self.decks.selectors(),
                    all_decks=self.decks.all_decks,
                    note_types=self.note_types.selected(),
                    include_suspended=self.include_suspended.isChecked(),
                ),
                conditions=expression,
                actions=tuple(action for row in self._actions for action in row.actions()),
            )
        )

    def _policy_from_json(self, *, preview: bool = False) -> Policy | None:
        try:
            raw = json.loads(self.json_text.toPlainText())
            if isinstance(raw, dict):
                raw["id"] = self._policy_id
                if (
                    preview
                    and isinstance(raw.get("name", ""), str)
                    and not raw.get("name", "").strip()
                ):
                    raw["name"] = "Unnamed policy"
            return parse_policy(raw)
        except (ValueError, TypeError) as exc:
            showWarning(f"Invalid policy JSON:\n\n{exc}", parent=self)
            return None

    def _toggle_json(self) -> None:
        if self._json_mode:
            return
        self.json_text.setPlainText(json.dumps(self._form_payload(), indent=2, ensure_ascii=False))
        self._json_initial_text = self.json_text.toPlainText()
        self._set_json_mode(enabled=True)

    def _set_json_mode(self, *, enabled: bool) -> None:
        if enabled:
            self._form_size = self.size()
        self._json_mode = enabled
        self._form_spacer.changeSize(
            0,
            0,
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed if self._json_mode else QSizePolicy.Policy.Expanding,
        )
        self.layout().invalidate()
        # Hide the outgoing content before showing the other view. Otherwise
        # Qt can briefly size the window to fit both the form and JSON editor.
        if enabled:
            for group in self._form_groups:
                group.hide()
            self.json_text.show()
        else:
            self.json_text.hide()
            for group in self._form_groups:
                group.show()
        self.json_button.setVisible(not self._json_mode)
        self.save_button.setText("Apply" if self._json_mode else "Save")
        self.save_button.setToolTip(
            "Apply valid JSON to the unsaved form" if self._json_mode else "Save this policy"
        )
        self.cancel_button.setToolTip(
            "Discard JSON edits and return to the form" if self._json_mode else "Close this editor"
        )
        self.layout().activate()
        if not enabled and not self.isMaximized():
            self._adjusting_size = True
            try:
                self.resize(self._form_size)
            finally:
                self._adjusting_size = False
        (self.json_text if self._json_mode else self.name).setFocus()

    def _apply_json(self) -> None:
        policy = self._policy_from_json()
        if policy is None:
            return
        self._apply_policy(policy)
        self._set_json_mode(enabled=False)

    def _cancel_json(self) -> None:
        if self.json_text.toPlainText() != self._json_initial_text and not askUser(
            "Discard JSON edits and return to the form?",
            parent=self,
            defaultno=True,
            title="Card Janitor",
        ):
            return
        self._set_json_mode(enabled=False)

    def _apply_policy(self, policy: Policy) -> None:
        self.name.setText(policy.name)
        self.triggers.set_selected(policy.triggers)
        decks = DeckPicker(
            self._deck_names, policy.scope.selectors, self, all_decks=policy.scope.all_decks
        )
        types = NoteTypePicker(
            [item.name for item in mw.col.models.all_names_and_ids()], policy.scope.note_types, self
        )
        for old, new in ((self.decks, decks), (self.note_types, types)):
            self._scope_form.replaceWidget(old, new)
            old.hide()
            old.deleteLater()
        self.decks, self.note_types = decks, types
        for rows, row_layout in (
            (self._conditions, self.conditions_layout),
            (self._actions, self.actions_layout),
        ):
            for row in rows:
                row_layout.removeWidget(row)
                row.hide()
                row.deleteLater()
            rows.clear()
        expression = policy.conditions
        conditions = (
            expression.conditions
            if isinstance(expression, (AllConditions, AnyConditions))
            else (expression,)
        )
        all_cards = any(isinstance(condition, AllCardsCondition) for condition in conditions)
        self.match.setCurrentIndex(
            self.match.findData(
                "all_cards"
                if all_cards
                else "any"
                if isinstance(expression, AnyConditions)
                else "all"
            )
        )
        for condition in conditions:
            if not isinstance(condition, AllCardsCondition):
                self._add_condition(condition)
        self._load_actions(policy.actions)
        self._update_conditions_extent()
        self._update_actions_extent()
        self.include_suspended.setChecked(policy.scope.include_suspended)
        self._update_warning_panels()
        self._update_tab_order()

    def _has_unsaved_changes(self) -> bool:
        return self._form_payload() != self._initial_snapshot or (
            self._json_mode and self.json_text.toPlainText() != self._json_initial_text
        )

    def reject(self) -> None:
        if self._json_mode:
            self._cancel_json()
            return
        if self._has_unsaved_changes() and not askUser(
            "Discard unsaved policy changes?", parent=self, defaultno=True, title="Card Janitor"
        ):
            return
        super().reject()

    def _accept(self) -> None:
        if self._json_mode:
            self._apply_json()
            return
        policy = self._policy_from_form()
        if policy is None:
            return
        self.result_policy = policy
        self.accept()

    def _close_preview(self) -> None:
        if self._preview_dialog is not None:
            self._preview_dialog.close()
            self._preview_dialog.deleteLater()
            self._preview_dialog = None

    def _preview(self, _checked: object = None) -> None:
        self._browse(preview=True)

    def _browse(self, _checked: object = None, *, preview: bool = False) -> None:
        self._close_preview()
        policy = self._policy_from_form(preview=preview)
        if policy is None:
            return
        collection = mw.col
        profile = mw.pm.profile
        snapshot = self.json_text.toPlainText() if self._json_mode else self._form_payload()
        json_mode = self._json_mode

        def is_current() -> bool:
            return (
                mw.col is collection
                and mw.pm.profile is profile
                and json_mode == self._json_mode
                and snapshot
                == (self.json_text.toPlainText() if self._json_mode else self._form_payload())
            )

        self.browse_button.setEnabled(False)
        self.preview_button.setEnabled(False)

        def restore_button() -> None:
            if self.isVisible():
                self.browse_button.setEnabled(True)
                self.preview_button.setEnabled(True)

        def on_success(result: tuple) -> None:
            report, rows = result
            restore_button()
            if not is_current() or not self.isVisible():
                return
            if report.errors:
                showWarning("\n".join(report.errors), parent=self)
                return
            if preview:
                self._preview_dialog = CleanupPreviewDialog(
                    rows, self, policy_name=policy.name, is_current=is_current
                )
                self._preview_dialog.show()
                self._preview_dialog.raise_()
                self._preview_dialog.activateWindow()
                return
            card_ids = {card.card_id for card in report.actionable}
            if not card_ids:
                tooltip("No cards would be cleaned up by this policy", parent=self)
                return
            open_cards_in_browser(card_ids, origin=self)

        def on_failure(exception: Exception) -> None:
            restore_button()
            error("policy preview failed", reason=str(exception))
            if self.isVisible() and mw.col is collection and mw.pm.profile is profile:
                showWarning(f"Could not preview this policy:\n\n{exception}", parent=self)

        def evaluate(col: Collection) -> tuple:
            report = evaluate_policy(col, policy)
            rows = ()
            if preview and not report.errors:
                plan = build_execution_plan((report,), col)
                names = {int(deck.id): deck.name for deck in col.decks.all_names_and_ids()}
                rows = build_preview_rows(plan, (report,), names)
            return report, rows

        QueryOp(
            parent=self,
            op=evaluate,
            success=on_success,
        ).failure(on_failure).with_progress("Finding cards…").run_in_background()


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
    kind = _raw_string(raw, "type")
    days = raw.get("days")
    if not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= MAX_DAYS:
        days = 365
    if kind == "all_cards":
        return AllCardsCondition()
    if kind == "age":
        source = _raw_string(raw, "source")
        return AgeCondition(
            days,
            source if source in {"first_review", "card_created"} else "first_review",
            _raw_string(raw, "operator")
            if _raw_string(raw, "operator") in NUMERIC_OPERATOR_SYMBOLS
            else "gte",
        )
    if kind == "interval":
        operator = _raw_string(raw, "operator")
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
    if kind == "review_history" and _raw_string(raw, "operator") in {"exists", "not_exists"}:
        return ReviewHistoryCondition(raw["operator"])
    if kind == "tags" and _raw_string(raw, "operator") in {
        "contains_any",
        "contains_all",
        "contains_none",
    }:
        tags = _raw_string_list(raw, "tags")
        if tags:
            return TagCondition(tags, raw["operator"])
    if kind == "suspension" and _raw_string(raw, "operator") in {
        "is_suspended",
        "is_not_suspended",
    }:
        return SuspensionCondition(raw["operator"])
    if kind in {"sibling_suspension", "sibling_review_history"} and _raw_string(
        raw, "operator"
    ) in {
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
        kind = _raw_string(item, "type")
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
