# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from dataclasses import replace

from card_janitor.actions import build_execution_plan
from card_janitor.engine import CardFacts, PolicyReport, ResolvedAction
from card_janitor.models import (
    DeckSelector,
    DeleteNoteAction,
    IntervalCondition,
    MoveAction,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    Scope,
    SuspendAction,
    TagAction,
    UnsuspendAction,
)


def card(card_id: int = 1, note_id: int = 10) -> CardFacts:
    return CardFacts(card_id, note_id, 1, 0, 2, 2, 100, 1000, 2000, frozenset())


def report(
    actions: tuple[ResolvedAction, ...],
    value: CardFacts | None = None,
    *,
    actionable: bool = True,
) -> PolicyReport:
    item = value or card()
    policy = Policy(
        id="p",
        name="Policy",
        mode="automatic",
        scope=Scope((DeckSelector("Mining"),)),
        conditions=IntervalCondition(1, "gte"),
        actions=tuple(a.action for a in actions),
    )
    return PolicyReport(policy, (item,), (item,) if actionable else (), 0, actions)


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


def test_build_plan_includes_inverse_actions_and_replacements() -> None:
    plan = build_execution_plan(
        (
            report(
                (ResolvedAction(RemoveTagAction("leech")),),
                replace(card(), tags=frozenset({"leech"})),
            ),
            report((ResolvedAction(ReplaceTagsAction(("kept",))),), card(2, 20)),
            report((ResolvedAction(UnsuspendAction()),), replace(card(3, 30), queue=-1)),
        )
    )

    assert plan.remove_tags == (("leech", (10,)),)
    assert plan.replace_tags == ((("kept",), (20,)),)
    assert plan.unsuspend_card_ids == (3,)


def test_note_tag_conflict_applies_across_sibling_cards() -> None:
    first = report((ResolvedAction(TagAction("leech")),), card(1), actionable=False)
    second = report((ResolvedAction(RemoveTagAction("LEECH")),), card(2))

    plan = build_execution_plan((first, second))

    assert plan.is_empty
    assert plan.conflicted_card_ids == (2,)


def test_replace_tags_conflicts_with_incremental_tag_action() -> None:
    replacement = report((ResolvedAction(ReplaceTagsAction(("only",))),))
    addition = report((ResolvedAction(TagAction("extra")),))

    plan = build_execution_plan((replacement, addition))

    assert plan.is_empty
    assert plan.conflicted_card_ids == (1,)


def test_suspend_conflicts_with_satisfied_unsuspend_policy() -> None:
    suspend = report((ResolvedAction(SuspendAction()),))
    unsuspend = report((ResolvedAction(UnsuspendAction()),), actionable=False)

    plan = build_execution_plan((suspend, unsuspend))

    assert plan.is_empty
    assert plan.conflicted_card_ids == (1,)


def test_delete_note_deduplicates_siblings_and_conflicts_with_other_note_actions() -> None:
    first = report((ResolvedAction(DeleteNoteAction()),), card(1, 10))
    sibling = report((ResolvedAction(DeleteNoteAction()),), card(2, 10))
    plan = build_execution_plan((first, sibling))
    assert plan.delete_note_ids == (10,)

    tag = report((ResolvedAction(TagAction("keep")),), card(2, 10))
    conflicted = build_execution_plan((first, tag))
    assert conflicted.is_empty
    assert conflicted.conflicted_card_ids == (1, 2)
