# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from time import time

from .models import (
    Action,
    AgeRule,
    AllRule,
    AnswerCountRule,
    AnyRule,
    DeleteCardAction,
    IntervalRule,
    MoveAction,
    Policy,
    Rule,
    SuccessfulAnswersRule,
    SuspendAction,
    TagAction,
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
    interval: int
    answer_count: int
    created_at_ms: int
    first_review_ms: int | None
    successful_answers: int
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
class PolicyReport:
    policy: Policy
    qualifying: tuple[CardFacts, ...]
    actionable: tuple[CardFacts, ...]
    missing_first_review: int
    resolved_actions: tuple[ResolvedAction, ...]
    errors: tuple[str, ...] = ()


def matches_rule(rule: Rule, card: CardFacts, now_ms: int) -> bool:
    if isinstance(rule, AgeRule):
        timestamp = card.first_review_ms if rule.source == "first_review" else card.created_at_ms
        return timestamp is not None and now_ms - timestamp >= rule.days * MILLIS_PER_DAY
    if isinstance(rule, IntervalRule):
        return card.interval >= rule.days
    if isinstance(rule, SuccessfulAnswersRule):
        return card.successful_answers >= rule.count
    if isinstance(rule, AnswerCountRule):
        return card.answer_count >= rule.count
    if isinstance(rule, AllRule):
        return all(matches_rule(child, card, now_ms) for child in rule.rules)
    if isinstance(rule, AnyRule):
        return any(matches_rule(child, card, now_ms) for child in rule.rules)
    raise TypeError(f"Unsupported rule: {rule!r}")


def rule_needs_first_review(rule: Rule) -> bool:
    if isinstance(rule, AgeRule):
        return rule.source == "first_review"
    if isinstance(rule, (AllRule, AnyRule)):
        return any(rule_needs_first_review(child) for child in rule.rules)
    return False


def action_is_satisfied(action: ResolvedAction, card: CardFacts) -> bool:
    if isinstance(action.action, TagAction):
        return action.action.tag.casefold() in card.tags
    if isinstance(action.action, SuspendAction):
        return card.queue == SUSPENDED_QUEUE
    if isinstance(action.action, MoveAction):
        return card.home_deck_id == action.target_deck_id
    if isinstance(action.action, DeleteCardAction):
        return False
    raise TypeError(f"Unsupported action: {action.action!r}")


def evaluate_facts(
    policy: Policy,
    cards: list[CardFacts],
    deck_ids: set[int],
    resolved_actions: tuple[ResolvedAction, ...],
    *,
    now_ms: int | None = None,
) -> PolicyReport:
    now_ms = int(time() * 1000) if now_ms is None else now_ms
    in_scope: list[CardFacts] = []
    for card in cards:
        if card.home_deck_id not in deck_ids:
            continue
        if not policy.scope.include_suspended and card.queue == SUSPENDED_QUEUE:
            continue
        if not policy.scope.include_filtered_decks and card.is_filtered:
            continue
        in_scope.append(card)

    qualifying = tuple(card for card in in_scope if matches_rule(policy.rule, card, now_ms))
    actionable = tuple(
        card
        for card in qualifying
        if any(not action_is_satisfied(action, card) for action in resolved_actions)
    )
    missing = (
        sum(card.first_review_ms is None for card in in_scope)
        if rule_needs_first_review(policy.rule)
        else 0
    )
    return PolicyReport(
        policy=policy,
        qualifying=qualifying,
        actionable=actionable,
        missing_first_review=missing,
        resolved_actions=resolved_actions,
    )
