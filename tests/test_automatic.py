# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from types import SimpleNamespace

import pytest
from aqt.main import AnkiQt
from card_janitor import addon, automatic
from card_janitor.configuration import COLLECTION_POLICIES_KEY
from card_janitor.execution import StalePolicyDefinitionsError
from card_janitor.history_semantics import PlanSemantics
from card_janitor.models import Trigger, parse_policy, policy_to_dict


def test_registration_uses_hooks_without_modifying_anki_window_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_methods = dict(AnkiQt.__dict__)
    hooks = SimpleNamespace(
        profile_did_open=[], day_did_change=[], sync_did_finish=[], profile_will_close=[]
    )
    monkeypatch.setattr(addon, "gui_hooks", hooks)
    addon.register_addon()
    addon.register_addon()
    assert dict(AnkiQt.__dict__) == original_methods
    assert hooks.profile_did_open == [addon.on_profile_loaded]
    assert hooks.day_did_change == [addon.on_day_changed]
    assert hooks.sync_did_finish == [addon.on_sync_finished]
    assert hooks.profile_will_close == [addon.on_profile_closing]


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.usefixtures("runner")
def test_automatic_notification_preserves_existing_tooltip(
    monkeypatch: pytest.MonkeyPatch, visible: bool
) -> None:
    timers = []
    shown = []
    label = SimpleNamespace(isVisible=lambda: visible)
    monkeypatch.setattr(automatic.aqt_utils, "_tooltipLabel", label)
    monkeypatch.setattr(
        automatic, "QTimer", SimpleNamespace(singleShot=lambda _delay, cb: timers.append(cb))
    )
    monkeypatch.setattr(automatic, "tooltip", lambda message, **_kwargs: shown.append(message))
    automatic._notify_automatic("Cleaned up")
    if visible:
        assert not shown
        assert len(timers) == 1
        timers.pop()()
        assert not shown  # Still wait if another notification remains visible.
        label.isVisible = lambda: False
        timers.pop()()
    assert shown == ["Cleaned up"]


