# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from html import escape
from typing import TYPE_CHECKING
from uuid import uuid4

from aqt import mw
from aqt.operations import QueryOp
from aqt.qt import (
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
    QStyle,
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
from .editor_utils import _split_tags, pad_text_field, show_text_from_start
from .evaluator import evaluate_policy, validate_policy_references
from .line_numbers import LineNumberArea
from .log import error
from .models import (
    FLAG_NAMES,
    MAX_COUNT,
    MAX_DAYS,
    MAX_EASE_PERCENT,
    Action,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnswerCountCondition,
    AnyConditions,
    CardFlagCondition,
    CardStateCondition,
    ClearFlagAction,
    ConditionExpression,
    CorrectAnswerCountCondition,
    CorrectAnswerRateCondition,
    DeckSelector,
    DeleteCardAction,
    DeleteNoteAction,
    FsrsDifficultyCondition,
    FsrsRetrievabilityCondition,
    FsrsStabilityCondition,
    IntervalCondition,
    LapseCountCondition,
    MoveAction,
    NoteTypeSelector,
    OverdueCondition,
    Policy,
    PolicyRecord,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    Scope,
    SetFlagAction,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    Sm2EaseCondition,
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
        self._operation_running = False
        self._record = record
        self._existing_ids = existing_ids
        self.result_policy: Policy | None = None
        self._conditions: list[ConditionRow] = []
        self._actions: list[ActionRow] = []
        self.setWindowTitle("Add Policy" if record is None else "Edit Policy")
        self.resize(650, 600)
        raw = record.raw if record is not None and isinstance(record.raw, dict) else {}
        policy = initial_policy or (record.policy if record is not None else None)
        self._policy_id = policy.id if policy else _raw_string(raw, "id") or str(uuid4())
        if record and record.policy is None and self._policy_id.casefold() in existing_ids:
            self._policy_id = str(uuid4())
        deck_names = [
            item.name
            for item in mw.col.decks.all_names_and_ids(
                skip_empty_default=True,
                include_filtered=False,
            )
        ]
        self._deck_names = deck_names
        self._note_types = _collection_note_types()

        layout = QVBoxLayout(self)
        layout.setSpacing(18)
        initial_errors: tuple[str, ...] = ()
        if record is not None:
            initial_errors = tuple(str(issue) for issue in record.issues)
            if record.policy is not None:
                initial_errors += validate_policy_references(mw.col, record.policy)
        self._displayed_error_title = ""
        self._displayed_errors: tuple[str, ...] = ()
        self._top_errors: tuple[str, ...] = ()
        self._section_errors: dict[str, tuple[str, ...]] = {
            "general": (),
            "scope": (),
            "conditions": (),
            "actions": (),
        }
        self._pre_json_errors: tuple[str, tuple[str, ...]] = ("", ())
        self.policy_error_warning = warning_panel("", self, kind="error")
        self.policy_error_warning.hide()
        layout.addWidget(self.policy_error_warning, alignment=Qt.AlignmentFlag.AlignTop)
        general_group = QGroupBox("General", self)
        form = QFormLayout(general_group)
        self._general_form = form
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.name = QLineEdit(policy.name if policy else _raw_string(raw, "name"), self)
        pad_text_field(self.name)
        show_text_from_start(self.name)
        qconnect(self.name.editingFinished, self._schedule_button_state)
        form.addRow("Name", self.name)
        self.automatic_warning = warning_panel(
            "This policy is applied <b>without confirmation</b>",
            self,
        )
        form.insertRow(0, self.automatic_warning)
        self.general_error_warning = warning_panel("", self, kind="error")
        self.general_error_warning.hide()
        self._general_error_row_visible = False
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
        self.scope_error_warning = warning_panel("", self, kind="error")
        self.scope_error_warning.hide()
        scope_layout.addWidget(self.scope_error_warning)
        raw_scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        deck_values = policy.scope.selectors if policy else _raw_deck_selectors(raw_scope)
        self.decks = DeckPicker(
            deck_names,
            deck_values,
            self,
            all_decks=policy.scope.all_decks if policy else raw_scope.get("all_decks") is True,
        )
        qconnect(self.decks.changed, self._schedule_button_state)
        scope_form = QFormLayout()
        self._scope_form = scope_form
        scope_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        scope_form.addRow("Decks", self.decks)
        self.note_types = NoteTypePicker(
            self._note_types,
            policy.scope.note_types
            if policy
            else (_raw_note_type_selectors(raw_scope) if "note_types" in raw_scope else None),
            self,
        )
        qconnect(self.note_types.changed, self._schedule_button_state)
        scope_form.addRow("Notes", self.note_types)
        scope_layout.addLayout(scope_form)
        layout.addWidget(scope_group, alignment=Qt.AlignmentFlag.AlignTop)

        conditions_group = QGroupBox("Conditions", self)
        conditions_group_layout = QVBoxLayout(conditions_group)
        conditions_group_layout.addWidget(QLabel("Choose what this policy matches", self))
        self.conditions_error_warning = warning_panel("", self, kind="error")
        self.conditions_error_warning.hide()
        conditions_group_layout.addWidget(self.conditions_error_warning)
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
        qconnect(self.add_condition_button.clicked, self._add_default_condition)
        qconnect(self.match.currentIndexChanged, self._match_changed)
        self._update_condition_warning()
        layout.addWidget(conditions_group, alignment=Qt.AlignmentFlag.AlignTop)

        actions_group = QGroupBox("Actions", self)
        actions_group_layout = QVBoxLayout(actions_group)
        action_header = QHBoxLayout()
        action_header.addWidget(QLabel("Choose what happens to matches", self))
        action_header.addStretch()
        self.add_action_button = QPushButton("Add Action", self)
        self.add_action_button.setToolTip("Add another action to this policy")
        add_button_width = max(
            self.add_condition_button.sizeHint().width(),
            self.add_action_button.sizeHint().width(),
        )
        self.add_condition_button.setMinimumWidth(add_button_width)
        self.add_action_button.setMinimumWidth(add_button_width)
        action_header.addWidget(self.add_action_button)
        actions_group_layout.addLayout(action_header)
        self.actions_error_warning = warning_panel("", self, kind="error")
        self.actions_error_warning.hide()
        actions_group_layout.addWidget(self.actions_error_warning)
        self.delete_warning = warning_panel(
            "Matching cards will be <b>DELETED</b> from your collection",
            self,
            kind="error",
        )
        self.replace_tags_warning = warning_panel(
            "<b>All tags</b> on matching notes will be replaced",
            self,
            kind="error",
        )
        self.delete_note_warning = warning_panel(
            "Matching notes and all their cards will be <b>DELETED</b> from your collection",
            self,
            kind="error",
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
        self._form_groups = (
            self.policy_error_warning,
            general_group,
            scope_group,
            conditions_group,
            actions_group,
        )
        self.json_text = QPlainTextEdit(self)
        self._line_numbers = LineNumberArea(self.json_text)
        self.json_text.setToolTip(
            "Edit one policy. Its internal ID is managed automatically and cannot be changed here."
        )
        self.json_text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        qconnect(self.json_text.textChanged, self._schedule_button_state)
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
        self.browse_button.setToolTip("Open cards matching the current scope and conditions")
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
        self._set_policy_errors(initial_errors)
        self._update_action_buttons()
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

    def _schedule_button_state(self, _value: object = None) -> None:
        if hasattr(self, "browse_button"):
            self._update_action_buttons()
            self._clear_resolved_errors()

    def _update_action_buttons(self) -> None:
        if not hasattr(self, "browse_button"):
            return
        self.browse_button.setEnabled(not self._operation_running)
        self.preview_button.setEnabled(not self._operation_running)

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
        for widget in widgets:
            widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for current, following in itertools.pairwise(widgets):
            QWidget.setTabOrder(current, following)

    def _clear_resolved_errors(self) -> None:
        if self._json_mode or not self._displayed_errors:
            return
        current_errors: list[str] = []
        policy = self._policy_from_form(show_errors=False, error_sink=current_errors)
        if policy is not None:
            current_errors.extend(validate_policy_references(mw.col, policy))
        else:
            browse_policy = self._policy_from_form(
                browse=True,
                show_errors=False,
                error_sink=current_errors,
            )
            if browse_policy is None:
                # An incomplete condition/action can temporarily prevent any
                # meaningful reference check. Preserve existing messages until
                # the form is complete enough to prove one has been resolved.
                return
            current_errors.extend(validate_policy_references(mw.col, browse_policy))
        retained = tuple(
            error
            for error in self._displayed_errors
            if error in current_errors
            or (policy is None and ("policies[" in error or error.startswith("destination deck ")))
        )
        if retained != self._displayed_errors:
            self._set_policy_errors(retained, title=self._displayed_error_title)

    def _add_condition(self, condition: ConditionExpression | None) -> None:
        row = ConditionRow(condition, self)
        self._conditions.append(row)
        self.conditions_layout.addWidget(row, alignment=Qt.AlignmentFlag.AlignTop)
        qconnect(row.remove_button.clicked, lambda: self._remove_condition(row))
        qconnect(row.kind.currentIndexChanged, self._update_condition_warning)
        qconnect(row.operator.currentIndexChanged, self._update_condition_warning)
        for signal in (
            row.kind.currentIndexChanged,
            row.operator.currentIndexChanged,
            row.states.changed,
            row.flags.changed,
            row.tags.editingFinished,
        ):
            qconnect(signal, self._schedule_button_state)
        self._renumber_conditions()
        self._update_condition_warning()
        self._update_conditions_extent()
        if hasattr(self, "save_button"):
            self._update_tab_order()
            row.kind.setFocus()
            self._schedule_button_state()

    def _add_default_condition(self, _checked: object = None) -> None:
        if self.match.currentData() == "all_cards":
            self.match.setCurrentIndex(self.match.findData("all"))
        self._add_condition(None)

    def _match_changed(self, _value: object = None) -> None:
        if self.match.currentData() == "all_cards" and self._conditions:
            for row in self._conditions:
                self.conditions_layout.removeWidget(row)
                row.deleteLater()
            self._conditions.clear()
            self._renumber_conditions()
        self._update_condition_warning()
        self._update_conditions_extent()
        self._update_tab_order()
        self._schedule_button_state()

    def _remove_condition(self, row: ConditionRow) -> None:
        index = self._conditions.index(row)
        self._conditions.remove(row)
        self.conditions_layout.removeWidget(row)
        row.deleteLater()
        self._renumber_conditions()
        self._update_condition_warning()
        self._update_conditions_extent()
        self._update_tab_order()
        self._schedule_button_state()
        if self._conditions:
            self._conditions[min(index, len(self._conditions) - 1)].kind.setFocus()
        else:
            self.add_condition_button.setFocus()

    def _renumber_conditions(self) -> None:
        for index, row in enumerate(self._conditions, start=1):
            row.number_label.setText(f"{index}.")

    def _update_conditions_extent(self) -> None:
        # Adding a condition is useful even when the policy currently matches
        # all cards: the click changes it to AND and creates the first row.
        self.add_condition_button.setVisible(True)
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
        self._fit_row_scroll_width(
            self.conditions_scroll,
            self.conditions_layout,
            self._conditions,
        )
        self._schedule_form_fit()

    def _update_condition_warning(self, _value: object = None) -> None:
        all_cards = self.match.currentData() == "all_cards"
        self.add_condition_button.setVisible(True)
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
        self._render_section_error_panels()

    def _has_incompatible_schedulers(self) -> bool:
        if self.match.currentData() == "all_cards":
            return False
        kinds = {row.kind.currentData() for row in self._conditions}
        return "sm2_ease" in kinds and bool(
            {"fsrs_stability", "fsrs_difficulty", "fsrs_retrievability"} & kinds
        )

    def _add_action(
        self,
        kind: str,
        *,
        tags: tuple[str, ...] = (),
        deck: str = "",
        flag: str = "red",
    ) -> None:
        row = ActionRow(
            self._deck_names,
            kind=kind,
            tags=tags,
            deck=deck,
            flag=flag,
            parent=self,
        )
        self._actions.append(row)
        self.actions_layout.addWidget(row, alignment=Qt.AlignmentFlag.AlignTop)
        qconnect(row.remove_button.clicked, lambda: self._remove_action(row))
        qconnect(row.kind.currentIndexChanged, self._update_action_warnings)
        for signal in (
            row.kind.currentIndexChanged,
            row.tags.editingFinished,
            row.deck.activated,
            row.deck.lineEdit().editingFinished,
            row.flag.currentIndexChanged,
        ):
            qconnect(signal, self._schedule_button_state)
        self._renumber_actions()
        self._update_action_warnings()
        self._update_actions_extent()
        if hasattr(self, "save_button"):
            self._update_tab_order()
            row.kind.setFocus()
            self._schedule_button_state()

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
            elif isinstance(action, SetFlagAction):
                self._add_action("set_flag", flag=action.flag)
            elif isinstance(action, ClearFlagAction):
                self._add_action("clear_flag")

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
            "set_flag",
            "clear_flag",
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
        if "set_flag" in used:
            candidates = [kind for kind in candidates if kind != "clear_flag"]
        if "clear_flag" in used:
            candidates = [kind for kind in candidates if kind != "set_flag"]
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
        self._schedule_button_state()
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
        self._fit_row_scroll_width(
            self.actions_scroll,
            self.actions_layout,
            self._actions,
        )
        self._schedule_form_fit()

    @staticmethod
    def _fit_row_scroll_width(
        scroll: QScrollArea,
        rows_layout: QVBoxLayout,
        rows: list[ConditionRow] | list[ActionRow],
    ) -> None:
        if not rows:
            scroll.setMinimumWidth(0)
        else:
            margins = rows_layout.contentsMargins()
            row_width = max(row.minimumSizeHint().width() for row in rows)
            scrollbar_width = scroll.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
            scroll.setMinimumWidth(row_width + margins.left() + margins.right() + scrollbar_width)
        # Horizontal scrolling is deliberately unavailable. Reset offsets that
        # Qt may retain while rows are added, removed, or the window is resized.
        scroll.horizontalScrollBar().setValue(0)

    def _update_action_warnings(self, _value: object = None) -> None:
        kinds = {row.action_kind() for row in self._actions}
        self.delete_warning.setVisible("delete_card" in kinds)
        self.delete_note_warning.setVisible("delete_note" in kinds)
        self.replace_tags_warning.setVisible("replace_tags" in kinds)
        self.note_action_warning.setVisible(
            bool({"suspend_note", "unsuspend_note", "move_note"} & kinds)
        )
        self.add_action_button.setEnabled(not bool({"delete_card", "delete_note"} & kinds))

    def _update_warning_panels(self, _value: object = None) -> None:
        if self.triggers.menu().isVisible():
            return
        self.automatic_warning.setVisible(bool(self.triggers.selected()))

    def _set_policy_errors(
        self,
        errors: tuple[str, ...],
        *,
        title: str = "This policy has errors",
        place_in_sections: bool = True,
    ) -> None:
        errors = tuple(dict.fromkeys(errors))
        self._displayed_error_title = title if errors else ""
        self._displayed_errors = errors
        grouped: dict[str, list[str]] = {
            "general": [],
            "scope": [],
            "conditions": [],
            "actions": [],
            "top": [],
        }
        for item in errors:
            section = self._error_section(item) if place_in_sections else "top"
            grouped[section].append(item)
        self._section_errors = {
            section: tuple(grouped[section])
            for section in ("general", "scope", "conditions", "actions")
        }
        self._render_section_error_panels()
        top_errors = tuple(grouped["top"])
        self._top_errors = top_errors
        if top_errors:
            details = "" if not top_errors else "<br>" + self._format_error_lines(top_errors)
            self.policy_error_warning.setText(f"⚠ <b>{escape(title, quote=False)}</b>{details}")
        else:
            self.policy_error_warning.setText("")
        self.policy_error_warning.setVisible(bool(top_errors))
        if hasattr(self, "save_button"):
            self._schedule_form_fit()

    def _render_section_error_panels(self) -> None:
        scheduler_mix = "FSRS and SM-2 conditions cannot be used together"
        for section, panel in (
            ("general", self.general_error_warning),
            ("scope", self.scope_error_warning),
            ("conditions", self.conditions_error_warning),
            ("actions", getattr(self, "actions_error_warning", None)),
        ):
            if panel is None:
                continue
            section_errors = self._section_errors[section]
            if section == "conditions" and self._has_incompatible_schedulers():
                section_errors = tuple(dict.fromkeys((*section_errors, scheduler_mix)))
            if section_errors:
                panel.setText(
                    "⚠ "
                    + (
                        self._format_error_lines(section_errors)
                        if len(section_errors) == 1
                        else "<b>Fix the following:</b><br>"
                        + self._format_error_lines(section_errors)
                    )
                )
            else:
                panel.setText("")
            if section == "general":
                if section_errors and not self._general_error_row_visible:
                    self._general_form.insertRow(1, panel)
                    self._general_error_row_visible = True
                elif not section_errors and self._general_error_row_visible:
                    self._general_form.takeRow(panel)
                    self._general_error_row_visible = False
                panel.setVisible(bool(section_errors))
            else:
                panel.setVisible(bool(section_errors))

    @staticmethod
    def _format_error_lines(errors: tuple[str, ...]) -> str:
        escaped = tuple(escape(item, quote=False).replace(chr(10), "<br>") for item in errors)
        if len(escaped) == 1:
            return escaped[0]
        return "<br>".join(f"• {item}" for item in escaped)

    @staticmethod
    def _error_section(message: str) -> str:
        lowered = message.casefold()
        if ".name" in lowered or ".triggers" in lowered or "policy name" in lowered:
            return "general"
        if (
            ".scope" in lowered
            or lowered.startswith("scope ")
            or "at least one deck" in lowered
            or ("deck " in lowered and " not found" in lowered)
            or "note type" in lowered
            or "card type" in lowered
        ):
            return "scope"
        if (
            ".conditions" in lowered
            or ".match" in lowered
            or "condition" in lowered
            or "fsrs" in lowered
            or "sm-2" in lowered
        ):
            return "conditions"
        if (
            ".actions" in lowered
            or "action" in lowered
            or "destination deck" in lowered
            or "tag cannot" in lowered
            or "tags cannot" in lowered
            or "deletion must" in lowered
            or "suspend and unsuspend" in lowered
            or "setting and clearing" in lowered
        ):
            return "actions"
        return "top"

    def _hide_policy_errors(self) -> None:
        self._set_policy_errors(())

    def _policy_from_form(  # noqa: PLR0912
        self,
        *,
        preview: bool = False,
        browse: bool = False,
        show_errors: bool = True,
        error_sink: list[str] | None = None,
    ) -> Policy | None:
        if self._json_mode:
            return self._policy_from_json(
                preview=preview,
                browse=browse,
                show_errors=show_errors,
                error_sink=error_sink,
            )
        errors: list[str] = []
        name = self.name.text().strip()
        if (preview or browse) and not name:
            name = "Unnamed policy"
        decks = self.decks.selectors()
        if not name:
            errors.append("Enter a policy name")
        if not decks and not self.decks.all_decks:
            errors.append("Choose at least one deck")
        if self.note_types.selected() == ():
            errors.append("Choose at least one note type")
        simple_conditions = (
            (AllCardsCondition(),)
            if self.match.currentData() == "all_cards"
            else tuple(row.condition() for row in self._conditions)
        )
        if not simple_conditions:
            errors.append("Add at least one condition or choose 'All cards'")
        if any(isinstance(item, TagCondition) and not item.tags for item in simple_conditions):
            errors.append("Enter one or more tags for every tag condition")
        condition: ConditionExpression = (
            AllConditions(simple_conditions)
            if self.match.currentData() in {"all", "all_cards"}
            else AnyConditions(simple_conditions)
        )
        actions: list[Action] = []
        if not browse:
            kinds = [row.action_kind() for row in self._actions]
            if not kinds:
                errors.append("Add at least one action")
            if len(kinds) != len(set(kinds)):
                errors.append("The same action cannot be added more than once")
            if {"delete_card", "delete_note"} & set(kinds) and len(kinds) != 1:
                errors.append("Deletion must be the only action")
            if {"suspend", "suspend_note"} & set(kinds) and {
                "unsuspend",
                "unsuspend_note",
            } & set(kinds):
                errors.append("Suspend and unsuspend cannot be combined")
            if "replace_tags" in kinds and {
                "add_tags",
                "remove_tags",
            } & set(kinds):
                errors.append("Replacing tags cannot be combined with adding or removing tags")
            if "set_flag" in kinds and "clear_flag" in kinds:
                errors.append("Setting and clearing a card flag cannot be combined")
            for row in self._actions:
                kind = row.action_kind()
                if kind in {"add_tags", "remove_tags"} and not _split_tags(row.tags.text()):
                    errors.append(f"Enter one or more tags for action {row.number_label.text()}")
                if kind in {"move", "move_note"} and not row.deck.currentText().strip():
                    errors.append("Choose a destination deck for the move action")
            actions = [action for row in self._actions for action in row.actions()]
            if (
                len(
                    {action.deck.casefold() for action in actions if isinstance(action, MoveAction)}
                )
                > 1
            ):
                errors.append("A policy cannot have multiple move destinations")
            added = {action.tag.casefold() for action in actions if isinstance(action, TagAction)}
            removed = {
                action.tag.casefold() for action in actions if isinstance(action, RemoveTagAction)
            }
            if added & removed:
                errors.append("The same tag cannot be added and removed")
        policy_id = self._policy_id
        normalized_id = policy_id.casefold()
        if not (preview or browse) and normalized_id in self._existing_ids:
            if self._record is not None and self._record.policy is None:
                policy_id = str(uuid4())
                self._policy_id = policy_id
            else:
                errors.append("Another policy has the same internal ID")
        if self._has_incompatible_schedulers():
            errors.append("FSRS and SM-2 conditions cannot be used together")
        if errors:
            if error_sink is not None:
                error_sink.extend(errors)
            if show_errors:
                self._set_policy_errors(tuple(errors))
            return None
        policy = Policy(
            id=policy_id,
            name=name,
            triggers=self.triggers.selected(),
            scope=Scope(
                decks=decks,
                all_decks=self.decks.all_decks,
                note_types=self.note_types.selected(),
            ),
            conditions=condition,
            actions=tuple(actions),
        )
        if browse:
            return policy
        try:
            parsed = parse_policy(policy_to_dict(policy))
        except (TypeError, ValueError) as exc:
            if error_sink is not None:
                error_sink.append(str(exc))
            if show_errors:
                self._set_policy_errors((str(exc),))
            return None
        if show_errors and not preview:
            self._hide_policy_errors()
        return parsed

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
                ),
                conditions=expression,
                actions=tuple(action for row in self._actions for action in row.actions()),
            )
        )

    def _policy_from_json(
        self,
        *,
        preview: bool = False,
        browse: bool = False,
        show_errors: bool = True,
        error_sink: list[str] | None = None,
    ) -> Policy | None:
        try:
            raw = json.loads(self.json_text.toPlainText())
            if isinstance(raw, dict):
                raw["id"] = self._policy_id
                if preview or browse:
                    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
                        raw["name"] = "Unnamed policy"
                    raw["triggers"] = []
                if browse:
                    # Matching does not depend on the draft's actions. A valid
                    # placeholder lets the schema validate everything else.
                    raw["actions"] = [{"type": "suspend"}]
            policy = parse_policy(raw)
            if browse:
                policy = replace(policy, actions=())
        except (ValueError, TypeError) as exc:
            if error_sink is not None:
                error_sink.append(str(exc))
            if show_errors:
                self._set_policy_errors(
                    (str(exc),),
                    title="Policy JSON has errors",
                    place_in_sections=False,
                )
            return None
        if show_errors and not (preview or browse):
            self._hide_policy_errors()
        return policy

    def _toggle_json(self) -> None:
        if self._json_mode:
            return
        self._pre_json_errors = (self._displayed_error_title, self._displayed_errors)
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
        if enabled:
            for group in self._form_groups:
                group.hide()
            self.json_text.show()
        else:
            self.json_text.hide()
            for group in self._form_groups:
                group.show()
            self._render_section_error_panels()
            self.policy_error_warning.setVisible(bool(self._top_errors))
            self._update_warning_panels()
        self.json_button.setVisible(not self._json_mode)
        self.save_button.setText("Apply" if self._json_mode else "Save")
        self.save_button.setToolTip(
            "Apply valid JSON to the unsaved form" if self._json_mode else "Save this policy"
        )
        self.cancel_button.setToolTip(
            "Discard JSON edits and return to the form" if self._json_mode else "Close this editor"
        )
        self.layout().activate()
        self._schedule_button_state()
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
        reference_errors = validate_policy_references(mw.col, policy)
        if reference_errors:
            self._set_policy_errors(reference_errors)
        else:
            self._hide_policy_errors()
        if not self.isMaximized():
            self._fit_timer.stop()
            self._adjusting_size = True
            try:
                self.resize(self._form_size)
            finally:
                self._adjusting_size = False

    def _cancel_json(self) -> None:
        if self.json_text.toPlainText() != self._json_initial_text and not askUser(
            "Discard JSON edits and return to the form?",
            parent=self,
            defaultno=True,
            title="Card Janitor",
        ):
            return
        title, errors = self._pre_json_errors
        self._set_policy_errors(errors, title=title or "This policy has errors")
        self._set_json_mode(enabled=False)

    def _apply_policy(self, policy: Policy) -> None:
        self.name.setText(policy.name)
        show_text_from_start(self.name)
        self.triggers.set_selected(policy.triggers)
        decks = DeckPicker(
            self._deck_names, policy.scope.selectors, self, all_decks=policy.scope.all_decks
        )
        types = NoteTypePicker(self._note_types, policy.scope.note_types, self)
        for old, new in ((self.decks, decks), (self.note_types, types)):
            self._scope_form.replaceWidget(old, new)
            old.hide()
            old.deleteLater()
        self.decks, self.note_types = decks, types
        qconnect(self.decks.changed, self._schedule_button_state)
        qconnect(self.note_types.changed, self._schedule_button_state)
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
        self._update_warning_panels()
        self._update_tab_order()
        self._schedule_button_state()

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
        errors: list[str] = []
        policy = self._policy_from_form(error_sink=errors)
        if policy is None:
            if errors:
                self._show_save_errors(tuple(errors))
            return
        reference_errors = validate_policy_references(mw.col, policy)
        if reference_errors:
            self._set_policy_errors(reference_errors)
            self._show_save_errors(reference_errors)
            return
        self.result_policy = policy
        self.accept()

    def _show_save_errors(self, errors: tuple[str, ...]) -> None:
        body = errors[0] if len(errors) == 1 else "\n".join(f"• {item}" for item in errors)
        showWarning(
            f"Cannot save this policy:\n\n{body}",
            parent=self,
            title="Card Janitor",
        )

    def _close_preview(self) -> None:
        if self._preview_dialog is not None:
            self._preview_dialog.close()
            self._preview_dialog.deleteLater()
            self._preview_dialog = None

    def _preview(self, _checked: object = None) -> None:
        self._browse(preview=True)

    def _show_operation_errors(self, errors: tuple[str, ...], *, preview: bool) -> None:
        operation = "preview changes" if preview else "browse matching cards"
        body = errors[0] if len(errors) == 1 else "\n".join(f"• {item}" for item in errors)
        showWarning(
            f"Cannot {operation}:\n\n{body}",
            parent=self,
            title="Card Janitor",
        )

    def _browse(self, _checked: object = None, *, preview: bool = False) -> None:
        self._close_preview()
        operation_errors: list[str] = []
        policy = self._policy_from_form(
            preview=preview,
            browse=not preview,
            show_errors=False,
            error_sink=operation_errors,
        )
        if policy is None:
            if operation_errors:
                self._show_operation_errors(tuple(operation_errors), preview=preview)
            return
        reference_errors = validate_policy_references(mw.col, policy)
        if reference_errors:
            self._show_operation_errors(reference_errors, preview=preview)
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

        self._operation_running = True
        self.browse_button.setEnabled(False)
        self.preview_button.setEnabled(False)

        def restore_button() -> None:
            if self.isVisible():
                self._operation_running = False
                self._update_action_buttons()

        def on_success(result: tuple) -> None:
            report, rows = result
            restore_button()
            if not is_current() or not self.isVisible():
                return
            if report.errors:
                self._show_operation_errors(report.errors, preview=preview)
                return
            if preview:
                if self._displayed_error_title == "Cannot preview changes":
                    self._hide_policy_errors()
                self._preview_dialog = CleanupPreviewDialog(
                    rows, self, policy_name=policy.name, is_current=is_current
                )
                self._preview_dialog.show()
                self._preview_dialog.raise_()
                self._preview_dialog.activateWindow()
                return
            if self._displayed_error_title == "Cannot browse matches":
                self._hide_policy_errors()
            card_ids = {card.card_id for card in report.qualifying}
            if not card_ids:
                tooltip("No cards match this policy's scope and conditions", parent=self)
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


