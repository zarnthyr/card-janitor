# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import pytest
from card_janitor.engine import (
    MILLIS_PER_DAY,
    CardFacts,
    NoteFacts,
    ResolvedAction,
    action_is_satisfied,
    evaluate_facts,
    matches_conditions,
)
from card_janitor.models import (
    Action,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnyConditions,
    CardStateCondition,
    ConditionExpression,
    DeckSelector,
    IntervalCondition,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    Scope,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    SuspendAction,
    SuspensionCondition,
    TagAction,
    TagCondition,
    UnsuspendAction,
)


def facts(**overrides: object) -> CardFacts:
    values = {
        "card_id": 1000,
        "note_id": 10,
        "deck_id": 1,
        "original_deck_id": 0,
        "queue": 2,
        "card_type": 2,
        "interval": 100,
        "created_at_ms": 1000,
        "first_review_ms": 2000,
        "tags": frozenset(),
    }
    values.update(overrides)
    return CardFacts(**values)


def policy(
    conditions: ConditionExpression | None = None,
    actions: tuple[Action, ...] | None = None,
    scope: Scope | None = None,
) -> Policy:
    return Policy(
        id="test",
        name="Test",
        mode="on_demand",
        scope=scope or Scope((DeckSelector("Mining"),)),
        conditions=conditions or AgeCondition(365, "first_review", "gte"),
        actions=actions or (SuspendAction(),),
    )


def test_sibling_conditions_handle_all_any_none_and_compound_conditions() -> None:
    card = facts()
    for count in (0, 1, 2):
        note = NoteFacts(2, count, count)
        for condition_type in (SiblingSuspensionCondition, SiblingReviewHistoryCondition):
            for operator, expected in {
                "all": count == 2,
                "any": count > 0,
                "none": count == 0,
            }.items():
                condition = condition_type(operator)
                assert matches_conditions(condition, card, 3000, note=note) is expected
                assert (
                    matches_conditions(
                        AllConditions((AllCardsCondition(), condition)), card, 3000, note=note
                    )
                    is expected
                )
                assert (
                    matches_conditions(
                        AnyConditions((condition, TagCondition(("missing",), "contains_any"))),
                        card,
                        3000,
                        note=note,
                    )
                    is expected
                )


def test_first_review_age_uses_elapsed_days_inclusively() -> None:
    condition = AgeCondition(365, "first_review", "gte")
    card = facts(first_review_ms=5000)
    assert not matches_conditions(condition, card, 5000 + 365 * MILLIS_PER_DAY - 1)
    assert matches_conditions(condition, card, 5000 + 365 * MILLIS_PER_DAY)


def test_all_cards_condition_matches_every_card() -> None:
    assert matches_conditions(AllCardsCondition(), facts(), 0)


def test_first_review_age_does_not_approximate_missing_history() -> None:
    for operator in ("gt", "gte", "eq", "lte", "lt"):
        assert not matches_conditions(
            AgeCondition(1, "first_review", operator), facts(first_review_ms=None), 10**12
        )


def test_any_conditions_matches_either_child() -> None:
    condition = AnyConditions(
        (AgeCondition(365, "first_review", "gte"), IntervalCondition(180, "gte"))
    )
    assert matches_conditions(condition, facts(interval=180), 2000 + MILLIS_PER_DAY)


def test_interval_condition_uses_current_interval_without_requiring_revlog() -> None:
    assert matches_conditions(
        IntervalCondition(180, "gte"), facts(interval=180, first_review_ms=None), 0
    )


def test_card_state_membership_uses_card_type_even_when_buried() -> None:
    condition = CardStateCondition(("new", "learning"))
    assert matches_conditions(condition, facts(card_type=0, queue=-2), 0)
    assert not matches_conditions(condition, facts(card_type=2), 0)


def test_review_history_uses_genuine_answer_entries() -> None:
    condition = ReviewHistoryCondition("not_exists")
    assert matches_conditions(condition, facts(card_type=0, first_review_ms=None), 0)
    assert not matches_conditions(condition, facts(card_type=0, first_review_ms=2000), 0)
    assert matches_conditions(ReviewHistoryCondition("exists"), facts(first_review_ms=2000), 0)


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("contains_any", True), ("contains_all", False), ("contains_none", False)],
)
def test_tag_conditions_are_case_insensitive(operator: str, expected: bool) -> None:
    condition = TagCondition(("LEECH", "difficult"), operator)
    assert matches_conditions(condition, facts(tags=frozenset({"leech"})), 0) is expected


def test_suspension_condition_uses_queue_independently_of_card_state() -> None:
    assert matches_conditions(SuspensionCondition("is_suspended"), facts(queue=-1, card_type=2), 0)
    assert matches_conditions(
        SuspensionCondition("is_not_suspended"), facts(queue=2, card_type=2), 0
    )


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("gt", False), ("gte", True), ("eq", True), ("lte", True), ("lt", False)],
)
def test_numeric_operators(operator: str, expected: bool) -> None:
    assert matches_conditions(IntervalCondition(30, operator), facts(interval=30), 0) is expected


def test_exact_age_matches_one_completed_day_bucket() -> None:
    condition = AgeCondition(30, "first_review", "eq")
    card = facts(first_review_ms=5000)
    assert matches_conditions(condition, card, 5000 + 30 * MILLIS_PER_DAY)
    assert matches_conditions(condition, card, 5000 + 31 * MILLIS_PER_DAY - 1)
    assert not matches_conditions(condition, card, 5000 + 31 * MILLIS_PER_DAY)


def test_scope_excludes_suspended_and_filtered_by_default() -> None:
    cards = [facts(), facts(card_id=2, queue=-1), facts(card_id=3, original_deck_id=1)]
    report = evaluate_facts(
        policy(conditions=IntervalCondition(1, "gte")),
        cards,
        {1},
        (ResolvedAction(SuspendAction()),),
        now_ms=10**12,
    )
    assert [card.card_id for card in report.qualifying] == [1000]


def test_actionable_excludes_cards_with_all_actions_satisfied() -> None:
    card = facts(tags=frozenset({"retired"}), queue=-1)
    configured = policy(
        actions=(TagAction("retired"), SuspendAction()),
        scope=Scope((DeckSelector("Mining"),), include_suspended=True),
        conditions=IntervalCondition(1, "gte"),
    )
    actions = (ResolvedAction(TagAction("retired")), ResolvedAction(SuspendAction()))
    report = evaluate_facts(configured, [card], {1}, actions, now_ms=10**12)
    assert len(report.qualifying) == 1
    assert not report.actionable


def test_tag_satisfaction_is_case_insensitive() -> None:
    assert action_is_satisfied(
        ResolvedAction(TagAction("Retired")), facts(tags=frozenset({"retired"}))
    )


def test_inverse_and_replace_action_satisfaction() -> None:
    card = facts(tags=frozenset({"leech", "difficult"}), queue=-1)
    assert not action_is_satisfied(ResolvedAction(RemoveTagAction("LEECH")), card)
    assert action_is_satisfied(ResolvedAction(RemoveTagAction("missing")), card)
    assert action_is_satisfied(ResolvedAction(ReplaceTagsAction(("Difficult", "Leech"))), card)
    assert not action_is_satisfied(ResolvedAction(UnsuspendAction()), card)
