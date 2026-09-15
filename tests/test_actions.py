# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from card_janitor.actions import build_execution_plan
from card_janitor.engine import CardFacts, PolicyReport, ResolvedAction
from card_janitor.models import (
    IntervalCondition,
    MoveAction,
    Policy,
    Scope,
    SuspendAction,
    TagAction,
)


def card(card_id: int = 1) -> CardFacts:
    return CardFacts(card_id, 10, 1, 0, 2, 2, 100, 1000, 2000, frozenset())


def report(actions: tuple[ResolvedAction, ...], value: CardFacts | None = None) -> PolicyReport:
    item = value or card()
    policy = Policy(
        id="p",
        name="Policy",
        mode="automatic",
        scope=Scope(("Mining",)),
        conditions=IntervalCondition(1, "gte"),
        actions=tuple(a.action for a in actions),
    )
    return PolicyReport(policy, (item,), (item,), 0, actions)


def test_build_plan_merges_compatible_actions() -> None:
    first = report((ResolvedAction(TagAction("retired")),))
    second = report((ResolvedAction(SuspendAction()),))
    plan = build_execution_plan((first, second))
    assert plan.tags == (("retired", (10,)),)
    assert plan.suspend_card_ids == (1,)
    assert plan.card_count == 1
    assert not plan.conflicted_card_ids


def test_build_plan_skips_conflicting_moves() -> None:
    first = report((ResolvedAction(MoveAction("A"), 2),))
    second = report((ResolvedAction(MoveAction("B"), 3),))
    plan = build_execution_plan((first, second))
    assert plan.is_empty
    assert plan.conflicted_card_ids == (1,)