def _collection_note_types() -> list[tuple[str, tuple[str, ...]]]:
    return sorted(
        (
            str(note_type["name"]),
            tuple(str(card_type["name"]) for card_type in note_type.get("tmpls", [])),
        )
        for note_type in mw.col.models.all()
    )


def _raw_note_type_selectors(raw: dict) -> tuple[NoteTypeSelector, ...]:
    values = raw.get("note_types", [])
    if not isinstance(values, list):
        return ()
    selectors = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("name"), str):
            continue
        name = value["name"].strip()
        if not name:
            continue
        card_types = value.get("card_types")
        selectors.append(
            NoteTypeSelector(
                name,
                tuple(item for item in card_types if isinstance(item, str))
                if isinstance(card_types, list)
                else None,
            )
        )
    return tuple(selectors)


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


def _best_effort_condition(  # noqa: PLR0911, PLR0912
    raw: object,
) -> ConditionExpression | None:
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
            source if source in {"first_review", "last_review", "card_created"} else "first_review",
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
    if kind == "card_flag":
        raw_flags = raw.get("flags")
        flags = tuple(
            value for value in FLAG_NAMES if isinstance(raw_flags, list) and value in raw_flags
        )
        if flags:
            return CardFlagCondition(flags)
    count_types = {
        "answer_count": AnswerCountCondition,
        "correct_answer_count": CorrectAnswerCountCondition,
        "lapse_count": LapseCountCondition,
    }
    if kind in count_types:
        count = _raw_bounded_int(raw, "count", maximum=MAX_COUNT)
        operator = _raw_numeric_operator(raw)
        return count_types[kind](count, operator)
    if kind == "correct_answer_rate":
        return CorrectAnswerRateCondition(
            _raw_bounded_int(raw, "percent", maximum=100), _raw_numeric_operator(raw)
        )
    if kind == "overdue":
        return OverdueCondition(days, _raw_numeric_operator(raw))
    if kind == "fsrs_stability":
        return FsrsStabilityCondition(days, _raw_numeric_operator(raw, allow_equal=False))
    if kind in {"fsrs_difficulty", "fsrs_retrievability"}:
        condition_type = (
            FsrsDifficultyCondition if kind == "fsrs_difficulty" else FsrsRetrievabilityCondition
        )
        return condition_type(
            _raw_bounded_int(raw, "percent", maximum=100),
            _raw_numeric_operator(raw, allow_equal=False),
        )
    if kind == "sm2_ease":
        return Sm2EaseCondition(
            _raw_bounded_int(raw, "percent", maximum=MAX_EASE_PERCENT),
            _raw_numeric_operator(raw),
        )
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


def _best_effort_actions(raw: object) -> tuple[Action, ...]:  # noqa: PLR0912
    if not isinstance(raw, list):
        return ()
    actions: list[Action] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = _raw_string(item, "type")
        if kind == "add_tags" and isinstance(item.get("tags"), list):
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
        elif kind == "set_flag" and item.get("flag") in FLAG_NAMES[1:]:
            actions.append(SetFlagAction(item["flag"]))
        elif kind == "clear_flag":
            actions.append(ClearFlagAction())
    return tuple(actions)


def _raw_bounded_int(raw: dict, key: str, *, maximum: int) -> int:
    value = raw.get(key)
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum
        else 0
    )


def _raw_numeric_operator(raw: dict, *, allow_equal: bool = True) -> str:
    valid = set(NUMERIC_OPERATOR_SYMBOLS)
    if not allow_equal:
        valid.remove("eq")
    operator = _raw_string(raw, "operator")
    return operator if operator in valid else "gte"
