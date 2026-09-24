# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from datetime import UTC, datetime
from pathlib import Path

import pytest
from anki.collection import OpChanges
from card_janitor import history_runtime
from card_janitor.actions import ExecutionResult, build_execution_plan
from card_janitor.engine import CardFacts, ResolvedAction, evaluate_facts
from card_janitor.history_events import Invocation, PolicyActivation
from card_janitor.history_runtime import AUDIT_GAP_MESSAGE, prepare_history_session
from card_janitor.history_semantics import (
    ExecutionLedger,
    ExecutionLedgerStep,
    ExecutionLedgerTarget,
)
from card_janitor.models import DeckSelector, IntervalCondition, Policy, Scope, SuspendAction


def policy_and_result() -> tuple[Policy, ExecutionResult]:
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
    return policy, ExecutionResult(OpChanges(), 1, 0, plan.semantics, ledger)


def test_disabled_history_does_not_create_identity_or_session(tmp_path: Path) -> None:
    policy, _result = policy_and_result()
    profile: dict[str, object] = {}

    session = prepare_history_session(
        enabled=False,
        profile=profile,
        policies=(policy,),
        invocation=Invocation("manual"),
        activations=(PolicyActivation(policy.id, "manual"),),
        anki_version="26.8.1",
        root=tmp_path,
    )

    assert session is None
    assert profile == {}
    assert not tuple(tmp_path.iterdir())


def test_successful_session_appends_one_terminal_event(tmp_path: Path) -> None:
    policy, result = policy_and_result()
    profile: dict[str, object] = {}
    session = prepare_history_session(
        enabled=True,
        profile=profile,
        policies=(policy,),
        invocation=Invocation("manual"),
        activations=(PolicyActivation(policy.id, "manual"),),
        anki_version="26.8.1",
        root=tmp_path,
        started_at=datetime(2026, 9, 23, tzinfo=UTC),
    )
    assert session is not None

    session.record_success(result)
    session.record_success(result)

    page = session.store.read_recent()
    assert len(page.records) == 1
    assert page.records[0].event.outcome.status == "succeeded"
    assert page.records[0].event.producer.card_janitor_version
    assert session.error_message is None


def test_writer_failure_is_an_audit_gap_not_an_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, result = policy_and_result()
    session = prepare_history_session(
        enabled=True,
        profile={},
        policies=(policy,),
        invocation=Invocation("manual"),
        activations=(PolicyActivation(policy.id, "manual"),),
        anki_version="26.8.1",
        root=tmp_path,
    )
    assert session is not None

    def fail(_event: object) -> None:
        message = "disk full"
        raise OSError(message)

    monkeypatch.setattr(session.store, "append", fail)
    monkeypatch.setattr(history_runtime, "exception", lambda *_args, **_kwargs: None)

    session.record_success(result)

    assert session.error_message == AUDIT_GAP_MESSAGE
    assert session.terminal_recorded


def test_event_construction_failure_is_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy, result = policy_and_result()
    session = prepare_history_session(
        enabled=True,
        profile={},
        policies=(policy,),
        invocation=Invocation("manual"),
        activations=(PolicyActivation(policy.id, "manual"),),
        anki_version="26.8.1",
        root=tmp_path,
    )
    assert session is not None

    monkeypatch.setattr(
        history_runtime,
        "build_success_event",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    monkeypatch.setattr(history_runtime, "exception", lambda *_args, **_kwargs: None)

    session.record_success(result)

    assert session.error_message == AUDIT_GAP_MESSAGE
    assert not session.store.path.exists()
