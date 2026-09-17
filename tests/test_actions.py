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
        triggers=(),
        scope=Scope((DeckSelector("Mining"),)),
        conditions=IntervalCondition(1, "gte"),
        actions=tuple(a.action for a in actions),
    )
    return PolicyReport(
        policy=policy,
        qualifying=(item,),
        actionable=(item,) if actionable else (),
        missing_first_review=0,
        resolved_actions=actions,
        card_actions=((item, actions),),
    )


def test_build_plan_merges_compatible_actions() -> None:
    first = report((ResolvedAction(TagAction("retired")),))
    second = report((ResolvedAction(SuspendAction()),))
    plan = build_execution_plan((first, second))
    assert plan.tags == (("retired", (10,)),)
    assert plan.suspend_card_ids == (1,)
    assert plan.card_count == 1
    assert not plan.conflicted_card_ids
    assert not plan.conflict_details


def test_build_plan_uses_explicit_card_intentions_not_policy_actions() -> None:
    item = card()
    suspend = ResolvedAction(SuspendAction())
    value = report((ResolvedAction(TagAction("trigger-only")), suspend), item)
    value = replace(value, qualifying=(), card_actions=((item, (suspend,)),))
    plan = build_execution_plan((value,))
    assert plan.suspend_card_ids == (item.card_id,)
    assert not plan.tags


def test_empty_card_intentions_do_not_reconstruct_qualifying_actions() -> None:
    value = report((ResolvedAction(UnsuspendAction()),), actionable=False)
    value = replace(value, card_actions=())
    plan = build_execution_plan((value, report((ResolvedAction(SuspendAction()),))))
    assert plan.suspend_card_ids == (1,)
    assert not plan.conflicted_card_ids


def test_build_plan_skips_conflicting_moves() -> None:
    first = report((ResolvedAction(MoveAction("A"), 2),))
    second = report((ResolvedAction(MoveAction("B"), 3),))
    plan = build_execution_plan((first, second))
    assert plan.is_empty
    assert plan.conflicted_card_ids == (1,)
    assert plan.conflict_details[0].reasons == (
        (
            "Move actions specify different destination decks\n"
            "Policy: Move cards to 'A'\nPolicy: Move cards to 'B'"
        ),
    )


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
    assert plan.conflict_details[0].reasons == (
        (
            "Tags are both added and removed: leech\n"
            "Policy: Add tag 'leech'\nPolicy: Remove tag 'LEECH'"
        ),
    )


def test_replace_tags_conflicts_with_incremental_tag_action() -> None:
    replacement = report((ResolvedAction(ReplaceTagsAction(("only",))),))
    addition = report((ResolvedAction(TagAction("extra")),))

    plan = build_execution_plan((replacement, addition))

    assert plan.is_empty
    assert plan.conflicted_card_ids == (1,)
    assert plan.conflict_details[0].reasons == (
        (
            "Replacing tags is combined with adding or removing tags\n"
            "Policy: Add tag 'extra'\nPolicy: Replace all tags with 'only'"
        ),
    )


def test_case_only_replacement_difference_is_compatible() -> None:
    lower = report((ResolvedAction(ReplaceTagsAction(("kept", "other"))),))
    upper = report((ResolvedAction(ReplaceTagsAction(("OTHER", "KEPT"))),))
    plan = build_execution_plan((lower, upper))
    assert not plan.conflicted_card_ids
    assert plan.card_count == 1
    assert len(plan.replace_tags) == 1


def test_suspend_conflicts_with_satisfied_unsuspend_policy() -> None:
    suspend = report((ResolvedAction(SuspendAction()),))
    unsuspend = report((ResolvedAction(UnsuspendAction()),), actionable=False)

    plan = build_execution_plan((suspend, unsuspend))

    assert plan.is_empty
    assert plan.conflicted_card_ids == (1,)
    assert plan.conflict_details[0].reasons == (
        (
            "Suspend and unsuspend actions target the same card\n"
            "Policy: Suspend cards\nPolicy: Unsuspend cards"
        ),
    )


def test_note_wide_conflict_details_propagate_reasons_and_policy_names() -> None:
    first, sibling = card(1), card(2)
    note_move = ResolvedAction(MoveAction("A", "note"), 2)
    wide = report((note_move,), first)
    wide = replace(
        wide,
        policy=replace(wide.policy, name="Move note"),
        card_actions=((first, (note_move,)), (sibling, (note_move,))),
        actionable=(first, sibling),
    )
    narrow = report((ResolvedAction(MoveAction("B"), 3),), sibling)
    narrow = replace(narrow, policy=replace(narrow.policy, name="Move sibling"))
    plan = build_execution_plan((wide, narrow))
    assert plan.is_empty
    assert (
        tuple(detail.card_id for detail in plan.conflict_details)
        == plan.conflicted_card_ids
        == (1, 2)
    )
    for detail in plan.conflict_details:
        assert detail.policies == ("Move note", "Move sibling")
        assert detail.reasons == (
            "All affected cards of the note are skipped together",
            (
                "Move actions specify different destination decks\n"
                "Move note: Move all cards of matching notes to 'A'\n"
                "Move sibling: Move cards to 'B'"
            ),
        )


def test_delete_note_deduplicates_siblings_and_conflicts_with_other_note_actions() -> None:
    first = report((ResolvedAction(DeleteNoteAction()),), card(1, 10))
    sibling = report((ResolvedAction(DeleteNoteAction()),), card(2, 10))
    plan = build_execution_plan((first, sibling))
    assert plan.delete_note_ids == (10,)

    tag = report((ResolvedAction(TagAction("keep")),), card(2, 10))
    conflicted = build_execution_plan((first, tag))
    assert conflicted.is_empty
    assert conflicted.conflicted_card_ids == (1, 2)
