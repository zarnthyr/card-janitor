# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import re

from aqt.qt import QCheckBox, QEvent, QLineEdit, QMenu, QMouseEvent, QObject, QPoint, QWidget


class PopupCheckBox(QCheckBox):
    def hitButton(self, position: QPoint) -> bool:  # noqa: N802
        return self.rect().contains(position)


class _PopupAnchorGuard(QObject):
    def __init__(self, field: QWidget, menu: QMenu) -> None:
        super().__init__(field)
        self.field = field
        self.menu = menu
        menu.installEventFilter(self)
        ancestor = field
        while ancestor is not None:
            ancestor.installEventFilter(self)
            ancestor = ancestor.parentWidget()

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
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


def _split_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[\s,]+", value.strip()):
        normalized = item.casefold()
        if item and normalized not in seen:
            tags.append(item)
            seen.add(normalized)
    return tuple(tags)
