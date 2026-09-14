# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def test_configuration_schema_and_default_config_are_valid() -> None:
    schema = json.loads((ROOT / "src/config.schema.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "src/config.json").read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(config)


def test_schema_accepts_documented_example() -> None:
    schema = json.loads((ROOT / "src/config.schema.json").read_text(encoding="utf-8"))
    help_text = (ROOT / "docs/config.md").read_text(encoding="utf-8")
    block = help_text.split("## Example\n", 1)[1].split("\nIn JSON,", 1)[0]
    config_text = "\n".join(line[4:] for line in block.splitlines() if line.startswith("    "))
    config = json.loads(config_text)

    Draft202012Validator(schema).validate(config)
