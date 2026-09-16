# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import json_editor
from card_janitor.configuration import ConfigWriteError


def test_json_editor_saves_original_snapshot_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    writes = []
    closed = []
    original = [{"original": True}]
    editor = SimpleNamespace(
        _original_policies=original,
        form=SimpleNamespace(editor=SimpleNamespace(toPlainText=lambda: '{"policies": []}')),
        onClose=lambda: closed.append("closed"),
    )
    monkeypatch.setattr(
        json_editor,
        "save_raw_collection_config",
        lambda config, **kwargs: writes.append((config, kwargs)),
    )
    monkeypatch.setattr(
        json_editor, "QDialog", SimpleNamespace(accept=lambda _editor: closed.append("accepted"))
    )
    json_editor.CollectionConfigEditor.accept(editor)
    assert writes == [({"policies": []}, {"expected_policies": original})]
    assert editor.conf == {"policies": []}
    assert closed == ["closed", "accepted"]


def test_json_editor_stale_save_keeps_editor_open(monkeypatch: pytest.MonkeyPatch) -> None:
    warnings = []

    def stale(_config: object, **_kwargs: object) -> None:
        message = "Policies changed"
        raise ConfigWriteError(message)

    editor = SimpleNamespace(
        _original_policies=[],
        form=SimpleNamespace(editor=SimpleNamespace(toPlainText=lambda: '{"policies": []}')),
    )
    monkeypatch.setattr(json_editor, "save_raw_collection_config", stale)
    monkeypatch.setattr(json_editor, "showWarning", lambda text, **_kwargs: warnings.append(text))
    json_editor.CollectionConfigEditor.accept(editor)
    assert warnings == ["Policies changed"]
    assert not hasattr(editor, "conf")
