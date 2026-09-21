# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import pytest
from card_janitor.configuration import DEFAULT_CONFIG
from card_janitor.models import (
    MAX_DAYS,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnswerCountCondition,
    AnyConditions,
    CardFlagCondition,
    CardStateCondition,
    ClearFlagAction,
    CorrectAnswerCountCondition,
    CorrectAnswerRateCondition,
    DeckSelector,
    DeleteCardAction,
    DeleteNoteAction,
    FsrsDifficultyCondition,
    FsrsRetrievabilityCondition,
    FsrsStabilityCondition,
    IntervalCondition,
    LapseCountCondition,
    MoveAction,
    NoteTypeSelector,
    OverdueCondition,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    SetFlagAction,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    Sm2EaseCondition,
    SuspensionCondition,
    TagAction,
    TagCondition,
    UnsuspendAction,
    parse_config,
    policy_to_dict,
)
from card_janitor.presentation import describe_scope, scope_tooltip


@pytest.mark.parametrize("enabled", [True, False])
def test_automatic_cleanup_setting_is_boolean(enabled: bool) -> None:
    parsed = parse_config({**DEFAULT_CONFIG, "automatic_cleanup_enabled": enabled, "policies": []})
    assert not parsed.issues
    assert parsed.config.automatic_cleanup_enabled is enabled


def test_automatic_cleanup_defaults_enabled_and_rejects_invalid_values() -> None:
    assert parse_config({"config_version": 1, "policies": []}).config.automatic_cleanup_enabled
    parsed = parse_config(
        {"config_version": 1, "automatic_cleanup_enabled": "false", "policies": []}
    )
    assert any(issue.path == "automatic_cleanup_enabled" for issue in parsed.issues)
    assert not parsed.config.automatic_cleanup_enabled


def test_invalid_automatic_policy_warning_setting_defaults_on_and_validates() -> None:
    assert parse_config(
        {"config_version": 1, "policies": []}
    ).config.warn_on_invalid_automatic_policies
    parsed = parse_config(
        {
            **DEFAULT_CONFIG,
            "warn_on_invalid_automatic_policies": "yes",
            "policies": [],
        }
    )
    assert any(issue.path == "warn_on_invalid_automatic_policies" for issue in parsed.issues)
    assert parsed.config.warn_on_invalid_automatic_policies


