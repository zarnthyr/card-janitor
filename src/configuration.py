# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from copy import deepcopy
from typing import Any

from aqt import mw

from .models import CONFIG_VERSION, ParsedConfig, Policy, PolicyRecord, parse_config, policy_to_dict

ADDON_MODULE = "card_janitor"
COLLECTION_POLICIES_KEY = "card_janitor_policies"
DEFAULT_SETTINGS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,
    "automatic_cleanup_enabled": True,
    "notify_after_automatic_run": True,
    "warn_on_invalid_automatic_policies": True,
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


def load_raw_collection_config() -> object:
    """Return Card Janitor data stored in the current collection."""
    return {"policies": deepcopy(load_raw_policies())}


def load_config() -> ParsedConfig:
    return parse_config(load_raw_config())


def _write_policies(policies: object) -> None:
    if mw is None or getattr(mw, "col", None) is None:
        message = "No collection is open"
        raise ConfigWriteError(message)
    mw.col.set_config(COLLECTION_POLICIES_KEY, policies)


def save_raw_collection_config(config: object, *, expected_policies: object) -> None:
    """Save collection-scoped Card Janitor data without changing settings."""
    if not isinstance(config, dict):
        message = "The configuration is not a JSON object"
        raise ConfigWriteError(message)
    policies = config.get("policies")
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    if load_raw_policies() != expected_policies:
        message = "Policies changed while the editor was open. Reopen it and try again."
        raise ConfigWriteError(message)
    _write_policies(deepcopy(policies))


def _checked_index(policies: list[object], record: PolicyRecord) -> int:
    if not 0 <= record.index < len(policies) or policies[record.index] != record.raw:
        message = "The policy changed while the editor was open. Refresh and try again."
        raise ConfigWriteError(message)
    return record.index


def save_policy(policy: Policy, *, record: PolicyRecord | None = None) -> None:
    """Add or replace one policy while preserving all other collection policies."""
    policies = load_raw_policies()
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    updated = deepcopy(policies)
    index = _checked_index(updated, record) if record is not None else None
    if any(
        position != index
        and isinstance(raw, dict)
        and isinstance(raw.get("id"), str)
        and raw["id"].strip().casefold() == policy.id.casefold()
        for position, raw in enumerate(updated)
    ):
        message = "Another policy already has this ID. Refresh and try again."
        raise ConfigWriteError(message)
    serialized = policy_to_dict(policy)
    if index is None:
        updated.append(serialized)
    else:
        updated[index] = serialized
    _write_policies(updated)


def remove_policy(*, record: PolicyRecord) -> None:
    """Remove one policy while preserving all other collection policies."""
    policies = load_raw_policies()
    if not isinstance(policies, list):
        message = "The policies setting is not an array"
        raise ConfigWriteError(message)
    index = _checked_index(policies, record)
    updated = deepcopy(policies)
    del updated[index]
    _write_policies(updated)


def save_settings(
    *,
    automatic_cleanup_enabled: bool = True,
    notify_after_automatic_run: bool,
    debug_logging: bool,
    warn_on_invalid_automatic_policies: bool = True,
) -> None:
    """Update add-on-wide settings without changing collection policies."""
    raw = load_raw_settings()
    if not isinstance(raw, dict):
        message = "The add-on configuration is not a JSON object"
        raise ConfigWriteError(message)
    updated = deepcopy(raw)
    updated.pop("policies", None)
    updated["config_version"] = CONFIG_VERSION
    updated["automatic_cleanup_enabled"] = automatic_cleanup_enabled
    updated["notify_after_automatic_run"] = notify_after_automatic_run
    updated["warn_on_invalid_automatic_policies"] = warn_on_invalid_automatic_policies
    updated["debug_logging"] = debug_logging
    mw.addonManager.writeConfig(ADDON_MODULE, updated)
