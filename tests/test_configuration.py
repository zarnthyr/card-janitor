# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import configuration
from card_janitor.models import AgeRule, Policy, Scope, SuspendAction


def test_save_policy_replaces_only_target_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {
        "config_version": 1,
        "notify_after_automatic_run": True,
        "debug_logging": False,
        "policies": [{"broken": True}, {"also": "preserved"}],
    }
    writes = []
    monkeypatch.setattr(configuration, "load_raw_config", lambda: raw)
    monkeypatch.setattr(
        configuration,
        "mw",
        SimpleNamespace(
            addonManager=SimpleNamespace(
                writeConfig=lambda module, value: writes.append((module, value))
            )
        ),
    )
    policy = Policy(
        id="fixed",
        name="Fixed",
        state="manual",
        scope=Scope(("Mining",)),
        rule=AgeRule(365, "first_review", "gte"),
        actions=(SuspendAction(),),
    )

    configuration.save_policy(policy, index=0)

    assert raw["policies"][0] == {"broken": True}
    assert writes[0][0] == "card_janitor"
    assert writes[0][1]["policies"][0]["id"] == "fixed"
    assert writes[0][1]["policies"][1] == {"also": "preserved"}


def test_save_settings_preserves_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    policies = [{"broken": True}]
    raw = {
        "config_version": 1,
        "notify_after_automatic_run": "invalid",
        "debug_logging": "invalid",
        "policies": policies,
    }
    writes = []
    monkeypatch.setattr(configuration, "load_raw_config", lambda: raw)
    monkeypatch.setattr(
        configuration,
        "mw",
        SimpleNamespace(
            addonManager=SimpleNamespace(
                writeConfig=lambda module, value: writes.append((module, value))
            )
        ),
    )

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
                "policies": policies,
            },
        )
    ]
    assert raw["notify_after_automatic_run"] == "invalid"