def policy_config(**overrides: object) -> dict:
    policy = {
        "id": "mining",
        "name": "Mining",
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
        "actions": [{"type": "add_tags", "tags": ["retired"]}, {"type": "suspend"}],
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

    parsed = parse_config(
        policy_config(
            scope={
                "decks": [{"deck": "Mining", "include_subdecks": True}],
                "include_suspended": False,
            }
        )
    )
    assert "include_suspended: unknown field" in str(parsed.issues[0])
    assert not parsed.config.policies


def test_omitted_scope_round_trips_as_unrestricted() -> None:
    raw = policy_config()
    del raw["policies"][0]["scope"]
    parsed = parse_config(raw)
    assert not parsed.issues
    policy = parsed.config.policies[0]
    assert policy.scope.all_decks
    assert not policy.scope.decks
    assert "scope" not in policy_to_dict(policy)
    for scope in ({"all_decks": True}, {"decks": []}, {"note_types": []}, {}):
        assert parse_config(policy_config(scope=scope)).issues


def test_note_type_scope_round_trip_and_validation() -> None:
    for deck_scope in (
        {},
        {"decks": [{"deck": "Mining", "include_subdecks": True}]},
    ):
        scope = {
            **deck_scope,
            "note_types": [
                {"name": "Cloze"},
                {"name": " Basic ", "card_types": ["Card 2"]},
            ],
        }
        parsed = parse_config(policy_config(scope=scope))
        assert not parsed.issues
        assert parsed.config.policies[0].scope.note_types == (
            NoteTypeSelector("Basic", ("Card 2",)),
            NoteTypeSelector("Cloze"),
        )
        assert policy_to_dict(parsed.config.policies[0])["scope"]["note_types"] == [
            {"name": "Basic", "card_types": ["Card 2"]},
            {"name": "Cloze"},
        ]
    for names in (
        [],
        [{"name": "Basic"}, {"name": " Basic "}],
        [{"name": " "}],
        [{"name": "Basic", "card_types": []}],
        ["Basic"],
        [1],
        None,
        "Basic",
    ):
        assert parse_config(policy_config(scope={"note_types": names})).issues


def test_sibling_conditions_round_trip_and_validate_operators() -> None:
    for kind, condition_type in (
        ("sibling_suspension", SiblingSuspensionCondition),
        ("sibling_review_history", SiblingReviewHistoryCondition),
    ):
        for operator in ("all", "any", "none"):
            condition = {"type": kind, "operator": operator}
            parsed = parse_config(
                policy_config(
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


def test_scope_summary_only_adds_restricted_note_types() -> None:
    def scope(names: list[dict] | None) -> object:
        raw = policy_config(**({"scope": {"note_types": names}} if names is not None else {}))
        if names is None:
            del raw["policies"][0]["scope"]
        return parse_config(raw).config.policies[0].scope

    assert describe_scope(scope(None)) == "All decks"
    assert describe_scope(scope([{"name": "Basic"}])) == "All decks\nNote type: Basic"
    assert (
        describe_scope(scope([{"name": "Basic"}, {"name": "Cloze"}])) == "All decks\n2 note types"
    )
    assert "Note types: Basic, Cloze" in scope_tooltip(
        scope([{"name": "Cloze"}, {"name": "Basic"}])
    )


def test_omitted_matching_round_trips_as_all_cards_and_pseudo_condition_is_rejected() -> None:
    raw = policy_config()
    del raw["policies"][0]["match"]
    del raw["policies"][0]["conditions"]
    parsed = parse_config(raw)

    assert not parsed.issues
    assert parsed.config.policies[0].conditions == AllCardsCondition()
    serialized = policy_to_dict(parsed.config.policies[0])
    assert "match" not in serialized
    assert "conditions" not in serialized

    obsolete = parse_config(policy_config(conditions=[{"type": "all_cards"}]))
    assert "unknown condition type" in str(obsolete.issues[0])


def test_condition_group_parses_and_round_trips() -> None:
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
    assert not parsed.issues
    assert parsed.config.policies[0].conditions == AllConditions(
        (
            AnyConditions(
                (
                    CardStateCondition(("new",)),
                    IntervalCondition(180, "gte"),
                )
            ),
        )
    )
    assert (
        policy_to_dict(parsed.config.policies[0])["conditions"] == raw["policies"][0]["conditions"]
    )


@pytest.mark.parametrize(
    "group",
    [
        {
            "match": "any",
            "conditions": [{"type": "card_state", "states": ["new"]}],
        },
        {
            "match": "any",
            "conditions": [
                {
                    "match": "all",
                    "conditions": [
                        {"type": "card_state", "states": ["new"]},
                        {"type": "interval", "days": 30, "operator": "gte"},
                    ],
                },
                {"type": "interval", "days": 180, "operator": "gte"},
            ],
        },
    ],
)
def test_condition_groups_require_two_simple_children(group: dict) -> None:
    raw = policy_config(conditions=[group])
    parsed = parse_config(raw)
    assert parsed.issues
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
    "field",
    ["trigger_type", "match", "condition_type", "operator", "source", "state", "action_type"],
)
def test_malformed_json_fields_produce_repairable_issues(field: str, value: object) -> None:
    raw = policy_config()
    policy = raw["policies"][0]
    condition = policy["conditions"][0]
    if field == "trigger_type":
        policy["triggers"] = [{"type": value}]
    elif field == "match":
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
@pytest.mark.parametrize("kind", ["add_tags", "remove_tags", "replace_tags"])
def test_individual_json_tags_cannot_be_split_by_anki_or_editor(tag: str, kind: str) -> None:
    parsed = parse_config(policy_config(actions=[{"type": kind, "tags": [tag]}]))
    assert "individual tags" in str(parsed.issues[0])


@pytest.mark.parametrize("days", [-1, MAX_DAYS + 1, 10**100, True])
def test_numeric_bounds_match_editor(days: int) -> None:
    parsed = parse_config(
        policy_config(conditions=[{"type": "interval", "days": days, "operator": "gte"}])
    )
    assert parsed.issues


@pytest.mark.parametrize("actions", [None, []])
def test_actions_are_required_and_non_empty(actions: object) -> None:
    raw = policy_config()
    if actions is None:
        del raw["policies"][0]["actions"]
    else:
        raw["policies"][0]["actions"] = actions
    parsed = parse_config(raw)
    assert "actions: must be a non-empty array" in str(parsed.issues[0])


def test_automatic_delete_requires_explicit_configuration_but_is_supported() -> None:
    parsed = parse_config(
        policy_config(triggers=[{"type": "daily"}], actions=[{"type": "delete_card"}])
    )
    assert not parsed.issues
    assert parsed.config.policies[0].triggers[0].type == "daily"
    assert isinstance(parsed.config.policies[0].actions[0], DeleteCardAction)


@pytest.mark.parametrize("kind", ["delete_card", "delete_note"])
def test_collection_wide_unrestricted_delete_is_rejected(kind: str) -> None:
    raw = policy_config(actions=[{"type": kind}])
    policy = raw["policies"][0]
    del policy["scope"]
    del policy["match"]
    del policy["conditions"]

    parsed = parse_config(raw)

    assert str(parsed.issues[0]).endswith(
        "Collection-wide deletion is not allowed. Restrict the scope or add at least one condition."
    )


@pytest.mark.parametrize(
    "restriction",
    [
        {"scope": {"decks": [{"deck": "Mining", "include_subdecks": False}]}},
        {"scope": {"note_types": [{"name": "Basic"}]}},
        {
            "match": "all",
            "conditions": [{"type": "interval", "days": 30, "operator": "gte"}],
        },
    ],
)
def test_restricted_delete_is_valid(restriction: dict) -> None:
    raw = policy_config(actions=[{"type": "delete_note"}])
    policy = raw["policies"][0]
    policy.pop("scope")
    policy.pop("match")
    policy.pop("conditions")
    policy.update(restriction)

    assert not parse_config(raw).issues


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
        policy_config(actions=[{"type": "add_tags", "tags": ["retired", "vocabulary"]}])
    )
    policy = parsed.config.policies[0]

    assert policy.actions == (TagAction("retired"), TagAction("vocabulary"))
    assert policy_to_dict(policy)["actions"] == [
        {"type": "add_tags", "tags": ["retired", "vocabulary"]}
    ]


def test_tag_and_suspension_conditions_parse_and_round_trip() -> None:
    parsed = parse_config(
        policy_config(
            scope={"decks": [{"deck": "Mining", "include_subdecks": True}]},
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


def test_matching_suspended_cards_needs_only_a_condition() -> None:
    parsed = parse_config(
        policy_config(conditions=[{"type": "suspension", "operator": "is_suspended"}])
    )

    assert not parsed.issues
    assert parsed.config.policies


def test_inverse_and_replacement_actions_parse_and_round_trip() -> None:
    parsed = parse_config(
        policy_config(
            scope={"decks": [{"deck": "Mining", "include_subdecks": True}]},
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
            {"type": "add_tags", "tags": ["leech"]},
            {"type": "remove_tags", "tags": ["Leech"]},
        ],
        [
            {"type": "replace_tags", "tags": ["only"]},
            {"type": "add_tags", "tags": ["extra"]},
        ],
    ):
        parsed = parse_config(policy_config(actions=actions))
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


def test_policy_triggers_are_validated() -> None:
    parsed = parse_config(policy_config(triggers=[{"type": "notify"}]))
    assert "policies[0].triggers[0].type:" in str(parsed.issues[0])
    assert not parsed.config.policies


def test_omitted_policy_triggers_mean_manual_only() -> None:
    raw = policy_config()
    parsed = parse_config(raw)
    assert not parsed.issues
    assert not parsed.config.policies[0].triggers
    assert "triggers" not in policy_to_dict(parsed.config.policies[0])


@pytest.mark.parametrize(
    "triggers",
    [
        [],
        None,
        {},
        "daily",
        ["daily"],
        [{}],
        [{"type": "review_end"}],
        [{"type": "profile_open"}],
        [{"type": "after_sync"}],
        [{"type": "daily", "extra": True}],
        [{"type": "daily"}, {"type": "daily"}],
    ],
)
def test_invalid_trigger_shapes_are_rejected(triggers: object) -> None:
    parsed = parse_config(policy_config(triggers=triggers))
    assert parsed.issues
    assert not parsed.config.policies


@pytest.mark.parametrize(
    "kinds",
    [["daily"], ["on_open", "on_sync"], ["daily", "on_open", "on_sync"]],
)
def test_triggers_round_trip(kinds: list[str]) -> None:
    raw = policy_config(triggers=[{"type": kind} for kind in kinds])
    parsed = parse_config(raw)
    assert not parsed.issues
    assert policy_to_dict(parsed.config.policies[0])["triggers"] == raw["policies"][0]["triggers"]


def test_match_and_conditions_are_required() -> None:
    for missing in ("match", "conditions"):
        raw = policy_config()
        del raw["policies"][0][missing]
        parsed = parse_config(raw)
        assert missing in str(parsed.issues[0])
        assert not parsed.config.policies

    assert parse_config(policy_config(conditions=[])).issues


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


def test_removed_successful_answers_condition_is_rejected() -> None:
    parsed = parse_config(policy_config(conditions=[{"type": "successful_answers", "count": 3}]))
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {"type": "card_flag", "flags": ["none", "purple"]},
            CardFlagCondition(("none", "purple")),
        ),
        (
            {"type": "answer_count", "count": 20, "operator": "gte"},
            AnswerCountCondition(20, "gte"),
        ),
        (
            {"type": "correct_answer_count", "count": 10, "operator": "lt"},
            CorrectAnswerCountCondition(10, "lt"),
        ),
        (
            {"type": "lapse_count", "count": 3, "operator": "eq"},
            LapseCountCondition(3, "eq"),
        ),
        (
            {"type": "correct_answer_rate", "percent": 80, "operator": "gte"},
            CorrectAnswerRateCondition(80, "gte"),
        ),
        (
            {"type": "overdue", "days": 30, "operator": "gte"},
            OverdueCondition(30, "gte"),
        ),
        (
            {"type": "fsrs_stability", "days": 90, "operator": "gt"},
            FsrsStabilityCondition(90, "gt"),
        ),
        (
            {"type": "fsrs_difficulty", "percent": 70, "operator": "lte"},
            FsrsDifficultyCondition(70, "lte"),
        ),
        (
            {"type": "fsrs_retrievability", "percent": 60, "operator": "lt"},
            FsrsRetrievabilityCondition(60, "lt"),
        ),
        (
            {"type": "sm2_ease", "percent": 250, "operator": "eq"},
            Sm2EaseCondition(250, "eq"),
        ),
        (
            {"type": "age", "days": 7, "source": "last_review", "operator": "gte"},
            AgeCondition(7, "last_review", "gte"),
        ),
    ],
)
def test_new_card_conditions_round_trip(raw: dict, expected: object) -> None:
    parsed = parse_config(policy_config(conditions=[raw]))
    assert not parsed.issues
    assert parsed.config.policies[0].conditions == AllConditions((expected,))
    assert policy_to_dict(parsed.config.policies[0])["conditions"] == [raw]


