# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from aqt.qt import (
    QAbstractItemView,
    QEvent,
    QKeyEvent,
    QMenu,
    QPushButton,
    QStyle,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    qconnect,
)

from .editor_utils import guard_popup_anchor
from .picker_state import NoteTypeSelection

MAX_SUMMARY_LENGTH = 55


class NoteTypePicker(QPushButton):
    def __init__(
        self,
        names: list[str],
        selected: tuple[str, ...] | None,
        parent: QWidget,
    ) -> None:
        super().__init__(parent)
        self._state = NoteTypeSelection(names, selected)
        self._menu = QMenu(self)
        self._container = QWidget(self._menu)
        layout = QVBoxLayout(self._container)
        self.tree = QTreeWidget(self._container)
        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setMinimumHeight(240)
        self.tree.installEventFilter(self)
        self._root = QTreeWidgetItem(["All note types"])
        self._root.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._root.setToolTip(0, "All current and future note types")
        self.tree.addTopLevelItem(self._root)
        self._root.setExpanded(True)
        self._items = {}
        for name in self._state.names:
            item = QTreeWidgetItem([name])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setToolTip(0, name)
            self._root.addChild(item)
            self._items[name] = item
        layout.addWidget(self.tree)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._container)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self._anchor_guard = guard_popup_anchor(self, self._menu)
        qconnect(self._menu.aboutToShow, self._resize_popup)
        qconnect(self.tree.itemClicked, self._clicked)
        qconnect(self.tree.itemDoubleClicked, self._clicked)
        self._refresh()

    def selected(self) -> tuple[str, ...] | None:
        return self._state.selected()

    def _resize_popup(self) -> None:
        width = min(self.width(), self.screen().availableGeometry().width())
        panel_width = self._menu.style().pixelMetric(QStyle.PixelMetric.PM_MenuPanelWidth)
        self._menu.setFixedWidth(width)
        self._container.setFixedWidth(max(1, width - 2 * panel_width))

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self.tree
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Space
        ):
            item = self.tree.currentItem()
            if item is not None:
                self._clicked(item, 0)
            return True
        return super().eventFilter(watched, event)

    def _clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item is self._root:
            self._state.toggle_all()
        else:
            self._state.toggle(item.text(0))
        self._refresh()

    def _refresh(self) -> None:
        self._root.setCheckState(
            0,
            Qt.CheckState.Checked
            if self.selected() is None
            else Qt.CheckState.PartiallyChecked
            if self.selected()
            else Qt.CheckState.Unchecked,
        )
        for name, item in self._items.items():
            item.setCheckState(
                0,
                Qt.CheckState.Checked if self._state.contains(name) else Qt.CheckState.Unchecked,
            )
        selected = self.selected()
        if selected is None:
            self.setText("All note types")
            self.setToolTip("All current and future note types")
        elif not selected:
            self.setText("Choose note types…")
            self.setToolTip("Choose one or more note types")
        else:
            summary = (
                selected[0]
                if len(selected) == 1 and len(selected[0]) <= MAX_SUMMARY_LENGTH
                else "1 note type"
                if len(selected) == 1
                else f"{len(selected)} note types"
            )
            self.setText(summary)
            self.setToolTip("\n".join(selected) + "\nFuture note types are excluded")
