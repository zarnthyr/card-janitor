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

from .models import DeckSelector
from .presentation import warning_panel

MAX_SUMMARY_LENGTH = 55


class DeckPicker(QPushButton):
    def __init__(
        self,
        names: list[str],
        selectors: tuple[DeckSelector, ...],
        parent: QWidget,
        *,
        all_decks: bool = False,
    ) -> None:
        super().__init__(parent)
        self.all_decks = all_decks
        self._selected = {selector.deck: selector.include_subdecks for selector in selectors}
        if all_decks:
            self._selected.clear()
        self._items: dict[str, QTreeWidgetItem] = {}
        self._children: dict[str, list[str]] = {}
        self._menu = QMenu(self)
        container = QWidget(self._menu)
        self._container = container
        layout = QVBoxLayout(container)
        layout.addWidget(
            warning_panel(
                "Fully checked branches include <b>future subdecks</b>. ",
                container,
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
        all_names = set(names) | set(self._selected)
        for name in tuple(all_names):
            parts = name.split("::")
            all_names.update("::".join(parts[:index]) for index in range(1, len(parts)))
        for name in sorted(all_names):
            parent_name, _, label = name.rpartition("::")
            item = QTreeWidgetItem([label or name])
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            self._items[name] = item
            self._children.setdefault(name, [])
            if parent_name:
                self._items[parent_name].addChild(item)
                self._children[parent_name].append(name)
            else:
                self._root.addChild(item)
        layout.addWidget(self.tree)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(container)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        qconnect(self._menu.aboutToShow, self._resize_popup)
        qconnect(self.tree.itemClicked, self._clicked)
        qconnect(self.tree.itemDoubleClicked, self._clicked)
        self._normalize()
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

    def selectors(self) -> tuple[DeckSelector, ...]:
        return tuple(
            DeckSelector(name, recursive) for name, recursive in sorted(self._selected.items())
        )

    def _recursive(self, name: str) -> bool:
        return self.all_decks or any(
            recursive and (name == root or name.startswith(root + "::"))
            for root, recursive in self._selected.items()
        )

    def _clear_branch(self, name: str) -> None:
        for selected in tuple(self._selected):
            if selected == name or selected.startswith(name + "::"):
                del self._selected[selected]

    def _clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item is self._root:
            self.all_decks = not self.all_decks
            self._selected.clear()
            self._refresh()
            return
        if self.all_decks:
            self.all_decks = False
            self._selected = {
                self._root.child(index).data(0, Qt.ItemDataRole.UserRole): True
                for index in range(self._root.childCount())
            }
        name = item.data(0, Qt.ItemDataRole.UserRole)
        recursive = self._recursive(name)
        exact = name in self._selected and not self._selected[name]
        # Split recursive ancestors before changing an individual descendant.
        ancestors = [
            root
            for root, enabled in self._selected.items()
            if enabled and name.startswith(root + "::")
        ]
        for root in sorted(ancestors, key=len):
            self._clear_branch(root)
            current = root
            while current != name:
                self._selected[current] = False
                for child in self._children[current]:
                    self._selected[child] = True
                current = next(
                    child
                    for child in self._children[current]
                    if child == name or name.startswith(child + "::")
                )
                self._selected.pop(current, None)
        self._clear_branch(name)
        if recursive:
            self._selected[name] = False
        elif not exact:
            self._selected[name] = True
        self._normalize()
        self._refresh()

    def _normalize(self) -> None:
        for name in sorted(self._items, key=len, reverse=True):
            children = self._children[name]
            if (
                children
                and name in self._selected
                and all(self._selected.get(child) is True for child in children)
            ):
                self._clear_branch(name)
                self._selected[name] = True
        for name in tuple(self._selected):
            if any(
                root != name and recursive and name.startswith(root + "::")
                for root, recursive in self._selected.items()
            ):
                self._selected.pop(name, None)

    def _refresh(self) -> None:
        self._root.setCheckState(
            0,
            Qt.CheckState.Checked
            if self.all_decks
            else Qt.CheckState.PartiallyChecked
            if self._selected
            else Qt.CheckState.Unchecked,
        )
        for name, item in self._items.items():
            recursive = self._recursive(name)
            descendants_selected = any(
                selected.startswith(name + "::") for selected in self._selected
            )
            partial = name in self._selected or descendants_selected
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
                    if name in self._selected and descendants_selected
                    else " (deck only)"
                    if name in self._selected
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
