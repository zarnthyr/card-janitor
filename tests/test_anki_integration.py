# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import inspect
from pathlib import Path

import pytest
from anki.collection import Collection
from aqt.operations import QueryOp
from card_retirement import ui
from card_retirement.actions import build_execution_plan, execute_plan
from card_retirement.evaluator import evaluate_policy, evaluate_selected_cards
from card_retirement.models import AgeRule, Policy, Scope, SuspendAction, TagAction


def test_query_op_requires_constructor_success_callback() -> None:
    signature = inspect.signature(QueryOp)
    success = signature.parameters["success"]
    assert success.kind is inspect.Parameter.KEYWORD_ONLY
    assert success.default is inspect.Parameter.empty


def test_find_cards_uses_current_query_op_constructor(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(ui, "_choose_configuration", lambda *_args: object())
    monkeypatch.setattr(ui, "QueryOp", FakeQueryOp)

    ui.find_cards_to_retire()

    assert len(created) == 1
    assert callable(created[0].op)
    assert callable(created[0].success)
    assert created[0].started


@pytest.mark.parametrize(
    ("notify", "affected", "conflicts", "expected"),
    [
        (True, 1, 0, "Retired 1 card."),
        (True, 3, 0, "Retired 3 cards."),
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
    ("schedule", "trigger", "last_day", "expected"),
    [
        ("profile_open", "profile_open", None, True),
        ("profile_open", "day_change", None, False),
        ("daily", "profile_open", 9, True),
        ("daily", "profile_open", 10, False),
        ("daily", "day_change", 9, True),
        ("profile_open_and_daily", "profile_open", 10, True),
        ("profile_open_and_daily", "day_change", 10, True),
    ],
)
def test_automatic_schedule(schedule: str, trigger: str, last_day: object, expected: bool) -> None:
    assert (
        ui.automatic_run_is_due(
            schedule,
            trigger,
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
            enabled=True,
            mode="manual",
            scope=Scope(("Mining",)),
            rule=AgeRule(1, "first_review"),
            actions=(TagAction("retired"), SuspendAction()),
        )
        report = evaluate_policy(collection, policy, now_ms=first_review + 86_400_000)
        assert [card.card_id for card in report.actionable] == [card_id]

        selected = evaluate_selected_cards(collection, policy, {card_id})
        assert [card.card_id for card in selected.actionable] == [card_id]

        manual_override = Policy(
            id="manual-override",
            name="Manual override",
            enabled=True,
            mode="manual",
            scope=Scope(("Missing",)),
            rule=AgeRule(999_999, "card_created"),
            actions=(SuspendAction(),),
        )
        selected = evaluate_selected_cards(collection, manual_override, {card_id})
        assert [card.card_id for card in selected.actionable] == [card_id]

        result = execute_plan(collection, build_execution_plan((report,)), "Retire test card")
        assert result.affected_cards == 1
        assert collection.get_card(card_id).queue == -1
        assert collection.get_note(note.id).has_tag("retired")
    finally:
        collection.close()
