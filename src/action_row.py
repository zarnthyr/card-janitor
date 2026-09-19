# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from aqt.qt import (
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QStandardItem,
    QWidget,
    qconnect,
)

from .editor_utils import (
    _split_tags,
    configure_policy_row_layout,
    ignore_policy_row_size_hints,
    pad_text_field,
    show_text_from_start,
)
from .models import (
    Action,
    ClearFlagAction,
    DeleteCardAction,
    DeleteNoteAction,
    MoveAction,
    RemoveTagAction,
    ReplaceTagsAction,
    SetFlagAction,
    SuspendAction,
    TagAction,
    UnsuspendAction,
)


def _add_action_group(combo: QComboBox, title: str, choices: tuple[tuple[str, str], ...]) -> None:
    header = QStandardItem(title)
    header.setEnabled(False)
    header.setSelectable(False)
    font = header.font()
    font.setBold(True)
    header.setFont(font)
    combo.model().appendRow(header)
    for label, value in choices:
        combo.addItem(label, value)


class ActionRow(QWidget):
    def __init__(
        self,
        deck_names: list[str],
        *,
        kind: str = "add_tags",
        tags: tuple[str, ...] = (),
        deck: str = "",
        flag: str = "red",
        parent: QWidget | None = None,
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
                "Tags",
                (
                    ("Add tags", "add_tags"),
                    ("Remove tags", "remove_tags"),
                    ("Replace all tags", "replace_tags"),
                ),
            ),
            (
                "Cards",
                (
                    ("Suspend card", "suspend"),
                    ("Unsuspend card", "unsuspend"),
                    ("Move card to deck", "move"),
                    ("Delete card", "delete_card"),
                ),
            ),
            (
                "Notes",
                (
                    ("Suspend note", "suspend_note"),
                    ("Unsuspend note", "unsuspend_note"),
                    ("Move note to deck", "move_note"),
                    ("Delete note", "delete_note"),
                ),
            ),
            ("Flags", (("Set card flag", "set_flag"), ("Clear card flag", "clear_flag"))),
        ):
            _add_action_group(self.kind, group, choices)
        self.kind.setMinimumWidth(165)
        self.tags = QLineEdit(self)
        pad_text_field(self.tags)
        self.tags.setPlaceholderText("Separate tags with spaces or commas")
        self.tags.setToolTip("Enter one or more tags, separated by spaces or commas")
        self.deck = QComboBox(self)
        self.deck.setEditable(True)
        pad_text_field(self.deck.lineEdit())
        self.deck.addItems(deck_names)
        self.deck.setToolTip("Choose or enter the destination deck")
        self.flag = QComboBox(self)
        for name in ("red", "orange", "green", "blue", "pink", "turquoise", "purple"):
            self.flag.addItem(name.title(), name)
        self.flag.setToolTip("Choose the card flag colour")
        self.no_value = QWidget(self)
        self.value_stack = QStackedWidget(self)
        self.value_stack.setMinimumWidth(140)
        self.value_stack.addWidget(self.tags)
        self.value_stack.addWidget(self.deck)
        self.value_stack.addWidget(self.flag)
        self.value_stack.addWidget(self.no_value)
        self.blank_column = QWidget(self)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setMinimumWidth(75)
        self.remove_button.setToolTip("Remove this action")
        ignore_policy_row_size_hints(self.kind, self.value_stack, self.blank_column)
        layout.addWidget(self.number_label, 0, 0)
        layout.addWidget(self.kind, 0, 1)
        # Actions have no operator. Fill optional controls from the left and
        # leave the final middle column empty, while keeping Remove aligned.
        layout.addWidget(self.value_stack, 0, 2)
        layout.addWidget(self.blank_column, 0, 3)
        layout.addWidget(self.remove_button, 0, 4)
        selected_index = self.kind.findData(kind)
        self.kind.setCurrentIndex(
            selected_index if selected_index >= 0 else self.kind.findData("add_tags")
        )
        self.tags.setText(" ".join(tags))
        show_text_from_start(self.tags)
        self.deck.setCurrentText(deck)
        show_text_from_start(self.deck.lineEdit())
        self.flag.setCurrentIndex(max(self.flag.findData(flag), 0))
        qconnect(self.kind.currentIndexChanged, self._update_value)
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
        elif kind == "set_flag":
            self.value_stack.setCurrentWidget(self.flag)
        else:
            self.value_stack.setCurrentWidget(self.no_value)

    def action_kind(self) -> str:
        return self.kind.currentData()

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
        if kind == "delete_note":
            return (DeleteNoteAction(),)
        if kind == "set_flag":
            return (SetFlagAction(self.flag.currentData()),)
        return (ClearFlagAction(),)

    def focus_widgets(self) -> tuple[QWidget, ...]:
        return (self.kind, self.tags, self.deck, self.flag, self.remove_button)
