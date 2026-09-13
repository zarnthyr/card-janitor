# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from typing import Any

from aqt import mw

from .models import ParsedConfig, parse_config

ADDON_MODULE = "card_retirement"
DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": 1,
    "automatic_check_interval_hours": 20,
    "policies": [],
}


def load_raw_config() -> object:
    if mw is None:
        return DEFAULT_CONFIG.copy()
    config = mw.addonManager.getConfig(ADDON_MODULE)
    return DEFAULT_CONFIG.copy() if config is None else config


def load_config() -> ParsedConfig:
    return parse_config(load_raw_config())
