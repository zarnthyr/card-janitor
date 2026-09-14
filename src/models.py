# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

CONFIG_VERSION = 1


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


@dataclass(frozen=True)
class AgeRule:
    days: int
    source: Literal["first_review", "card_created"]
    operator: NumericOperator


@dataclass(frozen=True)
class IntervalRule:
    days: int
    operator: NumericOperator


@dataclass(frozen=True)
class CardStateRule:
    states: tuple[CardState, ...]


@dataclass(frozen=True)
class ReviewHistoryRule:
    operator: Literal["exists", "not_exists"]


@dataclass(frozen=True)
class AllRule:
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class AnyRule:
    rules: tuple[Rule, ...]


Rule: TypeAlias = AgeRule | IntervalRule | CardStateRule | ReviewHistoryRule | AllRule | AnyRule


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
PolicyMode: TypeAlias = Literal["on_demand", "automatic"]
NumericOperator: TypeAlias = Literal["gt", "gte", "eq", "lte", "lt"]
CardState: TypeAlias = Literal["new", "learning", "review", "relearning"]


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    mode: PolicyMode
    scope: Scope
    rule: Rule
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class AddonConfig:
    config_version: int
    notify_after_automatic_run: bool
    debug_logging: bool
    policies: tuple[Policy, ...]


@dataclass(frozen=True)
class ParsedConfig:
    config: AddonConfig
    issues: tuple[ConfigIssue, ...]
    policy_records: tuple[PolicyRecord, ...]


@dataclass(frozen=True)
class PolicyRecord:
    index: int
    raw: object
    policy: Policy | None
    issues: tuple[ConfigIssue, ...]

    @property
    def key(self) -> str:
        return self.policy.id if self.policy is not None else f"invalid:{self.index}"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _reject_unknown_keys(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed, key=str)
    if unknown:
        key = unknown[0]
        raise ValueError(f"{path}.{key}: unknown field")


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


def _nonnegative_int(data: dict[str, Any], key: str, path: str) -> int:
    value = data.get(key)
    if not _is_int(value) or value < 0:
        raise ValueError(f"{path}.{key}: must be a non-negative integer")
    return value


def _parse_simple_rule(
    value: object, path: str
) -> AgeRule | IntervalRule | CardStateRule | ReviewHistoryRule:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    rule_type = value.get("type")
    if rule_type == "age":
        _reject_unknown_keys(value, {"type", "days", "source", "operator"}, path)
        days = _nonnegative_int(value, "days", path)
        source = value.get("source")
        if source not in {"first_review", "card_created"}:
            raise ValueError(f"{path}.source: must be 'first_review' or 'card_created'")
        operator = value.get("operator")
        if operator not in {"gt", "gte", "eq", "lte", "lt"}:
            raise ValueError(f"{path}.operator: must be 'gt', 'gte', 'eq', 'lte', or 'lt'")
        return AgeRule(days=days, source=source, operator=operator)
    if rule_type == "interval":
        _reject_unknown_keys(value, {"type", "days", "operator"}, path)
        operator = value.get("operator")
        if operator not in {"gt", "gte", "eq", "lte", "lt"}:
            raise ValueError(f"{path}.operator: must be 'gt', 'gte', 'eq', 'lte', or 'lt'")
        return IntervalRule(days=_nonnegative_int(value, "days", path), operator=operator)
    if rule_type == "card_state":
        _reject_unknown_keys(value, {"type", "states"}, path)
        states = value.get("states")
        valid_states = {"new", "learning", "review", "relearning"}
        if (
            not isinstance(states, list)
            or not states
            or any(state not in valid_states for state in states)
            or len(states) != len(set(states))
        ):
            raise ValueError(
                f"{path}.states: must be a non-empty array of unique values containing "
                "'new', 'learning', 'review', or 'relearning'"
            )
        return CardStateRule(tuple(states))
    if rule_type == "review_history":
        _reject_unknown_keys(value, {"type", "operator"}, path)
        operator = value.get("operator")
        if operator not in {"exists", "not_exists"}:
            raise ValueError(f"{path}.operator: must be 'exists' or 'not_exists'")
        return ReviewHistoryRule(operator)
    raise ValueError(f"{path}.type: unknown rule type {rule_type!r}")


