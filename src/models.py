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


@dataclass(frozen=True)
class IntervalRule:
    days: int


@dataclass(frozen=True)
class NewRule:
    pass


@dataclass(frozen=True)
class CardStateRule:
    state: Literal["new", "learning", "review", "relearning"]


@dataclass(frozen=True)
class StudyStatusRule:
    status: Literal["never_studied"]


@dataclass(frozen=True)
class AllRule:
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class AnyRule:
    rules: tuple[Rule, ...]


Rule: TypeAlias = (
    AgeRule | IntervalRule | NewRule | CardStateRule | StudyStatusRule | AllRule | AnyRule
)


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


def _parse_simple_rule(
    value: object, path: str
) -> AgeRule | IntervalRule | NewRule | CardStateRule | StudyStatusRule:
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
    if rule_type == "card_state":
        state = value.get("state")
        if state not in {"new", "learning", "review", "relearning"}:
            raise ValueError(f"{path}.state: must be 'new', 'learning', 'review', or 'relearning'")
        return CardStateRule(state)
    if rule_type == "study_status":
        status = value.get("status")
        if status != "never_studied":
            raise ValueError(f"{path}.status: must be 'never_studied'")
        return StudyStatusRule(status)
    if rule_type in {"all", "any"}:
        raise ValueError(f"{path}.type: compound rules cannot be nested")
    raise ValueError(f"{path}.type: unknown rule type {rule_type!r}")


def _parse_rule(value: object, path: str) -> Rule:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    rule_type = value.get("type")
    if rule_type in {"all", "any"}:
        children = value.get("rules")
        if not isinstance(children, list) or not children:
            raise ValueError(f"{path}.rules: must be a non-empty array")
        rules = tuple(
            _parse_simple_rule(child, f"{path}.rules[{index}]")
            for index, child in enumerate(children)
        )
        return AllRule(rules) if rule_type == "all" else AnyRule(rules)
    return _parse_simple_rule(value, path)


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
    )


def parse_policy(value: object, index: int = 0) -> Policy:
    path = f"policies[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    policy_id = _required_string(value, "id", path)
    name = _required_string(value, "name", path)
    state = value.get("state")
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

    notify = value.get("notify_after_automatic_run", True)
    if not isinstance(notify, bool):
        issues.append(ConfigIssue("notify_after_automatic_run", "must be a boolean"))
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


def rule_to_dict(rule: Rule) -> dict[str, Any]:
    if isinstance(rule, AgeRule):
        return {"type": "age", "days": rule.days, "from": rule.source}
    if isinstance(rule, IntervalRule):
        return {"type": "interval", "days": rule.days}
    if isinstance(rule, NewRule):
        return {"type": "new"}
    if isinstance(rule, CardStateRule):
        return {"type": "card_state", "state": rule.state}
    if isinstance(rule, StudyStatusRule):
        return {"type": "study_status", "status": rule.status}
    if isinstance(rule, (AllRule, AnyRule)):
        return {
            "type": "all" if isinstance(rule, AllRule) else "any",
            "rules": [rule_to_dict(child) for child in rule.rules],
        }
    raise AssertionError(f"unknown rule: {rule!r}")


def action_to_dict(action: Action) -> dict[str, Any]:
    if isinstance(action, TagAction):
        return {"type": "tag", "tag": action.tag}
    if isinstance(action, SuspendAction):
        return {"type": "suspend"}
    if isinstance(action, MoveAction):
        return {"type": "move", "deck": action.deck}
    if isinstance(action, DeleteCardAction):
        return {"type": "delete_card"}
    raise AssertionError(f"unknown action: {action!r}")


def policy_to_dict(policy: Policy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "name": policy.name,
        "state": policy.state,
        "scope": {
            "decks": list(policy.scope.decks),
            "include_subdecks": policy.scope.include_subdecks,
            "include_suspended": policy.scope.include_suspended,
        },
        "rule": rule_to_dict(policy.rule),
        "actions": [action_to_dict(action) for action in policy.actions],
    }