@pytest.mark.parametrize("change", ["profile", "collection", "close"])
@pytest.mark.usefixtures("runner")
def test_deferred_notification_is_discarded_after_context_changes(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    timers = []
    shown = []
    monkeypatch.setattr(
        automatic.aqt_utils, "_tooltipLabel", SimpleNamespace(isVisible=lambda: True)
    )
    monkeypatch.setattr(
        automatic, "QTimer", SimpleNamespace(singleShot=lambda _delay, cb: timers.append(cb))
    )
    monkeypatch.setattr(automatic, "tooltip", lambda message, **_kwargs: shown.append(message))
    automatic._notify_automatic("Cleaned up")
    if change == "profile":
        automatic.mw.pm.profile = {}
    elif change == "collection":
        automatic.mw.col = object()
    else:
        automatic.cancel_automatic_run()
    timers.pop()()
    assert not shown
    assert not timers


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
            policies=(SimpleNamespace(id="p", name="Daily policy", triggers=(Trigger("daily"),)),),
            notify_after_automatic_run=False,
            warn_on_invalid_automatic_policies=False,
            automatic_cleanup_enabled=True,
            cleanup_history_enabled=False,
        ),
    )
    monkeypatch.setattr(automatic, "mw", window)
    monkeypatch.setattr(automatic, "_run_state", automatic._RunState())
    monkeypatch.setattr(automatic, "_load_configured", lambda: parsed)
    monkeypatch.setattr(automatic, "QueryOp", Query)
    monkeypatch.setattr(automatic, "CollectionOp", Mutation)
    monkeypatch.setattr(automatic, "showWarning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(automatic, "tooltip", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(automatic, "evaluate_policies", lambda *_args: ())
    monkeypatch.setattr(
        automatic,
        "build_execution_plan",
        lambda *_args: SimpleNamespace(is_empty=True, conflicted_card_ids=()),
    )
    return SimpleNamespace(window=window, operations=operations, mutations=mutations, parsed=parsed)


def test_profile_open_warns_about_invalid_inactive_automatic_policy_without_recording_cleanup(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.parsed.config.warn_on_invalid_automatic_policies = True
    runner.parsed.config.policies = (
        SimpleNamespace(id="daily", name="Daily", triggers=(Trigger("daily"),)),
        SimpleNamespace(id="sync", name="Sync", triggers=(Trigger("on_sync"),)),
    )
    monkeypatch.setattr(
        automatic,
        "validate_policy_references",
        lambda _col, policy: ("missing deck",) if policy.id == "sync" else (),
    )
    notices = []
    monkeypatch.setattr(automatic, "_notify_automatic", notices.append)

    automatic.run_automatic_policies(events=frozenset({"on_open"}))

    assert len(runner.operations) == 2
    validation = runner.operations[0]
    validation.success(validation.op(runner.window.col))
    assert notices == [("Card Janitor: “Sync” was not applied because it has an error.")]
    assert "card_janitor_last_cleanup" not in runner.window.pm.profile


def test_startup_warning_counts_multiple_invalid_automatic_policies(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.parsed.config.warn_on_invalid_automatic_policies = True
    runner.parsed.config.policies = tuple(
        SimpleNamespace(id=name.casefold(), name=name, triggers=(Trigger("on_sync"),))
        for name in ("First", "Second")
    )
    monkeypatch.setattr(
        automatic,
        "validate_policy_references",
        lambda _col, _policy: ("missing deck",),
    )
    notices = []
    monkeypatch.setattr(automatic, "_notify_automatic", notices.append)

    automatic.run_automatic_policies(events=frozenset({"on_open"}))
    validation = runner.operations[0]
    validation.success(validation.op(runner.window.col))
    assert notices == [
        ("Card Janitor: 2 automatic policies were not applied because they have errors.")
    ]


def test_due_invalid_policy_notification_names_policy(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    notices = []
    monkeypatch.setattr(automatic, "_notify_automatic", notices.append)
    policy = runner.parsed.config.policies[0]
    report = SimpleNamespace(policy=policy, errors=("missing deck",))

    automatic.run_automatic_policies(events=frozenset({"on_open"}))
    runner.operations[0].success((report,))

    assert notices == [("Card Janitor: “Daily policy” was not applied because it has an error.")]
    assert runner.window.pm.profile["card_janitor_last_cleanup"]["failure"] == (
        "Daily policy: missing deck"
    )


def test_enabled_history_records_automatic_no_op_with_exact_triggers(
    runner: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner.parsed.config.cleanup_history_enabled = True
    prepared = {}
    recorded = []
    provenance_requests = []
    session = SimpleNamespace(
        collect_history=True,
        error_message=None,
        terminal_recorded=False,
        record_terminal=lambda **kwargs: recorded.append(kwargs),
    )

    def prepare(**kwargs: object) -> object:
        prepared.update(kwargs)
        return session

    def evaluate(*_args: object, **kwargs: object) -> tuple[()]:
        provenance_requests.append(kwargs.get("collect_provenance"))
        return ()

    monkeypatch.setattr(automatic, "prepare_history_session", prepare)
    monkeypatch.setattr(automatic, "evaluate_policies", evaluate)
    monkeypatch.setattr(
        automatic,
        "build_execution_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            is_empty=True,
            conflicted_card_ids=(),
            semantics=PlanSemantics("complete"),
        ),
    )

    automatic.run_automatic_policies(events=frozenset({"on_open"}))
    operation = runner.operations[0]
    operation.success(operation.op(runner.window.col))

    assert prepared["enabled"] is True
    assert prepared["invocation"].kind == "automatic"
    assert prepared["invocation"].events == ("daily", "on_open")
    assert prepared["activations"][0].triggers == ("daily",)
    assert provenance_requests == [True]
    assert len(recorded) == 1
    assert recorded[0]["status"] == "succeeded"
    assert recorded[0]["stage"] == "complete"
    assert recorded[0]["ledger"].status == "complete"


def test_audit_gap_warning_failure_does_not_interrupt_automatic_completion(
    runner: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner.parsed.config.cleanup_history_enabled = True
    session = SimpleNamespace(
        collect_history=True,
        error_message="audit gap",
        terminal_recorded=False,
        record_terminal=lambda **_kwargs: None,
    )
    monkeypatch.setattr(automatic, "prepare_history_session", lambda **_kwargs: session)
    monkeypatch.setattr(automatic, "evaluate_policies", lambda *_args, **_kwargs: ())
    monkeypatch.setattr(
        automatic,
        "build_execution_plan",
        lambda *_args, **_kwargs: SimpleNamespace(
            is_empty=True,
            conflicted_card_ids=(),
            semantics=PlanSemantics("complete"),
        ),
    )
    monkeypatch.setattr(
        automatic,
        "showWarning",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("display failed")),
    )
    monkeypatch.setattr(automatic, "exception", lambda *_args, **_kwargs: None)

    automatic.run_automatic_policies(events=frozenset({"on_open"}))
    operation = runner.operations[0]
    operation.success(operation.op(runner.window.col))

    assert runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY] == {"p": 10}
    assert automatic._run_state.token is None


@pytest.mark.parametrize("event", ["on_open", "day_change", "on_sync"])
def test_disabled_automatic_cleanup_leaves_profile_and_policies_unchanged(
    runner: SimpleNamespace, event: str
) -> None:
    runner.parsed.config.automatic_cleanup_enabled = False
    policies = runner.parsed.config.policies
    runner.window.pm.profile["existing"] = "keep"
    automatic.run_automatic_policies(events=frozenset({event}))
    assert runner.operations == []
    assert runner.mutations == []
    assert runner.window.pm.profile == {"existing": "keep"}
    assert runner.parsed.config.policies == policies


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        (frozenset({"on_open"}), {"daily", "open", "both"}),
        (frozenset({"day_change"}), {"daily"}),
        (frozenset({"on_sync"}), {"sync", "both"}),
        (frozenset({"on_open", "on_sync"}), {"daily", "open", "sync", "both"}),
    ],
)
def test_events_select_only_eligible_policies_once(
    runner: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    events: frozenset[str],
    expected: set[str],
) -> None:
    runner.parsed.config.policies = tuple(
        SimpleNamespace(
            id=policy_id, name=policy_id, triggers=tuple(Trigger(kind) for kind in kinds)
        )
        for policy_id, kinds in (
            ("manual", ()),
            ("daily", ("daily",)),
            ("open", ("on_open",)),
            ("sync", ("on_sync",)),
            ("both", ("on_open", "on_sync")),
        )
    )
    evaluated = []
    monkeypatch.setattr(
        automatic, "evaluate_policies", lambda _col, policies: evaluated.extend(policies) or ()
    )
    automatic.run_automatic_policies(events=events)
    operation = runner.operations[0]
    operation.success(operation.op(runner.window.col))
    assert {policy.id for policy in evaluated} == expected
    assert len(evaluated) == len(expected)
    result = runner.window.pm.profile["card_janitor_last_cleanup"]
    assert set(result["policies"]) == expected
    expected_triggers = {
        "on_open": {"Daily", "On open"},
        "day_change": {"Daily"},
        "on_sync": {"On sync"},
    }
    assert set(result["triggers"]) == set().union(*(expected_triggers[event] for event in events))
    evaluated[0].name = "Renamed later"
    assert "Renamed later" not in result["policies"]


def test_daily_limits_are_per_policy_and_do_not_limit_events(runner: SimpleNamespace) -> None:
    runner.parsed.config.policies = (
        SimpleNamespace(id="old", name="Old", triggers=(Trigger("daily"),)),
        SimpleNamespace(id="new", name="New", triggers=(Trigger("daily"),)),
        SimpleNamespace(id="event", name="Event", triggers=(Trigger("daily"), Trigger("on_sync"))),
    )
    runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY] = {"old": 10, "event": 10}
    automatic.run_automatic_policies()
    runner.operations[0].success(())
    assert runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY] == {
        "old": 10,
        "new": 10,
        "event": 10,
    }
    for _ in range(2):
        automatic.run_automatic_policies(trigger="on_sync")
        runner.operations[-1].success(())
    assert len(runner.operations) == 3
    assert runner.window.pm.profile["card_janitor_last_cleanup"]["triggers"] == ["On sync"]


