# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from typing import TYPE_CHECKING

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
    pyqtSignal,
    qconnect,
)

from .editor_utils import guard_popup_anchor
from .picker_state import DeckSelection
from .presentation import warning_panel

if TYPE_CHECKING:
    from .models import DeckSelector

MAX_SUMMARY_LENGTH = 55


class DeckPicker(QPushButton):
    changed = pyqtSignal()

    def __init__(
        self,
        names: list[str],
        selectors: tuple[DeckSelector, ...],
        parent: QWidget,
        *,
        all_decks: bool = False,
    ) -> None:
        super().__init__(parent)
        self._state = DeckSelection(names, selectors, all_decks=all_decks)
        self._items: dict[str, QTreeWidgetItem] = {}
        self._menu = QMenu(self)
        container = QWidget(self._menu)
        self._container = container
        layout = QVBoxLayout(container)
        layout.addWidget(
            warning_panel(
                "Fully checked branches include <b>future subdecks</b>.",
                container,
                kind="info",
            )
        )
        self.tree = QTreeWidget(container)
        self.tree.setHeaderHidden(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.installEventFilter(self)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.setMinimumHeight(280)
        self._root = QTreeWidgetItem(["All decks"])
        self._root.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._root.setToolTip(0, "All current and future decks")
        self.tree.addTopLevelItem(self._root)
        self._root.setExpanded(True)
        for name in self._state.names:
            parent_name, _, label = name.rpartition("::")
            item = QTreeWidgetItem([label or name])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            self._items[name] = item
            if parent_name:
                self._items[parent_name].addChild(item)
            else:
                self._root.addChild(item)
        layout.addWidget(self.tree)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(container)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self._anchor_guard = guard_popup_anchor(self, self._menu)
        qconnect(self._menu.aboutToShow, self._resize_popup)
        qconnect(self.tree.itemClicked, self._clicked)
        qconnect(self.tree.itemDoubleClicked, self._clicked)
        self._refresh()

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

    @property
    def all_decks(self) -> bool:
        return self._state.all_decks

    def selectors(self) -> tuple[DeckSelector, ...]:
        return self._state.selectors()

    def _clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item is self._root:
            self._state.toggle_all()
        else:
            self._state.toggle(item.data(0, Qt.ItemDataRole.UserRole))
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        self._root.setCheckState(
            0,
            Qt.CheckState.Checked
            if self.all_decks
            else Qt.CheckState.PartiallyChecked
            if self.selectors()
            else Qt.CheckState.Unchecked,
        )
        for name, item in self._items.items():
            recursive = self._state.recursive(name)
            descendants_selected = self._state.descendants_selected(name)
            partial = self._state.exact(name) or descendants_selected
            state = (
                Qt.CheckState.Checked
                if recursive
                else Qt.CheckState.PartiallyChecked
                if partial
                else Qt.CheckState.Unchecked
            )
            item.setCheckState(0, state)
            item.setToolTip(
                0,
                name
                + (
                    " + subdecks"
                    if recursive
                    else " (this deck and some descendants selected)"
                    if self._state.exact(name) and descendants_selected
                    else " (deck only)"
                    if self._state.exact(name)
                    else " (descendants selected; this deck unselected)"
                    if partial
                    else ""
                ),
            )
        selectors = self.selectors()
        if self.all_decks:
            self.setText("All decks")
        elif not selectors:
            self.setText("Select decks…")
        elif len(selectors) == 1:
            selector = selectors[0]
            summary = selector.deck + (" + subdecks" if selector.include_subdecks else "")
            self.setText(summary if len(summary) <= MAX_SUMMARY_LENGTH else "1 deck selection")
        else:
            self.setText(f"{len(selectors)} deck selections")
        self.setToolTip(
            "All current and future decks"
            if self.all_decks
            else "\n".join(
                selector.deck + (" + subdecks" if selector.include_subdecks else "")
                for selector in selectors
            )
            or "Select one or more decks"
        )
