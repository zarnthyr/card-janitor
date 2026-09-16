# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from card_janitor import addon, automatic


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    operations = []
    mutations = []

    class Query:
        def __init__(self, *, parent: object, op: object, success: object) -> None:
            self.parent = parent
            self.op = op
            self.success = success
            operations.append(self)

        def failure(self, callback: object) -> "Query":
            self.failed = callback
            return self

        def run_in_background(self) -> None:
            pass

    class Mutation(Query):
        def __init__(self, *, parent: object, op: object) -> None:
            self.parent = parent
            self.op = op
            mutations.append(self)

        def success(self, callback: object) -> "Mutation":
            self.applied = callback
            return self

    window = SimpleNamespace(
        col=SimpleNamespace(sched=SimpleNamespace(today=10)), pm=SimpleNamespace(profile={})
    )
    parsed = SimpleNamespace(
        issues=(),
        config=SimpleNamespace(
            policies=(SimpleNamespace(mode="automatic"),), notify_after_automatic_run=False
        ),
    )
    monkeypatch.setattr(automatic, "mw", window)
    monkeypatch.setattr(automatic, "_run_state", automatic._RunState())
    monkeypatch.setattr(automatic, "_load_configured", lambda: parsed)
    monkeypatch.setattr(automatic, "QueryOp", Query)
    monkeypatch.setattr(automatic, "CollectionOp", Mutation)
    monkeypatch.setattr(automatic, "showWarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(automatic, "evaluate_policies", lambda *_args: ())
    monkeypatch.setattr(
        automatic,
        "build_execution_plan",
        lambda *_args: SimpleNamespace(is_empty=True, conflicted_card_ids=()),
    )
    return SimpleNamespace(window=window, operations=operations, mutations=mutations)


@pytest.mark.parametrize("outcome", ["success", "failure", "changed_collection", "cancelled"])
def test_automatic_mutation_phase_checks_context_and_marks_only_success(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    report = SimpleNamespace(
        policy=SimpleNamespace(id="p", name="Policy"),
        errors=(),
        actionable=(SimpleNamespace(card_id=1),),
    )
    result = SimpleNamespace(affected_cards=1, conflicts=0)
    executions = []
    monkeypatch.setattr(
        automatic, "build_execution_plan", lambda *_args: SimpleNamespace(is_empty=False)
    )
    monkeypatch.setattr(
        automatic, "execute_approved_reports", lambda *args: executions.append(args) or result
    )
    automatic.run_automatic_policies()
    runner.operations[0].success((report,))
    assert automatic.LAST_AUTOMATIC_DAY_PROFILE_KEY not in runner.window.pm.profile
    automatic.run_automatic_policies()
    assert len(runner.operations) == 1
    mutation = runner.mutations[0]
    if outcome == "failure":
        mutation.failed(RuntimeError("execution failed"))
    elif outcome == "success":
        mutation.applied(mutation.op(runner.window.col))
        assert executions[0][2] == {"p": {1}}
    else:
        if outcome == "changed_collection":
            runner.window.col = SimpleNamespace(sched=SimpleNamespace(today=10))
        else:
            automatic.cancel_automatic_run()
        with pytest.raises(automatic.CancelledAutomaticRunError):
            mutation.op(runner.window.col)
        mutation.failed(automatic.CancelledAutomaticRunError())
        assert not executions
    assert runner.window.pm.profile.get(automatic.LAST_AUTOMATIC_DAY_PROFILE_KEY) == (
        10 if outcome == "success" else None
    )
    assert automatic._run_state.token is None


def test_automatic_run_coalesces_and_marks_only_completed_day(runner: SimpleNamespace) -> None:
    automatic.run_automatic_policies()
    automatic.run_automatic_policies()
    assert len(runner.operations) == 1
    operation = runner.operations[0]
    operation.success(operation.op(runner.window.col))
    assert runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAY_PROFILE_KEY] == 10
    automatic.run_automatic_policies()
    assert len(runner.operations) == 1


def test_failed_automatic_run_can_retry(runner: SimpleNamespace) -> None:
    automatic.run_automatic_policies()
    runner.operations[0].failed(RuntimeError("failure"))
    assert not runner.window.pm.profile
    automatic.run_automatic_policies()
    assert len(runner.operations) == 2


def test_profile_change_invalidates_background_work(runner: SimpleNamespace) -> None:
    automatic.run_automatic_policies()
    old_profile = runner.window.pm.profile
    runner.window.pm.profile = {}
    operation = runner.operations[0]
    with pytest.raises(automatic.CancelledAutomaticRunError):
        operation.op(runner.window.col)
    operation.success(())
    assert not old_profile
    assert not runner.window.pm.profile
    assert automatic._run_state.token is None


def test_day_rollover_schedules_next_day_after_completion(runner: SimpleNamespace) -> None:
    automatic.run_automatic_policies()
    runner.window.col.sched.today = 11
    runner.operations[0].success(())
    assert runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAY_PROFILE_KEY] == 10
    assert len(runner.operations) == 2


def test_callback_failure_releases_inflight_guard(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object) -> None:
        message = "planning failed"
        raise RuntimeError(message)

    monkeypatch.setattr(automatic, "build_execution_plan", fail)
    automatic.run_automatic_policies()
    runner.operations[0].success(())
    assert automatic._run_state.token is None
    assert not runner.window.pm.profile


@pytest.mark.parametrize("sync_enabled", [False, True])
def test_opening_cleanup_waits_for_sync_and_close_invalidates_timer(
    monkeypatch: pytest.MonkeyPatch, sync_enabled: bool
) -> None:
    profile = {}
    calls = []
    timers = []
    monkeypatch.setattr(
        addon,
        "mw",
        SimpleNamespace(can_auto_sync=lambda: sync_enabled, pm=SimpleNamespace(profile=profile)),
    )
    monkeypatch.setattr(addon, "_opening_state", addon._OpeningState())
    monkeypatch.setattr(addon, "safe_install_menu", lambda: None)
    monkeypatch.setattr(addon, "close_card_janitor", lambda: None)
    monkeypatch.setattr(addon, "cancel_automatic_run", lambda: None)
    monkeypatch.setattr(addon, "run_automatic_policies", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        addon,
        "QTimer",
        SimpleNamespace(singleShot=lambda _delay, callback: timers.append(callback)),
    )
    addon.on_profile_loaded()
    if sync_enabled:
        assert not calls
        addon.on_sync_finished()
        assert len(timers) == 1
        timers[0]()
        assert calls == [{"trigger": "profile_open"}]
        addon.on_profile_loaded()
        addon.on_sync_finished()
        addon.on_profile_closing()
        timers[1]()
        assert len(calls) == 1
    else:
        assert calls == [{"trigger": "profile_open"}]
        addon.on_sync_finished()
        assert not timers
