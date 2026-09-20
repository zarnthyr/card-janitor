# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import json_editor
from card_janitor.configuration import ConfigWriteError


def test_bulk_json_editor_uses_actual_parent_and_releases_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = []
    callbacks = []
    closed = []
    parent = SimpleNamespace()
    manager = object()
    window = SimpleNamespace(addonManager=manager)

    def create_editor(actual_parent: object, addon: str, config: dict) -> object:
        editor = SimpleNamespace(
            finished=SimpleNamespace(connect=callbacks.append), parent=actual_parent
        )
        created.append((editor, addon, config))
        return editor

    monkeypatch.setattr(json_editor, "mw", window)
    monkeypatch.setattr(json_editor, "load_raw_collection_config", lambda: {"policies": []})
    monkeypatch.setattr(json_editor, "CollectionConfigEditor", create_editor)
    json_editor.open_policy_json(parent=parent, on_close=lambda: closed.append(True))
    editor, addon, config = created[0]
    assert editor.parent is parent
    assert parent.mgr is manager
    assert addon == json_editor.ADDON_MODULE
    assert config == {"policies": []}
    assert getattr(window, json_editor.CONFIG_EDITOR_ATTR) is editor
    callbacks[0](0)
    assert getattr(window, json_editor.CONFIG_EDITOR_ATTR) is None
    assert closed == [True]


def test_json_editor_saves_original_snapshot_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    writes = []
    closed = []
    original = [{"original": True}]
    editor = SimpleNamespace(
        _original_policies=original,
        form=SimpleNamespace(editor=SimpleNamespace(toPlainText=lambda: '{"policies": []}')),
        onClose=lambda: closed.append("closed"),
        _hide_error=lambda: None,
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
    errors = []

    def stale(_config: object, **_kwargs: object) -> None:
        message = "Policies changed"
        raise ConfigWriteError(message)

    editor = SimpleNamespace(
        _original_policies=[],
        form=SimpleNamespace(editor=SimpleNamespace(toPlainText=lambda: '{"policies": []}')),
        _show_error=lambda title, details: errors.append((title, details)),
    )
    monkeypatch.setattr(json_editor, "save_raw_collection_config", stale)
    json_editor.CollectionConfigEditor.accept(editor)
    assert errors == [("Could not save policies", ("Policies changed",))]
    assert not hasattr(editor, "conf")


@pytest.mark.parametrize(
    ("text", "expected_title"),
    [
        ("{", "Invalid JSON"),
        ('{"policies": [{"id": "invalid"}]}', "Card Janitor configuration has errors"),
    ],
)
def test_bulk_json_validation_uses_inline_error(text: str, expected_title: str) -> None:
    errors = []
    editor = SimpleNamespace(
        _original_policies=[],
        form=SimpleNamespace(editor=SimpleNamespace(toPlainText=lambda: text)),
        _show_error=lambda title, details: errors.append((title, details)),
    )

    json_editor.CollectionConfigEditor.accept(editor)

    assert errors[0][0] == expected_title
    assert errors[0][1]
    assert not hasattr(editor, "conf")


@pytest.mark.parametrize(
    "key",
    [
        "config_version",
        "automatic_cleanup_enabled",
        "notify_after_automatic_run",
        "warn_on_invalid_automatic_policies",
        "debug_logging",
    ],
)
def test_bulk_json_editor_rejects_installation_setting_keys(key: str) -> None:
    errors = []
    editor = SimpleNamespace(
        _original_policies=[],
        form=SimpleNamespace(
            editor=SimpleNamespace(toPlainText=lambda: f'{{"policies": [], "{key}": true}}')
        ),
        _show_error=lambda title, details: errors.append((title, details)),
    )

    json_editor.CollectionConfigEditor.accept(editor)

    assert errors == [
        ("Card Janitor configuration has errors", (f"{key}: unknown collection setting",))
    ]
    assert not hasattr(editor, "conf")


def test_bulk_json_editor_rejects_unknown_top_level_keys() -> None:
    errors = []
    editor = SimpleNamespace(
        _original_policies=[],
        form=SimpleNamespace(
            editor=SimpleNamespace(toPlainText=lambda: '{"policies": [], "unexpected": true}')
        ),
        _show_error=lambda title, details: errors.append((title, details)),
    )

    json_editor.CollectionConfigEditor.accept(editor)

    assert errors == [
        ("Card Janitor configuration has errors", ("unexpected: unknown collection setting",))
    ]
    assert not hasattr(editor, "conf")
