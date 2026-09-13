# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki.collection import OpChanges

from .models import DeleteCardAction, MoveAction, SuspendAction, TagAction

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport, ResolvedAction


@dataclass(frozen=True)
class ExecutionPlan:
    tags: tuple[tuple[str, tuple[int, ...]], ...]
    suspend_card_ids: tuple[int, ...]
    moves: tuple[tuple[int, tuple[int, ...]], ...]
    delete_card_ids: tuple[int, ...]
    conflicted_card_ids: tuple[int, ...]
    planned_card_ids: tuple[int, ...]

    @property
    def card_count(self) -> int:
        return len(self.planned_card_ids)

    @property
    def is_empty(self) -> bool:
        return not (self.tags or self.suspend_card_ids or self.moves or self.delete_card_ids)


@dataclass(frozen=True)
class ExecutionResult:
    changes: OpChanges
    affected_cards: int
    conflicts: int


def build_execution_plan(reports: tuple[PolicyReport, ...]) -> ExecutionPlan:
    card_actions: dict[int, set[ResolvedAction]] = {}
    card_notes: dict[int, int] = {}
    for report in reports:
        for card in report.actionable:
            card_notes[card.card_id] = card.note_id
            card_actions.setdefault(card.card_id, set()).update(report.resolved_actions)

    conflicts: set[int] = set()
    for card_id, actions in card_actions.items():
        move_targets = {
            action.target_deck_id for action in actions if isinstance(action.action, MoveAction)
        }
        has_delete = any(isinstance(action.action, DeleteCardAction) for action in actions)
        if len(move_targets) > 1 or (has_delete and len(actions) > 1):
            conflicts.add(card_id)

    tags: dict[str, set[int]] = {}
    suspend: set[int] = set()
    moves: dict[int, set[int]] = {}
    delete: set[int] = set()
    for card_id, actions in card_actions.items():
        if card_id in conflicts:
            continue
        for resolved in actions:
            action = resolved.action
            if isinstance(action, TagAction):
                tags.setdefault(action.tag, set()).add(card_notes[card_id])
            elif isinstance(action, SuspendAction):
                suspend.add(card_id)
            elif isinstance(action, MoveAction):
                if resolved.target_deck_id is not None:
                    moves.setdefault(resolved.target_deck_id, set()).add(card_id)
            elif isinstance(action, DeleteCardAction):
                delete.add(card_id)

    return ExecutionPlan(
        tags=tuple((tag, tuple(sorted(ids))) for tag, ids in sorted(tags.items())),
        suspend_card_ids=tuple(sorted(suspend)),
        moves=tuple((deck_id, tuple(sorted(ids))) for deck_id, ids in sorted(moves.items())),
        delete_card_ids=tuple(sorted(delete)),
        conflicted_card_ids=tuple(sorted(conflicts)),
        planned_card_ids=tuple(sorted(set(card_actions) - conflicts)),
    )


def execute_plan(col: Collection, plan: ExecutionPlan, undo_name: str) -> ExecutionResult:
    if plan.is_empty:
        return ExecutionResult(
            changes=OpChanges(),
            affected_cards=0,
            conflicts=len(plan.conflicted_card_ids),
        )
    undo_target = col.add_custom_undo_entry(undo_name)
    for tag, note_ids in plan.tags:
        col.tags.bulk_add(note_ids, tag)
    for deck_id, card_ids in plan.moves:
        col.set_deck(card_ids, deck_id)
    if plan.suspend_card_ids:
        col.sched.suspend_cards(plan.suspend_card_ids)
    if plan.delete_card_ids:
        col.remove_cards_and_orphaned_notes(plan.delete_card_ids)
    changes = col.merge_undo_entries(undo_target)
    return ExecutionResult(
        changes=changes,
        affected_cards=plan.card_count,
        conflicts=len(plan.conflicted_card_ids),
    )
