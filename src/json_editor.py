# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import aqt
import markdown
from aqt import mw
from aqt.addons import ConfigEditor
from aqt.qt import (
    QDialog,
    QDialogButtonBox,
    QWidget,
    qconnect,
)
from aqt.utils import showWarning
from markdown.extensions import md_in_html

from .configuration import (
    ADDON_MODULE,
    DEFAULT_CONFIG,
    ConfigWriteError,
    load_raw_collection_config,
    save_raw_collection_config,
)
from .line_numbers import LineNumberArea
from .log import error
from .models import parse_config

if TYPE_CHECKING:
    from collections.abc import Callable


CONFIG_EDITOR_ATTR = "_card_janitor_config_editor"


class CollectionConfigEditor(ConfigEditor):
    def __init__(self, parent: QDialog, addon: str, config: dict) -> None:
        self._original_policies = deepcopy(config.get("policies"))
        super().__init__(parent, addon, config)
        self._line_numbers = LineNumberArea(self.form.editor)
        self.setWindowTitle("Card Janitor — Edit Policies as JSON")
        clear_button = self.form.buttonBox.button(QDialogButtonBox.StandardButton.RestoreDefaults)
        clear_button.setText("Clear Policies")
        clear_button.setToolTip("Replace the editor contents with an empty policy list")

    def updateHelp(self) -> None:  # noqa: N802 - Qt/Anki virtual method
        text = Path(__file__).with_name("policies.md").read_text(encoding="utf-8")
        html = markdown.markdown(text, extensions=[md_in_html.makeExtension()])
        self.form.help.stdHtml(html, js=[], css=["css/addonconf.css"], context=self)

    def onRestoreDefaults(self) -> None:  # noqa: N802 - Qt/Anki virtual method
        self.updateText({"policies": []})

    def accept(self) -> None:
        text = self.form.editor.toPlainText()
        text = aqt.gui_hooks.addon_config_editor_will_update_json(text, ADDON_MODULE)
        try:
            config = json.loads(text)
        except (TypeError, ValueError) as exc:
            showWarning(f"Invalid JSON: {exc}", parent=self)
            return
        parsed = parse_config({**DEFAULT_CONFIG, **config} if isinstance(config, dict) else config)
        if parsed.issues:
            details = "\n".join(f"• {issue}" for issue in parsed.issues)
            showWarning(f"Card Janitor configuration has errors:\n\n{details}", parent=self)
            return
        try:
            save_raw_collection_config(config, expected_policies=self._original_policies)
        except ConfigWriteError as exc:
            error("failed to save collection configuration", reason=str(exc))
            showWarning(str(exc), parent=self)
            return
        self.conf = config
        self.onClose()
        QDialog.accept(self)


def open_policy_json(*, parent: QWidget, on_close: Callable[[], None] | None = None) -> None:
    config = load_raw_collection_config()
    if not isinstance(config, dict):
        error("cannot open policy JSON", reason="collection configuration is not an object")
        showWarning("The collection configuration is not a JSON object", parent=parent)
        return
    editor_parent = QDialog(parent)
    editor_parent.mgr = mw.addonManager
    editor = CollectionConfigEditor(editor_parent, ADDON_MODULE, config)
    setattr(mw, CONFIG_EDITOR_ATTR, (editor_parent, editor))

    def editor_closed(_result: int) -> None:
        setattr(mw, CONFIG_EDITOR_ATTR, None)
        if callable(on_close):
            on_close()

    qconnect(editor.finished, editor_closed)
