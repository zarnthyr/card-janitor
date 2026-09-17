# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from anki.collection import Collection
from aqt.operations import QueryOp
from card_janitor import automatic, ui
from card_janitor.actions import CleanupError, build_execution_plan, execute_plan
from card_janitor.evaluator import evaluate_policy
from card_janitor.execution import execute_approved_reports
from card_janitor.models import (
    AgeCondition,
    AllConditions,
    DeckSelector,
    DeleteCardAction,
    DeleteNoteAction,
    Policy,
    ReplaceTagsAction,
    Scope,
    SuspendAction,
    SuspensionCondition,
    TagAction,
    TagCondition,
    UnsuspendAction,
    parse_policy,
)


def test_partial_cleanup_failure_is_grouped_and_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = Collection(str(tmp_path / "failure.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        policy = parse_policy(
            {
                "id": "failure",
                "name": "Failure",
                "triggers": [],
                "scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]},
                "match": "all",
                "conditions": [{"type": "tags", "operator": "contains_none", "tags": ["retired"]}],
                "actions": [{"type": "tag", "tags": ["retired"]}, {"type": "suspend"}],
            }
        )
        report = evaluate_policy(collection, policy)

        def fail(*_args: object) -> None:
            message = "injected suspension failure"
            raise RuntimeError(message)

        monkeypatch.setattr(collection.sched, "suspend_cards", fail)
        with pytest.raises(CleanupError, match="Earlier changes may have been applied"):
            execute_plan(collection, build_execution_plan((report,), collection), "Failed cleanup")
        assert collection.get_note(note.id).has_tag("retired")
        assert collection.undo_status().undo == "Failed cleanup"
        collection.undo()
        assert not collection.get_note(note.id).has_tag("retired")
    finally:
        collection.close()


def test_tag_evaluation_does_not_query_review_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = Collection(str(tmp_path / "queries.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        note.tags = ["leech"]
        collection.add_note(note, deck_id)
        queries = []
        original_all = collection.db.all

        def capture(sql: str, *args: object, **kwargs: object) -> list:
            queries.append(sql)
            return original_all(sql, *args, **kwargs)

        monkeypatch.setattr(collection.db, "all", capture)
        policy = Policy(
            id="query",
            name="Query",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=TagCondition(("leech",), "contains_any"),
            actions=(SuspendAction(),),
        )
        assert len(evaluate_policy(collection, policy).actionable) == 1
        fact_queries = [sql for sql in queries if "from cards c" in sql]
        assert fact_queries
        assert all("revlog" not in sql for sql in fact_queries)
        assert "where c.did in" in fact_queries[0]
        assert "and c.odid = 0" in fact_queries[0]
    finally:
        collection.close()


def test_query_op_requires_constructor_success_callback() -> None:
    signature = inspect.signature(QueryOp)
    success = signature.parameters["success"]
    assert success.kind is inspect.Parameter.KEYWORD_ONLY
    assert success.default is inspect.Parameter.empty


def test_dashboard_uses_current_query_op_constructor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeQueryOp:
        def __init__(self, *, parent: object, op: object, success: object) -> None:
            self.parent = parent
            self.op = op
            self.success = success
            self.started = False
            created.append(self)

        def run_in_background(self) -> None:
            self.started = True

    created: list[FakeQueryOp] = []

    on_demand = SimpleNamespace(triggers=())
    automatic = SimpleNamespace(triggers=())
    parsed = SimpleNamespace(config=SimpleNamespace(policies=(on_demand, automatic)))
    evaluated: list[tuple[object, tuple[object, ...]]] = []
    monkeypatch.setattr(ui, "mw", SimpleNamespace(col="current collection"))
    monkeypatch.setattr(ui, "_load_configured", lambda: parsed)
    monkeypatch.setattr(
        ui,
        "evaluate_policies",
        lambda col, policies: evaluated.append((col, policies)) or (),
    )
    monkeypatch.setattr(ui, "QueryOp", FakeQueryOp)

    ui.open_card_janitor()

    assert len(created) == 1
    assert callable(created[0].op)
    assert callable(created[0].success)
    assert created[0].started
    created[0].op("collection")
    assert evaluated == [("collection", (on_demand, automatic))]


@pytest.mark.parametrize(
    ("notify", "affected", "conflicts", "expected"),
    [
        (True, 1, 0, "Card Janitor cleaned up 1 card"),
        (True, 3, 0, "Card Janitor cleaned up 3 cards"),
        (True, 0, 0, ""),
        (False, 3, 0, ""),
        (False, 3, 2, "2 conflicting cards were skipped"),
        (
            True,
            3,
            2,
            "Card Janitor cleaned up 3 cards. 2 conflicting cards were skipped.",
        ),
    ],
)
def test_automatic_completion_message(
    notify: bool, affected: int, conflicts: int, expected: str
) -> None:
    assert (
        automatic._automatic_completion_message(
            notify=notify,
            affected_cards=affected,
            conflicts=conflicts,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("last_day", "expected"),
    [
        (None, True),
        (9, True),
        (10, False),
    ],
)
def test_daily_automatic_run_is_due(last_day: object, expected: bool) -> None:
    assert (
        automatic.automatic_run_is_due(
            today=10,
            last_automatic_day=last_day,
        )
        is expected
    )


def test_note_type_scope_filters_triggers_and_preserves_note_siblings(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        source = collection.decks.add_normal_deck_with_name("Source").id
        outside = collection.decks.add_normal_deck_with_name("Outside").id
        basic = collection.models.by_name("Basic")
        reverse = collection.models.by_name("Basic (and reversed card)")
        assert basic is not None
        assert reverse is not None
        for model in (basic, reverse):
            note = collection.new_note(model)
            note["Front"], note["Back"] = model["name"], "back"
            collection.add_note(note, source)
        first, sibling = collection.card_ids_of_note(note.id)
        collection.set_deck([sibling], outside)

        def evaluate(scope: dict, action: dict) -> object:
            return evaluate_policy(
                collection,
                parse_policy(
                    {
                        "id": "types",
                        "name": "Types",
                        "triggers": [],
                        "scope": scope,
                        "match": "all",
                        "conditions": [{"type": "all_cards"}],
                        "actions": [action],
                    }
                ),
            )

        scope = {
            "decks": [{"deck": "Source", "include_subdecks": False}],
            "note_types": [reverse["name"]],
        }
        report = evaluate(scope, {"type": "delete_note"})
        assert [card.card_id for card in report.qualifying] == [first]
        assert {card.card_id for card in report.actionable} == {first, sibling}
        assert (
            len(
                evaluate(
                    {"all_decks": True, "note_types": [basic["name"]]}, {"type": "suspend"}
                ).actionable
            )
            == 1
        )
        assert len(evaluate({"all_decks": True}, {"type": "suspend"}).actionable) == 3
        missing = evaluate({"all_decks": True, "note_types": ["Missing"]}, {"type": "suspend"})
        assert missing.errors == ("scope note type does not exist: 'Missing'",)
        assert not missing.actionable
    finally:
        collection.close()


def test_sibling_conditions_include_suspended_and_studied_cards_outside_scope(
    tmp_path: Path,
) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        source = collection.decks.add_normal_deck_with_name("Source").id
        outside = collection.decks.add_normal_deck_with_name("Outside").id
        model = collection.models.by_name("Basic (and reversed card)")
        assert model is not None
        note = collection.new_note(model)
        note["Front"], note["Back"] = "front", "back"
        collection.add_note(note, source)
        first, sibling = collection.card_ids_of_note(note.id)
        collection.set_deck([sibling], outside)
        collection.sched.suspend_cards([sibling])
        collection.db.execute(
            "insert into revlog values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sibling - 1000,
            sibling,
            -1,
            3,
            1,
            0,
            2500,
            1000,
            0,
        )

        def evaluate(condition: dict) -> object:
            return evaluate_policy(
                collection,
                parse_policy(
                    {
                        "id": "siblings",
                        "name": "Siblings",
                        "triggers": [],
                        "scope": {"decks": [{"deck": "Source", "include_subdecks": False}]},
                        "match": "all",
                        "conditions": [condition],
                        "actions": [{"type": "delete_note"}],
                    }
                ),
            )

        ordinary = evaluate({"type": "review_history", "operator": "not_exists"})
        assert len(ordinary.actionable) == 2
        assert not evaluate({"type": "sibling_review_history", "operator": "none"}).actionable
        for kind in ("sibling_review_history", "sibling_suspension"):
            matched = evaluate({"type": kind, "operator": "any"})
            assert [card.card_id for card in matched.qualifying] == [first]
            assert {card.card_id for card in matched.actionable} == {first, sibling}
            assert not evaluate({"type": kind, "operator": "none"}).qualifying
        collection.sched.unsuspend_cards([sibling])
        assert len(evaluate({"type": "sibling_suspension", "operator": "none"}).qualifying) == 1
    finally:
        collection.close()


def test_evaluate_and_apply_against_anki_collection(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        deck_id = collection.decks.add_normal_deck_with_name("Mining").id
        notetype = collection.models.by_name("Basic")
        assert notetype is not None
        note = collection.new_note(notetype)
        note["Front"] = "word"
        note["Back"] = "meaning"
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        first_review = card_id - 1000
        collection.db.execute(
            "insert into revlog values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            first_review,
            card_id,
            -1,
            3,
            1,
            0,
            2500,
            1000,
            0,
        )

        policy = Policy(
            id="mining",
            name="Mining",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AgeCondition(0, "first_review", "gte"),
            actions=(TagAction("retired"), SuspendAction()),
        )
        report = evaluate_policy(collection, policy, now_ms=first_review + 86_400_000)
        assert [card.card_id for card in report.actionable] == [card_id]

        result = execute_approved_reports(
            collection,
            (report,),
            {policy.id: {card_id}},
            "Clean test card",
        )
        assert result.affected_cards == 1
        assert collection.get_card(card_id).queue == -1
        assert collection.get_note(note.id).has_tag("retired")
        assert collection.undo_status().undo == "Clean test card"

        collection.undo()

        assert collection.get_card(card_id).queue != -1
        assert not collection.get_note(note.id).has_tag("retired")
    finally:
        collection.close()


def test_delete_action_can_be_undone(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        deck_id = collection.decks.add_normal_deck_with_name("Cleanup").id
        notetype = collection.models.by_name("Basic")
        assert notetype is not None
        note = collection.new_note(notetype)
        note["Front"] = "temporary"
        note["Back"] = "card"
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        policy = Policy(
            id="delete",
            name="Delete",
            triggers=(),
            scope=Scope((DeckSelector("Cleanup"),)),
            conditions=AgeCondition(0, "card_created", "gte"),
            actions=(DeleteCardAction(),),
        )
        report = evaluate_policy(collection, policy, now_ms=card_id)

        execute_plan(collection, build_execution_plan((report,)), "Delete test card")

        assert not collection.find_cards(f"cid:{card_id}")
        assert collection.undo_status().undo == "Delete test card"

        collection.undo()

        assert collection.find_cards(f"cid:{card_id}") == [card_id]
    finally:
        collection.close()


def test_delete_note_removes_sibling_cards_outside_scope_and_can_be_undone(
    tmp_path: Path,
) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        deck_id = collection.decks.add_normal_deck_with_name("Delete Note").id
        outside_id = collection.decks.add_normal_deck_with_name("Outside").id
        notetype = collection.models.by_name("Basic (and reversed card)")
        assert notetype is not None
        note = collection.new_note(notetype)
        note["Front"] = "front"
        note["Back"] = "back"
        collection.add_note(note, deck_id)
        card_ids = [int(card_id) for card_id in collection.card_ids_of_note(note.id)]
        assert len(card_ids) == 2
        collection.set_deck([card_ids[1]], outside_id)
        policy = Policy(
            id="delete-note",
            name="Delete Note",
            triggers=(),
            scope=Scope((DeckSelector("Delete Note"),)),
            conditions=AgeCondition(0, "card_created", "gte"),
            actions=(DeleteNoteAction(),),
        )
        report = evaluate_policy(collection, policy, now_ms=max(card_ids))
        assert len(report.qualifying) == 1
        assert len(report.actionable) == 2
        plan = build_execution_plan((report,), collection)
        assert plan.card_count == 2

        result = execute_plan(collection, plan, "Delete test note")

        assert result.affected_cards == 2
        assert not collection.card_ids_of_note(note.id)

        collection.undo()

        assert set(collection.card_ids_of_note(note.id)) == set(card_ids)
    finally:
        collection.close()


@pytest.mark.parametrize("kind", ["suspend_note", "unsuspend_note", "move_note"])
def test_note_actions_expand_to_unsatisfied_siblings_and_can_be_undone(
    tmp_path: Path,
    kind: str,
) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        source = collection.decks.add_normal_deck_with_name("Source").id
        outside = collection.decks.add_normal_deck_with_name("Outside").id
        model = collection.models.by_name("Basic (and reversed card)")
        assert model is not None
        note = collection.new_note(model)
        note["Front"], note["Back"] = "front", "back"
        collection.add_note(note, source)
        first, sibling = collection.card_ids_of_note(note.id)
        collection.set_deck([sibling], outside)
        if kind == "suspend_note":
            collection.sched.suspend_cards([first])
        elif kind == "unsuspend_note":
            collection.sched.suspend_cards([sibling])
        action = {"type": kind}
        if kind == "move_note":
            action["deck"] = "Source"
        policy = parse_policy(
            {
                "id": "note-action",
                "name": "Note action",
                "triggers": [],
                "scope": {
                    "decks": [{"deck": "Source", "include_subdecks": False}],
                    "include_suspended": kind == "suspend_note",
                },
                "match": "all",
                "conditions": [{"type": "all_cards"}],
                "actions": [action],
            }
        )
        report = evaluate_policy(collection, policy)
        assert [card.card_id for card in report.qualifying] == [first]
        assert [card.card_id for card in report.actionable] == [sibling]
        plan = build_execution_plan((report,), collection)
        assert plan.planned_card_ids == (sibling,)
        if kind == "suspend_note":
            collection.sched.unsuspend_cards([first])
        elif kind == "unsuspend_note":
            collection.sched.suspend_cards([first])
        else:
            collection.set_deck([sibling], source)
            collection.set_deck([first], outside)
        skipped = execute_approved_reports(
            collection,
            (report,),
            {policy.id: {sibling}},
            "Skip unapproved note",
        )
        assert skipped.affected_cards == 0
        collection.set_deck([first], source)
        collection.set_deck([sibling], outside)
        if kind == "suspend_note":
            collection.sched.suspend_cards([first])
        elif kind == "unsuspend_note":
            collection.sched.unsuspend_cards([first])
        result = execute_approved_reports(
            collection,
            (report,),
            {policy.id: {sibling}},
            "Apply note action",
        )
        assert result.affected_cards == 1
        changed = collection.get_card(sibling)
        assert (
            changed.did == source
            if kind == "move_note"
            else changed.queue == (-1 if kind == "suspend_note" else 0)
        )
        collection.undo()
        restored = collection.get_card(sibling)
        assert restored.did == outside
        assert restored.queue == (-1 if kind == "unsuspend_note" else 0)
    finally:
        collection.close()


def test_replace_tags_and_unsuspend_can_be_undone(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        deck_id = collection.decks.add_normal_deck_with_name("Leeches").id
        notetype = collection.models.by_name("Basic")
        assert notetype is not None
        note = collection.new_note(notetype)
        note["Front"] = "difficult"
        note["Back"] = "answer"
        note.tags = ["leech", "old"]
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        collection.sched.suspend_cards([card_id])
        policy = Policy(
            id="repair-leech",
            name="Repair Leech",
            triggers=(),
            scope=Scope((DeckSelector("Leeches"),), include_suspended=True),
            conditions=AllConditions(
                (
                    TagCondition(("leech",), "contains_any"),
                    SuspensionCondition("is_suspended"),
                )
            ),
            actions=(ReplaceTagsAction(("kept",)), UnsuspendAction()),
        )
        report = evaluate_policy(collection, policy)

        execute_plan(collection, build_execution_plan((report,)), "Repair test card")

        assert collection.get_note(note.id).tags == ["kept"]
        assert collection.get_card(card_id).queue != -1

        collection.undo()

        assert set(collection.get_note(note.id).tags) == {"leech", "old"}
        assert collection.get_card(card_id).queue == -1
    finally:
        collection.close()


def test_note_actions_skip_whole_note_on_sibling_conflict(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "collection.anki2"))
    try:
        source = collection.decks.add_normal_deck_with_name("Source").id
        outside = collection.decks.add_normal_deck_with_name("Outside").id
        model = collection.models.by_name("Basic (and reversed card)")
        assert model is not None
        note = collection.new_note(model)
        note["Front"], note["Back"] = "front", "back"
        collection.add_note(note, source)
        first, sibling = collection.card_ids_of_note(note.id)
        collection.set_deck([sibling], outside)

        def policy(policy_id: str, deck: str, action: dict) -> Policy:
            return parse_policy(
                {
                    "id": policy_id,
                    "name": policy_id,
                    "triggers": [],
                    "scope": {
                        "decks": [{"deck": deck, "include_subdecks": False}],
                        "include_suspended": True,
                    },
                    "match": "all",
                    "conditions": [{"type": "all_cards"}],
                    "actions": [action],
                }
            )

        note_policy = policy("note", "Source", {"type": "suspend_note"})
        sibling_policy = policy("sibling", "Outside", {"type": "unsuspend"})
        reports = (
            evaluate_policy(collection, note_policy),
            evaluate_policy(collection, sibling_policy),
        )
        plan = build_execution_plan(reports, collection)
        assert plan.is_empty
        assert set(plan.conflicted_card_ids) == {first, sibling}
    finally:
        collection.close()
