# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from time import time

from .models import (
    Action,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnyConditions,
    CardStateCondition,
    ConditionExpression,
    DeleteCardAction,
    DeleteNoteAction,
    IntervalCondition,
    MoveAction,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    SuspendAction,
    SuspensionCondition,
    TagAction,
    TagCondition,
    UnsuspendAction,
)

MILLIS_PER_DAY = 86_400_000
SUSPENDED_QUEUE = -1


@dataclass(frozen=True)
class CardFacts:
    card_id: int
    note_id: int
    deck_id: int
    original_deck_id: int
    queue: int
    card_type: int
    interval: int
    created_at_ms: int
    first_review_ms: int | None
    tags: frozenset[str]

    @property
    def home_deck_id(self) -> int:
        return self.original_deck_id or self.deck_id

    @property
    def is_filtered(self) -> bool:
        return self.original_deck_id != 0


@dataclass(frozen=True)
class ResolvedAction:
    action: Action
    target_deck_id: int | None = None


@dataclass(frozen=True)
class NoteFacts:
    card_count: int
    suspended_count: int
    studied_count: int


@dataclass(frozen=True)
class PolicyReport:
    policy: Policy
    qualifying: tuple[CardFacts, ...]
    actionable: tuple[CardFacts, ...]
    missing_first_review: int
    resolved_actions: tuple[ResolvedAction, ...]
    errors: tuple[str, ...] = ()
    card_actions: tuple[tuple[CardFacts, tuple[ResolvedAction, ...]], ...] = ()


def _matches_number(actual: int, operator: str, expected: int) -> bool:
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "eq":
        return actual == expected
    if operator == "lte":
        return actual <= expected
    if operator == "lt":
        return actual < expected
    raise ValueError(f"Unsupported numeric operator: {operator!r}")


def matches_conditions(  # noqa: PLR0911, PLR0912
    condition: ConditionExpression,
    card: CardFacts,
    now_ms: int,
    *,
    note: NoteFacts | None = None,
) -> bool:
    if isinstance(condition, AllCardsCondition):
        return True
    if isinstance(condition, AgeCondition):
        timestamp = (
            card.first_review_ms if condition.source == "first_review" else card.created_at_ms
        )
        if timestamp is None:
            return False
        elapsed_days = (now_ms - timestamp) // MILLIS_PER_DAY
        return _matches_number(elapsed_days, condition.operator, condition.days)
    if isinstance(condition, IntervalCondition):
        return _matches_number(card.interval, condition.operator, condition.days)
    if isinstance(condition, CardStateCondition):
        state = {0: "new", 1: "learning", 2: "review", 3: "relearning"}.get(card.card_type)
        if state is None:
            return False
        return state in condition.states
    if isinstance(condition, ReviewHistoryCondition):
        exists = card.first_review_ms is not None
        return exists if condition.operator == "exists" else not exists
    if isinstance(condition, TagCondition):
        expected = {tag.casefold() for tag in condition.tags}
        if condition.operator == "contains_any":
            return bool(card.tags & expected)
        if condition.operator == "contains_all":
            return expected <= card.tags
        return not bool(card.tags & expected)
    if isinstance(condition, SuspensionCondition):
        suspended = card.queue == SUSPENDED_QUEUE
        return suspended if condition.operator == "is_suspended" else not suspended
    if isinstance(condition, (SiblingSuspensionCondition, SiblingReviewHistoryCondition)):
        if note is None:
            raise TypeError(f"Sibling conditions require note facts for note {card.note_id}")
        count = (
            note.suspended_count
            if isinstance(condition, SiblingSuspensionCondition)
            else note.studied_count
        )
        return {
            "all": count == note.card_count,
            "any": count > 0,
            "none": count == 0,
        }[condition.operator]
    if isinstance(condition, AllConditions):
        return all(
            matches_conditions(child, card, now_ms, note=note) for child in condition.conditions
        )
    if isinstance(condition, AnyConditions):
        return any(
            matches_conditions(child, card, now_ms, note=note) for child in condition.conditions
        )
    raise TypeError(f"Unsupported condition: {condition!r}")


def conditions_need_siblings(condition: ConditionExpression) -> bool:
    if isinstance(condition, (SiblingSuspensionCondition, SiblingReviewHistoryCondition)):
        return True
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(conditions_need_siblings(child) for child in condition.conditions)
    return False


def conditions_need_first_review(condition: ConditionExpression) -> bool:
    if isinstance(condition, AgeCondition):
        return condition.source == "first_review"
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(conditions_need_first_review(child) for child in condition.conditions)
    return False


def action_is_satisfied(  # noqa: PLR0911
    action: ResolvedAction, card: CardFacts
) -> bool:
    if isinstance(action.action, TagAction):
        return action.action.tag.casefold() in card.tags
    if isinstance(action.action, RemoveTagAction):
        return action.action.tag.casefold() not in card.tags
    if isinstance(action.action, ReplaceTagsAction):
        return card.tags == {tag.casefold() for tag in action.action.tags}
    if isinstance(action.action, SuspendAction):
        return card.queue == SUSPENDED_QUEUE
    if isinstance(action.action, UnsuspendAction):
        return card.queue != SUSPENDED_QUEUE
    if isinstance(action.action, MoveAction):
        return card.home_deck_id == action.target_deck_id
    if isinstance(action.action, DeleteCardAction):
        return False
    if isinstance(action.action, DeleteNoteAction):
        return False
    raise TypeError(f"Unsupported action: {action.action!r}")


def evaluate_facts(
    policy: Policy,
    cards: list[CardFacts],
    deck_ids: set[int],
    resolved_actions: tuple[ResolvedAction, ...],
    *,
    now_ms: int | None = None,
    note_facts: dict[int, NoteFacts] | None = None,
) -> PolicyReport:
    now_ms = int(time() * 1000) if now_ms is None else now_ms
    in_scope: list[CardFacts] = []
    for card in cards:
        if card.home_deck_id not in deck_ids:
            continue
        if not policy.scope.include_suspended and card.queue == SUSPENDED_QUEUE:
            continue
        if card.is_filtered:
            continue
        in_scope.append(card)

    qualifying = tuple(
        card
        for card in in_scope
        if matches_conditions(
            policy.conditions,
            card,
            now_ms,
            note=note_facts.get(card.note_id) if note_facts is not None else None,
        )
    )
    actionable = tuple(
        card
        for card in qualifying
        if any(not action_is_satisfied(action, card) for action in resolved_actions)
    )
    missing = (
        sum(card.first_review_ms is None for card in in_scope)
        if conditions_need_first_review(policy.conditions)
        else 0
    )
    return PolicyReport(
        policy=policy,
        qualifying=qualifying,
        actionable=actionable,
        missing_first_review=missing,
        resolved_actions=resolved_actions,
    )
