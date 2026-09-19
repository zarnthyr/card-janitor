# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from .models import DeckSelector, NoteTypeSelector


class DeckSelection:
    """Deck union and recursive branch transitions, independent of Qt."""

    def __init__(
        self, names: list[str], selectors: tuple[DeckSelector, ...], *, all_decks: bool = False
    ) -> None:
        self.all_decks = all_decks
        self._selected = (
            {}
            if all_decks
            else {selector.deck: selector.include_subdecks for selector in selectors}
        )
        all_names = set(names) | set(self._selected)
        for name in tuple(all_names):
            parts = name.split("::")
            all_names.update("::".join(parts[:index]) for index in range(1, len(parts)))
        self.names = tuple(sorted(all_names))
        self._children: dict[str, list[str]] = {name: [] for name in self.names}
        self._roots = []
        for name in self.names:
            parent, _, _label = name.rpartition("::")
            if parent:
                self._children[parent].append(name)
            else:
                self._roots.append(name)
        self._normalize()

    def selectors(self) -> tuple[DeckSelector, ...]:
        return tuple(
            DeckSelector(name, recursive) for name, recursive in sorted(self._selected.items())
        )

    def recursive(self, name: str) -> bool:
        return self.all_decks or any(
            recursive and (name == root or name.startswith(root + "::"))
            for root, recursive in self._selected.items()
        )

    def exact(self, name: str) -> bool:
        return name in self._selected

    def descendants_selected(self, name: str) -> bool:
        return any(selected.startswith(name + "::") for selected in self._selected)

    def toggle_all(self) -> None:
        self.all_decks = not self.all_decks
        self._selected.clear()

    def _clear_branch(self, name: str) -> None:
        for selected in tuple(self._selected):
            if selected == name or selected.startswith(name + "::"):
                del self._selected[selected]

    def toggle(self, name: str) -> None:
        if name not in self._children:
            raise KeyError(name)
        if self.all_decks:
            self.all_decks = False
            self._selected = dict.fromkeys(self._roots, True)
        recursive = self.recursive(name)
        exact = name in self._selected and not self._selected[name]
        # Materialize the path while retaining recursion in untouched siblings.
        ancestors = [
            root
            for root, enabled in self._selected.items()
            if enabled and name.startswith(root + "::")
        ]
        for root in sorted(ancestors, key=len):
            self._clear_branch(root)
            current = root
            while current != name:
                self._selected[current] = False
                for child in self._children[current]:
                    self._selected[child] = True
                current = next(
                    child
                    for child in self._children[current]
                    if child == name or name.startswith(child + "::")
                )
                self._selected.pop(current, None)
        self._clear_branch(name)
        if recursive:
            self._selected[name] = False
        elif not exact:
            self._selected[name] = True
        self._normalize()

    def _normalize(self) -> None:
        for name in sorted(self.names, key=len, reverse=True):
            children = self._children[name]
            if (
                children
                and name in self._selected
                and all(self._selected.get(child) is True for child in children)
            ):
                self._clear_branch(name)
                self._selected[name] = True
        for name in tuple(self._selected):
            if any(
                root != name and recursive and name.startswith(root + "::")
                for root, recursive in self._selected.items()
            ):
                self._selected.pop(name, None)


class NoteTypeSelection:
    """Hierarchical note-type/card-type selection, independent of Qt."""

    def __init__(
        self,
        note_types: list[tuple[str, tuple[str, ...]]],
        selected: tuple[NoteTypeSelector, ...] | None,
    ) -> None:
        card_types = {name: set(values) for name, values in note_types}
        if selected is not None:
            for selector in selected:
                card_types.setdefault(selector.name, set()).update(selector.card_types or ())
        self.card_types = {
            name: tuple(sorted(values, key=str.casefold))
            for name, values in sorted(card_types.items(), key=lambda item: item[0].casefold())
        }
        self.names = tuple(self.card_types)
        self._selected: dict[str, set[str] | None] | None = (
            None
            if selected is None
            else {
                selector.name: (None if selector.card_types is None else set(selector.card_types))
                for selector in selected
            }
        )

    def selected(self) -> tuple[NoteTypeSelector, ...] | None:
        if self._selected is None:
            return None
        return tuple(
            NoteTypeSelector(
                name,
                None if card_types is None else tuple(sorted(card_types, key=str.casefold)),
            )
            for name, card_types in sorted(
                self._selected.items(), key=lambda item: item[0].casefold()
            )
        )

    def note_selection(self, name: str) -> set[str] | bool | None:
        """Return False for excluded, None for all card types, or an explicit set."""
        if self._selected is None:
            return None
        return self._selected.get(name, False)

    def card_type_selected(self, name: str, card_type: str) -> bool:
        selected = self.note_selection(name)
        return selected is None or (isinstance(selected, set) and card_type in selected)

    def toggle_all(self) -> None:
        self._selected = {} if self._selected is None else None

    def _materialize_all(self) -> None:
        if self._selected is None:
            self._selected = dict.fromkeys(self.names)

    def toggle_note(self, name: str) -> None:
        if name not in self.names:
            raise KeyError(name)
        self._materialize_all()
        selected = self._selected
        if selected is None:
            return
        if name in selected:
            del selected[name]
        else:
            selected[name] = None

    def toggle_card_type(self, name: str, card_type: str) -> None:
        if card_type not in self.card_types.get(name, ()):
            raise KeyError((name, card_type))
        self._materialize_all()
        materialized = self._selected
        if materialized is None:
            return
        current = materialized.get(name, False)
        if current is None:
            selected = set(self.card_types[name])
            selected.remove(card_type)
        elif current is False:
            selected = {card_type}
        else:
            selected = set(current)
            if card_type in selected:
                selected.remove(card_type)
            else:
                selected.add(card_type)
        if selected:
            materialized[name] = selected
        else:
            materialized.pop(name, None)
