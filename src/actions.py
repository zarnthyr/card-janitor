# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki.collection import OpChanges

from .engine import action_is_satisfied
from .models import (
    DeleteCardAction,
    DeleteNoteAction,
    MoveAction,
    RemoveTagAction,
    ReplaceTagsAction,
    SuspendAction,
    TagAction,
    UnsuspendAction,
    action_targets_note,
)

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport, ResolvedAction


@dataclass(frozen=True)
class ExecutionPlan:
    tags: tuple[tuple[str, tuple[int, ...]], ...]
    remove_tags: tuple[tuple[str, tuple[int, ...]], ...]
    replace_tags: tuple[tuple[tuple[str, ...], tuple[int, ...]], ...]
    suspend_card_ids: tuple[int, ...]
    unsuspend_card_ids: tuple[int, ...]
    moves: tuple[tuple[int, tuple[int, ...]], ...]
    delete_card_ids: tuple[int, ...]
    delete_note_ids: tuple[int, ...]
    conflicted_card_ids: tuple[int, ...]
    planned_card_ids: tuple[int, ...]

    @property
    def card_count(self) -> int:
        return len(self.planned_card_ids)

    @property
    def is_empty(self) -> bool:
        return not (
            self.tags
            or self.remove_tags
            or self.replace_tags
            or self.suspend_card_ids
            or self.unsuspend_card_ids
            or self.moves
            or self.delete_card_ids
            or self.delete_note_ids
        )


@dataclass(frozen=True)
class ExecutionResult:
    changes: OpChanges
    affected_cards: int
    conflicts: int


def build_execution_plan(  # noqa: PLR0912
    reports: tuple[PolicyReport, ...], col: Collection | None = None
) -> ExecutionPlan:
    card_actions: dict[int, set[ResolvedAction]] = {}
    desired_card_actions: dict[int, set[ResolvedAction]] = {}
    card_notes: dict[int, int] = {}
    note_wide_ids: set[int] = set()
    for report in reports:
        desired = report.card_actions or tuple(
            (card, report.resolved_actions) for card in report.qualifying
        )
        per_card = {card.card_id: actions for card, actions in desired}
        for card, actions in desired:
            card_notes[card.card_id] = card.note_id
            desired_card_actions.setdefault(card.card_id, set()).update(actions)
            if any(action_targets_note(action.action) for action in actions):
                note_wide_ids.add(card.note_id)
        for card in report.actionable:
            card_notes[card.card_id] = card.note_id
            card_actions.setdefault(card.card_id, set()).update(
                action
                for action in per_card.get(card.card_id, report.resolved_actions)
                if not action_is_satisfied(action, card)
            )

    conflicts: set[int] = set()
    for card_id, actions in desired_card_actions.items():
        move_targets = {
            action.target_deck_id for action in actions if isinstance(action.action, MoveAction)
        }
        has_delete = any(
            isinstance(action.action, (DeleteCardAction, DeleteNoteAction)) for action in actions
        )
        has_suspend = any(isinstance(action.action, SuspendAction) for action in actions)
        has_unsuspend = any(isinstance(action.action, UnsuspendAction) for action in actions)
        if card_id in card_actions and (
            len(move_targets) > 1
            or (has_delete and len(actions) > 1)
            or (has_suspend and has_unsuspend)
        ):
            conflicts.add(card_id)

    note_actions: dict[int, set[ResolvedAction]] = {}
    all_note_actions: dict[int, set[ResolvedAction]] = {}
    note_cards: dict[int, set[int]] = {}
    for card_id, actions in desired_card_actions.items():
        note_id = card_notes[card_id]
        if card_id in card_actions:
            note_cards.setdefault(note_id, set()).add(card_id)
        all_note_actions.setdefault(note_id, set()).update(actions)
        note_actions.setdefault(note_id, set()).update(
            action
            for action in actions
            if isinstance(action.action, (TagAction, RemoveTagAction, ReplaceTagsAction))
        )
    for note_id, actions in note_actions.items():
        added = {
            action.action.tag.casefold()
            for action in actions
            if isinstance(action.action, TagAction)
        }
        removed = {
            action.action.tag.casefold()
            for action in actions
            if isinstance(action.action, RemoveTagAction)
        }
        replacements = {
            action.action.tags for action in actions if isinstance(action.action, ReplaceTagsAction)
        }
        if added & removed or len(replacements) > 1 or (replacements and (added or removed)):
            conflicts.update(note_cards.get(note_id, ()))
    for note_id, actions in all_note_actions.items():
        if any(isinstance(action.action, DeleteNoteAction) for action in actions) and any(
            not isinstance(action.action, DeleteNoteAction) for action in actions
        ):
            conflicts.update(note_cards.get(note_id, ()))
    # A note-wide operation is atomic: never apply it to only some siblings.
    for note_id in note_wide_ids:
        if conflicts.intersection(note_cards.get(note_id, ())):
            conflicts.update(note_cards.get(note_id, ()))

    tags: dict[str, set[int]] = {}
    remove_tags: dict[str, set[int]] = {}
    replace_tags: dict[tuple[str, ...], set[int]] = {}
    suspend: set[int] = set()
    unsuspend: set[int] = set()
    moves: dict[int, set[int]] = {}
    delete: set[int] = set()
    delete_notes: set[int] = set()
    for card_id, actions in card_actions.items():
        if card_id in conflicts:
            continue
        for resolved in actions:
            action = resolved.action
            if isinstance(action, TagAction):
                tags.setdefault(action.tag, set()).add(card_notes[card_id])
            elif isinstance(action, RemoveTagAction):
                remove_tags.setdefault(action.tag, set()).add(card_notes[card_id])
            elif isinstance(action, ReplaceTagsAction):
                replace_tags.setdefault(action.tags, set()).add(card_notes[card_id])
            elif isinstance(action, SuspendAction):
                suspend.add(card_id)
            elif isinstance(action, UnsuspendAction):
                unsuspend.add(card_id)
            elif isinstance(action, MoveAction):
                if resolved.target_deck_id is not None:
                    moves.setdefault(resolved.target_deck_id, set()).add(card_id)
            elif isinstance(action, DeleteCardAction):
                delete.add(card_id)
            elif isinstance(action, DeleteNoteAction):
                delete_notes.add(card_notes[card_id])

    planned_card_ids = set(card_actions) - conflicts
    if col is not None:
        for note_id in delete_notes:
            planned_card_ids.update(int(card_id) for card_id in col.card_ids_of_note(note_id))

    return ExecutionPlan(
        tags=tuple((tag, tuple(sorted(ids))) for tag, ids in sorted(tags.items())),
        remove_tags=tuple((tag, tuple(sorted(ids))) for tag, ids in sorted(remove_tags.items())),
        replace_tags=tuple(
            (replacement, tuple(sorted(ids))) for replacement, ids in sorted(replace_tags.items())
        ),
        suspend_card_ids=tuple(sorted(suspend)),
        unsuspend_card_ids=tuple(sorted(unsuspend)),
        moves=tuple((deck_id, tuple(sorted(ids))) for deck_id, ids in sorted(moves.items())),
        delete_card_ids=tuple(sorted(delete)),
        delete_note_ids=tuple(sorted(delete_notes)),
        conflicted_card_ids=tuple(sorted(conflicts)),
        planned_card_ids=tuple(sorted(planned_card_ids)),
    )


