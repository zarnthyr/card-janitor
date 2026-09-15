# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from card_janitor.models import (
    AgeCondition,
    AllConditions,
    CardStateCondition,
    DeleteCardAction,
    MoveAction,
    ReviewHistoryCondition,
    TagAction,
    parse_config,
    policy_to_dict,
)


def policy_config(**overrides: object) -> dict:
    policy = {
        "id": "mining",
        "name": "Mining",
        "mode": "on_demand",
        "scope": {"decks": ["Mining"], "include_subdecks": True},
        "match": "all",
        "conditions": [
            {
                "type": "age",
                "days": 365,
                "source": "first_review",
                "operator": "gte",
            }
        ],
        "actions": [{"type": "tag", "tags": ["retired"]}, {"type": "suspend"}],
    }
    policy.update(overrides)
    return {
        "config_version": 1,
        "notify_after_automatic_run": True,
        "debug_logging": False,
        "policies": [policy],
    }


def test_parses_flat_compound_policy() -> None:
    raw = policy_config(
        match="all",
        conditions=[
            {"type": "age", "days": 365, "source": "first_review", "operator": "gte"},
            {
                "type": "card_state",
                "states": ["new"],
            },
        ],
    )
    parsed = parse_config(raw)
    assert not parsed.issues
    condition = parsed.config.policies[0].conditions
    assert isinstance(condition, AllConditions)
    assert condition.conditions == (
        AgeCondition(days=365, source="first_review", operator="gte"),
        CardStateCondition(states=("new",)),
    )


def test_rejects_nested_compound_policy() -> None:
    raw = policy_config(
        conditions=[
            {
                "match": "any",
                "conditions": [
                    {"type": "card_state", "states": ["new"]},
                    {"type": "interval", "days": 180, "operator": "gte"},
                ],
            }
        ],
    )
    parsed = parse_config(raw)
    assert "unknown condition type" in str(parsed.issues[0])
    assert parsed.policy_records[0].policy is None
    assert parsed.policy_records[0].raw is raw["policies"][0]


def test_invalid_policy_is_omitted() -> None:
    raw = policy_config(
        conditions=[{"type": "age", "days": -1, "source": "first_review", "operator": "gte"}]
    )
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


def test_tag_action_parses_multiple_tags_and_round_trips_as_one_action() -> None:
    parsed = parse_config(
        policy_config(actions=[{"type": "tag", "tags": ["retired", "vocabulary"]}])
    )
    policy = parsed.config.policies[0]

    assert policy.actions == (TagAction("retired"), TagAction("vocabulary"))
    assert policy_to_dict(policy)["actions"] == [{"type": "tag", "tags": ["retired", "vocabulary"]}]


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


def test_addon_settings_are_required() -> None:
    for missing in (
        "config_version",
        "notify_after_automatic_run",
        "debug_logging",
        "policies",
    ):
        raw = policy_config()
        del raw[missing]
        parsed = parse_config(raw)
        assert any(issue.path == missing for issue in parsed.issues)


def test_policy_mode_is_validated() -> None:
    parsed = parse_config(policy_config(mode="notify"))
    assert str(parsed.issues[0]) == "policies[0].mode: must be 'on_demand' or 'automatic'"
    assert not parsed.config.policies


def test_policy_mode_is_required() -> None:
    raw = policy_config()
    del raw["policies"][0]["mode"]
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "policies[0].mode: must be 'on_demand' or 'automatic'"
    assert not parsed.config.policies


def test_match_and_conditions_are_required() -> None:
    for missing in ("match", "conditions"):
        raw = policy_config()
        del raw["policies"][0][missing]
        parsed = parse_config(raw)
        assert missing in str(parsed.issues[0])
        assert not parsed.config.policies


def test_unknown_configuration_and_policy_fields_are_rejected() -> None:
    raw = policy_config()
    raw["unexpected"] = True
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "unexpected: unknown setting"

    raw = policy_config(unexpected=True)
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "policies[0].unexpected: unknown field"
    assert not parsed.config.policies


def test_duplicate_scope_values_and_condition_states_are_rejected() -> None:
    parsed = parse_config(policy_config(scope={"decks": ["Mining", "Mining"]}))
    assert "duplicates" in str(parsed.issues[0])

    parsed = parse_config(
        policy_config(conditions=[{"type": "card_state", "states": ["new", "new"]}])
    )
    assert "states" in str(parsed.issues[0])


def test_previous_rule_object_schema_is_rejected() -> None:
    raw = policy_config()
    policy = raw["policies"][0]
    del policy["match"]
    del policy["conditions"]
    policy["rule"] = {
        "type": "age",
        "days": 365,
        "from": "first_review",
        "operator": "gte",
    }

    parsed = parse_config(raw)

    assert parsed.issues
    assert not parsed.config.policies


def test_wrong_config_version_fails_closed() -> None:
    raw = policy_config()
    raw["config_version"] = 2
    parsed = parse_config(raw)
    assert str(parsed.issues[0]) == "config_version: must be 1"


def test_removed_answer_conditions_are_rejected() -> None:
    for condition_type in ("answer_count", "successful_answers"):
        parsed = parse_config(policy_config(conditions=[{"type": condition_type, "count": 3}]))
        assert "unknown condition type" in str(parsed.issues[0])
        assert not parsed.config.policies


def test_review_history_condition_parses() -> None:
    parsed = parse_config(
        policy_config(conditions=[{"type": "review_history", "operator": "not_exists"}])
    )
    assert not parsed.issues
    assert parsed.config.policies[0].conditions == AllConditions(
        (ReviewHistoryCondition("not_exists"),)
    )


def test_card_state_condition_parses() -> None:
    parsed = parse_config(
        policy_config(conditions=[{"type": "card_state", "states": ["new", "learning"]}])
    )
    assert not parsed.issues
    assert parsed.config.policies[0].conditions == AllConditions(
        (CardStateCondition(("new", "learning")),)
    )


def test_numeric_operator_is_required() -> None:
    parsed = parse_config(policy_config(conditions=[{"type": "interval", "days": 30}]))
    assert "operator" in str(parsed.issues[0])


def test_serialized_policy_round_trips() -> None:
    parsed = parse_config(policy_config())
    policy = parsed.config.policies[0]
    round_tripped = parse_config({**policy_config(), "policies": [policy_to_dict(policy)]})
    assert not round_tripped.issues
    assert round_tripped.config.policies == (policy,)
