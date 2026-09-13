# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

try:
    from .addon import register_addon
except ModuleNotFoundError as error:
    if error.name not in {"anki", "aqt"}:
        raise
else:
    register_addon()
