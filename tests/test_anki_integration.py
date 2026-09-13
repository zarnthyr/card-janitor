# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from pathlib import Path

from anki.collection import Collection
from card_retirement.actions import build_execution_plan, execute_plan
from card_retirement.evaluator import evaluate_policy
from card_retirement.models import AgeRule, Policy, Scope, SuspendAction, TagAction


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
        assert report.qualifying[0].successful_answers == 1

        result = execute_plan(collection, build_execution_plan((report,)), "Retire test card")
        assert result.affected_cards == 1
        assert collection.get_card(card_id).queue == -1
        assert collection.get_note(note.id).has_tag("retired")
    finally:
        collection.close()
