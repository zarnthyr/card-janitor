# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from anki.collection import Collection
from aqt.operations import QueryOp
from card_janitor import ui
from card_janitor.actions import build_execution_plan, execute_plan
from card_janitor.evaluator import evaluate_policy
from card_janitor.models import (
    AgeRule,
    DeleteCardAction,
    Policy,
    Scope,
    SuspendAction,
    TagAction,
)


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

    on_demand = SimpleNamespace(state="on_demand")
    automatic = SimpleNamespace(state="automatic")
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
        (True, 1, 0, "Card Janitor cleaned up 1 card."),
        (True, 3, 0, "Card Janitor cleaned up 3 cards."),
        (True, 0, 0, ""),
        (False, 3, 0, ""),
        (False, 3, 2, "2 conflicting cards were skipped."),
    ],
)
def test_automatic_completion_message(
    notify: bool, affected: int, conflicts: int, expected: str
) -> None:
    assert (
        ui._automatic_completion_message(
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
        ui.automatic_run_is_due(
            today=10,
            last_automatic_day=last_day,
        )
        is expected
    )


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
        first_review = card_id + 1000
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
            state="on_demand",
            scope=Scope(("Mining",)),
            rule=AgeRule(1, "first_review", "gte"),
            actions=(TagAction("retired"), SuspendAction()),
        )
        report = evaluate_policy(collection, policy, now_ms=first_review + 86_400_000)
        assert [card.card_id for card in report.actionable] == [card_id]

        result = execute_plan(collection, build_execution_plan((report,)), "Clean test card")
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
            state="on_demand",
            scope=Scope(("Cleanup",)),
            rule=AgeRule(0, "card_created", "gte"),
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
