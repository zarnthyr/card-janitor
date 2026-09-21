# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import evaluator


def test_policy_batch_uses_one_evaluation_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[object, int]] = []
    policies = (object(), object(), object())
    monkeypatch.setattr(evaluator, "time", lambda: 123.456)
    monkeypatch.setattr(
        evaluator,
        "evaluate_policy",
        lambda _col, policy, *, now_ms: seen.append((policy, now_ms)) or policy,
    )

    assert evaluator.evaluate_policies(object(), policies) == policies
    assert seen == [(policy, 123456) for policy in policies]


def test_fsrs_query_uses_supplied_evaluation_timestamp() -> None:
    calls = []
    collection = SimpleNamespace(
        sched=SimpleNamespace(today=100, day_cutoff=200),
        db=SimpleNamespace(all=lambda *args: calls.append(args) or []),
    )

    evaluator._load_facts_where(
        collection,
        "c.did",
        {1},
        load_history=False,
        load_fsrs=True,
        now_seconds=345,
    )

    assert calls
    assert calls[0][1:4] == (100, 200, 345)
