# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from anki.collection import OpChanges
from card_janitor import history_store as history_store_module
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
    HistoryStorageError,
    HistoryStore,
    existing_source_id_for_profile,
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


def test_existing_profile_source_id_lookup_does_not_create_or_replace_it() -> None:
    empty: dict[str, object] = {}
    invalid: dict[str, object] = {SOURCE_ID_PROFILE_KEY: "not-a-uuid"}

    assert existing_source_id_for_profile(empty) is None
    assert empty == {}
    assert existing_source_id_for_profile(invalid) is None
    assert invalid[SOURCE_ID_PROFILE_KEY] == "not-a-uuid"


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
        handle.write('{"schema": NaN}\n')
        handle.write(
            '{"schema":{"name":"card_janitor.cleanup_history","version":1},'
            '"schema":{"name":"card_janitor.cleanup_history","version":1}}\n'
        )
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
    assert sum(record.error is not None for record in records) == 3
    assert sum(record.unsupported_schema is not None for record in records) == 1


def test_record_from_another_source_is_isolated_as_corrupt(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))
    foreign = cleanup_event(str(uuid4()))
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write(event_to_json(foreign) + "\n")

    records = store.read_recent().records

    assert records[0].event is None
    assert records[0].error == "event source does not match history store"
    assert records[1].event is not None


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


def test_append_after_truncated_final_write_keeps_new_event_readable(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    first = cleanup_event(source_id)
    second = cleanup_event(source_id)
    store.append(first)
    truncated = store.path.read_bytes()[:-12]
    assert not truncated.endswith(b"\n")
    store.path.write_bytes(truncated)

    store.append(second)

    records = store.read_recent().records
    assert records[0].event == second
    assert records[1].event is None
    assert records[1].error is not None


def test_export_copies_entire_history_without_modifying_it(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path / "history")
    event = cleanup_event(source_id)
    store.append(event)
    with store.path.open("ab") as handle:
        handle.write(b"not-json\n")
        handle.write(b"\xff\xfe\n")
    destination = tmp_path / "export" / "history.jsonl"
    original = store.path.read_bytes()

    exported = store.export(destination)

    assert exported == 3
    assert destination.read_bytes() == original
    assert store.path.read_bytes() == original


def test_export_rejects_active_log_through_symlink_alias(tmp_path: Path) -> None:
    source_id = str(uuid4())
    root = tmp_path / "history"
    store = HistoryStore(source_id, root=root)
    store.append(cleanup_event(source_id))
    alias = tmp_path / "history-alias"
    alias.symlink_to(root, target_is_directory=True)

    with pytest.raises(ValueError, match="active log"):
        store.export(alias / source_id / HISTORY_FILENAME)


def test_failed_export_does_not_replace_existing_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path / "history")
    store.append(cleanup_event(source_id))
    destination = tmp_path / "history.jsonl"
    destination.write_bytes(b"existing export\n")

    def fail(_handle: object) -> None:
        message = "injected export failure"
        raise OSError(message)

    monkeypatch.setattr(history_store_module, "_flush_file", fail)

    with pytest.raises(HistoryStorageError, match="could not export"):
        store.export(destination)

    assert destination.read_bytes() == b"existing export\n"
    assert not tuple(destination.parent.glob(f".{destination.name}.*.tmp"))


def test_clear_permanently_deletes_all_profile_history(tmp_path: Path) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    first = cleanup_event(source_id)
    second = cleanup_event(source_id)
    store.append(first)
    legacy_archive = store.directory / "archive" / "old.jsonl"
    legacy_archive.parent.mkdir()
    legacy_archive.write_text(event_to_json(first) + "\n", encoding="utf-8")

    assert store.clear()

    assert not store.directory.exists()
    assert not tuple(store.root.glob(f".{source_id}.clearing-*"))
    assert store.read_recent().records == ()
    assert not store.clear()
    store.append(second)
    assert store.path.read_text(encoding="utf-8") == event_to_json(second) + "\n"


def test_read_treats_a_log_deleted_while_waiting_for_its_lock_as_empty(
    tmp_path: Path,
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    store.append(cleanup_event(source_id))

    class DeleteOnEnter:
        def __enter__(self) -> None:
            store.path.unlink()

        def __exit__(self, *_args: object) -> None:
            return None

    store._lock = DeleteOnEnter()

    assert store.read_recent().records == ()


def test_clear_failure_does_not_prevent_future_appends_or_clear_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = str(uuid4())
    store = HistoryStore(source_id, root=tmp_path)
    first = cleanup_event(source_id)
    second = cleanup_event(source_id)
    store.append(first)
    original_rmtree = history_store_module.shutil.rmtree
    failures = 0

    def fail_once(path: Path) -> None:
        nonlocal failures
        if failures == 0:
            failures += 1
            message = "injected clear failure"
            raise OSError(message)
        original_rmtree(path)

    monkeypatch.setattr(history_store_module.shutil, "rmtree", fail_once)

    with pytest.raises(HistoryStorageError, match="could not clear"):
        store.clear()

    assert not store.directory.exists()
    assert len(tuple(store.root.glob(f".{source_id}.clearing-*"))) == 1
    store.append(second)
    assert store.read_recent().records[0].event == second
    assert store.clear()
    assert not store.directory.exists()
    assert not tuple(store.root.glob(f".{source_id}.clearing-*"))


def test_store_rejects_event_from_another_source_before_creating_files(tmp_path: Path) -> None:
    store = HistoryStore(str(uuid4()), root=tmp_path)

    with pytest.raises(ValueError, match="source"):
        store.append(cleanup_event(str(uuid4())))

    assert not store.path.exists()
