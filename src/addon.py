# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib

from aqt import gui_hooks, mw
from aqt.utils import showWarning

from .automatic import run_automatic_policies
from .configuration import ConfigWriteError, migrate_global_policies
from .log import debug, error, exception
from .ui import close_card_janitor, safe_install_menu


def _callback_key(callback: object) -> tuple[object, object]:
    return (getattr(callback, "__self__", None), getattr(callback, "__func__", callback))


def _callbacks(hook: object) -> list[object]:
    if isinstance(hook, list):
        return list(hook)
    values = getattr(hook, "_hooks", None)
    return list(values) if isinstance(values, list) else []


def _replace_hook(hook: object, callback: object) -> None:
    for existing in _callbacks(hook):
        if _callback_key(existing) == _callback_key(callback):
            with contextlib.suppress(ValueError):
                hook.remove(existing)
    hook.append(callback)


def on_profile_loaded() -> None:
    try:
        safe_install_menu()
        try:
            migrated = migrate_global_policies()
        except ConfigWriteError as exc:
            error("failed to migrate policies into collection configuration", reason=str(exc))
            showWarning(
                "Card Janitor could not move existing policies into this collection. "
                "Automatic cleanup was not run.",
                parent=mw,
            )
            return
        if migrated:
            debug("migrated policies into collection configuration")
        run_automatic_policies(trigger="profile_open")
    except Exception:
        exception("profile-open callback failed")


def on_day_changed() -> None:
    try:
        run_automatic_policies(trigger="day_change")
    except Exception:
        exception("day-change callback failed")


def register_addon() -> None:
    _replace_hook(gui_hooks.profile_did_open, on_profile_loaded)
    _replace_hook(gui_hooks.day_did_change, on_day_changed)
    _replace_hook(gui_hooks.profile_will_close, close_card_janitor)