def _parse_conditions(value: object, match: object, path: str) -> Rule:
    if match not in {"all", "any"}:
        raise ValueError(f"{path}.match: must be 'all' or 'any'")
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path}.conditions: must be a non-empty array")
    conditions = tuple(
        _parse_simple_rule(condition, f"{path}.conditions[{index}]")
        for index, condition in enumerate(value)
    )
    return AllRule(conditions) if match == "all" else AnyRule(conditions)


def _parse_action(value: object, path: str) -> tuple[Action, ...]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    action_type = value.get("type")
    if action_type == "tag":
        _reject_unknown_keys(value, {"type", "tags"}, path)
        tags = value.get("tags")
        if (
            not isinstance(tags, list)
            or not tags
            or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        ):
            raise ValueError(f"{path}.tags: must be a non-empty array of strings")
        normalized_tags = [tag.strip() for tag in tags]
        if len(normalized_tags) != len(set(normalized_tags)):
            raise ValueError(f"{path}.tags: must not contain duplicates")
        return tuple(TagAction(tag) for tag in normalized_tags)
    if action_type == "suspend":
        _reject_unknown_keys(value, {"type"}, path)
        return (SuspendAction(),)
    if action_type == "move":
        _reject_unknown_keys(value, {"type", "deck"}, path)
        return (MoveAction(deck=_required_string(value, "deck", path)),)
    if action_type == "delete_card":
        _reject_unknown_keys(value, {"type"}, path)
        return (DeleteCardAction(),)
    raise ValueError(f"{path}.type: unknown action type {action_type!r}")


def _parse_scope(value: object, path: str) -> Scope:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    _reject_unknown_keys(value, {"decks", "include_subdecks", "include_suspended"}, path)
    decks = value.get("decks")
    if not isinstance(decks, list) or not decks:
        raise ValueError(f"{path}.decks: must be a non-empty array")
    if any(not isinstance(deck, str) or not deck.strip() for deck in decks):
        raise ValueError(f"{path}.decks: every deck must be a non-empty string")
    normalized = tuple(deck.strip() for deck in decks)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{path}.decks: must not contain duplicates")
    return Scope(
        decks=normalized,
        include_subdecks=_bool(value, "include_subdecks", default=True, path=path),
        include_suspended=_bool(value, "include_suspended", default=False, path=path),
    )


def parse_policy(value: object, index: int = 0) -> Policy:
    path = f"policies[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    _reject_unknown_keys(
        value,
        {"id", "name", "mode", "scope", "match", "conditions", "actions"},
        path,
    )
    policy_id = _required_string(value, "id", path)
    name = _required_string(value, "name", path)
    mode = value.get("mode")
    if mode not in {"on_demand", "automatic"}:
        raise ValueError(f"{path}.mode: must be 'on_demand' or 'automatic'")
    actions_value = value.get("actions")
    if not isinstance(actions_value, list) or not actions_value:
        raise ValueError(f"{path}.actions: must be a non-empty array")
    actions = tuple(
        parsed_action
        for index, action in enumerate(actions_value)
        for parsed_action in _parse_action(action, f"{path}.actions[{index}]")
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
        mode=mode,
        scope=_parse_scope(value.get("scope"), f"{path}.scope"),
        rule=_parse_conditions(value.get("conditions"), value.get("match"), path),
        actions=actions,
    )


