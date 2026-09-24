# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import inspect
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from anki.collection import Collection
from aqt.operations import QueryOp
from card_janitor import actions as actions_module
from card_janitor import automatic, evaluator, ui
from card_janitor import execution as execution_module
from card_janitor.actions import CleanupError, build_execution_plan, execute_plan
from card_janitor.cleanup_preview import build_preview_rows
from card_janitor.configuration import COLLECTION_POLICIES_KEY
from card_janitor.evaluator import evaluate_policy
from card_janitor.execution import StalePolicyDefinitionsError, execute_approved_reports
from card_janitor.history_events import Invocation, PolicyActivation
from card_janitor.history_runtime import prepare_history_session
from card_janitor.models import (
    AgeCondition,
    AllCardsCondition,
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
    policy_to_dict,
)


def save_policies(collection: Collection, *policies: Policy) -> None:
    collection.set_config(
        COLLECTION_POLICIES_KEY,
        [policy_to_dict(policy) for policy in policies],
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
                "scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]},
                "match": "all",
                "conditions": [{"type": "tags", "operator": "contains_none", "tags": ["retired"]}],
                "actions": [{"type": "add_tags", "tags": ["retired"]}, {"type": "suspend"}],
            }
        )
        report = evaluate_policy(collection, policy)

        def fail(*_args: object) -> None:
            message = "injected suspension failure"
            raise RuntimeError(message)

        monkeypatch.setattr(collection.sched, "suspend_cards", fail)
        with pytest.raises(CleanupError, match="Earlier changes may have been applied") as exc_info:
            execute_plan(
                collection,
                build_execution_plan((report,), collection, collect_history=True),
                "Failed cleanup",
            )
        ledger = exc_info.value.execution_ledger
        assert exc_info.value.semantics.status == "complete"
        assert ledger.status == "partial"
        assert [step.status for step in ledger.steps] == ["completed", "failed_unknown"]
        assert ledger.steps[0].targets[0].value == "retired"
        assert ledger.steps[0].targets[0].target_ids == (note.id,)
        assert not ledger.effects_complete
        assert ledger.unknown_effects_possible
        assert collection.get_note(note.id).has_tag("retired")
        assert collection.undo_status().undo == "Failed cleanup"
        collection.undo()
        assert not collection.get_note(note.id).has_tag("retired")
    finally:
        collection.close()


