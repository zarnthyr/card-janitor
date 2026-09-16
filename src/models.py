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
class DeckSelector:
    deck: str
    include_subdecks: bool = True


@dataclass(frozen=True)
class Scope:
    decks: tuple[DeckSelector, ...] = ()
    include_suspended: bool = False
    all_decks: bool = False
    note_types: tuple[str, ...] | None = None

    @property
    def selectors(self) -> tuple[DeckSelector, ...]:
        return self.decks


@dataclass(frozen=True)
class AllCardsCondition:
    pass


@dataclass(frozen=True)
class AgeCondition:
    days: int
    source: Literal["first_review", "card_created"]
    operator: NumericOperator


@dataclass(frozen=True)
class IntervalCondition:
    days: int
    operator: NumericOperator


@dataclass(frozen=True)
class CardStateCondition:
    states: tuple[CardState, ...]


@dataclass(frozen=True)
class ReviewHistoryCondition:
    operator: Literal["exists", "not_exists"]


@dataclass(frozen=True)
class TagCondition:
    tags: tuple[str, ...]
    operator: Literal["contains_any", "contains_all", "contains_none"]


@dataclass(frozen=True)
class SuspensionCondition:
    operator: Literal["is_suspended", "is_not_suspended"]


@dataclass(frozen=True)
class SiblingSuspensionCondition:
    operator: Literal["all", "any", "none"]


@dataclass(frozen=True)
class SiblingReviewHistoryCondition:
    operator: Literal["all", "any", "none"]


@dataclass(frozen=True)
class AllConditions:
    conditions: tuple[ConditionExpression, ...]


@dataclass(frozen=True)
class AnyConditions:
    conditions: tuple[ConditionExpression, ...]


ConditionExpression: TypeAlias = (
    AllCardsCondition
    | AgeCondition
    | IntervalCondition
    | CardStateCondition
    | ReviewHistoryCondition
    | TagCondition
    | SuspensionCondition
    | SiblingSuspensionCondition
    | SiblingReviewHistoryCondition
    | AllConditions
    | AnyConditions
)


@dataclass(frozen=True)
class TagAction:
    tag: str


@dataclass(frozen=True)
class RemoveTagAction:
    tag: str


@dataclass(frozen=True)
class ReplaceTagsAction:
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        """Store this set-like value in a deterministic order."""
        object.__setattr__(self, "tags", tuple(sorted(self.tags, key=str.casefold)))


@dataclass(frozen=True)
class SuspendAction:
    target: Literal["card", "note"] = "card"


@dataclass(frozen=True)
class UnsuspendAction:
    target: Literal["card", "note"] = "card"


@dataclass(frozen=True)
class MoveAction:
    deck: str
    target: Literal["card", "note"] = "card"


@dataclass(frozen=True)
class DeleteCardAction:
    pass


@dataclass(frozen=True)
class DeleteNoteAction:
    pass


Action: TypeAlias = (
    TagAction
    | RemoveTagAction
    | ReplaceTagsAction
    | SuspendAction
    | UnsuspendAction
    | MoveAction
    | DeleteCardAction
    | DeleteNoteAction
)


def action_targets_note(action: Action) -> bool:
    return isinstance(action, DeleteNoteAction) or (
        isinstance(action, (SuspendAction, UnsuspendAction, MoveAction)) and action.target == "note"
    )


PolicyMode: TypeAlias = Literal["on_demand", "automatic"]
NumericOperator: TypeAlias = Literal["gt", "gte", "eq", "lte", "lt"]
CardState: TypeAlias = Literal["new", "learning", "review", "relearning"]


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    mode: PolicyMode
    scope: Scope
    conditions: ConditionExpression
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


