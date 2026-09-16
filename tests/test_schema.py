# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
from pathlib import Path

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
                "mode": "automatic",
                "scope": {
                    "decks": [{"deck": "Mining", "include_subdecks": True}],
                    "include_suspended": True,
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


def test_collection_schema_accepts_only_standalone_all_cards_condition() -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    policy = {
        "id": "everything",
        "name": "Everything",
        "mode": "on_demand",
        "scope": {"decks": [{"deck": "Mining", "include_subdecks": True}]},
        "match": "all",
        "conditions": [{"type": "all_cards"}],
        "actions": [{"type": "suspend"}],
    }
    validator = Draft202012Validator(schema)

    assert validator.is_valid({"policies": [policy]})
    policy["scope"] = {"all_decks": True}
    assert validator.is_valid({"policies": [policy]})
    policy["scope"]["note_types"] = ["Basic"]
    assert validator.is_valid({"policies": [policy]})
    policy["scope"]["note_types"] = []
    assert not validator.is_valid({"policies": [policy]})
    del policy["scope"]["note_types"]
    policy["scope"]["decks"] = [{"deck": "Mining", "include_subdecks": True}]
    assert not validator.is_valid({"policies": [policy]})
    policy["scope"] = {"all_decks": True}
    policy["conditions"].append({"type": "interval", "days": 1, "operator": "gte"})
    assert not validator.is_valid({"policies": [policy]})


def test_manual_development_profile_config_is_valid() -> None:
    schema = json.loads((ROOT / "src/collection-config.schema.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "tests/manual/dev-profile-config.json").read_text(encoding="utf-8"))

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
