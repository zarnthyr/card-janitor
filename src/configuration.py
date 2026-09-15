# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aqt import mw

from .models import CONFIG_VERSION, ParsedConfig, Policy, parse_config, policy_to_dict

ADDON_MODULE = "card_janitor"
DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "notify_after_automatic_run": True,
    "debug_logging": False,
    "policies": [],
}


class ConfigWriteError(ValueError):
    """Raised when the raw document cannot safely be updated."""


def load_raw_config() -> object:
    if mw is None:
        return DEFAULT_CONFIG.copy()
    config = mw.addonManager.getConfig(ADDON_MODULE)
    return DEFAULT_CONFIG.copy() if config is None else config


def load_config() -> ParsedConfig:
    return parse_config(load_raw_config())


def save_policy(policy: Policy, *, index: int | None = None) -> None:
    """Add or replace one policy while preserving all other raw configuration."""
    raw = load_raw_config()
    if not isinstance(raw, dict):
        message = "The add-on configuration is not a JSON object"
        raise ConfigWriteError(message)
    policies = raw.get("policies")
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    updated = deepcopy(raw)
    updated_policies = updated["policies"]
    serialized = policy_to_dict(policy)
    if index is None:
        updated_policies.append(serialized)
    elif 0 <= index < len(updated_policies):
        updated_policies[index] = serialized
    else:
        message = "The policy no longer exists. Refresh and try again."
        raise ConfigWriteError(message)
    mw.addonManager.writeConfig(ADDON_MODULE, updated)


def remove_policy(*, index: int) -> None:
    """Remove one policy while preserving all other raw configuration."""
    raw = load_raw_config()
    if not isinstance(raw, dict):
        message = "The add-on configuration is not a JSON object"
        raise ConfigWriteError(message)
    policies = raw.get("policies")
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    if not 0 <= index < len(policies):
        message = "The policy no longer exists. Refresh and try again."
        raise ConfigWriteError(message)
    updated = deepcopy(raw)
    del updated["policies"][index]
    mw.addonManager.writeConfig(ADDON_MODULE, updated)


def save_settings(
    *,
    notify_after_automatic_run: bool,
    debug_logging: bool,
) -> None:
    """Update add-on-wide settings while preserving every policy entry."""
    raw = load_raw_config()
    if not isinstance(raw, dict):
        message = "The add-on configuration is not a JSON object"
        raise ConfigWriteError(message)
    updated = deepcopy(raw)
    updated["config_version"] = CONFIG_VERSION
    updated["notify_after_automatic_run"] = notify_after_automatic_run
    updated["debug_logging"] = debug_logging
    mw.addonManager.writeConfig(ADDON_MODULE, updated)