def parse_config(value: object) -> ParsedConfig:
    issues: list[ConfigIssue] = []
    if not isinstance(value, dict):
        value = {
            "config_version": CONFIG_VERSION,
            "notify_after_automatic_run": True,
            "debug_logging": False,
            "policies": [],
        }
        issues.append(ConfigIssue("config", "must be an object; no policies were loaded"))
    else:
        allowed = {
            "config_version",
            "notify_after_automatic_run",
            "debug_logging",
            "policies",
        }
        issues.extend(
            ConfigIssue(str(key), "unknown setting")
            for key in sorted(set(value) - allowed, key=str)
        )

    version = value.get("config_version")
    if not _is_int(version) or version != CONFIG_VERSION:
        issues.append(ConfigIssue("config_version", f"must be {CONFIG_VERSION}"))
        version = CONFIG_VERSION

    notify = value.get("notify_after_automatic_run")
    if not isinstance(notify, bool):
        issues.append(ConfigIssue("notify_after_automatic_run", "must be a boolean"))
        notify = True

    debug_logging = value.get("debug_logging")
    if not isinstance(debug_logging, bool):
        issues.append(ConfigIssue("debug_logging", "must be a boolean"))
        debug_logging = False

    raw_policies = value.get("policies")
    if not isinstance(raw_policies, list):
        issues.append(ConfigIssue("policies", "must be an array; no policies were loaded"))
        raw_policies = []

    policies: list[Policy] = []
    records: list[PolicyRecord] = []
    ids: set[str] = set()
    for index, raw_policy in enumerate(raw_policies):
        try:
            policy = parse_policy(raw_policy, index)
            normalized_id = policy.id.casefold()
            if normalized_id in ids:
                raise ValueError(f"policies[{index}].id: duplicate policy id {policy.id!r}")
            ids.add(normalized_id)
            policies.append(policy)
            records.append(PolicyRecord(index, raw_policy, policy, ()))
        except ValueError as error:
            message = str(error)
            issue_path, separator, detail = message.partition(": ")
            issue = ConfigIssue(issue_path, detail if separator else message)
            issues.append(issue)
            records.append(PolicyRecord(index, raw_policy, None, (issue,)))

    return ParsedConfig(
        config=AddonConfig(
            config_version=CONFIG_VERSION,
            notify_after_automatic_run=notify,
            debug_logging=debug_logging,
            policies=tuple(policies),
        ),
        issues=tuple(issues),
        policy_records=tuple(records),
    )


def condition_to_dict(rule: Rule) -> dict[str, Any]:
    if isinstance(rule, AgeRule):
        return {
            "type": "age",
            "days": rule.days,
            "source": rule.source,
            "operator": rule.operator,
        }
    if isinstance(rule, IntervalRule):
        return {"type": "interval", "days": rule.days, "operator": rule.operator}
    if isinstance(rule, CardStateRule):
        return {
            "type": "card_state",
            "states": list(rule.states),
        }
    if isinstance(rule, ReviewHistoryRule):
        return {"type": "review_history", "operator": rule.operator}
    raise AssertionError(f"unknown rule: {rule!r}")


def action_to_dict(action: Action) -> dict[str, Any]:
    if isinstance(action, TagAction):
        return {"type": "tag", "tags": [action.tag]}
    if isinstance(action, SuspendAction):
        return {"type": "suspend"}
    if isinstance(action, MoveAction):
        return {"type": "move", "deck": action.deck}
    if isinstance(action, DeleteCardAction):
        return {"type": "delete_card"}
    raise AssertionError(f"unknown action: {action!r}")


def policy_to_dict(policy: Policy) -> dict[str, Any]:
    if isinstance(policy.rule, (AllRule, AnyRule)):
        match = "all" if isinstance(policy.rule, AllRule) else "any"
        conditions = policy.rule.rules
    else:
        match = "all"
        conditions = (policy.rule,)
    serialized_actions: list[dict[str, Any]] = []
    tags = list(
        dict.fromkeys(action.tag for action in policy.actions if isinstance(action, TagAction))
    )
    if tags:
        serialized_actions.append({"type": "tag", "tags": tags})
    serialized_actions.extend(
        action_to_dict(action) for action in policy.actions if not isinstance(action, TagAction)
    )
    return {
        "id": policy.id,
        "name": policy.name,
        "mode": policy.mode,
        "scope": {
            "decks": list(policy.scope.decks),
            "include_subdecks": policy.scope.include_subdecks,
            "include_suspended": policy.scope.include_suspended,
        },
        "match": match,
        "conditions": [condition_to_dict(condition) for condition in conditions],
        "actions": serialized_actions,
    }
