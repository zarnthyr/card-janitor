# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from aqt.qt import (
    QCheckBox,
    QMenu,
    QPushButton,
    QSignalBlocker,
    QStyle,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
    pyqtSignal,
    qconnect,
)

from .editor_utils import PopupCheckBox, guard_popup_anchor
from .models import Trigger
from .presentation import TRIGGER_HELP, TRIGGER_LABELS, trigger_summary, trigger_tooltip


class TriggerPicker(QPushButton):
    changed = pyqtSignal()

    def __init__(self, selected: tuple[Trigger, ...], parent: QWidget) -> None:
        super().__init__(parent)
        self._selected: tuple[Trigger, ...] = ()
        self._menu = QMenu(self)
        self._container = QWidget(self._menu)
        layout = QVBoxLayout(self._container)
        self._none = PopupCheckBox("None", self._container)
        self._none.setToolTip("No automatic triggers")
        qconnect(self._none.clicked, self._clear)
        layout.addWidget(self._none)
        self._checks: dict[str, QCheckBox] = {}
        for kind, label in TRIGGER_LABELS.items():
            checkbox = PopupCheckBox(label, self._container)
            checkbox.setToolTip(TRIGGER_HELP[kind])
            qconnect(checkbox.toggled, lambda checked, kind=kind: self._toggled(kind, checked))
            self._checks[kind] = checkbox
            layout.addWidget(checkbox)
        action = QWidgetAction(self._menu)
        action.setDefaultWidget(self._container)
        self._menu.addAction(action)
        self.setMenu(self._menu)
        self._anchor_guard = guard_popup_anchor(self, self._menu)
        qconnect(self._menu.aboutToShow, self._resize_popup)
        self.set_selected(selected)

    def selected(self) -> tuple[Trigger, ...]:
        return self._selected

    def set_selected(self, selected: tuple[Trigger, ...]) -> None:
        previous = self._selected
        self._selected = tuple(dict.fromkeys(selected))
        self._refresh()
        if previous != self._selected:
            self.changed.emit()

    def _toggled(self, kind: str, checked: bool) -> None:
        self.set_selected(
            (*self._selected, Trigger(kind))
            if checked
            else tuple(trigger for trigger in self._selected if trigger.type != kind)
        )

    def _clear(self) -> None:
        self.set_selected(())

    def _refresh(self) -> None:
        with QSignalBlocker(self._none):
            self._none.setChecked(not self._selected)
        kinds = {trigger.type for trigger in self._selected}
        for kind, checkbox in self._checks.items():
            with QSignalBlocker(checkbox):
                checkbox.setChecked(kind in kinds)
        self.setText(trigger_summary(self._selected))
        self.setToolTip(trigger_tooltip(self._selected))

    def _resize_popup(self) -> None:
        width = min(self.width(), self.screen().availableGeometry().width())
        panel_width = self._menu.style().pixelMetric(QStyle.PixelMetric.PM_MenuPanelWidth)
        self._menu.setFixedWidth(width)
        self._container.setFixedWidth(max(1, width - 2 * panel_width))
