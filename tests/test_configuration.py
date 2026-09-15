# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import configuration
from card_janitor.models import AgeRule, Policy, Scope, SuspendAction


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
    *,
    user_settings: object | None = None,
) -> SimpleNamespace:
    metadata = {"config": settings if user_settings is None else user_settings}
    return SimpleNamespace(
        col=collection,
        addonManager=SimpleNamespace(
            addonMeta=lambda _module: metadata,
            getConfig=lambda _module: settings,
            writeConfig=lambda module, value: settings_writes.append((module, value)),
        ),
    )


def sample_policy() -> Policy:
    return Policy(
        id="fixed",
        name="Fixed",
        mode="on_demand",
        scope=Scope(("Mining",)),
        rule=AgeRule(365, "first_review", "gte"),
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

    configuration.save_policy(sample_policy(), index=0)

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

    configuration.remove_policy(index=1)

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
        notify_after_automatic_run=False,
        debug_logging=True,
    )

    assert writes == [
        (
            "card_janitor",
            {
                "config_version": 1,
                "notify_after_automatic_run": False,
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

    configuration.save_raw_collection_config(collection_config)

    assert collection.values[configuration.COLLECTION_POLICIES_KEY] == policies
    assert writes == []


def test_migrate_global_policies_moves_them_to_current_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policies = [{"name": "Existing"}]
    settings = {
        "config_version": 1,
        "notify_after_automatic_run": True,
        "debug_logging": False,
        "policies": policies,
    }
    collection = FakeCollection()
    writes: list[tuple[str, object]] = []
    monkeypatch.setattr(configuration, "mw", fake_main_window(collection, settings, writes))

    assert configuration.migrate_global_policies()
    assert collection.values[configuration.COLLECTION_POLICIES_KEY] == policies
    assert writes == [
        (
            "card_janitor",
            {
                "config_version": 1,
                "notify_after_automatic_run": True,
                "debug_logging": False,
            },
        )
    ]


def test_migration_ignores_stale_default_after_collection_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_defaults = {
        "config_version": 1,
        "notify_after_automatic_run": True,
        "debug_logging": False,
        "policies": [{"name": "Stale default"}],
    }
    collection = FakeCollection(
        {configuration.COLLECTION_POLICIES_KEY: [{"name": "Migrated policies"}]}
    )
    writes: list[tuple[str, object]] = []
    monkeypatch.setattr(
        configuration,
        "mw",
        fake_main_window(collection, stale_defaults, writes, user_settings={}),
    )

    assert not configuration.migrate_global_policies()
    assert collection.writes == []
    assert writes == []
