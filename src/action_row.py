# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from aqt.qt import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QWidget,
    qconnect,
)

from .editor_utils import _split_tags, pad_text_field
from .models import (
    Action,
    DeleteCardAction,
    DeleteNoteAction,
    MoveAction,
    RemoveTagAction,
    ReplaceTagsAction,
    SuspendAction,
    TagAction,
    UnsuspendAction,
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
        pad_text_field(self.tags)
        self.tags.setPlaceholderText("Separate tags with spaces or commas")
        self.tags.setToolTip("Enter one or more tags, separated by spaces or commas")
        self.deck = QComboBox(self)
        self.deck.setEditable(True)
        pad_text_field(self.deck.lineEdit())
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
