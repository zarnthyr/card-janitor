# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import pytest
from card_janitor.models import (
    MAX_DAYS,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    CardStateCondition,
    DeckSelector,
    DeleteCardAction,
    DeleteNoteAction,
    MoveAction,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    SuspensionCondition,
    TagAction,
    TagCondition,
    UnsuspendAction,
    parse_config,
    policy_to_dict,
)
from card_janitor.presentation import describe_scope, scope_tooltip


def policy_config(**overrides: object) -> dict:
    policy = {
        "id": "mining",
        "name": "Mining",
        "mode": "on_demand",
        "scope": {"decks": [{"deck": "Mining", "include_subdecks": True}]},
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


def test_mixed_deck_selectors_round_trip() -> None:
    selectors = [
        {"deck": "Mining", "include_subdecks": False},
        {"deck": "Mining::Selected", "include_subdecks": True},
    ]
    parsed = parse_config(policy_config(scope={"decks": selectors}))
    assert not parsed.issues
    policy = parsed.config.policies[0]
    assert policy.scope.selectors == (
        DeckSelector("Mining", include_subdecks=False),
        DeckSelector("Mining::Selected", include_subdecks=True),
    )
    assert policy_to_dict(policy)["scope"]["decks"] == selectors


def test_legacy_scope_format_is_rejected() -> None:
    parsed = parse_config(policy_config(scope={"decks": ["Mining"], "include_subdecks": True}))

    assert "include_subdecks: unknown field" in str(parsed.issues[0])
    assert not parsed.config.policies


def test_all_decks_scope_round_trip_and_excludes_individual_selectors() -> None:
    parsed = parse_config(policy_config(scope={"all_decks": True, "include_suspended": True}))
    assert not parsed.issues
    policy = parsed.config.policies[0]
    assert policy.scope.all_decks
    assert not policy.scope.decks
    assert policy_to_dict(policy)["scope"] == {"all_decks": True, "include_suspended": True}
    for scope in ({"all_decks": False}, {"all_decks": True, "decks": []}, {}):
        assert parse_config(policy_config(scope=scope)).issues


def test_note_type_scope_round_trip_and_validation() -> None:
    for deck_scope in (
        {"all_decks": True},
        {"decks": [{"deck": "Mining", "include_subdecks": True}]},
    ):
        scope = {**deck_scope, "note_types": ["Cloze", " Basic "]}
        parsed = parse_config(policy_config(scope=scope))
        assert not parsed.issues
        assert parsed.config.policies[0].scope.note_types == ("Basic", "Cloze")
        assert policy_to_dict(parsed.config.policies[0])["scope"]["note_types"] == [
            "Basic",
            "Cloze",
        ]
    for names in ([], ["Basic", " Basic "], [" "], [1], None, "Basic"):
        assert parse_config(policy_config(scope={"all_decks": True, "note_types": names})).issues


def test_sibling_conditions_round_trip_and_validate_operators() -> None:
    for kind, condition_type in (
        ("sibling_suspension", SiblingSuspensionCondition),
        ("sibling_review_history", SiblingReviewHistoryCondition),
    ):
        for operator in ("all", "any", "none"):
            condition = {"type": kind, "operator": operator}
            parsed = parse_config(
                policy_config(
                    scope={"all_decks": True, "include_suspended": True},
                    conditions=[condition],
                )
            )
            assert not parsed.issues
            assert parsed.config.policies[0].conditions == AllConditions(
                (condition_type(operator),)
            )
            assert policy_to_dict(parsed.config.policies[0])["conditions"] == [condition]
        assert parse_config(
            policy_config(conditions=[{"type": kind, "operator": "invalid"}])
        ).issues
    assert parse_config(
        policy_config(conditions=[{"type": "sibling_suspension", "operator": "all"}])
    ).issues


def test_scope_summary_only_adds_restricted_note_types() -> None:
    def scope(names: list[str] | None) -> object:
        return (
            parse_config(
                policy_config(
                    scope={
                        "all_decks": True,
                        **({"note_types": names} if names is not None else {}),
                    }
                )
            )
            .config.policies[0]
            .scope
        )

    assert describe_scope(scope(None)) == "All decks"
    assert describe_scope(scope(["Basic"])) == "All decks\nNote type: Basic"
    assert describe_scope(scope(["Basic", "Cloze"])) == "All decks\n2 note types"
    assert "Note types: Basic, Cloze" in scope_tooltip(scope(["Cloze", "Basic"]))


def test_all_cards_condition_parses_round_trips_and_must_be_used_alone() -> None:
    parsed = parse_config(policy_config(conditions=[{"type": "all_cards"}]))

    assert not parsed.issues
    assert parsed.config.policies[0].conditions == AllConditions((AllCardsCondition(),))
    assert policy_to_dict(parsed.config.policies[0])["conditions"] == [{"type": "all_cards"}]

    combined = parse_config(
        policy_config(
            conditions=[
                {"type": "all_cards"},
                {"type": "interval", "days": 1, "operator": "gte"},
            ]
        )
    )
    assert "must be the only condition" in str(combined.issues[0])


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
    assert "type: must be a string" in str(parsed.issues[0])
    assert parsed.policy_records[0].policy is None
    assert parsed.policy_records[0].raw is raw["policies"][0]


def test_invalid_policy_is_omitted() -> None:
    raw = policy_config(
        conditions=[{"type": "age", "days": -1, "source": "first_review", "operator": "gte"}]
    )
    parsed = parse_config(raw)
    assert parsed.issues
    assert not parsed.config.policies


@pytest.mark.parametrize("value", [[], {}, ["nested"], {"nested": True}])
@pytest.mark.parametrize(
    "field", ["mode", "match", "condition_type", "operator", "source", "state", "action_type"]
)
def test_malformed_json_fields_produce_repairable_issues(field: str, value: object) -> None:
    raw = policy_config()
    policy = raw["policies"][0]
    condition = policy["conditions"][0]
    if field in {"mode", "match"}:
        policy[field] = value
    elif field == "condition_type":
        condition["type"] = value
    elif field in {"operator", "source"}:
        condition[field] = value
    elif field == "state":
        policy["conditions"] = [{"type": "card_state", "states": [value]}]
    else:
        policy["actions"][0]["type"] = value
    parsed = parse_config(raw)
    assert parsed.issues
    assert parsed.policy_records[0].raw is policy
    assert not parsed.config.policies


@pytest.mark.parametrize("tag", ["two words", "two\twords", "two,words"])
@pytest.mark.parametrize("kind", ["tag", "remove_tags", "replace_tags"])
def test_individual_json_tags_cannot_be_split_by_anki_or_editor(tag: str, kind: str) -> None:
    parsed = parse_config(policy_config(actions=[{"type": kind, "tags": [tag]}]))
    assert "individual tags" in str(parsed.issues[0])


@pytest.mark.parametrize("days", [-1, MAX_DAYS + 1, 10**100, True])
def test_numeric_bounds_match_editor(days: int) -> None:
    parsed = parse_config(
        policy_config(conditions=[{"type": "interval", "days": days, "operator": "gte"}])
    )
    assert parsed.issues


def test_automatic_delete_requires_explicit_configuration_but_is_supported() -> None:
    parsed = parse_config(policy_config(mode="automatic", actions=[{"type": "delete_card"}]))
    assert not parsed.issues
    assert parsed.config.policies[0].mode == "automatic"
    assert isinstance(parsed.config.policies[0].actions[0], DeleteCardAction)


def test_delete_must_be_only_action() -> None:
    parsed = parse_config(policy_config(actions=[{"type": "delete_card"}, {"type": "suspend"}]))
    assert parsed.issues
    assert not parsed.config.policies


def test_delete_note_parses_and_must_be_only_action() -> None:
    parsed = parse_config(policy_config(actions=[{"type": "delete_note"}]))
    assert not parsed.issues
    assert parsed.config.policies[0].actions == (DeleteNoteAction(),)
    assert policy_to_dict(parsed.config.policies[0])["actions"] == [{"type": "delete_note"}]

    conflicting = parse_config(
        policy_config(actions=[{"type": "delete_note"}, {"type": "suspend"}])
    )
    assert conflicting.issues
    assert not conflicting.config.policies


def test_move_action_parses() -> None:
    parsed = parse_config(policy_config(actions=[{"type": "move", "deck": "Retired"}]))
    action = parsed.config.policies[0].actions[0]
    assert action == MoveAction("Retired")
    assert not isinstance(action, DeleteCardAction)


def test_note_actions_parse_and_round_trip() -> None:
    for action in (
        {"type": "suspend_note"},
        {"type": "unsuspend_note"},
        {"type": "move_note", "deck": "Retired"},
    ):
        parsed = parse_config(policy_config(actions=[action]))
        assert not parsed.issues
        policy = parsed.config.policies[0]
        assert policy.actions[0].target == "note"
        assert policy_to_dict(policy)["actions"] == [action]


def test_tag_action_parses_multiple_tags_and_round_trips_as_one_action() -> None:
    parsed = parse_config(
        policy_config(actions=[{"type": "tag", "tags": ["retired", "vocabulary"]}])
    )
    policy = parsed.config.policies[0]

    assert policy.actions == (TagAction("retired"), TagAction("vocabulary"))
    assert policy_to_dict(policy)["actions"] == [{"type": "tag", "tags": ["retired", "vocabulary"]}]


def test_tag_and_suspension_conditions_parse_and_round_trip() -> None:
    parsed = parse_config(
        policy_config(
            scope={
                "decks": [{"deck": "Mining", "include_subdecks": True}],
                "include_suspended": True,
            },
            conditions=[
                {
                    "type": "tags",
                    "operator": "contains_any",
                    "tags": ["leech", "difficult"],
                },
                {"type": "suspension", "operator": "is_suspended"},
            ],
        )
    )

    assert not parsed.issues
    assert parsed.config.policies[0].conditions == AllConditions(
        (
            TagCondition(("leech", "difficult"), "contains_any"),
            SuspensionCondition("is_suspended"),
        )
    )
    assert policy_to_dict(parsed.config.policies[0])["conditions"] == [
        {
            "type": "tags",
            "tags": ["leech", "difficult"],
            "operator": "contains_any",
        },
        {"type": "suspension", "operator": "is_suspended"},
    ]


def test_matching_suspended_cards_requires_suspended_scope() -> None:
    parsed = parse_config(
        policy_config(conditions=[{"type": "suspension", "operator": "is_suspended"}])
    )

    assert "include_suspended" in str(parsed.issues[0])
    assert not parsed.config.policies


def test_inverse_and_replacement_actions_parse_and_round_trip() -> None:
    parsed = parse_config(
        policy_config(
            scope={
                "decks": [{"deck": "Mining", "include_subdecks": True}],
                "include_suspended": True,
            },
            actions=[
                {"type": "remove_tags", "tags": ["leech"]},
                {"type": "unsuspend"},
            ],
        )
    )

    assert not parsed.issues
    assert parsed.config.policies[0].actions == (
        RemoveTagAction("leech"),
        UnsuspendAction(),
    )
    assert policy_to_dict(parsed.config.policies[0])["actions"] == [
        {"type": "remove_tags", "tags": ["leech"]},
        {"type": "unsuspend"},
    ]

    replaced = parse_config(policy_config(actions=[{"type": "replace_tags", "tags": []}]))
    assert replaced.config.policies[0].actions == (ReplaceTagsAction(()),)


def test_conflicting_inverse_actions_are_rejected_within_policy() -> None:
    for actions in (
        [{"type": "suspend"}, {"type": "unsuspend"}],
        [
            {"type": "tag", "tags": ["leech"]},
            {"type": "remove_tags", "tags": ["Leech"]},
        ],
        [
            {"type": "replace_tags", "tags": ["only"]},
            {"type": "tag", "tags": ["extra"]},
        ],
    ):
        raw = policy_config(actions=actions)
        if any(action["type"] == "unsuspend" for action in actions):
            raw["policies"][0]["scope"]["include_suspended"] = True
        parsed = parse_config(raw)
        assert parsed.issues
        assert not parsed.config.policies


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
    parsed = parse_config(
        policy_config(
            scope={
                "decks": [
                    {"deck": "Mining", "include_subdecks": True},
                    {"deck": "Mining", "include_subdecks": False},
                ]
            }
        )
    )
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
