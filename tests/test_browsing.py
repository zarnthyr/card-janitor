# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import browsing


class Signal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback: object) -> None:
        self.callbacks.append(callback)

    def emit(self) -> None:
        for callback in self.callbacks:
            callback()


class Origin:
    def __init__(self) -> None:
        self.visible = True
        self.deleted = False
        self.restorations = []

    def isVisible(self) -> bool:  # noqa: N802 - Qt stand-in
        if self.deleted:
            message = "wrapped C++ object has been deleted"
            raise RuntimeError(message)
        return self.visible

    def raise_(self) -> None:
        self.restorations.append("raise")

    def activateWindow(self) -> None:  # noqa: N802 - Qt stand-in
        self.restorations.append("activate")


@pytest.fixture
def browser_fixture(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    browsers = [SimpleNamespace(destroyed=Signal())]
    timers = []
    searches = []

    def open_browser(_name: str, _parent: object, *, search: tuple) -> object:
        searches.append(search)
        return browsers[0]

    monkeypatch.setattr(browsing.aqt, "dialogs", SimpleNamespace(open=open_browser))
    monkeypatch.setattr(browsing, "_browse_generation", 0)
    monkeypatch.setattr(
        browsing,
        "QTimer",
        SimpleNamespace(singleShot=lambda _delay, callback: timers.append(callback)),
    )
    return SimpleNamespace(browsers=browsers, timers=timers, searches=searches)


def test_browse_restores_latest_origin_once_after_teardown(
    browser_fixture: SimpleNamespace,
) -> None:
    dashboard = Origin()
    editor = Origin()
    browser = browsing.open_cards_in_browser({2, 1}, origin=dashboard)
    browsing.open_cards_in_browser({3}, origin=editor)
    browsing.open_cards_in_browser({4}, origin=editor)
    assert len(browser.destroyed.callbacks) == 1
    assert browser_fixture.searches[0][0].parsable_text == "cid:1,2"
    browser.destroyed.emit()
    assert not editor.restorations
    assert len(browser_fixture.timers) == 1
    browser_fixture.timers[0]()
    assert editor.restorations == ["raise", "activate"]
    assert not dashboard.restorations


@pytest.mark.parametrize("unavailable", ["hidden", "deleted", "collected"])
def test_browse_does_not_restore_unavailable_origin(
    browser_fixture: SimpleNamespace, unavailable: str
) -> None:
    origin = Origin()
    browser = browsing.open_cards_in_browser({1}, origin=origin)
    state = getattr(browser, browsing._RETURN_ATTR)
    if unavailable == "hidden":
        origin.visible = False
    elif unavailable == "deleted":
        origin.deleted = True
    else:
        del origin
        assert state.origin() is None
    browser.destroyed.emit()
    browser_fixture.timers[0]()
    if unavailable != "collected":
        assert not state.origin().restorations


def test_old_browse_return_does_not_steal_focus_from_new_browser(
    browser_fixture: SimpleNamespace,
) -> None:
    first_origin = Origin()
    old_browser = browsing.open_cards_in_browser({1}, origin=first_origin)
    old_browser.destroyed.emit()
    browser_fixture.browsers[0] = SimpleNamespace(destroyed=Signal())
    second_origin = Origin()
    browsing.open_cards_in_browser({2}, origin=second_origin)
    browser_fixture.timers[0]()
    assert not first_origin.restorations
    assert not second_origin.restorations
