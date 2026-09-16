# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
from dataclasses import dataclass

from aqt import gui_hooks, mw
from aqt.qt import QTimer

from .automatic import cancel_automatic_run, run_automatic_policies
from .log import exception
from .ui import close_card_janitor, safe_install_menu


@dataclass
class _OpeningState:
    profile: object | None = None
    generation: int = 0


_opening_state = _OpeningState()


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
    _opening_state.generation += 1
    try:
        safe_install_menu()
        if mw.can_auto_sync():
            _opening_state.profile = mw.pm.profile
        else:
            _opening_state.profile = None
            run_automatic_policies(trigger="profile_open")
    except Exception:
        exception("profile-open callback failed")


def on_day_changed() -> None:
    try:
        if _opening_state.profile is None:
            run_automatic_policies(trigger="day_change")
    except Exception:
        exception("day-change callback failed")


def on_sync_finished() -> None:
    profile = _opening_state.profile
    generation = _opening_state.generation
    _opening_state.profile = None
    if profile is None or mw.pm.profile is not profile:
        return

    def run_after_sync() -> None:
        if mw.pm.profile is profile and _opening_state.generation == generation:
            try:
                run_automatic_policies(trigger="profile_open")
            except Exception:
                exception("post-sync opening cleanup failed")

    # Allow Anki's sync completion/reset (including full downloads) to finish.
    QTimer.singleShot(0, run_after_sync)


def on_profile_closing() -> None:
    _opening_state.generation += 1
    _opening_state.profile = None
    cancel_automatic_run()
    close_card_janitor()


def register_addon() -> None:
    _replace_hook(gui_hooks.profile_did_open, on_profile_loaded)
    _replace_hook(gui_hooks.day_did_change, on_day_changed)
    _replace_hook(gui_hooks.sync_did_finish, on_sync_finished)
    _replace_hook(gui_hooks.profile_will_close, on_profile_closing)