def test_sync_event_during_run_is_queued_and_coalesced(runner: SimpleNamespace) -> None:
    runner.parsed.config.policies = (
        SimpleNamespace(id="p", name="Both", triggers=(Trigger("on_open"), Trigger("on_sync"))),
    )
    automatic.run_automatic_policies()
    automatic.run_automatic_policies(trigger="on_sync")
    automatic.run_automatic_policies(trigger="on_sync")
    assert len(runner.operations) == 1
    runner.operations[0].success(())
    assert len(runner.operations) == 2
    runner.operations[1].success(())
    assert automatic._run_state.token is None
    assert not automatic._run_state.pending
    assert automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY not in runner.window.pm.profile


def test_failed_run_clears_pending_events_without_automatic_retry(runner: SimpleNamespace) -> None:
    automatic.run_automatic_policies()
    automatic.run_automatic_policies(trigger="on_sync")
    runner.operations[0].failed(RuntimeError("failure"))
    assert len(runner.operations) == 1
    assert not automatic._run_state.pending


def test_malformed_daily_bookkeeping_is_replaced(runner: SimpleNamespace) -> None:
    runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY] = None
    automatic.run_automatic_policies()
    runner.operations[0].success(())
    assert runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY] == {"p": 10}


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
    assert automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY not in runner.window.pm.profile
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
    assert runner.window.pm.profile.get(automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY, {}).get("p") == (
        10 if outcome == "success" else None
    )
    assert automatic._run_state.token is None


