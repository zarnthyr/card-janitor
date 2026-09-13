# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import traceback

PREFIX = "[Card Janitor]"
_debug_logging = False


def configure(*, debug_logging: bool) -> None:
    global _debug_logging
    _debug_logging = debug_logging


def _write(level: str, message: str, values: dict[str, object]) -> None:
    details = " ".join(f"{key}={value!r}" for key, value in values.items())
    suffix = f" {details}" if details else ""
    print(f"{PREFIX} {level}: {message}{suffix}", flush=True)


def debug(message: str, **values: object) -> None:
    if _debug_logging:
        _write("DEBUG", message, values)


def error(message: str, **values: object) -> None:
    _write("ERROR", message, values)


def exception(message: str, **values: object) -> None:
    error(message, **values)
    traceback.print_exc()