def test_flag_actions_round_trip_and_conflict() -> None:
    parsed = parse_config(policy_config(actions=[{"type": "set_flag", "flag": "purple"}]))
    assert not parsed.issues
    assert parsed.config.policies[0].actions == (SetFlagAction("purple"),)
    assert policy_to_dict(parsed.config.policies[0])["actions"] == [
        {"type": "set_flag", "flag": "purple"}
    ]
    cleared = parse_config(policy_config(actions=[{"type": "clear_flag"}]))
    assert cleared.config.policies[0].actions == (ClearFlagAction(),)
    conflicting = parse_config(
        policy_config(actions=[{"type": "set_flag", "flag": "red"}, {"type": "clear_flag"}])
    )
    assert "different card flags" in str(conflicting.issues[0])


def test_legacy_tag_action_name_is_rejected() -> None:
    parsed = parse_config(policy_config(actions=[{"type": "tag", "tags": ["old"]}]))
    assert "unknown action type" in str(parsed.issues[0])


def test_fsrs_and_sm2_conditions_cannot_be_combined() -> None:
    parsed = parse_config(
        policy_config(
            conditions=[
                {"type": "fsrs_stability", "days": 30, "operator": "gte"},
                {"type": "sm2_ease", "percent": 250, "operator": "gte"},
            ]
        )
    )
    assert "FSRS and SM-2 conditions cannot be used together" in str(parsed.issues[0])


def test_fsrs_and_sm2_conditions_cannot_be_split_across_group() -> None:
    parsed = parse_config(
        policy_config(
            conditions=[
                {"type": "fsrs_stability", "days": 30, "operator": "gte"},
                {
                    "match": "any",
                    "conditions": [
                        {"type": "sm2_ease", "percent": 250, "operator": "gte"},
                        {"type": "interval", "days": 30, "operator": "gte"},
                    ],
                },
            ]
        )
    )

    assert "FSRS and SM-2 conditions cannot be used together" in str(parsed.issues[0])
