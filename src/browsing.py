# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from weakref import ReferenceType, ref

import aqt
from anki.collection import SearchNode
from aqt import mw
from aqt.qt import QTimer, qconnect

if TYPE_CHECKING:
    from aqt.qt import QWidget


_RETURN_ATTR = "_card_janitor_browse_return"
_browse_generation = 0


@dataclass
class _BrowseReturn:
    origin: ReferenceType[QWidget]
    generation: int


def _restore_origin(state: _BrowseReturn) -> None:
    if state.generation != _browse_generation:
        return
    origin = state.origin()
    if origin is None:
        return
    try:
        if origin.isVisible():
            origin.raise_()
            origin.activateWindow()
    except RuntimeError:
        # The Python wrapper may outlive a deleted Qt window.
        return


def open_cards_in_browser(card_ids: set[int], *, origin: QWidget) -> QWidget:
    global _browse_generation  # noqa: PLW0603 - invalidates deferred focus restoration
    node = SearchNode(parsable_text="cid:" + ",".join(str(card_id) for card_id in sorted(card_ids)))
    browser = aqt.dialogs.open("Browser", mw, search=(node,))
    _browse_generation += 1
    state = getattr(browser, _RETURN_ATTR, None)
    if state is None:
        state = _BrowseReturn(ref(origin), _browse_generation)
        setattr(browser, _RETURN_ATTR, state)
        qconnect(
            browser.destroyed,
            lambda _object=None: QTimer.singleShot(0, lambda: _restore_origin(state)),
        )
    else:
        state.origin = ref(origin)
        state.generation = _browse_generation
    return browser
