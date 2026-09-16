# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import re


def _split_tags(value: str) -> tuple[str, ...]:
    tags: list[str] = []
    seen: set[str] = set()
    for item in re.split(r"[\s,]+", value.strip()):
        normalized = item.casefold()
        if item and normalized not in seen:
            tags.append(item)
            seen.add(normalized)
    return tuple(tags)
