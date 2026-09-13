# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from card_retirement.engine import (
    MILLIS_PER_DAY,
    CardFacts,
    ResolvedAction,
    action_is_satisfied,
    evaluate_facts,
    matches_rule,
)
from card_retirement.models import (
    Action,
    AgeRule,
    AnyRule,
    IntervalRule,
    Policy,
    Rule,
    Scope,
    SuspendAction,
    TagAction,
)


def facts(**overrides: object) -> CardFacts:
    values = {
        "card_id": 1000,
        "note_id": 10,
        "deck_id": 1,
        "original_deck_id": 0,
        "queue": 2,
        "interval": 100,
        "answer_count": 8,
        "created_at_ms": 1000,
        "first_review_ms": 2000,
        "successful_answers": 6,
        "tags": frozenset(),
    }
    values.update(overrides)
    return CardFacts(**values)


def policy(
    rule: Rule | None = None,
    actions: tuple[Action, ...] | None = None,
    scope: Scope | None = None,
) -> Policy:
    return Policy(
        id="test",
        name="Test",
        enabled=True,
        mode="manual",
        scope=scope or Scope(("Mining",)),
        rule=rule or AgeRule(365, "first_review"),
        actions=actions or (SuspendAction(),),
    )


def test_first_review_age_uses_elapsed_days_inclusively() -> None:
    rule = AgeRule(365, "first_review")
    card = facts(first_review_ms=5000)
    assert not matches_rule(rule, card, 5000 + 365 * MILLIS_PER_DAY - 1)
    assert matches_rule(rule, card, 5000 + 365 * MILLIS_PER_DAY)


def test_first_review_age_does_not_approximate_missing_history() -> None:
    assert not matches_rule(AgeRule(1, "first_review"), facts(first_review_ms=None), 10**12)


def test_any_rule_matches_either_child() -> None:
    rule = AnyRule((AgeRule(365, "first_review"), IntervalRule(180)))
    assert matches_rule(rule, facts(interval=180), 2000 + MILLIS_PER_DAY)


def test_interval_rule_uses_current_interval_without_requiring_revlog() -> None:
    assert matches_rule(IntervalRule(180), facts(interval=180, first_review_ms=None), 0)


def test_scope_excludes_suspended_and_filtered_by_default() -> None:
    cards = [facts(), facts(card_id=2, queue=-1), facts(card_id=3, original_deck_id=1)]
    report = evaluate_facts(
        policy(rule=IntervalRule(1)),
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
        scope=Scope(("Mining",), include_suspended=True),
        rule=IntervalRule(1),
    )
    actions = (ResolvedAction(TagAction("retired")), ResolvedAction(SuspendAction()))
    report = evaluate_facts(configured, [card], {1}, actions, now_ms=10**12)
    assert len(report.qualifying) == 1
    assert not report.actionable


def test_tag_satisfaction_is_case_insensitive() -> None:
    assert action_is_satisfied(
        ResolvedAction(TagAction("Retired")), facts(tags=frozenset({"retired"}))
    )
