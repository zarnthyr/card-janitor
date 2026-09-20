# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
from pathlib import Path

import pytest
from card_janitor.models import parse_config
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def test_configuration_schema_and_default_config_are_valid() -> None:
    schema = json.loads((ROOT / "src/config.schema.json").read_text(encoding="utf-8"))
    collection_schema = json.loads(
        (ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8")
    )
    config = json.loads((ROOT / "src/config.json").read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(collection_schema)
    Draft202012Validator(schema).validate(config)


def test_schema_accepts_documented_example() -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    help_text = (ROOT / "docs/policies.md").read_text(encoding="utf-8")
    block = help_text.split("## Example\n", 1)[1].split("\nIn JSON,", 1)[0]
    config_text = "\n".join(line[4:] for line in block.splitlines() if line.startswith("    "))
    config = json.loads(config_text)

    Draft202012Validator(schema).validate(config)


def test_collection_schema_accepts_tag_and_suspension_features() -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    config = {
        "policies": [
            {
                "id": "leeches",
                "name": "Leeches",
                "triggers": [{"type": "daily"}],
                "scope": {
                    "decks": [{"deck": "Mining", "include_subdecks": True}],
                },
                "match": "all",
                "conditions": [
                    {
                        "type": "tags",
                        "operator": "contains_any",
                        "tags": ["leech"],
                    },
                    {"type": "suspension", "operator": "is_suspended"},
                ],
                "actions": [{"type": "replace_tags", "tags": []}],
            }
        ]
    }

    Draft202012Validator(schema).validate(config)


def test_collection_schema_accepts_new_scope_conditions_and_flag_actions() -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    policy = {
        "id": "maintenance",
        "name": "Maintenance",
        "scope": {
            "note_types": [
                {"name": "Basic"},
                {"name": "Reverse", "card_types": ["Card 2"]},
            ],
        },
        "match": "all",
        "conditions": [
            {"type": "card_flag", "flags": ["none", "red"]},
            {"type": "answer_count", "count": 20, "operator": "gte"},
            {"type": "correct_answer_count", "count": 10, "operator": "gte"},
            {"type": "lapse_count", "count": 3, "operator": "gte"},
            {"type": "correct_answer_rate", "percent": 60, "operator": "lt"},
            {"type": "overdue", "days": 30, "operator": "gte"},
            {"type": "age", "days": 60, "source": "last_review", "operator": "gte"},
        ],
        "actions": [{"type": "set_flag", "flag": "purple"}],
    }
    validator = Draft202012Validator(schema)
    assert validator.is_valid({"policies": [policy]})
    policy["actions"] = [{"type": "clear_flag"}]
    assert validator.is_valid({"policies": [policy]})
    policy["actions"] = [{"type": "tag", "tags": ["legacy"]}]
    assert not validator.is_valid({"policies": [policy]})


def test_collection_schema_uses_omission_for_unrestricted_components() -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    policy = {
        "id": "everything",
        "name": "Everything",
        "scope": {"decks": [{"deck": "Mining", "include_subdecks": True}]},
        "actions": [{"type": "suspend"}],
    }
    validator = Draft202012Validator(schema)

    assert validator.is_valid({"policies": [policy]})
    del policy["scope"]
    assert validator.is_valid({"policies": [policy]})
    policy["scope"] = {"note_types": [{"name": "Basic"}]}
    assert validator.is_valid({"policies": [policy]})
    policy["scope"]["note_types"] = []
    assert not validator.is_valid({"policies": [policy]})
    policy["scope"] = {}
    assert not validator.is_valid({"policies": [policy]})
    policy["scope"] = {"decks": []}
    assert not validator.is_valid({"policies": [policy]})


def test_collection_schema_requires_matching_fields_together_and_blocks_global_delete() -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    base = {"id": "policy", "name": "Policy", "actions": [{"type": "suspend"}]}

    assert validator.is_valid({"policies": [base]})
    assert not validator.is_valid({"policies": [{**base, "triggers": []}]})
    assert not validator.is_valid({"policies": [{**base, "match": "all"}]})
    assert not validator.is_valid(
        {"policies": [{**base, "conditions": [{"type": "interval", "days": 1, "operator": "gte"}]}]}
    )
    assert not validator.is_valid({"policies": [{**base, "match": "all", "conditions": []}]})
    assert not validator.is_valid(
        {"policies": [{**base, "match": "all", "conditions": [{"type": "all_cards"}]}]}
    )
    delete = {**base, "actions": [{"type": "delete_note"}]}
    assert not validator.is_valid({"policies": [delete]})
    assert validator.is_valid(
        {
            "policies": [
                {
                    **delete,
                    "match": "all",
                    "conditions": [{"type": "interval", "days": 1, "operator": "gte"}],
                }
            ]
        }
    )


@pytest.mark.parametrize(
    "fixture_name",
    ["dev-profile-config.json", "undo-stress-config.json"],
)
def test_manual_development_profile_config_is_valid(fixture_name: str) -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "tests/manual" / fixture_name).read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(config)
    parsed = parse_config(
        {
            "config_version": 1,
            "notify_after_automatic_run": True,
            "debug_logging": False,
            **config,
        }
    )
    assert not parsed.issues
