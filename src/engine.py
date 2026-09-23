# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from time import time

from .history_semantics import (
    NOT_COLLECTED_PROVENANCE,
    AnyNodeMatch,
    BoundaryDisposition,
    CardMatchProvenance,
    PolicyMatchProvenance,
)
from .log import exception
from .models import (
    Action,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnswerCountCondition,
    AnyConditions,
    CardFlagCondition,
    CardStateCondition,
    ClearFlagAction,
    ConditionExpression,
    CorrectAnswerCountCondition,
    CorrectAnswerRateCondition,
    DeleteCardAction,
    DeleteNoteAction,
    FsrsDifficultyCondition,
    FsrsRetrievabilityCondition,
    FsrsStabilityCondition,
    IntervalCondition,
    LapseCountCondition,
    MoveAction,
    OverdueCondition,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    SetFlagAction,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    Sm2EaseCondition,
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
    note_type_id: int = 0
    card_type_idx: int = 0
    flag: int = 0
    answer_count: int = 0
    correct_answer_count: int = 0
    lapses: int = 0
    overdue_days: int | None = None
    fsrs_stability: float | None = None
    fsrs_difficulty_percent: float | None = None
    fsrs_retrievability_percent: float | None = None
    sm2_ease_percent: float | None = None
    last_review_ms: int | None = None

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
    """Evaluation results with desired actions for every targeted card.

    card_actions includes satisfied intentions for conflict detection, whereas
    actionable contains only cards requiring a change. Note-wide actions may
    target siblings outside qualifying (the in-scope matching cards).
    """

    policy: Policy
    qualifying: tuple[CardFacts, ...]
    actionable: tuple[CardFacts, ...]
    missing_first_review: int
    resolved_actions: tuple[ResolvedAction, ...]
    card_actions: tuple[tuple[CardFacts, tuple[ResolvedAction, ...]], ...]
    errors: tuple[str, ...] = ()
    match_provenance: PolicyMatchProvenance = NOT_COLLECTED_PROVENANCE
    boundary_dispositions: tuple[BoundaryDisposition, ...] = ()
    evaluation_qualifying_cards: int | None = None
    evaluation_actionable_cards: int | None = None


def _matches_number(actual: float, operator: str, expected: float) -> bool:
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
            card.first_review_ms
            if condition.source == "first_review"
            else card.last_review_ms
            if condition.source == "last_review"
            else card.created_at_ms
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
    if isinstance(condition, CardFlagCondition):
        flag_names = (
            "none",
            "red",
            "orange",
            "green",
            "blue",
            "pink",
            "turquoise",
            "purple",
        )
        flag = flag_names[card.flag] if 0 <= card.flag < len(flag_names) else "none"
        return flag in condition.flags
    if isinstance(condition, AnswerCountCondition):
        return _matches_number(card.answer_count, condition.operator, condition.count)
    if isinstance(condition, CorrectAnswerCountCondition):
        return _matches_number(card.correct_answer_count, condition.operator, condition.count)
    if isinstance(condition, LapseCountCondition):
        return _matches_number(card.lapses, condition.operator, condition.count)
    if isinstance(condition, CorrectAnswerRateCondition):
        if not card.answer_count:
            return False
        percent = card.correct_answer_count * 100 / card.answer_count
        return _matches_number(percent, condition.operator, condition.percent)
    if isinstance(condition, OverdueCondition):
        return card.overdue_days is not None and _matches_number(
            card.overdue_days, condition.operator, condition.days
        )
    if isinstance(condition, FsrsStabilityCondition):
        return card.fsrs_stability is not None and _matches_number(
            card.fsrs_stability, condition.operator, condition.days
        )
    if isinstance(condition, FsrsDifficultyCondition):
        return card.fsrs_difficulty_percent is not None and _matches_number(
            card.fsrs_difficulty_percent, condition.operator, condition.percent
        )
    if isinstance(condition, FsrsRetrievabilityCondition):
        return card.fsrs_retrievability_percent is not None and _matches_number(
            card.fsrs_retrievability_percent, condition.operator, condition.percent
        )
    if isinstance(condition, Sm2EaseCondition):
        return card.sm2_ease_percent is not None and _matches_number(
            card.sm2_ease_percent, condition.operator, condition.percent
        )
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


def _trace_conditions(
    condition: ConditionExpression,
    card: CardFacts,
    now_ms: int,
    *,
    note: NoteFacts | None,
    path: str,
) -> tuple[bool, tuple[AnyNodeMatch, ...]]:
    """Evaluate every alternative while retaining traces only on successful routes."""
    if isinstance(condition, (AllConditions, AnyConditions)):
        evaluated = tuple(
            _trace_conditions(
                child,
                card,
                now_ms,
                note=note,
                path=f"{path}/conditions/{index}",
            )
            for index, child in enumerate(condition.conditions)
        )
        matched = (
            all(child_matched for child_matched, _trace in evaluated)
            if isinstance(condition, AllConditions)
            else any(child_matched for child_matched, _trace in evaluated)
        )
        if not matched:
            return False, ()
        child_traces = tuple(
            item for child_matched, trace in evaluated if child_matched for item in trace
        )
        if isinstance(condition, AllConditions):
            return True, child_traces
        matched_children = tuple(
            f"{path}/conditions/{index}"
            for index, (child_matched, _trace) in enumerate(evaluated)
            if child_matched
        )
        return True, (AnyNodeMatch(path, matched_children), *child_traces)
    return matches_conditions(condition, card, now_ms, note=note), ()


def collect_match_provenance(
    condition: ConditionExpression,
    cards: tuple[CardFacts, ...],
    now_ms: int,
    *,
    note_facts: dict[int, NoteFacts] | None,
) -> PolicyMatchProvenance:
    """Collect complete explanatory traces without affecting authoritative matching."""
    try:
        matches = []
        for card in cards:
            matched, any_nodes = _trace_conditions(
                condition,
                card,
                now_ms,
                note=note_facts.get(card.note_id) if note_facts is not None else None,
                path="",
            )
            if not matched:
                return PolicyMatchProvenance("unavailable", reason_code="trace_mismatch")
            matches.append(CardMatchProvenance(card.card_id, any_nodes))
        return PolicyMatchProvenance("complete", tuple(matches))
    except Exception:
        with suppress(Exception):
            exception("match provenance collection failed")
        return PolicyMatchProvenance("unavailable", reason_code="trace_evaluation_failed")


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


def conditions_need_history(condition: ConditionExpression) -> bool:
    if isinstance(
        condition,
        (
            ReviewHistoryCondition,
            SiblingReviewHistoryCondition,
            AnswerCountCondition,
            CorrectAnswerCountCondition,
            CorrectAnswerRateCondition,
        ),
    ):
        return True
    if isinstance(condition, AgeCondition):
        return condition.source in {"first_review", "last_review"}
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(conditions_need_history(child) for child in condition.conditions)
    return False


def conditions_need_fsrs(condition: ConditionExpression) -> bool:
    if isinstance(
        condition,
        (FsrsStabilityCondition, FsrsDifficultyCondition, FsrsRetrievabilityCondition),
    ):
        return True
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(conditions_need_fsrs(child) for child in condition.conditions)
    return False


def conditions_need_sm2(condition: ConditionExpression) -> bool:
    if isinstance(condition, Sm2EaseCondition):
        return True
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(conditions_need_sm2(child) for child in condition.conditions)
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
    if isinstance(action.action, SetFlagAction):
        return (
            card.flag
            == {
                "red": 1,
                "orange": 2,
                "green": 3,
                "blue": 4,
                "pink": 5,
                "turquoise": 6,
                "purple": 7,
            }[action.action.flag]
        )
    if isinstance(action.action, ClearFlagAction):
        return card.flag == 0
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
    collect_provenance: bool = False,
) -> PolicyReport:
    now_ms = int(time() * 1000) if now_ms is None else now_ms
    in_scope: list[CardFacts] = []
    for card in cards:
        if card.home_deck_id not in deck_ids:
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
    provenance = (
        collect_match_provenance(
            policy.conditions,
            qualifying,
            now_ms,
            note_facts=note_facts,
        )
        if collect_provenance
        else NOT_COLLECTED_PROVENANCE
    )
    return PolicyReport(
        policy=policy,
        qualifying=qualifying,
        actionable=actionable,
        missing_first_review=missing,
        resolved_actions=resolved_actions,
        card_actions=tuple((card, resolved_actions) for card in qualifying),
        match_provenance=provenance,
        evaluation_qualifying_cards=len(qualifying),
        evaluation_actionable_cards=len(actionable),
    )