def _tags(data: dict[str, Any], path: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    tags = data.get("tags")
    if (
        not isinstance(tags, list)
        or (not tags and not allow_empty)
        or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
    ):
        requirement = "an array of strings" if allow_empty else "a non-empty array of strings"
        raise ValueError(f"{path}.tags: must be {requirement}")
    normalized = tuple(tag.strip() for tag in tags)
    if len(normalized) != len({tag.casefold() for tag in normalized}):
        raise ValueError(f"{path}.tags: must not contain duplicates")
    return normalized


def _parse_simple_condition(  # noqa: PLR0911, PLR0912
    value: object, path: str
) -> (
    AgeCondition
    | AllCardsCondition
    | IntervalCondition
    | CardStateCondition
    | ReviewHistoryCondition
    | TagCondition
    | SuspensionCondition
    | SiblingSuspensionCondition
    | SiblingReviewHistoryCondition
):
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    condition_type = value.get("type")
    if condition_type == "all_cards":
        _reject_unknown_keys(value, {"type"}, path)
        return AllCardsCondition()
    if condition_type == "age":
        _reject_unknown_keys(value, {"type", "days", "source", "operator"}, path)
        days = _nonnegative_int(value, "days", path)
        source = value.get("source")
        if source not in {"first_review", "card_created"}:
            raise ValueError(f"{path}.source: must be 'first_review' or 'card_created'")
        operator = value.get("operator")
        if operator not in {"gt", "gte", "eq", "lte", "lt"}:
            raise ValueError(f"{path}.operator: must be 'gt', 'gte', 'eq', 'lte', or 'lt'")
        return AgeCondition(days=days, source=source, operator=operator)
    if condition_type == "interval":
        _reject_unknown_keys(value, {"type", "days", "operator"}, path)
        operator = value.get("operator")
        if operator not in {"gt", "gte", "eq", "lte", "lt"}:
            raise ValueError(f"{path}.operator: must be 'gt', 'gte', 'eq', 'lte', or 'lt'")
        return IntervalCondition(days=_nonnegative_int(value, "days", path), operator=operator)
    if condition_type == "card_state":
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
        return CardStateCondition(tuple(states))
    if condition_type == "review_history":
        _reject_unknown_keys(value, {"type", "operator"}, path)
        operator = value.get("operator")
        if operator not in {"exists", "not_exists"}:
            raise ValueError(f"{path}.operator: must be 'exists' or 'not_exists'")
        return ReviewHistoryCondition(operator)
    if condition_type == "tags":
        _reject_unknown_keys(value, {"type", "tags", "operator"}, path)
        operator = value.get("operator")
        if operator not in {"contains_any", "contains_all", "contains_none"}:
            raise ValueError(
                f"{path}.operator: must be 'contains_any', 'contains_all', or 'contains_none'"
            )
        return TagCondition(_tags(value, path), operator)
    if condition_type == "suspension":
        _reject_unknown_keys(value, {"type", "operator"}, path)
        operator = value.get("operator")
        if operator not in {"is_suspended", "is_not_suspended"}:
            raise ValueError(f"{path}.operator: must be 'is_suspended' or 'is_not_suspended'")
        return SuspensionCondition(operator)
    if condition_type in {"sibling_suspension", "sibling_review_history"}:
        _reject_unknown_keys(value, {"type", "operator"}, path)
        operator = value.get("operator")
        if operator not in {"all", "any", "none"}:
            raise ValueError(f"{path}.operator: must be 'all', 'any', or 'none'")
        return (
            SiblingSuspensionCondition(operator)
            if condition_type == "sibling_suspension"
            else SiblingReviewHistoryCondition(operator)
        )
    raise ValueError(f"{path}.type: unknown condition type {condition_type!r}")


def _parse_conditions(value: object, match: object, path: str) -> ConditionExpression:
    if match not in {"all", "any"}:
        raise ValueError(f"{path}.match: must be 'all' or 'any'")
    if not isinstance(value, list) or not value:
        raise ValueError(f"{path}.conditions: must be a non-empty array")
    conditions = tuple(
        _parse_simple_condition(condition, f"{path}.conditions[{index}]")
        for index, condition in enumerate(value)
    )
    if (
        any(isinstance(condition, AllCardsCondition) for condition in conditions)
        and len(conditions) != 1
    ):
        raise ValueError(f"{path}.conditions: all_cards must be the only condition")
    return AllConditions(conditions) if match == "all" else AnyConditions(conditions)


def _parse_action(value: object, path: str) -> tuple[Action, ...]:  # noqa: PLR0911
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    action_type = value.get("type")
    if action_type in {"tag", "add_tags"}:
        _reject_unknown_keys(value, {"type", "tags"}, path)
        return tuple(TagAction(tag) for tag in _tags(value, path))
    if action_type == "remove_tags":
        _reject_unknown_keys(value, {"type", "tags"}, path)
        return tuple(RemoveTagAction(tag) for tag in _tags(value, path))
    if action_type == "replace_tags":
        _reject_unknown_keys(value, {"type", "tags"}, path)
        return (ReplaceTagsAction(_tags(value, path, allow_empty=True)),)
    if action_type in {"suspend", "suspend_note"}:
        _reject_unknown_keys(value, {"type"}, path)
        return (SuspendAction("note" if action_type == "suspend_note" else "card"),)
    if action_type in {"unsuspend", "unsuspend_note"}:
        _reject_unknown_keys(value, {"type"}, path)
        return (UnsuspendAction("note" if action_type == "unsuspend_note" else "card"),)
    if action_type in {"move", "move_note"}:
        _reject_unknown_keys(value, {"type", "deck"}, path)
        return (
            MoveAction(
                deck=_required_string(value, "deck", path),
                target="note" if action_type == "move_note" else "card",
            ),
        )
    if action_type == "delete_card":
        _reject_unknown_keys(value, {"type"}, path)
        return (DeleteCardAction(),)
    if action_type == "delete_note":
        _reject_unknown_keys(value, {"type"}, path)
        return (DeleteNoteAction(),)
    raise ValueError(f"{path}.type: unknown action type {action_type!r}")


def _parse_note_types(names: object, path: str) -> tuple[str, ...]:
    if not isinstance(names, list) or not names:
        raise ValueError(f"{path}: must be a non-empty array")
    if any(not isinstance(name, str) or not name.strip() for name in names):
        raise ValueError(f"{path}: every note type must be a non-empty string")
    note_types = tuple(sorted((name.strip() for name in names), key=str.casefold))
    if len(note_types) != len(set(note_types)):
        raise ValueError(f"{path}: must not contain duplicates")
    return note_types


def _parse_scope(value: object, path: str) -> Scope:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    _reject_unknown_keys(value, {"decks", "all_decks", "include_suspended", "note_types"}, path)
    note_types = (
        _parse_note_types(value["note_types"], f"{path}.note_types")
        if "note_types" in value
        else None
    )
    if "all_decks" in value:
        if value["all_decks"] is not True or "decks" in value:
            raise ValueError(f"{path}: use either all_decks: true or a non-empty decks array")
        return Scope(
            all_decks=True,
            note_types=note_types,
            include_suspended=_bool(value, "include_suspended", default=False, path=path),
        )
    decks = value.get("decks")
    if not isinstance(decks, list) or not decks:
        raise ValueError(f"{path}.decks: must be a non-empty array")
    normalized: list[DeckSelector] = []
    for index, deck in enumerate(decks):
        if isinstance(deck, dict):
            selector_path = f"{path}.decks[{index}]"
            _reject_unknown_keys(deck, {"deck", "include_subdecks"}, selector_path)
            if "include_subdecks" not in deck:
                raise ValueError(f"{selector_path}.include_subdecks: missing required setting")
            normalized.append(
                DeckSelector(
                    _required_string(deck, "deck", selector_path),
                    _bool(deck, "include_subdecks", default=False, path=selector_path),
                )
            )
        else:
            raise ValueError(f"{path}.decks: every deck must be a deck selector")
    if len(normalized) != len({selector.deck for selector in normalized}):
        raise ValueError(f"{path}.decks: must not contain duplicates")
    return Scope(
        decks=tuple(normalized),
        note_types=note_types,
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
    scope = _parse_scope(value.get("scope"), f"{path}.scope")
    conditions = _parse_conditions(value.get("conditions"), value.get("match"), path)
    if not scope.include_suspended and _requires_suspended_scope(conditions):
        raise ValueError(
            f"{path}.scope.include_suspended: must be true when matching suspended cards"
        )
    actions_value = value.get("actions")
    if not isinstance(actions_value, list) or not actions_value:
        raise ValueError(f"{path}.actions: must be a non-empty array")
    actions = tuple(
        parsed_action
        for index, action in enumerate(actions_value)
        for parsed_action in _parse_action(action, f"{path}.actions[{index}]")
    )
    if not scope.include_suspended and any(
        isinstance(action, UnsuspendAction) and action.target == "card" for action in actions
    ):
        raise ValueError(f"{path}.scope.include_suspended: must be true when unsuspending cards")
    delete_actions = [
        action for action in actions if isinstance(action, (DeleteCardAction, DeleteNoteAction))
    ]
    if delete_actions and len(actions) != 1:
        raise ValueError(f"{path}.actions: deletion must be the only action")
    move_decks = {action.deck.casefold() for action in actions if isinstance(action, MoveAction)}
    if len(move_decks) > 1:
        raise ValueError(f"{path}.actions: a policy cannot have multiple move destinations")
    if any(isinstance(action, SuspendAction) for action in actions) and any(
        isinstance(action, UnsuspendAction) for action in actions
    ):
        raise ValueError(f"{path}.actions: suspend and unsuspend cannot be combined")
    replacements = [action for action in actions if isinstance(action, ReplaceTagsAction)]
    incremental_tags = [
        action for action in actions if isinstance(action, (TagAction, RemoveTagAction))
    ]
    if len(replacements) > 1 or (replacements and incremental_tags):
        raise ValueError(f"{path}.actions: replace_tags cannot be combined with other tag actions")
    added = {action.tag.casefold() for action in actions if isinstance(action, TagAction)}
    removed = {action.tag.casefold() for action in actions if isinstance(action, RemoveTagAction)}
    if added & removed:
        raise ValueError(f"{path}.actions: the same tag cannot be added and removed")
    return Policy(
        id=policy_id,
        name=name,
        mode=mode,
        scope=scope,
        conditions=conditions,
        actions=actions,
    )


def _requires_suspended_scope(condition: ConditionExpression) -> bool:
    if isinstance(condition, SiblingSuspensionCondition):
        return condition.operator == "all"
    if isinstance(condition, SuspensionCondition):
        return condition.operator == "is_suspended"
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(_requires_suspended_scope(child) for child in condition.conditions)
    return False


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


def condition_to_dict(condition: ConditionExpression) -> dict[str, Any]:  # noqa: PLR0911
    if isinstance(condition, AllCardsCondition):
        return {"type": "all_cards"}
    if isinstance(condition, AgeCondition):
        return {
            "type": "age",
            "days": condition.days,
            "source": condition.source,
            "operator": condition.operator,
        }
    if isinstance(condition, IntervalCondition):
        return {"type": "interval", "days": condition.days, "operator": condition.operator}
    if isinstance(condition, CardStateCondition):
        return {
            "type": "card_state",
            "states": list(condition.states),
        }
    if isinstance(condition, ReviewHistoryCondition):
        return {"type": "review_history", "operator": condition.operator}
    if isinstance(condition, TagCondition):
        return {
            "type": "tags",
            "tags": list(condition.tags),
            "operator": condition.operator,
        }
    if isinstance(condition, SuspensionCondition):
        return {"type": "suspension", "operator": condition.operator}
    if isinstance(condition, SiblingSuspensionCondition):
        return {"type": "sibling_suspension", "operator": condition.operator}
    if isinstance(condition, SiblingReviewHistoryCondition):
        return {"type": "sibling_review_history", "operator": condition.operator}
    raise AssertionError(f"unknown condition: {condition!r}")


def action_to_dict(action: Action) -> dict[str, Any]:  # noqa: PLR0911
    if isinstance(action, TagAction):
        return {"type": "tag", "tags": [action.tag]}
    if isinstance(action, RemoveTagAction):
        return {"type": "remove_tags", "tags": [action.tag]}
    if isinstance(action, ReplaceTagsAction):
        return {"type": "replace_tags", "tags": list(action.tags)}
    if isinstance(action, SuspendAction):
        return {"type": "suspend_note" if action.target == "note" else "suspend"}
    if isinstance(action, UnsuspendAction):
        return {"type": "unsuspend_note" if action.target == "note" else "unsuspend"}
    if isinstance(action, MoveAction):
        return {"type": "move_note" if action.target == "note" else "move", "deck": action.deck}
    if isinstance(action, DeleteCardAction):
        return {"type": "delete_card"}
    if isinstance(action, DeleteNoteAction):
        return {"type": "delete_note"}
    raise AssertionError(f"unknown action: {action!r}")


def policy_to_dict(policy: Policy) -> dict[str, Any]:
    if isinstance(policy.conditions, (AllConditions, AnyConditions)):
        match = "all" if isinstance(policy.conditions, AllConditions) else "any"
        conditions = policy.conditions.conditions
    else:
        match = "all"
        conditions = (policy.conditions,)
    serialized_actions: list[dict[str, Any]] = []
    added_tags = list(
        dict.fromkeys(action.tag for action in policy.actions if isinstance(action, TagAction))
    )
    removed_tags = list(
        dict.fromkeys(
            action.tag for action in policy.actions if isinstance(action, RemoveTagAction)
        )
    )
    if added_tags:
        serialized_actions.append({"type": "tag", "tags": added_tags})
    if removed_tags:
        serialized_actions.append({"type": "remove_tags", "tags": removed_tags})
    serialized_actions.extend(
        action_to_dict(action)
        for action in policy.actions
        if not isinstance(action, (TagAction, RemoveTagAction))
    )
    return {
        "id": policy.id,
        "name": policy.name,
        "mode": policy.mode,
        "scope": {
            **(
                {"all_decks": True}
                if policy.scope.all_decks
                else {
                    "decks": [
                        {"deck": selector.deck, "include_subdecks": selector.include_subdecks}
                        for selector in policy.scope.selectors
                    ]
                }
            ),
            "include_suspended": policy.scope.include_suspended,
            **(
                {"note_types": list(policy.scope.note_types)}
                if policy.scope.note_types is not None
                else {}
            ),
        },
        "match": match,
        "conditions": [condition_to_dict(condition) for condition in conditions],
        "actions": serialized_actions,
    }
