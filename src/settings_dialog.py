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

from .automatic import cancel_automatic_run
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
        self.automatic_enabled = QCheckBox("Enable automatic cleanup", automatic_group)
        self.automatic_enabled.setChecked(config.automatic_cleanup_enabled)
        self.automatic_enabled.setToolTip(
            "Allow policy triggers to apply actions; manual cleanup remains available"
        )
        automatic_layout.addWidget(self.automatic_enabled)
        self.notify = QCheckBox(
            "Show notifications after cleanup",
            automatic_group,
        )
        self.notify.setChecked(config.notify_after_automatic_run)
        automatic_layout.addWidget(self.notify)
        self.warn_invalid = QCheckBox(
            "Warn on profile open when automatic policies need attention",
            automatic_group,
        )
        self.warn_invalid.setChecked(config.warn_on_invalid_automatic_policies)
        self.warn_invalid.setToolTip(
            "Check automatic policy references after opening sync, without scanning cards"
        )
        automatic_layout.addWidget(self.warn_invalid)
        qconnect(self.automatic_enabled.toggled, self._update_automatic_options)
        self._update_automatic_options(self.automatic_enabled.isChecked())
        layout.addWidget(automatic_group)

        history_group = QGroupBox("Cleanup History", self)
        history_layout = QVBoxLayout(history_group)
        self.history_enabled = QCheckBox("Record cleanup history on this device", history_group)
        self.history_enabled.setChecked(config.cleanup_history_enabled)
        self.history_enabled.setToolTip(
            "Keep an append-only local audit of future cleanup runs; turning this off does not "
            "delete existing history"
        )
        history_layout.addWidget(self.history_enabled)
        layout.addWidget(history_group)

        troubleshooting_group = QGroupBox("Troubleshooting", self)
        troubleshooting_layout = QVBoxLayout(troubleshooting_group)
        self.debug_logging = QCheckBox("Enable debug logging", troubleshooting_group)
        self.debug_logging.setChecked(config.debug_logging)
        self.debug_logging.setToolTip("Print cleanup diagnostics to Anki's terminal output")
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
        QWidget.setTabOrder(self.automatic_enabled, self.notify)
        QWidget.setTabOrder(self.notify, self.warn_invalid)
        QWidget.setTabOrder(self.warn_invalid, self.history_enabled)
        QWidget.setTabOrder(self.history_enabled, self.debug_logging)
        QWidget.setTabOrder(self.debug_logging, save_button)
        QWidget.setTabOrder(save_button, cancel_button)
        QTimer.singleShot(0, self.automatic_enabled.setFocus)

    def _update_automatic_options(self, enabled: bool) -> None:
        self.notify.setEnabled(enabled)
        self.warn_invalid.setEnabled(enabled)

    def _save(self) -> None:
        try:
            save_settings(
                automatic_cleanup_enabled=self.automatic_enabled.isChecked(),
                cleanup_history_enabled=self.history_enabled.isChecked(),
                notify_after_automatic_run=self.notify.isChecked(),
                warn_on_invalid_automatic_policies=self.warn_invalid.isChecked(),
                debug_logging=self.debug_logging.isChecked(),
            )
        except ConfigWriteError as exc:
            error("failed to save settings", reason=str(exc))
            showWarning(str(exc), parent=self)
            return
        configure_logging(debug_logging=self.debug_logging.isChecked())
        if not self.automatic_enabled.isChecked():
            cancel_automatic_run()
        debug(
            "settings saved",
            automatic_cleanup_enabled=self.automatic_enabled.isChecked(),
            cleanup_history_enabled=self.history_enabled.isChecked(),
            notify_after_automatic_run=self.notify.isChecked(),
            warn_on_invalid_automatic_policies=self.warn_invalid.isChecked(),
            debug_logging=self.debug_logging.isChecked(),
        )
        self.accept()
