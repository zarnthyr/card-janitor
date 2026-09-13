# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from card_retirement.models import (
    AgeRule,
    AllRule,
    DeleteCardAction,
    MoveAction,
    SuccessfulAnswersRule,
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
        "automatic_check_interval_hours": 20,
        "policies": [policy],
    }


def test_parses_nested_policy() -> None:
    raw = policy_config(
        rule={
            "type": "all",
            "rules": [
                {"type": "age", "days": 365, "from": "first_review"},
                {"type": "successful_answers", "count": 5},
            ],
        }
    )
    parsed = parse_config(raw)
    assert not parsed.issues
    rule = parsed.config.policies[0].rule
    assert isinstance(rule, AllRule)
    assert rule.rules == (
        AgeRule(days=365, source="first_review"),
        SuccessfulAnswersRule(count=5),
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
