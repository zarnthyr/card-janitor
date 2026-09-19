# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import re

from aqt.qt import (
    QCheckBox,
    QEvent,
    QGridLayout,
    QLineEdit,
    QMenu,
    QMouseEvent,
    QObject,
    QPoint,
    QSizePolicy,
    QTableWidget,
    QTimer,
    QWidget,
    sip,
)

POLICY_ROW_MINIMUM_WIDTHS = (20, 165, 140, 140, 75)
POLICY_ROW_STRETCHES = (0, 4, 3, 3, 0)


def configure_policy_row_layout(layout: QGridLayout) -> None:
    """Keep condition and action controls in the same visual columns."""
    for column, (minimum, stretch) in enumerate(
        zip(POLICY_ROW_MINIMUM_WIDTHS, POLICY_ROW_STRETCHES, strict=True)
    ):
        layout.setColumnMinimumWidth(column, minimum)
        layout.setColumnStretch(column, stretch)


def ignore_policy_row_size_hints(*widgets: QWidget) -> None:
    """Let shared grid proportions, rather than control text, size columns."""
    for widget in widgets:
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)


class _InitialTableHeight(QObject):
    """Fit complete rows once, after the window's initial layout settles."""

    def __init__(self, window: QWidget, table: QTableWidget) -> None:
        super().__init__(window)
        self.window = window
        self.table = table
        self._started = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fit)
        window.installEventFilter(self)

    def eventFilter(self, _watched: object, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and not self._started:
            self._started = True
            self._timer.start(0)
        return False

    def _fit(self) -> None:
        if not self.window.isVisible() or self.window.isMaximized() or not self.table.isVisible():
            return
        self.window.layout().activate()
        self.table.resizeRowsToContents()
        viewport_height = self.table.viewport().height()
        boundaries = []
        total = 0
        for row in range(self.table.rowCount()):
            total += self.table.rowHeight(row)
            boundaries.append(total)
        if total <= viewport_height:
            return
        screen = self.window.screen().availableGeometry()
        frame_extra = self.window.frameGeometry().height() - self.window.height()
        max_height = screen.height() - frame_extra
        minimum_height = self.window.minimumSizeHint().height()
        candidates = [
            self.window.height() + boundary - viewport_height
            for boundary in boundaries
            if minimum_height <= self.window.height() + boundary - viewport_height <= max_height
        ]
        if candidates:
            self.window.resize(
                self.window.width(),
                min(candidates, key=lambda height: abs(height - self.window.height())),
            )


def fit_initial_table_height(window: QWidget, table: QTableWidget) -> QObject:
    return _InitialTableHeight(window, table)


class PopupCheckBox(QCheckBox):
    def hitButton(self, position: QPoint) -> bool:  # noqa: N802
        return self.rect().contains(position)


class _PopupAnchorGuard(QObject):
    def __init__(self, field: QWidget, menu: QMenu) -> None:
        super().__init__(field)
        self.field = field
        self.menu: QMenu | None = menu
        menu.destroyed.connect(self._menu_destroyed)
        menu.installEventFilter(self)
        ancestor = field
        while ancestor is not None:
            ancestor.installEventFilter(self)
            ancestor = ancestor.parentWidget()

    def _menu_destroyed(self, _object: object = None) -> None:
        self.menu = None

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if self.menu is None or sip.isdeleted(self.menu) or sip.isdeleted(self.field):
            return False
        if watched is self.menu:
            # Child controls handle their own clicks. Ignore clicks reaching
            # the menu's unused interior, but retain native outside dismissal.
            if (
                isinstance(event, QMouseEvent)
                and event.type()
                in (
                    QEvent.Type.MouseButtonPress,
                    QEvent.Type.MouseButtonRelease,
                    QEvent.Type.MouseButtonDblClick,
                )
                and self.menu.rect().contains(event.position().toPoint())
            ):
                event.accept()
                return True
            return super().eventFilter(watched, event)
        if self.menu.isVisible() and (
            event.type() == QEvent.Type.Move
            or (event.type() == QEvent.Type.Resize and watched in (self.field, self.field.window()))
        ):
            self.menu.close()
        return super().eventFilter(watched, event)


def guard_popup_anchor(field: QWidget, menu: QMenu) -> QObject:
    return _PopupAnchorGuard(field, menu)


def pad_text_field(field: QLineEdit) -> None:
    field.setTextMargins(6, 0, 6, 0)


def show_text_from_start(field: QLineEdit) -> None:
    """Show the beginning of preloaded text until the user moves the cursor."""
    field.setCursorPosition(0)
    field.deselect()


def _split_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[\s,]+", value.strip()):
        normalized = item.casefold()
        if item and normalized not in seen:
            tags.append(item)
            seen.add(normalized)
    return tuple(tags)
