# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from anki.collection import OpChanges

from .engine import action_is_satisfied
from .log import exception
from .models import (
    DeleteCardAction,
    DeleteNoteAction,
    MoveAction,
    RemoveTagAction,
    ReplaceTagsAction,
    SuspendAction,
    TagAction,
    UnsuspendAction,
    action_expands_to_siblings,
)
from .presentation import describe_action

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport, ResolvedAction


@dataclass(frozen=True)
class ConflictDetail:
    card_id: int
    policies: tuple[str, ...]
    reasons: tuple[str, ...]


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
    conflict_details: tuple[ConflictDetail, ...]

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


class CleanupError(RuntimeError):
    """Cleanup failed; earlier backend operations may have completed."""


def build_execution_plan(  # noqa: PLR0912
    reports: tuple[PolicyReport, ...], col: Collection | None = None
) -> ExecutionPlan:
    card_actions: dict[int, set[ResolvedAction]] = {}
    desired_card_actions: dict[int, set[ResolvedAction]] = {}
    card_notes: dict[int, int] = {}
    note_wide_ids: set[int] = set()
    card_policies: dict[int, set[str]] = {}
    note_policies: dict[int, set[str]] = {}
    action_sources: dict[int, dict[ResolvedAction, set[str]]] = {}
    card_action_sources: dict[int, dict[ResolvedAction, set[str]]] = {}
    for report in reports:
        desired = report.card_actions
        per_card = {card.card_id: actions for card, actions in desired}
        for card, actions in desired:
            for action in actions:
                card_action_sources.setdefault(card.card_id, {}).setdefault(action, set()).add(
                    report.policy.name
                )
                action_sources.setdefault(card.note_id, {}).setdefault(action, set()).add(
                    report.policy.name
                )
            card_policies.setdefault(card.card_id, set()).add(report.policy.name)
            note_policies.setdefault(card.note_id, set()).add(report.policy.name)
            card_notes[card.card_id] = card.note_id
            desired_card_actions.setdefault(card.card_id, set()).update(actions)
            if any(action_expands_to_siblings(action.action) for action in actions):
                note_wide_ids.add(card.note_id)
        for card in report.actionable:
            card_notes[card.card_id] = card.note_id
            card_actions.setdefault(card.card_id, set()).update(
                action for action in per_card[card.card_id] if not action_is_satisfied(action, card)
            )

    conflicts: set[int] = set()
    reasons: dict[int, set[str]] = {}

    def mark_conflict(
        ids: set[int] | tuple[int, ...], reason: str, policies: set[str] | None = None
    ) -> None:
        for card_id in ids:
            conflicts.add(card_id)
            reasons.setdefault(card_id, set()).add(reason)
            if policies is not None:
                card_policies[card_id].update(policies)

    for card_id, actions in desired_card_actions.items():
        move_targets = {
            action.target_deck_id for action in actions if isinstance(action.action, MoveAction)
        }
        has_delete = any(
            isinstance(action.action, (DeleteCardAction, DeleteNoteAction)) for action in actions
        )
        has_suspend = any(isinstance(action.action, SuspendAction) for action in actions)
        has_unsuspend = any(isinstance(action.action, UnsuspendAction) for action in actions)
        if card_id in card_actions:
            if len(move_targets) > 1:
                mark_conflict((card_id,), "Move actions specify different destination decks")
            if has_delete and len(actions) > 1:
                mark_conflict((card_id,), "Deletion is combined with another action")
            if has_suspend and has_unsuspend:
                mark_conflict((card_id,), "Suspend and unsuspend actions target the same card")

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
            frozenset(tag.casefold() for tag in action.action.tags)
            for action in actions
            if isinstance(action.action, ReplaceTagsAction)
        }
        ids = note_cards.get(note_id, set())
        if added & removed:
            mark_conflict(
                ids,
                "Tags are both added and removed: " + ", ".join(sorted(added & removed)),
                note_policies[note_id],
            )
        if len(replacements) > 1:
            mark_conflict(
                ids, "Tag replacements specify different tag sets", note_policies[note_id]
            )
        if replacements and (added or removed):
            mark_conflict(
                ids,
                "Replacing tags is combined with adding or removing tags",
                note_policies[note_id],
            )
    for note_id, actions in all_note_actions.items():
        if any(isinstance(action.action, DeleteNoteAction) for action in actions) and any(
            not isinstance(action.action, DeleteNoteAction) for action in actions
        ):
            mark_conflict(
                note_cards.get(note_id, ()),
                "Deleting a note is combined with another action on that note",
                note_policies[note_id],
            )
    # A note-wide operation is atomic: never apply it to only some siblings.
    for note_id in note_wide_ids:
        if conflicts.intersection(note_cards.get(note_id, ())):
            inherited = {
                reason for card_id in note_cards[note_id] for reason in reasons.get(card_id, ())
            }
            for reason in inherited:
                mark_conflict(note_cards[note_id], reason, note_policies[note_id])
            mark_conflict(
                note_cards[note_id],
                "All affected cards of the note are skipped together",
                note_policies[note_id],
            )

    tags: dict[str, set[int]] = {}
    remove_tags: dict[str, set[int]] = {}
    replace_tags: dict[tuple[str, ...], set[int]] = {}
    replacement_values: dict[tuple[str, ...], tuple[str, ...]] = {}
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
                key = tuple(sorted(tag.casefold() for tag in action.tags))
                replace_tags.setdefault(key, set()).add(card_notes[card_id])
                replacement_values[key] = min(replacement_values.get(key, action.tags), action.tags)
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

    def explain(card_id: int, reason: str) -> str:
        if reason == "All affected cards of the note are skipped together":
            return reason
        types = (
            (MoveAction,)
            if reason.startswith("Move actions")
            else (SuspendAction, UnsuspendAction)
            if reason.startswith("Suspend and unsuspend")
            else (TagAction, RemoveTagAction, ReplaceTagsAction)
            if reason.startswith(("Tags are", "Tag replacements", "Replacing tags"))
            else None
        )
        descriptions: set[str] = set()
        note_id = card_notes[card_id]
        sources = action_sources[note_id]
        if note_id not in note_wide_ids and reason.startswith(
            ("Move actions", "Suspend and unsuspend", "Deletion is")
        ):
            sources = card_action_sources[card_id]
        for resolved, policies in sources.items():
            if types is not None and not isinstance(resolved.action, types):
                continue
            if reason.startswith("Tag replacements") and not isinstance(
                resolved.action, ReplaceTagsAction
            ):
                continue
            if reason.startswith("Tags are"):
                opposing_type = (
                    RemoveTagAction if isinstance(resolved.action, TagAction) else TagAction
                )
                if not isinstance(resolved.action, (TagAction, RemoveTagAction)) or not any(
                    isinstance(other.action, opposing_type)
                    and other.action.tag.casefold() == resolved.action.tag.casefold()
                    for other in sources
                ):
                    continue
            descriptions.update(
                f"{policy}: {describe_action(resolved.action)}" for policy in policies
            )
        return reason + "\n" + "\n".join(sorted(descriptions))

    return ExecutionPlan(
        tags=tuple((tag, tuple(sorted(ids))) for tag, ids in sorted(tags.items())),
        remove_tags=tuple((tag, tuple(sorted(ids))) for tag, ids in sorted(remove_tags.items())),
        replace_tags=tuple(
            (replacement_values[key], tuple(sorted(ids)))
            for key, ids in sorted(replace_tags.items())
        ),
        suspend_card_ids=tuple(sorted(suspend)),
        unsuspend_card_ids=tuple(sorted(unsuspend)),
        moves=tuple((deck_id, tuple(sorted(ids))) for deck_id, ids in sorted(moves.items())),
        delete_card_ids=tuple(sorted(delete)),
        delete_note_ids=tuple(sorted(delete_notes)),
        conflicted_card_ids=tuple(sorted(conflicts)),
        planned_card_ids=tuple(sorted(planned_card_ids)),
        conflict_details=tuple(
            ConflictDetail(
                card_id,
                tuple(sorted(card_policies[card_id])),
                tuple(explain(card_id, reason) for reason in sorted(reasons[card_id])),
            )
            for card_id in sorted(conflicts)
        ),
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
    try:
        _apply_plan(col, plan)
    except Exception as exc:
        try:
            col.merge_undo_entries(undo_target)
        except Exception:
            exception("failed to group undo entries after cleanup failure")
            recovery = "Use Anki's Undo to revert any earlier changes individually."
        else:
            recovery = f"Use Anki's Undo entry {undo_name!r} to revert any completed changes."
        message = f"Cleanup failed: {exc}\n\nEarlier changes may have been applied. {recovery}"
        raise CleanupError(message) from exc
    changes = col.merge_undo_entries(undo_target)
    return ExecutionResult(
        changes=changes,
        affected_cards=len(affected_card_ids),
        conflicts=len(plan.conflicted_card_ids),
    )


def _apply_plan(col: Collection, plan: ExecutionPlan) -> None:
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