def execute_plan(col: Collection, plan: ExecutionPlan, undo_name: str) -> ExecutionResult:
    if plan.is_empty:
        return ExecutionResult(
            changes=OpChanges(),
            affected_cards=0,
            conflicts=len(plan.conflicted_card_ids),
        )
    undo_target = col.add_custom_undo_entry(undo_name)
    affected_card_ids = set(plan.planned_card_ids)
    for note_id in plan.delete_note_ids:
        affected_card_ids.update(int(card_id) for card_id in col.card_ids_of_note(note_id))
    for tag, note_ids in plan.tags:
        col.tags.bulk_add(note_ids, tag)
    for tag, note_ids in plan.remove_tags:
        col.tags.bulk_remove(note_ids, tag)
    replacement_notes = []
    for tags, note_ids in plan.replace_tags:
        for note_id in note_ids:
            note = col.get_note(note_id)
            note.tags = list(tags)
            replacement_notes.append(note)
    if replacement_notes:
        col.update_notes(replacement_notes)
    for deck_id, card_ids in plan.moves:
        col.set_deck(card_ids, deck_id)
    if plan.suspend_card_ids:
        col.sched.suspend_cards(plan.suspend_card_ids)
    if plan.unsuspend_card_ids:
        col.sched.unsuspend_cards(plan.unsuspend_card_ids)
    if plan.delete_card_ids:
        col.remove_cards_and_orphaned_notes(plan.delete_card_ids)
    if plan.delete_note_ids:
        col.remove_notes(plan.delete_note_ids)
    changes = col.merge_undo_entries(undo_target)
    return ExecutionResult(
        changes=changes,
        affected_cards=len(affected_card_ids),
        conflicts=len(plan.conflicted_card_ids),
    )
