# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

CONFIG_VERSION = 2


@dataclass(frozen=True)
class ConfigIssue:
    path: str
    message: str

    def __str__(self) -> str:
        """Format the issue for display."""
        return f"{self.path}: {self.message}"


@dataclass(frozen=True)
class Scope:
    decks: tuple[str, ...]
    include_subdecks: bool = True
    include_suspended: bool = False
    include_filtered_decks: bool = False


@dataclass(frozen=True)
class AgeRule:
    days: int
    source: Literal["first_review", "card_created"]


@dataclass(frozen=True)
class IntervalRule:
    days: int


@dataclass(frozen=True)
class NewRule:
    pass


@dataclass(frozen=True)
class AllRule:
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class AnyRule:
    rules: tuple[Rule, ...]


Rule: TypeAlias = AgeRule | IntervalRule | NewRule | AllRule | AnyRule


@dataclass(frozen=True)
class TagAction:
    tag: str


@dataclass(frozen=True)
class SuspendAction:
    pass


@dataclass(frozen=True)
class MoveAction:
    deck: str


@dataclass(frozen=True)
class DeleteCardAction:
    pass


Action: TypeAlias = TagAction | SuspendAction | MoveAction | DeleteCardAction
PolicyState: TypeAlias = Literal["disabled", "manual", "automatic"]
AutomaticSchedule: TypeAlias = Literal["profile_open", "daily", "profile_open_and_daily"]


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    state: PolicyState
    scope: Scope
    rule: Rule
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class AddonConfig:
    config_version: int
    automatic_schedule: AutomaticSchedule
    notify_after_automatic_retirement: bool
    debug_logging: bool
    policies: tuple[Policy, ...]


@dataclass(frozen=True)
class ParsedConfig:
    config: AddonConfig
    issues: tuple[ConfigIssue, ...]


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _required_string(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}.{key}: must be a non-empty string")
    return value.strip()


def _bool(data: dict[str, Any], key: str, *, default: bool, path: str) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{path}.{key}: must be a boolean")
    return value


def _positive_int(data: dict[str, Any], key: str, path: str) -> int:
    value = data.get(key)
    if not _is_int(value) or value < 1:
        raise ValueError(f"{path}.{key}: must be a positive integer")
    return value


def _parse_rule(value: object, path: str) -> Rule:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    rule_type = value.get("type")
    if rule_type == "age":
        days = _positive_int(value, "days", path)
        source = value.get("from")
        if source not in {"first_review", "card_created"}:
            raise ValueError(f"{path}.from: must be 'first_review' or 'card_created'")
        return AgeRule(days=days, source=source)
    if rule_type == "interval":
        return IntervalRule(days=_positive_int(value, "days", path))
    if rule_type == "new":
        return NewRule()
    if rule_type in {"all", "any"}:
        children = value.get("rules")
        if not isinstance(children, list) or not children:
            raise ValueError(f"{path}.rules: must be a non-empty array")
        rules = tuple(
            _parse_rule(child, f"{path}.rules[{index}]") for index, child in enumerate(children)
        )
        return AllRule(rules) if rule_type == "all" else AnyRule(rules)
    raise ValueError(f"{path}.type: unknown rule type {rule_type!r}")


def _parse_action(value: object, path: str) -> Action:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    action_type = value.get("type")
    if action_type == "tag":
        return TagAction(tag=_required_string(value, "tag", path))
    if action_type == "suspend":
        return SuspendAction()
    if action_type == "move":
        return MoveAction(deck=_required_string(value, "deck", path))
    if action_type == "delete_card":
        return DeleteCardAction()
    raise ValueError(f"{path}.type: unknown action type {action_type!r}")


def _parse_scope(value: object, path: str) -> Scope:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    decks = value.get("decks")
    if not isinstance(decks, list) or not decks:
        raise ValueError(f"{path}.decks: must be a non-empty array")
    if any(not isinstance(deck, str) or not deck.strip() for deck in decks):
        raise ValueError(f"{path}.decks: every deck must be a non-empty string")
    normalized = tuple(dict.fromkeys(deck.strip() for deck in decks))
    return Scope(
        decks=normalized,
        include_subdecks=_bool(value, "include_subdecks", default=True, path=path),
        include_suspended=_bool(value, "include_suspended", default=False, path=path),
        include_filtered_decks=_bool(value, "include_filtered_decks", default=False, path=path),
    )


