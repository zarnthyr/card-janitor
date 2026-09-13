# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from card_janitor.models import (
    AgeRule,
    AllRule,
    DeleteCardAction,
    MoveAction,
    NewRule,
    StudyStatusRule,
    parse_config,
    policy_to_dict,
)


def policy_config(**overrides: object) -> dict:
    policy = {
        "id": "mining",
        "name": "Mining",
        "state": "manual",
        "scope": {"decks": ["Mining"], "include_subdecks": True},
        "rule": {"type": "age", "days": 365, "from": "first_review"},
        "actions": [{"type": "tag", "tag": "retired"}, {"type": "suspend"}],
    }
    policy.update(overrides)
    return {
        "config_version": 2,
        "automatic_schedule": "daily",
        "notify_after_automatic_run": True,
        "debug_logging": False,
        "policies": [policy],
    }


def test_parses_flat_compound_policy() -> None:
    raw = policy_config(
        rule={
            "type": "all",
            "rules": [
                {"type": "age", "days": 365, "from": "first_review"},
                {"type": "new"},
            ],
        }
    )
    parsed = parse_config(raw)
    assert not parsed.issues
    rule = parsed.config.policies[0].rule
    assert isinstance(rule, AllRule)
    assert rule.rules == (
        AgeRule(days=365, source="first_review"),
        NewRule(),
    )


def test_rejects_nested_compound_policy() -> None:
    raw = policy_config(
        rule={
            "type": "all",
            "rules": [
                {
                    "type": "any",
                    "rules": [
                        {"type": "new"},
                        {"type": "interval", "days": 180},
                    ],
                }
            ],
        }
    )
    parsed = parse_config(raw)
    assert "compound rules cannot be nested" in str(parsed.issues[0])
    assert parsed.policy_records[0].policy is None
    assert parsed.policy_records[0].raw is raw["policies"][0]


def test_invalid_policy_is_omitted() -> None:
    raw = policy_config(rule={"type": "age", "days": 0, "from": "first_review"})
    parsed = parse_config(raw)
    assert parsed.issues
    assert not parsed.config.policies


def test_automatic_delete_requires_explicit_configuration_but_is_supported() -> None:
    parsed = parse_config(policy_config(state="automatic", actions=[{"type": "delete_card"}]))
    assert not parsed.issues
    assert parsed.config.policies[0].state == "automatic"
    assert isinstance(parsed.config.policies[0].actions[0], DeleteCardAction)


def test_delete_must_be_only_action() -> None:
    parsed = parse_config(policy_config(actions=[{"type": "delete_card"}, {"type": "suspend"}]))
    assert parsed.issues
    assert not parsed.config.policies


def test_move_action_parses() -> None:
    parsed = parse_config(policy_config(actions=[{"type": "move", "deck": "Retired"}]))
    action = parsed.config.policies[0].actions[0]
    assert action == MoveAction("Retired")
    assert not isinstance(action, DeleteCardAction)


def test_duplicate_policy_id_is_rejected() -> None:
    raw = policy_config()
    raw["policies"].append({**raw["policies"][0], "name": "Duplicate"})
    parsed = parse_config(raw)
    assert len(parsed.config.policies) == 1
    assert "duplicate" in str(parsed.issues[0])


def test_debug_logging_must_be_boolean() -> None:
    raw = policy_config()
    raw["debug_logging"] = "yes"
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "debug_logging: must be a boolean"
    assert not parsed.config.debug_logging


def test_automatic_notification_setting_must_be_boolean() -> None:
    raw = policy_config()
    raw["notify_after_automatic_run"] = "yes"
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "notify_after_automatic_run: must be a boolean"
    assert parsed.config.notify_after_automatic_run


def test_policy_state_is_validated() -> None:
    parsed = parse_config(policy_config(state="notify"))
    assert str(parsed.issues[0]) == (
        "policies[0].state: must be 'disabled', 'manual', or 'automatic'"
    )
    assert not parsed.config.policies


def test_policy_state_defaults_to_manual() -> None:
    raw = policy_config()
    del raw["policies"][0]["state"]
    parsed = parse_config(raw)
    assert not parsed.issues
    assert parsed.config.policies[0].state == "manual"


def test_old_config_version_fails_closed() -> None:
    raw = policy_config()
    raw["config_version"] = 1
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "config_version: must be 2"


def test_legacy_policy_activation_fields_are_rejected() -> None:
    parsed = parse_config(policy_config(enabled=True, mode="manual"))
    assert str(parsed.issues[0]) == ("policies[0]: replace 'enabled' and 'mode' with 'state'")
    assert not parsed.config.policies


def test_automatic_schedule_is_validated() -> None:
    raw = policy_config()
    raw["automatic_schedule"] = "hourly"
    parsed = parse_config(raw)
    assert str(parsed.issues[0]).startswith("automatic_schedule:")
    assert parsed.config.automatic_schedule == "daily"


def test_removed_answer_rules_are_rejected() -> None:
    for rule_type in ("answer_count", "successful_answers"):
        parsed = parse_config(policy_config(rule={"type": rule_type, "count": 3}))
        assert "unknown rule type" in str(parsed.issues[0])
        assert not parsed.config.policies


def test_study_status_rule_parses() -> None:
    parsed = parse_config(policy_config(rule={"type": "study_status", "status": "never_studied"}))
    assert not parsed.issues
    assert parsed.config.policies[0].rule == StudyStatusRule("never_studied")


def test_serialized_policy_round_trips() -> None:
    parsed = parse_config(policy_config())
    policy = parsed.config.policies[0]
    round_tripped = parse_config({**policy_config(), "policies": [policy_to_dict(policy)]})
    assert not round_tripped.issues
    assert round_tripped.config.policies == (policy,)
