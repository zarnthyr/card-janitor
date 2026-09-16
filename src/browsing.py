# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from typing import TYPE_CHECKING

import aqt
from anki.collection import SearchNode
from aqt import mw

if TYPE_CHECKING:
    from aqt.qt import QWidget


def open_cards_in_browser(card_ids: set[int]) -> QWidget:
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in sorted(card_ids)))
    return aqt.dialogs.open("Browser", mw, search=(node,))
