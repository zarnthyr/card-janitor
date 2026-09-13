# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from card_retirement.models import (
    AgeRule,
    AllRule,
    DeleteCardAction,
    MoveAction,
    NewRule,
    parse_config,
)


def policy_config(**overrides: object) -> dict:
    policy = {
        "id": "mining",
        "name": "Mining",
        "enabled": True,
        "mode": "manual",
        "scope": {"decks": ["Mining"], "include_subdecks": True},
        "rule": {"type": "age", "days": 365, "from": "first_review"},
        "actions": [{"type": "tag", "tag": "retired"}, {"type": "suspend"}],
    }
    policy.update(overrides)
    return {
        "config_version": 1,
        "automatic_schedule": "daily",
        "notify_after_automatic_retirement": True,
        "debug_logging": False,
        "policies": [policy],
    }


def test_parses_nested_policy() -> None:
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


def test_invalid_policy_is_omitted() -> None:
    raw = policy_config(rule={"type": "age", "days": 0, "from": "first_review"})
    parsed = parse_config(raw)
    assert parsed.issues
    assert not parsed.config.policies


def test_automatic_delete_requires_explicit_configuration_but_is_supported() -> None:
    parsed = parse_config(policy_config(mode="automatic", actions=[{"type": "delete_card"}]))
    assert not parsed.issues
    assert parsed.config.policies[0].mode == "automatic"
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
    raw["notify_after_automatic_retirement"] = "yes"
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "notify_after_automatic_retirement: must be a boolean"
    assert parsed.config.notify_after_automatic_retirement


def test_notify_is_not_a_policy_mode() -> None:
    parsed = parse_config(policy_config(mode="notify"))
    assert str(parsed.issues[0]) == "policies[0].mode: must be 'manual' or 'automatic'"
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
