# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aqt import mw

from .models import CONFIG_VERSION, ParsedConfig, Policy, parse_config, policy_to_dict

ADDON_MODULE = "card_janitor"
COLLECTION_POLICIES_KEY = "card_janitor_policies"
DEFAULT_SETTINGS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "notify_after_automatic_run": True,
    "debug_logging": False,
}
DEFAULT_CONFIG: dict[str, Any] = {**DEFAULT_SETTINGS, "policies": []}


class ConfigWriteError(ValueError):
    """Raised when configuration cannot safely be updated."""


def load_raw_settings() -> object:
    if mw is None:
        return DEFAULT_SETTINGS.copy()
    config = mw.addonManager.getConfig(ADDON_MODULE)
    return DEFAULT_SETTINGS.copy() if config is None else config


def load_raw_policies() -> object:
    if mw is None or getattr(mw, "col", None) is None:
        return []
    return mw.col.get_config(COLLECTION_POLICIES_KEY, [])


def load_raw_config() -> object:
    """Combine add-on-wide settings with the current collection's policies."""
    settings = load_raw_settings()
    if not isinstance(settings, dict):
        return settings
    combined = deepcopy(settings)
    combined.pop("policies", None)
    combined["policies"] = deepcopy(load_raw_policies())
    return combined


def load_config() -> ParsedConfig:
    return parse_config(load_raw_config())


def _write_policies(policies: object) -> None:
    if mw is None or getattr(mw, "col", None) is None:
        message = "No collection is open"
        raise ConfigWriteError(message)
    mw.col.set_config(COLLECTION_POLICIES_KEY, policies)


def save_raw_config(config: object) -> None:
    """Split a combined advanced configuration into its two storage locations."""
    if not isinstance(config, dict):
        message = "The configuration is not a JSON object"
        raise ConfigWriteError(message)
    policies = config.get("policies")
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    settings = {key: deepcopy(config[key]) for key in DEFAULT_SETTINGS if key in config}
    _write_policies(deepcopy(policies))
    mw.addonManager.writeConfig(ADDON_MODULE, settings)


def migrate_global_policies() -> bool:
    """Move former add-on-wide policies into the open collection."""
    settings = load_raw_settings()
    if not isinstance(settings, dict) or "policies" not in settings:
        return False
    if mw is None or getattr(mw, "col", None) is None:
        return False

    policies = settings["policies"]
    missing = object()
    existing = mw.col.get_config(COLLECTION_POLICIES_KEY, missing)
    if existing is not missing and existing != policies:
        metadata = mw.addonManager.addonMeta(ADDON_MODULE)
        user_settings = metadata.get("config", {}) if isinstance(metadata, dict) else {}
        user_policies = user_settings.get("policies", missing)
        if user_policies is missing:
            # The collection write completed on an earlier attempt. The remaining
            # policy list comes from an obsolete development config.json default.
            return False
        policies = user_policies

    if existing is missing:
        _write_policies(deepcopy(policies))
    elif existing != policies:
        message = (
            "Policies already exist in this collection and differ from the add-on-wide policies"
        )
        raise ConfigWriteError(message)

    updated = deepcopy(settings)
    del updated["policies"]
    mw.addonManager.writeConfig(ADDON_MODULE, updated)
    return True


def save_policy(policy: Policy, *, index: int | None = None) -> None:
    """Add or replace one policy while preserving all other collection policies."""
    policies = load_raw_policies()
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    updated = deepcopy(policies)
    serialized = policy_to_dict(policy)
    if index is None:
        updated.append(serialized)
    elif 0 <= index < len(updated):
        updated[index] = serialized
    else:
        message = "The policy no longer exists. Refresh and try again."
        raise ConfigWriteError(message)
    _write_policies(updated)


def remove_policy(*, index: int) -> None:
    """Remove one policy while preserving all other collection policies."""
    policies = load_raw_policies()
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    if not 0 <= index < len(policies):
        message = "The policy no longer exists. Refresh and try again."
        raise ConfigWriteError(message)
    updated = deepcopy(policies)
    del updated[index]
    _write_policies(updated)


def save_settings(
    *,
    notify_after_automatic_run: bool,
    debug_logging: bool,
) -> None:
    """Update add-on-wide settings without changing collection policies."""
    raw = load_raw_settings()
    if not isinstance(raw, dict):
        message = "The add-on configuration is not a JSON object"
        raise ConfigWriteError(message)
    updated = deepcopy(raw)
    updated.pop("policies", None)
    updated["config_version"] = CONFIG_VERSION
    updated["notify_after_automatic_run"] = notify_after_automatic_run
    updated["debug_logging"] = debug_logging
    mw.addonManager.writeConfig(ADDON_MODULE, updated)
