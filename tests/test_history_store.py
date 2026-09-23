# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from anki.collection import OpChanges
from card_janitor.actions import ExecutionResult, build_execution_plan
from card_janitor.engine import CardFacts, ResolvedAction, evaluate_facts
from card_janitor.history_events import (
    CleanupEvent,
    CleanupEventContext,
    Invocation,
    PolicyActivation,
    Producer,
    build_success_event,
    event_to_json,
)
from card_janitor.history_semantics import (
    ExecutionLedger,
    ExecutionLedgerStep,
    ExecutionLedgerTarget,
)
from card_janitor.history_store import (
    HISTORY_FILENAME,
    SOURCE_ID_PROFILE_KEY,
    HistoryStore,
    source_id_for_profile,
)
from card_janitor.models import DeckSelector, IntervalCondition, Policy, Scope, SuspendAction


def cleanup_event(source_id: str, *, event_id: str | None = None) -> CleanupEvent:
    policy = Policy(
        id="policy",
        name="Policy",
        triggers=(),
        scope=Scope((DeckSelector("Mining"),)),
        conditions=IntervalCondition(1, "gte"),
        actions=(SuspendAction(),),
    )
    item = CardFacts(1, 10, 1, 0, 2, 2, 100, 1_000, 2_000, frozenset())
    report = evaluate_facts(
        policy,
        [item],
        {1},
        (ResolvedAction(policy.actions[0]),),
        now_ms=3_000,
        collect_provenance=True,
    )
    plan = build_execution_plan((report,), collect_history=True)
    ledger = ExecutionLedger(
        "complete",
        (
            ExecutionLedgerStep(
                0,
                "suspend",
                "card",
                (ExecutionLedgerTarget(None, (1,)),),
                "completed",
            ),
        ),
        effects_complete=True,
        unknown_effects_possible=False,
    )
    result = ExecutionResult(OpChanges(), 1, 0, plan.semantics, ledger)
    started = datetime(2026, 9, 23, 12, tzinfo=UTC)
    context = CleanupEventContext(
        event_id or str(uuid4()),
        source_id,
        Producer("0.8.0", "26.8.1"),
        started,
        started,
        Invocation("manual"),
        (PolicyActivation(policy.id, "manual"),),
    )
    return build_success_event(context=context, policies=(policy,), result=result)


def test_profile_source_id_is_stable_and_canonical() -> None:
    profile: dict[str, object] = {}

    first = source_id_for_profile(profile)
    second = source_id_for_profile(profile)

    assert first == second
    assert profile[SOURCE_ID_PROFILE_KEY] == first


def test_invalid_profile_source_id_is_replaced() -> None:
    profile: dict[str, object] = {SOURCE_ID_PROFILE_KEY: "../not-a-uuid"}

    source_id = source_id_for_profile(profile)

    assert source_id != "../not-a-uuid"
    assert profile[SOURCE_ID_PROFILE_KEY] == source_id


def test_append_and_read_recent_round_trip(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    event = cleanup_event(source_id)

    store.append(event)
    page = store.read_recent()

    assert store.path == tmp_path / source_id / HISTORY_FILENAME
    assert page.records[0].event == event
    assert page.records[0].raw_json == event_to_json(event)
    assert page.next_before_line is None


def test_recent_pages_are_newest_first_and_have_stable_line_cursors(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    events = tuple(cleanup_event(source_id) for _index in range(5))
    for event in events:
        store.append(event)

    newest = store.read_recent(limit=2)
    older = store.read_recent(limit=2, before_line=newest.next_before_line)
    oldest = store.read_recent(limit=2, before_line=older.next_before_line)

    assert [item.event for item in newest.records] == [events[4], events[3]]
    assert [item.event for item in older.records] == [events[2], events[1]]
    assert [item.event for item in oldest.records] == [events[0]]
    assert oldest.next_before_line is None


def test_corrupt_and_unsupported_lines_do_not_hide_valid_events(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    first = cleanup_event(source_id)
    second = cleanup_event(source_id)
    store.append(first)
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("{truncated\n")
        handle.write(
            json.dumps(
                {
                    "schema": {"name": "card_janitor.cleanup_history", "version": 999},
                    "event_id": str(uuid4()),
                }
            )
            + "\n"
        )
    store.append(second)

    records = store.read_recent(limit=10).records

    assert [record.event for record in records if record.event is not None] == [second, first]
    assert sum(record.error is not None for record in records) == 1
    assert sum(record.unsupported_schema is not None for record in records) == 1


def test_invalid_utf8_line_is_isolated(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    first = cleanup_event(source_id)
    second = cleanup_event(source_id)
    store.append(first)
    with store.path.open("ab") as handle:
        handle.write(b"\xff\xfe\n")
    store.append(second)

    records = store.read_recent(limit=10).records

    assert [record.event for record in records if record.event is not None] == [second, first]
    assert [record.error for record in records if record.error] == ["line is not valid UTF-8"]


def test_export_is_canonical_and_reports_skipped_corruption(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path / "history")
    event = cleanup_event(source_id)
    store.append(event)
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("not-json\n")
        handle.write(
            json.dumps(
                {
                    "schema": {"name": "card_janitor.cleanup_history", "version": 2},
                    "event_id": "future",
                }
            )
            + "\n"
        )
    destination = tmp_path / "export" / "history.jsonl"

    result = store.export(destination)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert lines[0] == event_to_json(event)
    assert json.loads(lines[1])["event_id"] == "future"
    assert result.exported == 2
    assert result.corrupt_skipped == 1
    assert result.unsupported_preserved == 1


def test_selected_export_filters_by_event_id(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path / "history")
    first = cleanup_event(source_id)
    second = cleanup_event(source_id)
    store.append(first)
    store.append(second)
    destination = tmp_path / "selected.jsonl"

    result = store.export(destination, event_ids={second.event_id})

    assert destination.read_text(encoding="utf-8") == event_to_json(second) + "\n"
    assert result.exported == 1


@pytest.mark.parametrize("operation", ["archive", "clear"])
def test_rotation_preserves_the_old_log_and_starts_fresh(
    tmp_path: Path,
    operation: str,
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    first = cleanup_event(source_id)
    second = cleanup_event(source_id)
    store.append(first)

    archived = getattr(store, operation)(now=datetime(2026, 9, 23, tzinfo=UTC))
    store.append(second)

    assert archived is not None
    assert archived.read_text(encoding="utf-8") == event_to_json(first) + "\n"
    assert store.path.read_text(encoding="utf-8") == event_to_json(second) + "\n"


def test_store_rejects_event_from_another_source_before_creating_files(tmp_path: Path) -> None:
    store = HistoryStore(str(uuid4()), root=tmp_path)

    with pytest.raises(ValueError, match="source"):
        store.append(cleanup_event(str(uuid4())))

    assert not store.path.exists()
