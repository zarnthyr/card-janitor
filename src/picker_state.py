# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from .models import DeckSelector


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
    """None selects current/future types; an explicit set selects current names."""

    def __init__(self, names: list[str], selected: tuple[str, ...] | None) -> None:
        self._selected = None if selected is None else set(selected)
        self.names = tuple(sorted(set(names) | (self._selected or set()), key=str.casefold))

    def selected(self) -> tuple[str, ...] | None:
        return None if self._selected is None else tuple(sorted(self._selected, key=str.casefold))

    def contains(self, name: str) -> bool:
        return self._selected is None or name in self._selected

    def toggle_all(self) -> None:
        self._selected = set() if self._selected is None else None

    def toggle(self, name: str) -> None:
        if name not in self.names:
            raise KeyError(name)
        if self._selected is None:
            self._selected = set(self.names)
        if name in self._selected:
            self._selected.remove(name)
        else:
            self._selected.add(name)
