# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import configuration
from card_janitor.models import (
    AgeCondition,
    DeckSelector,
    Policy,
    PolicyRecord,
    Scope,
    SuspendAction,
)


class FakeCollection:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}
        self.writes: list[tuple[str, object]] = []

    def get_config(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def set_config(self, key: str, value: object) -> None:
        self.values[key] = value
        self.writes.append((key, value))


def fake_main_window(
    collection: FakeCollection,
    settings: object,
    settings_writes: list[tuple[str, object]],
) -> SimpleNamespace:
    return SimpleNamespace(
        col=collection,
        addonManager=SimpleNamespace(
            getConfig=lambda _module: settings,
            writeConfig=lambda module, value: settings_writes.append((module, value)),
        ),
    )


def sample_policy() -> Policy:
    return Policy(
        id="fixed",
        name="Fixed",
        triggers=(),
        scope=Scope((DeckSelector("Mining"),)),
        conditions=AgeCondition(365, "first_review", "gte"),
        actions=(SuspendAction(),),
    )


def test_load_raw_config_combines_global_settings_and_collection_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = {
        "config_version": 1,
        "notify_after_automatic_run": True,
        "debug_logging": False,
    }
    policies = [{"broken": True}]
    collection = FakeCollection({configuration.COLLECTION_POLICIES_KEY: policies})
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, settings, []))

    assert configuration.load_raw_config() == {**settings, "policies": policies}


def test_load_raw_collection_config_reads_only_collection_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policies = [{"broken": True}]
    collection = FakeCollection({configuration.COLLECTION_POLICIES_KEY: policies})
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, {}, []))

    assert configuration.load_raw_collection_config() == {"policies": policies}


def test_save_policy_replaces_only_target_collection_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policies = [{"broken": True}, {"also": "preserved"}]
    collection = FakeCollection({configuration.COLLECTION_POLICIES_KEY: policies})
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, {}, []))

    configuration.save_policy(sample_policy(), record=PolicyRecord(0, policies[0], None, ()))

    written = collection.writes[0]
    assert written[0] == configuration.COLLECTION_POLICIES_KEY
    assert written[1][0]["id"] == "fixed"
    assert written[1][1] == {"also": "preserved"}
    assert policies[0] == {"broken": True}


def test_remove_policy_removes_only_target_collection_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policies = [{"first": True}, {"remove": True}, {"last": True}]
    collection = FakeCollection({configuration.COLLECTION_POLICIES_KEY: policies})
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, {}, []))

    configuration.remove_policy(record=PolicyRecord(1, policies[1], None, ()))

    assert collection.writes == [
        (
            configuration.COLLECTION_POLICIES_KEY,
            [{"first": True}, {"last": True}],
        )
    ]
    assert policies == [{"first": True}, {"remove": True}, {"last": True}]


def test_save_settings_does_not_change_collection_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = {
        "config_version": 1,
        "notify_after_automatic_run": "invalid",
        "debug_logging": "invalid",
    }
    collection = FakeCollection({configuration.COLLECTION_POLICIES_KEY: [{"broken": True}]})
    writes: list[tuple[str, object]] = []
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, settings, writes))

    configuration.save_settings(
        automatic_cleanup_enabled=False,
        notify_after_automatic_run=False,
        debug_logging=True,
    )

    assert writes == [
        (
            "card_janitor",
            {
                "config_version": 1,
                "notify_after_automatic_run": False,
                "automatic_cleanup_enabled": False,
                "cleanup_history_enabled": True,
                "warn_on_invalid_automatic_policies": True,
                "debug_logging": True,
            },
        )
    ]
    assert collection.writes == []


def test_save_raw_collection_config_changes_only_collection_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policies = [{"name": "Current collection"}]
    collection_config = {"policies": policies}
    collection = FakeCollection()
    writes: list[tuple[str, object]] = []
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, {}, writes))

    configuration.save_raw_collection_config(collection_config, expected_policies=[])

    assert collection.values[configuration.COLLECTION_POLICIES_KEY] == policies
    assert writes == []


def test_save_raw_collection_config_rejects_unknown_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = FakeCollection()
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, {}, []))

    with pytest.raises(configuration.ConfigWriteError, match="notify_after_automatic_run"):
        configuration.save_raw_collection_config(
            {"policies": [], "notify_after_automatic_run": False},
            expected_policies=[],
        )

    assert not collection.writes


@pytest.mark.parametrize("operation", ["save", "remove", "json"])
def test_stale_configuration_writes_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    original = [{"id": "fixed", "name": "Before sync"}]
    current = [{"id": "different", "name": "On sync"}]
    collection = FakeCollection({configuration.COLLECTION_POLICIES_KEY: current})
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, {}, []))
    record = PolicyRecord(0, original[0], None, ())
    operations = {
        "save": lambda: configuration.save_policy(sample_policy(), record=record),
        "remove": lambda: configuration.remove_policy(record=record),
        "json": lambda: configuration.save_raw_collection_config(
            {"policies": []}, expected_policies=original
        ),
    }
    with pytest.raises(configuration.ConfigWriteError, match="changed"):
        operations[operation]()
    assert not collection.writes
    assert collection.values[configuration.COLLECTION_POLICIES_KEY] == current


def test_new_policy_checks_current_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = FakeCollection({configuration.COLLECTION_POLICIES_KEY: [{"id": "FIXED"}]})
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, {}, []))
    with pytest.raises(configuration.ConfigWriteError, match="ID"):
        configuration.save_policy(sample_policy())
    assert not collection.writes