def _parse_policy(value: object, index: int) -> Policy:
    path = f"policies[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    if "enabled" in value or "mode" in value:
        raise ValueError(f"{path}: replace 'enabled' and 'mode' with 'state'")
    policy_id = _required_string(value, "id", path)
    name = _required_string(value, "name", path)
    state = value.get("state", "manual")
    if state not in {"disabled", "manual", "automatic"}:
        raise ValueError(f"{path}.state: must be 'disabled', 'manual', or 'automatic'")
    actions_value = value.get("actions")
    if not isinstance(actions_value, list) or not actions_value:
        raise ValueError(f"{path}.actions: must be a non-empty array")
    actions = tuple(
        _parse_action(action, f"{path}.actions[{i}]") for i, action in enumerate(actions_value)
    )
    delete_actions = [action for action in actions if isinstance(action, DeleteCardAction)]
    if delete_actions and len(actions) != 1:
        raise ValueError(f"{path}.actions: delete_card must be the only action")
    move_decks = {action.deck.casefold() for action in actions if isinstance(action, MoveAction)}
    if len(move_decks) > 1:
        raise ValueError(f"{path}.actions: a policy cannot have multiple move destinations")
    return Policy(
        id=policy_id,
        name=name,
        state=state,
        scope=_parse_scope(value.get("scope"), f"{path}.scope"),
        rule=_parse_rule(value.get("rule"), f"{path}.rule"),
        actions=actions,
    )


def parse_config(value: object) -> ParsedConfig:
    issues: list[ConfigIssue] = []
    if not isinstance(value, dict):
        value = {}
        issues.append(ConfigIssue("config", "must be an object; no policies were loaded"))

    version = value.get("config_version", CONFIG_VERSION)
    if not _is_int(version) or version != CONFIG_VERSION:
        issues.append(ConfigIssue("config_version", f"must be {CONFIG_VERSION}"))
        version = CONFIG_VERSION

    schedule = value.get("automatic_schedule", "daily")
    if schedule not in {"profile_open", "daily", "profile_open_and_daily"}:
        issues.append(
            ConfigIssue(
                "automatic_schedule",
                "must be 'profile_open', 'daily', or 'profile_open_and_daily'",
            )
        )
        schedule = "daily"

    notify = value.get("notify_after_automatic_retirement", True)
    if not isinstance(notify, bool):
        issues.append(ConfigIssue("notify_after_automatic_retirement", "must be a boolean"))
        notify = True

    debug_logging = value.get("debug_logging", False)
    if not isinstance(debug_logging, bool):
        issues.append(ConfigIssue("debug_logging", "must be a boolean"))
        debug_logging = False

    raw_policies = value.get("policies", [])
    if not isinstance(raw_policies, list):
        issues.append(ConfigIssue("policies", "must be an array; no policies were loaded"))
        raw_policies = []

    policies: list[Policy] = []
    ids: set[str] = set()
    for index, raw_policy in enumerate(raw_policies):
        try:
            policy = _parse_policy(raw_policy, index)
            normalized_id = policy.id.casefold()
            if normalized_id in ids:
                raise ValueError(f"policies[{index}].id: duplicate policy id {policy.id!r}")
            ids.add(normalized_id)
            policies.append(policy)
        except ValueError as error:
            message = str(error)
            issue_path, separator, detail = message.partition(": ")
            issues.append(ConfigIssue(issue_path, detail if separator else message))

    return ParsedConfig(
        config=AddonConfig(
            config_version=CONFIG_VERSION,
            automatic_schedule=schedule,
            notify_after_automatic_retirement=notify,
            debug_logging=debug_logging,
            policies=tuple(policies),
        ),
        issues=tuple(issues),
    )
