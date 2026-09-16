# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from typing import TYPE_CHECKING

from aqt.qt import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    qconnect,
)

from .browsing import open_cards_in_browser

if TYPE_CHECKING:
    from .actions import ConflictDetail


class ConflictDialog(QDialog):
    def __init__(self, details: tuple[ConflictDetail, ...], parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Card Janitor — Conflicting Actions")
        self.resize(760, 360)
        self._card_ids = {detail.card_id for detail in details}
        layout = QVBoxLayout(self)
        intro = QLabel("These cards would be skipped. Review the policies and reasons below.", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(len(details), 3, self)
        self.table.setHorizontalHeaderLabels(["Card", "Policies", "Reason"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for row, detail in enumerate(details):
            for column, text in enumerate(
                (str(detail.card_id), "\n".join(detail.policies), "\n".join(detail.reasons))
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, column, item)
        self.table.resizeRowsToContents()
        qconnect(header.sectionResized, lambda *_args: self.table.resizeRowsToContents())
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self.browse_button = buttons.addButton("Browse", QDialogButtonBox.ButtonRole.ActionRole)
        self.browse_button.setToolTip("Open all skipped cards in Browse")
        self.browse_button.setEnabled(bool(details))
        qconnect(self.browse_button.clicked, self._browse)
        qconnect(buttons.rejected, self.close)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        if self._card_ids:
            open_cards_in_browser(self._card_ids)