def test_cleanup_over_undo_history_limit_remains_one_undo_entry(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "large-undo.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        tags = tuple(f"cleanup-{index:03}" for index in range(101))
        policy = Policy(
            id="many-tags",
            name="Many tags",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=tuple(TagAction(tag) for tag in tags),
        )
        plan = build_execution_plan(
            (evaluate_policy(collection, policy),),
            collection,
            collect_history=True,
        )

        result = execute_plan(collection, plan, "Large cleanup")

        assert result.affected_cards == 1
        assert result.execution_ledger.status == "complete"
        assert all(step.status == "completed" for step in result.execution_ledger.steps)
        assert set(tags).issubset(collection.get_note(note.id).tags)
        assert collection.undo_status().undo == "Large cleanup"
        collection.undo()
        assert not set(tags).intersection(collection.get_note(note.id).tags)
    finally:
        collection.close()


def test_execution_ledger_failure_does_not_change_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = Collection(str(tmp_path / "ledger-unavailable.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        policy = Policy(
            id="ledger-unavailable",
            name="Ledger unavailable",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=(TagAction("retired"),),
        )
        plan = build_execution_plan(
            (evaluate_policy(collection, policy),),
            collection,
            collect_history=True,
        )

        def fail(_plan: object) -> object:
            message = "injected ledger failure"
            raise RuntimeError(message)

        monkeypatch.setattr(actions_module, "_planned_ledger_steps", fail)
        result = execute_plan(collection, plan, "Unavailable ledger")

        assert collection.get_note(note.id).has_tag("retired")
        assert result.execution_ledger.status == "unavailable"
        assert result.execution_ledger.reason_code == "execution_ledger_failed"
    finally:
        collection.close()


def test_large_partial_cleanup_failure_remains_one_undo_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = Collection(str(tmp_path / "large-failure.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        tags = tuple(f"cleanup-{index:03}" for index in range(101))
        policy = Policy(
            id="many-tags-failure",
            name="Many tags failure",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=(*tuple(TagAction(tag) for tag in tags), SuspendAction()),
        )
        plan = build_execution_plan(
            (evaluate_policy(collection, policy),),
            collection,
            collect_history=True,
        )

        def fail(*_args: object) -> None:
            message = "injected suspension failure"
            raise RuntimeError(message)

        monkeypatch.setattr(collection.sched, "suspend_cards", fail)
        with pytest.raises(CleanupError, match="Undo entry 'Large failed cleanup'"):
            execute_plan(collection, plan, "Large failed cleanup")

        assert set(tags).issubset(collection.get_note(note.id).tags)
        assert collection.undo_status().undo == "Large failed cleanup"
        collection.undo()
        assert not set(tags).intersection(collection.get_note(note.id).tags)
    finally:
        collection.close()


def test_completed_cleanup_reports_merge_undo_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = Collection(str(tmp_path / "merge-failure.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        policy = Policy(
            id="tag",
            name="Tag",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=(TagAction("retired"),),
        )
        plan = build_execution_plan(
            (evaluate_policy(collection, policy),),
            collection,
            collect_history=True,
        )

        def fail_merge(_undo_target: int) -> None:
            message = "injected merge failure"
            raise RuntimeError(message)

        monkeypatch.setattr(collection, "merge_undo_entries", fail_merge)
        with pytest.raises(CleanupError, match="after applying changes") as exc_info:
            execute_plan(collection, plan, "Merge failure")

        assert collection.get_note(note.id).has_tag("retired")
        assert "complete recovery cannot be guaranteed" in str(exc_info.value)
        ledger = exc_info.value.execution_ledger
        assert ledger.status == "partial"
        assert [step.status for step in ledger.steps] == ["completed_undo_merge_failed"]
        assert ledger.effects_complete
        assert not ledger.unknown_effects_possible
    finally:
        collection.close()


def test_cleanup_failure_does_not_promise_recovery_when_undo_state_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = Collection(str(tmp_path / "unavailable-undo.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        policy = Policy(
            id="unavailable-undo",
            name="Unavailable Undo",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=(TagAction("retired"), SuspendAction()),
        )
        plan = build_execution_plan((evaluate_policy(collection, policy),), collection)

        def fail(*_args: object) -> None:
            message = "injected suspension failure"
            raise RuntimeError(message)

        monkeypatch.setattr(collection.sched, "suspend_cards", fail)
        monkeypatch.setattr(
            collection,
            "undo_status",
            lambda: SimpleNamespace(last_step=0, undo=None),
        )
        with pytest.raises(CleanupError, match="could not verify") as exc_info:
            execute_plan(collection, plan, "Unavailable Undo")

        assert collection.get_note(note.id).has_tag("retired")
        assert "complete recovery cannot be guaranteed" in str(exc_info.value)
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

        def evaluate(scope: dict | None, action: dict) -> object:
            return evaluate_policy(
                collection,
                parse_policy(
                    {
                        "id": "types",
                        "name": "Types",
                        **({"scope": scope} if scope is not None else {}),
                        "actions": [action],
                    }
                ),
            )

        scope = {
            "decks": [{"deck": "Source", "include_subdecks": False}],
            "note_types": [{"name": reverse["name"]}],
        }
        report = evaluate(scope, {"type": "delete_note"})
        assert [card.card_id for card in report.qualifying] == [first]
        assert {card.card_id for card in report.actionable} == {first, sibling}
        assert (
            len(
                evaluate(
                    {"note_types": [{"name": basic["name"]}]},
                    {"type": "suspend"},
                ).actionable
            )
            == 1
        )
        assert len(evaluate(None, {"type": "suspend"}).actionable) == 3
        card_type_only = evaluate(
            {
                "note_types": [{"name": reverse["name"], "card_types": ["Card 2"]}],
            },
            {"type": "suspend"},
        )
        assert [card.card_type_idx for card in card_type_only.qualifying] == [1]
        missing_card_type = evaluate(
            {
                "note_types": [{"name": reverse["name"], "card_types": ["Missing"]}],
            },
            {"type": "suspend"},
        )
        missing_card_type_name = f"{reverse['name']}::Missing"
        assert missing_card_type.errors == (f"card type {missing_card_type_name!r} not found",)
        missing = evaluate(
            {"note_types": [{"name": "Missing"}]},
            {"type": "suspend"},
        )
        assert missing.errors == ("note type 'Missing' not found",)
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
        save_policies(collection, policy)
        report = evaluate_policy(collection, policy, now_ms=first_review + 86_400_000)
        assert [card.card_id for card in report.actionable] == [card_id]
        unrelated = replace(
            policy,
            id="unrelated",
            name="Unrelated",
            actions=(TagAction("other"),),
        )
        save_policies(collection, policy, unrelated)

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


def test_manual_execution_rejects_policy_changed_after_evaluation(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "stale-manual.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        policy = Policy(
            id="manual",
            name="Manual",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=(TagAction("retired"),),
        )
        save_policies(collection, policy)
        report = evaluate_policy(collection, policy)

        save_policies(collection, replace(policy, name="Changed"))
        with pytest.raises(StalePolicyDefinitionsError, match="selected policies changed"):
            execute_approved_reports(
                collection,
                (report,),
                {policy.id: {card_id}},
                "Stale manual cleanup",
            )

        assert not collection.get_note(note.id).has_tag("retired")
    finally:
        collection.close()


def test_approved_cleanup_writes_a_complete_local_history_event(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "recorded-cleanup.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        policy = Policy(
            id="recorded-cleanup",
            name="Recorded cleanup",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=(TagAction("retired"),),
        )
        save_policies(collection, policy)
        preview = evaluate_policy(collection, policy)
        session = prepare_history_session(
            enabled=True,
            profile={},
            policies=(policy,),
            invocation=Invocation("manual"),
            activations=(PolicyActivation(policy.id, "manual"),),
            anki_version="test",
            root=tmp_path / "local-history",
        )
        assert session is not None

        result = execute_approved_reports(
            collection,
            (preview,),
            {policy.id: {card_id}},
            "Recorded cleanup",
            history=session,
        )

        assert result.affected_cards == 1
        assert collection.get_note(note.id).has_tag("retired")
        assert session.error_message is None
        assert session.store is not None
        page = session.store.read_recent()
        assert len(page.records) == 1
        event = page.records[0].event
        assert event is not None
        assert event.event_id == session.context.event_id
        assert event.invocation == Invocation("manual")
        assert event.outcome.status == "succeeded"
        assert event.outcome.stage == "complete"
        assert event.outcome.result == "changed"
        assert event.outcome.effects_complete
        assert event.policies[0].definition == policy_to_dict(policy)
        assert event.policy_results[0].match_provenance.status == "complete"
        assert event.execution.status == "complete"
    finally:
        collection.close()


def test_preview_boundary_history_failure_does_not_change_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection = Collection(str(tmp_path / "boundary-history-failure.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        policy = Policy(
            id="boundary-history-failure",
            name="Boundary history failure",
            triggers=(),
            scope=Scope((DeckSelector("Mining"),)),
            conditions=AllCardsCondition(),
            actions=(TagAction("retired"),),
        )
        save_policies(collection, policy)
        preview = evaluate_policy(collection, policy)

        def fail(*_args: object, **_kwargs: object) -> object:
            message = "injected boundary history failure"
            raise RuntimeError(message)

        monkeypatch.setattr(execution_module, "_history_boundary_dispositions", fail)
        monkeypatch.setattr(execution_module, "exception", lambda *_args, **_kwargs: None)

        result = execute_approved_reports(
            collection,
            (preview,),
            {policy.id: {card_id}},
            "Boundary history failure",
            collect_history=True,
        )

        assert collection.get_note(note.id).has_tag("retired")
        assert result.affected_cards == 1
        assert result.semantics.status == "unavailable"
        assert result.semantics.reason_code == "preview_boundary_trace_failed"
        collection.undo()
        assert not collection.get_note(note.id).has_tag("retired")
    finally:
        collection.close()


def test_review_counts_and_last_review_are_loaded_from_genuine_answers(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "history.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "history"
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        for offset, ease in ((3000, 1), (2000, 3), (1000, 0)):
            collection.db.execute(
                "insert into revlog values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                card_id - offset,
                card_id,
                -1,
                ease,
                1,
                0,
                2500,
                1000,
                0,
            )
        policy = parse_policy(
            {
                "id": "history",
                "name": "History",
                "match": "all",
                "conditions": [
                    {"type": "answer_count", "count": 2, "operator": "eq"},
                    {"type": "correct_answer_count", "count": 1, "operator": "eq"},
                    {"type": "correct_answer_rate", "percent": 50, "operator": "eq"},
                    {"type": "age", "source": "last_review", "days": 0, "operator": "gte"},
                ],
                "actions": [{"type": "set_flag", "flag": "purple"}],
            }
        )
        report = evaluate_policy(collection, policy, now_ms=card_id)
        assert [card.card_id for card in report.actionable] == [card_id]
        assert report.qualifying[0].answer_count == 2
        assert report.qualifying[0].correct_answer_count == 1
        result = execute_plan(
            collection, build_execution_plan((report,), collection), "Set test flag"
        )
        assert result.affected_cards == 1
        assert collection.get_card(card_id).user_flag() == 7
    finally:
        collection.close()


def test_scheduler_specific_conditions_fail_closed_and_fsrs_sql_handles_missing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = Collection(str(tmp_path / "scheduler.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "scheduler"
        collection.add_note(note, deck_id)

        def policy(condition: dict) -> Policy:
            return parse_policy(
                {
                    "id": "scheduler",
                    "name": "Scheduler",
                    "match": "all",
                    "conditions": [condition],
                    "actions": [{"type": "suspend"}],
                }
            )

        fsrs_policy = policy({"type": "fsrs_stability", "days": 30, "operator": "gte"})
        assert evaluate_policy(collection, fsrs_policy).errors == (
            "FSRS conditions require FSRS to be enabled",
        )
        assert not evaluate_policy(
            collection,
            policy({"type": "sm2_ease", "percent": 250, "operator": "gte"}),
        ).errors

        monkeypatch.setattr(evaluator, "_fsrs_enabled", lambda *_args: True)
        report = evaluate_policy(collection, fsrs_policy)
        assert not report.errors
        assert not report.qualifying  # New cards have no FSRS memory state yet.
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
                "scope": {
                    "decks": [{"deck": "Source", "include_subdecks": False}],
                },
                "actions": [action],
            }
        )
        save_policies(collection, policy)
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
        skipped_session = prepare_history_session(
            enabled=True,
            profile={},
            policies=(policy,),
            invocation=Invocation("manual"),
            activations=(PolicyActivation(policy.id, "manual"),),
            anki_version="test",
            root=tmp_path / "skipped-history",
        )
        assert skipped_session is not None
        skipped = execute_approved_reports(
            collection,
            (report,),
            {policy.id: {sibling}},
            "Skip unapproved note",
            history=skipped_session,
        )
        assert skipped.affected_cards == 0
        assert {item.disposition for item in skipped.semantics.non_applied} == {
            "preview_note_boundary"
        }
        expected_trigger = sibling if kind == "move_note" else first
        assert all(
            item.trigger_card_ids == (expected_trigger,) for item in skipped.semantics.non_applied
        )
        assert skipped_session.store is not None
        skipped_event = skipped_session.store.read_recent().records[0].event
        assert skipped_event is not None
        assert len(skipped_event.non_applied) == 1
        skipped_record = skipped_event.non_applied[0]
        assert skipped_record.counts.cards == (1 if kind == "move_note" else 2)
        assert skipped_record.counts.matching_trigger_cards == (0 if kind == "move_note" else 1)
        assert skipped_record.counts.consequential_sibling_cards == 1
        assert skipped_record.contributors[0].trigger_cards == 1
        collection.set_deck([first], source)
        collection.set_deck([sibling], outside)
        if kind == "suspend_note":
            collection.sched.suspend_cards([first])
        elif kind == "unsuspend_note":
            collection.sched.unsuspend_cards([first])
        session = prepare_history_session(
            enabled=True,
            profile={},
            policies=(policy,),
            invocation=Invocation("manual"),
            activations=(PolicyActivation(policy.id, "manual"),),
            anki_version="test",
            root=tmp_path / "local-history",
        )
        assert session is not None
        result = execute_approved_reports(
            collection,
            (report,),
            {policy.id: {sibling}},
            "Apply note action",
            history=session,
        )
        assert result.affected_cards == 1
        changed = collection.get_card(sibling)
        assert (
            changed.did == source
            if kind == "move_note"
            else changed.queue == (-1 if kind == "suspend_note" else 0)
        )
        assert session.error_message is None
        assert session.store is not None
        history_event = session.store.read_recent().records[0].event
        assert history_event is not None
        assert history_event.outcome.status == "succeeded"
        assert history_event.outcome.result == "changed"
        assert len(history_event.effects) == 1
        assert history_event.effects[0].action.type == kind
        assert history_event.effects[0].counts.cards == 1
        assert history_event.effects[0].counts.matching_trigger_cards == 0
        assert history_event.effects[0].counts.consequential_sibling_cards == 1
        assert history_event.effects[0].contributors[0].trigger_cards == 1
        collection.undo()
        restored = collection.get_card(sibling)
        assert restored.did == outside
        assert restored.queue == (-1 if kind == "unsuspend_note" else 0)
    finally:
        collection.close()


def test_note_wide_preview_candidate_retains_lost_trigger_provenance(
    tmp_path: Path,
) -> None:
    collection = Collection(str(tmp_path / "lost-note-trigger.anki2"))
    try:
        source = collection.decks.add_normal_deck_with_name("Source").id
        outside = collection.decks.add_normal_deck_with_name("Outside").id
        model = collection.models.by_name("Basic (and reversed card)")
        assert model is not None
        note = collection.new_note(model)
        note["Front"], note["Back"] = "front", "back"
        collection.add_note(note, source)
        trigger, sibling = map(int, collection.card_ids_of_note(note.id))
        collection.set_deck([sibling], outside)
        collection.sched.suspend_cards([trigger])
        policy = Policy(
            id="lost-note-trigger",
            name="Lost note trigger",
            triggers=(),
            scope=Scope((DeckSelector("Source"),)),
            conditions=TagCondition(("excluded",), "contains_none"),
            actions=(SuspendAction("note"),),
        )
        save_policies(collection, policy)
        preview = evaluate_policy(collection, policy)
        assert [card.card_id for card in preview.qualifying] == [trigger]
        assert [card.card_id for card in preview.actionable] == [sibling]
        collection.tags.bulk_add([note.id], "excluded")
        session = prepare_history_session(
            enabled=True,
            profile={},
            policies=(policy,),
            invocation=Invocation("manual"),
            activations=(PolicyActivation(policy.id, "manual"),),
            anki_version="test",
            root=tmp_path / "local-history",
        )
        assert session is not None

        result = execute_approved_reports(
            collection,
            (preview,),
            {policy.id: {sibling}},
            "Lost note trigger",
            history=session,
        )

        assert result.affected_cards == 0
        disposition = result.semantics.non_applied[0]
        assert disposition.disposition == "preview_no_longer_matching"
        assert disposition.target_card_id == sibling
        assert disposition.trigger_card_ids == (trigger,)
        assert session.store is not None
        event = session.store.read_recent().records[0].event
        assert event is not None
        record = event.non_applied[0]
        assert record.counts.cards == 1
        assert record.counts.matching_trigger_cards == 0
        assert record.counts.consequential_sibling_cards == 1
        assert record.contributors[0].trigger_cards == 1
        assert record.contributors[0].match_signatures == ()
    finally:
        collection.close()


def test_policy_actions_only_affect_other_policy_matching_on_next_cleanup(tmp_path: Path) -> None:
    collection = Collection(str(tmp_path / "batch-matching.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        note = collection.new_note(collection.models.by_name("Basic"))
        note["Front"] = "word"
        collection.add_note(note, deck_id)
        card_id = int(collection.card_ids_of_note(note.id)[0])
        add_tag = parse_policy(
            {
                "id": "add-foo",
                "name": "Add foo",
                "scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]},
                "match": "all",
                "conditions": [{"type": "tags", "operator": "contains_none", "tags": ["foo"]}],
                "actions": [{"type": "add_tags", "tags": ["foo"]}],
            }
        )
        suspend = parse_policy(
            {
                "id": "suspend-foo",
                "name": "Suspend foo",
                "scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]},
                "match": "all",
                "conditions": [{"type": "tags", "operator": "contains_any", "tags": ["foo"]}],
                "actions": [{"type": "suspend"}],
            }
        )
        policies = (add_tag, suspend)
        save_policies(collection, *policies)

        first_reports = evaluator.evaluate_policies(collection, policies)
        assert [len(report.actionable) for report in first_reports] == [1, 0]
        execute_approved_reports(
            collection,
            first_reports,
            {
                report.policy.id: {card.card_id for card in report.actionable}
                for report in first_reports
            },
            "First batch",
        )
        assert collection.get_note(note.id).has_tag("foo")
        assert collection.get_card(card_id).queue != -1

        second_reports = evaluator.evaluate_policies(collection, policies)
        assert [len(report.actionable) for report in second_reports] == [0, 1]
        execute_approved_reports(
            collection,
            second_reports,
            {
                report.policy.id: {card.card_id for card in report.actionable}
                for report in second_reports
            },
            "Second batch",
        )
        assert collection.get_card(card_id).queue == -1
    finally:
        collection.close()


def test_fresh_execution_records_only_meaningful_preview_boundary_dispositions(
    tmp_path: Path,
) -> None:
    collection = Collection(str(tmp_path / "history-boundary.anki2"))
    try:
        deck_id = collection.decks.id("Mining")
        first_note = collection.new_note(collection.models.by_name("Basic"))
        first_note["Front"] = "first"
        collection.add_note(first_note, deck_id)
        first_card = int(collection.card_ids_of_note(first_note.id)[0])
        policy = parse_policy(
            {
                "id": "boundary",
                "name": "Boundary",
                "scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]},
                "match": "all",
                "conditions": [
                    {
                        "type": "tags",
                        "operator": "contains_none",
                        "tags": ["excluded"],
                    }
                ],
                "actions": [{"type": "suspend"}],
            }
        )
        save_policies(collection, policy)
        preview = evaluate_policy(collection, policy)
        assert [card.card_id for card in preview.actionable] == [first_card]

        collection.tags.bulk_add([first_note.id], "excluded")
        second_note = collection.new_note(collection.models.by_name("Basic"))
        second_note["Front"] = "second"
        collection.add_note(second_note, deck_id)
        second_card = int(collection.card_ids_of_note(second_note.id)[0])

        result = execute_approved_reports(
            collection,
            (preview,),
            {policy.id: {first_card}},
            "Boundary test",
            collect_history=True,
        )

        assert result.affected_cards == 0
        assert collection.get_card(second_card).queue != -1
        dispositions = {
            (item.disposition, item.target_card_id) for item in result.semantics.non_applied
        }
        assert dispositions == {
            ("preview_no_longer_matching", first_card),
            ("preview_newly_matching", second_card),
        }
        assert result.semantics.effects == ()
        assert result.execution_ledger.status == "complete"
        assert result.execution_ledger.steps == ()
    finally:
        collection.close()


def test_note_tag_effect_distinguishes_trigger_and_consequential_sibling(
    tmp_path: Path,
) -> None:
    collection = Collection(str(tmp_path / "tag-sibling-history.anki2"))
    try:
        source = collection.decks.id("Source")
        outside = collection.decks.id("Outside")
        model = collection.models.by_name("Basic (and reversed card)")
        assert model is not None
        note = collection.new_note(model)
        note["Front"], note["Back"] = "front", "back"
        collection.add_note(note, source)
        first, sibling = map(int, collection.card_ids_of_note(note.id))
        collection.set_deck([sibling], outside)
        policy = Policy(
            id="tag-note",
            name="Tag note",
            triggers=(),
            scope=Scope((DeckSelector("Source"),)),
            conditions=AllCardsCondition(),
            actions=(TagAction("retired"),),
        )

        report = evaluate_policy(collection, policy, collect_provenance=True)
        plan = build_execution_plan(
            (report,),
            collection,
            collect_history=True,
        )

        assert len(plan.semantics.effects) == 1
        effect = plan.semantics.effects[0]
        assert effect.affected_card_ids_complete
        assert effect.affected_card_ids == tuple(sorted((first, sibling)))
        assert effect.matching_trigger_card_ids == (first,)
        assert effect.consequential_sibling_card_ids == (sibling,)
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
            scope=Scope((DeckSelector("Leeches"),)),
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
                    "scope": {
                        "decks": [{"deck": deck, "include_subdecks": False}],
                    },
                    "actions": [action],
                }
            )

        note_policy = policy("note", "Source", {"type": "suspend_note"})
        sibling_policy = policy("sibling", "Outside", {"type": "unsuspend"})
        reports = (
            evaluate_policy(collection, note_policy),
            evaluate_policy(collection, sibling_policy),
        )
        assert [card.card_id for card in reports[0].qualifying] == [first]
        assert {card.card_id for card, _actions in reports[0].card_actions} == {first, sibling}
        plan = build_execution_plan(reports, collection)
        assert plan.is_empty
        assert set(plan.conflicted_card_ids) == {first, sibling}
        rows = build_preview_rows(
            plan,
            reports,
            {source: "Source", outside: "Outside"},
        )
        assert {row.card_id for row in rows} == {first, sibling}
        assert all(row.overlapping for row in rows)
        result = execute_plan(collection, plan, "Conflicted note cleanup")
        assert result.affected_cards == 0
        assert result.conflicts == 2
        assert collection.get_card(first).queue != -1
        assert collection.get_card(sibling).queue != -1
    finally:
        collection.close()
