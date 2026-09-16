# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from typing import TYPE_CHECKING

from aqt.qt import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import showWarning

from .configuration import ConfigWriteError, save_settings
from .log import configure as configure_logging
from .log import debug, error

if TYPE_CHECKING:
    from .models import AddonConfig


class SettingsDialog(QDialog):
    def __init__(self, config: AddonConfig, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Card Janitor Settings")

        layout = QVBoxLayout(self)

        automatic_group = QGroupBox("Automatic Cleanup", self)
        automatic_layout = QVBoxLayout(automatic_group)
        self.notify = QCheckBox(
            "Show a notification after cards are cleaned up automatically",
            automatic_group,
        )
        self.notify.setChecked(config.notify_after_automatic_run)
        automatic_layout.addWidget(self.notify)
        layout.addWidget(automatic_group)

        troubleshooting_group = QGroupBox("Troubleshooting", self)
        troubleshooting_layout = QVBoxLayout(troubleshooting_group)
        self.debug_logging = QCheckBox("Enable debug logging", troubleshooting_group)
        self.debug_logging.setChecked(config.debug_logging)
        self.debug_logging.setToolTip("Print policy evaluation details to Anki's terminal output")
        troubleshooting_layout.addWidget(self.debug_logging)
        layout.addWidget(troubleshooting_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        qconnect(buttons.accepted, self._save)
        qconnect(buttons.rejected, self.reject)
        layout.addWidget(buttons)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        cancel_button = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        QWidget.setTabOrder(self.notify, self.debug_logging)
        QWidget.setTabOrder(self.debug_logging, save_button)
        QWidget.setTabOrder(save_button, cancel_button)
        QTimer.singleShot(0, self.notify.setFocus)

    def _save(self) -> None:
        try:
            save_settings(
                notify_after_automatic_run=self.notify.isChecked(),
                debug_logging=self.debug_logging.isChecked(),
            )
        except ConfigWriteError as exc:
            error("failed to save settings", reason=str(exc))
            showWarning(str(exc), parent=self)
            return
        configure_logging(debug_logging=self.debug_logging.isChecked())
        debug(
            "settings saved",
            notify_after_automatic_run=self.notify.isChecked(),
            debug_logging=self.debug_logging.isChecked(),
        )
        self.accept()
