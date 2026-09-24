# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from dataclasses import replace

import pytest
from card_janitor import actions as actions_module
from card_janitor.actions import CleanupError, build_execution_plan
from card_janitor.cleanup_preview import build_preview_rows
from card_janitor.engine import CardFacts, PolicyReport, ResolvedAction
from card_janitor.models import (
    AllCardsCondition,
    ClearFlagAction,
    DeckSelector,
    DeleteCardAction,
    DeleteNoteAction,
    IntervalCondition,
    MoveAction,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    Scope,
    SetFlagAction,
    SuspendAction,
    TagAction,
    UnsuspendAction,
)


def card(card_id: int = 1, note_id: int = 10) -> CardFacts:
    return CardFacts(card_id, note_id, 1, 0, 2, 2, 100, 1000, 2000, frozenset())


def test_preview_shows_deduplicated_actual_changes_and_move_origin() -> None:
    actions = (ResolvedAction(TagAction("retired")), ResolvedAction(MoveAction("Retired"), 2))
    first = report(actions)
    second = replace(first, policy=replace(first.policy, id="second", name="Second"))
    reports = (first, second)
    plan = build_execution_plan(reports)
    rows = build_preview_rows(plan, reports, {1: "Mining", 2: "Retired"})
    assert len(rows) == plan.card_count == 1
    assert rows[0].policies == ("Policy", "Second")
    assert rows[0].overlapping
    assert rows[0].changes == ("Move: Mining → Retired", "Add tag 'retired'")


def test_preview_does_not_show_already_satisfied_actions() -> None:
    item = replace(card(), queue=-1)
    reports = (report((ResolvedAction(SuspendAction()), ResolvedAction(TagAction("new"))), item),)
    plan = build_execution_plan(reports)
    rows = build_preview_rows(plan, reports, {})
    assert rows[0].changes == ("Add tag 'new'",)
    satisfied = report((ResolvedAction(SuspendAction()),), item, actionable=False)
    assert build_preview_rows(build_execution_plan((satisfied,)), (satisfied,), {}) == ()


def test_preview_conflicts_have_no_planned_changes() -> None:
    reports = (
        report((ResolvedAction(TagAction("leech")),)),
        report((ResolvedAction(RemoveTagAction("leech")),)),
    )
    rows = build_preview_rows(build_execution_plan(reports), reports, {})
    assert rows[0].changes == ()
    assert rows[0].reasons[0].startswith("Tags are both added and removed")


def test_preview_includes_note_wide_siblings_expanded_sibling() -> None:
    first, sibling = card(1), card(2)
    action = ResolvedAction(DeleteNoteAction())
    wide = report((action,), first)
    wide = replace(
        wide, actionable=(first, sibling), card_actions=((first, (action,)), (sibling, (action,)))
    )
    plan = build_execution_plan((wide,))
    rows = build_preview_rows(plan, (wide,), {})
    assert len(rows) == plan.card_count == 2
    assert rows[0].changes == rows[1].changes == ("Delete note and all its cards",)
    assert not rows[0].expanded_sibling
    assert rows[1].expanded_sibling


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


def test_plan_semantics_preserve_all_effect_contributors() -> None:
    first = report((ResolvedAction(TagAction("retired")),))
    second = replace(first, policy=replace(first.policy, id="second", name="Second"))

    plan = build_execution_plan((first, second), collect_history=True)

    assert plan.semantics.status == "complete"
    assert len(plan.semantics.effects) == 1
    effect = plan.semantics.effects[0]
    assert effect.target_kind == "note"
    assert effect.target_id == 10
    assert tuple(item.policy_id for item in effect.contributors) == ("p", "second")


