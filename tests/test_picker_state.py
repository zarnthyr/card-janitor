# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import pytest
from card_janitor.models import DeckSelector
from card_janitor.picker_state import DeckSelection, NoteTypeSelection


def selector(name: str, *, recursive: bool = True) -> DeckSelector:
    return DeckSelector(name, include_subdecks=recursive)


def test_three_state_cycle_without_qt() -> None:
    state = DeckSelection(["Mining"], ())
    state.toggle("Mining")
    assert state.selectors() == (selector("Mining"),)
    assert state.recursive("Mining::Future")
    state.toggle("Mining")
    assert state.selectors() == (selector("Mining", recursive=False),)
    assert not state.recursive("Mining::Future")
    state.toggle("Mining")
    assert state.selectors() == ()


def test_deep_split_preserves_untouched_recursive_branches() -> None:
    state = DeckSelection(["A::B::C", "A::B::D", "A::E"], (selector("A"),))
    state.toggle("A::B::C")
    assert state.selectors() == (
        selector("A", recursive=False),
        selector("A::B", recursive=False),
        selector("A::B::C", recursive=False),
        selector("A::B::D"),
        selector("A::E"),
    )
    assert state.descendants_selected("A")
    assert not state.recursive("A::Future")
    assert not state.recursive("A::B::Future")
    assert state.recursive("A::E::Future")
    state.toggle("A::B::C")
    state.toggle("A::B::C")
    assert state.selectors() == (selector("A"),)


def test_normalization_retains_unselected_parent() -> None:
    state = DeckSelection(["A::B", "A::C"], (selector("A::B"), selector("A::C")))
    assert not state.exact("A")
    assert state.selectors() == (selector("A::B"), selector("A::C"))
    assert not state.recursive("A::Future")


def test_missing_selected_decks_and_ancestors_are_retained() -> None:
    state = DeckSelection([], (selector("Missing::Child", recursive=False),))
    assert state.names == ("Missing", "Missing::Child")
    assert state.selectors() == (selector("Missing::Child", recursive=False),)


def test_recursive_parent_removes_redundant_selectors() -> None:
    state = DeckSelection(["A::B"], (selector("A"), selector("A::B", recursive=False)))
    assert state.selectors() == (selector("A"),)


def test_all_current_roots_do_not_select_future_roots() -> None:
    state = DeckSelection(["A", "B"], (selector("A"), selector("B")))
    assert not state.all_decks
    assert not state.recursive("Future")
    state.toggle_all()
    assert state.all_decks
    assert state.selectors() == ()
    assert state.recursive("Future")
    state.toggle("A")
    assert state.selectors() == (selector("A", recursive=False), selector("B"))


def test_note_types_explicit_selection_retains_future_exclusion() -> None:
    state = NoteTypeSelection(["Basic", "Cloze"], None)
    assert state.contains("Future")
    state.toggle("Basic")
    state.toggle("Basic")
    assert state.selected() == ("Basic", "Cloze")
    assert not state.contains("Future")
    state.toggle_all()
    assert state.selected() is None
    state.toggle_all()
    assert state.selected() == ()


def test_note_types_keep_missing_selected_name() -> None:
    state = NoteTypeSelection(["Basic"], ("Missing",))
    assert state.names == ("Basic", "Missing")
    assert state.selected() == ("Missing",)


@pytest.mark.parametrize(
    "state", [DeckSelection(["A"], (), all_decks=True), NoteTypeSelection(["A"], None)]
)
def test_unknown_toggle_does_not_mutate_selection(state: DeckSelection | NoteTypeSelection) -> None:
    with pytest.raises(KeyError):
        state.toggle("Unknown")
    if isinstance(state, DeckSelection):
        assert state.all_decks
    else:
        assert state.selected() is None