def test_automatic_execution_rejects_policy_changed_after_evaluation(
    runner: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = parse_policy(
        {
            "id": "p",
            "name": "Before",
            "triggers": [{"type": "daily"}],
            "actions": [{"type": "suspend"}],
        }
    )
    stored = [policy_to_dict(policy)]
    collection = SimpleNamespace(
        sched=SimpleNamespace(today=10),
        get_config=lambda key, default: stored if key == COLLECTION_POLICIES_KEY else default,
    )
    runner.window.col = collection
    runner.parsed.config.policies = (policy,)
    report = SimpleNamespace(
        policy=policy,
        errors=(),
        actionable=(SimpleNamespace(card_id=1),),
    )
    monkeypatch.setattr(automatic, "evaluate_policies", lambda *_args: (report,))
    monkeypatch.setattr(
        automatic,
        "build_execution_plan",
        lambda *_args: SimpleNamespace(is_empty=False),
    )
    warnings = []
    monkeypatch.setattr(
        automatic, "showWarning", lambda message, **_kwargs: warnings.append(message)
    )

    automatic.run_automatic_policies()
    query = runner.operations[0]
    query.success(query.op(collection))
    stored[0] = {**stored[0], "name": "After"}

    mutation = runner.mutations[0]
    with pytest.raises(StalePolicyDefinitionsError, match="selected policies changed") as exc_info:
        mutation.op(collection)
    mutation.failed(exc_info.value)

    assert automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY not in runner.window.pm.profile
    assert (
        "selected policies changed"
        in runner.window.pm.profile["card_janitor_last_cleanup"]["failure"]
    )
    assert warnings == [str(exc_info.value)]


def test_automatic_run_coalesces_and_marks_only_completed_day(runner: SimpleNamespace) -> None:
    automatic.run_automatic_policies()
    automatic.run_automatic_policies()
    assert len(runner.operations) == 1
    operation = runner.operations[0]
    operation.success(operation.op(runner.window.col))
    assert runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY] == {"p": 10}
    automatic.run_automatic_policies()
    assert len(runner.operations) == 1


def test_failed_automatic_run_can_retry(runner: SimpleNamespace) -> None:
    automatic.run_automatic_policies()
    runner.operations[0].failed(RuntimeError("failure"))
    assert automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY not in runner.window.pm.profile
    assert runner.window.pm.profile["card_janitor_last_cleanup"]["failure"] == "failure"
    assert runner.window.pm.profile["card_janitor_last_cleanup"]["affected_cards"] is None
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
    assert runner.window.pm.profile[automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY] == {"p": 10}
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
    assert automatic.LAST_AUTOMATIC_DAYS_PROFILE_KEY not in runner.window.pm.profile


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
        assert calls == [{"events": frozenset({"on_open", "on_sync"})}]
        addon.on_profile_loaded()
        addon.on_sync_finished()
        addon.on_profile_closing()
        timers[1]()
        assert len(calls) == 1
        addon.on_sync_finished()
        addon.on_day_changed()
        assert len(timers) == 2
        assert len(calls) == 1
    else:
        assert calls == [{"trigger": "on_open"}]
        addon.on_sync_finished()
        assert len(timers) == 1
        timers[0]()
        assert calls[-1] == {"events": frozenset({"on_sync"})}