def test_note_wide_effect_keeps_satisfied_trigger_as_cause_not_changed_card() -> None:
    trigger = replace(card(1), queue=-1)
    sibling = card(2)
    action = ResolvedAction(SuspendAction("note"))
    base = report((action,), trigger, actionable=False)
    wide = replace(
        base,
        actionable=(sibling,),
        card_actions=((trigger, (action,)), (sibling, (action,))),
    )

    ordinary = build_execution_plan((wide,))
    plan = build_execution_plan((wide,), collect_history=True)

    assert plan == ordinary
    assert plan.suspend_card_ids == (sibling.card_id,)
    assert len(plan.semantics.effects) == 1
    effect = plan.semantics.effects[0]
    assert effect.affected_card_ids == (sibling.card_id,)
    assert effect.matching_trigger_card_ids == ()
    assert effect.consequential_sibling_card_ids == (sibling.card_id,)
    assert effect.contributors[0].trigger_card_ids == (trigger.card_id,)


def test_plan_semantics_admit_only_concrete_non_applied_intentions() -> None:
    satisfied = report(
        (ResolvedAction(SuspendAction()),),
        replace(card(), queue=-1),
        actionable=False,
    )
    conflicting = (
        report((ResolvedAction(TagAction("leech")),)),
        report((ResolvedAction(RemoveTagAction("leech")),)),
    )

    satisfied_plan = build_execution_plan((satisfied,), collect_history=True)
    conflict_plan = build_execution_plan(conflicting, collect_history=True)

    assert {item.disposition for item in satisfied_plan.semantics.non_applied} == {
        "already_satisfied"
    }
    assert {item.disposition for item in conflict_plan.semantics.non_applied} == {"conflict"}
    assert {code for item in conflict_plan.semantics.non_applied for code in item.reason_codes} == {
        "tag_add_remove"
    }


def test_planning_history_failure_does_not_change_authoritative_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports = (report((ResolvedAction(SuspendAction()),)),)
    ordinary = build_execution_plan(reports)

    def fail(*_args: object, **_kwargs: object) -> object:
        message = "injected planning history failure"
        raise RuntimeError(message)

    monkeypatch.setattr(actions_module, "_build_plan_semantics", fail)
    traced = build_execution_plan(reports, collect_history=True)

    assert traced == ordinary
    assert traced.semantics.status == "unavailable"


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
        ("Different move destinations\nPolicy: Move cards to 'A'\nPolicy: Move cards to 'B'"),
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


def test_build_plan_includes_card_flags_and_reports_conflicts() -> None:
    purple = report((ResolvedAction(SetFlagAction("purple")),))
    plan = build_execution_plan((purple,))
    assert plan.flags == ((7, (1,)),)
    assert build_preview_rows(plan, (purple,), {})[0].changes == ("Set purple card flag",)

    cleared = report((ResolvedAction(ClearFlagAction()),))
    conflicted = build_execution_plan((purple, cleared))
    assert conflicted.is_empty
    assert conflicted.conflicted_card_ids == (1,)
    assert conflicted.conflict_details[0].reasons == (
        "Different card flags\nPolicy: Clear card flag\nPolicy: Set purple card flag",
    )


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
            "Replacing tags combined with adding or removing tags\n"
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
        ("Suspend and unsuspend conflict\nPolicy: Suspend cards\nPolicy: Unsuspend cards"),
    )
    rows = build_preview_rows(plan, (suspend, unsuspend), {})
    assert len(rows) == 1
    assert rows[0].overlapping


@pytest.mark.parametrize("action", [DeleteCardAction(), DeleteNoteAction()])
def test_execution_plan_rejects_directly_constructed_unrestricted_delete(action: object) -> None:
    resolved = ResolvedAction(action)
    value = report((resolved,))
    value = replace(
        value,
        policy=replace(
            value.policy,
            scope=Scope(all_decks=True),
            conditions=AllCardsCondition(),
            actions=(action,),
        ),
    )

    with pytest.raises(CleanupError, match="without a scope or condition restriction"):
        build_execution_plan((value,))


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
                "Different move destinations\n"
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
