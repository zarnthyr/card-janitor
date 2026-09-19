# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

CONFIG_VERSION = 1
MAX_DAYS = 100000
MAX_COUNT = 1000000
MAX_EASE_PERCENT = 1000
FLAG_NAMES = ("none", "red", "orange", "green", "blue", "pink", "turquoise", "purple")


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
class NoteTypeSelector:
    name: str
    card_types: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Scope:
    decks: tuple[DeckSelector, ...] = ()
    all_decks: bool = False
    note_types: tuple[NoteTypeSelector, ...] | None = None

    @property
    def selectors(self) -> tuple[DeckSelector, ...]:
        return self.decks


@dataclass(frozen=True)
class AllCardsCondition:
    pass


@dataclass(frozen=True)
class AgeCondition:
    days: int
    source: Literal["first_review", "last_review", "card_created"]
    operator: NumericOperator


@dataclass(frozen=True)
class IntervalCondition:
    days: int
    operator: NumericOperator


@dataclass(frozen=True)
class CardStateCondition:
    states: tuple[CardState, ...]


@dataclass(frozen=True)
class CardFlagCondition:
    flags: tuple[CardFlag, ...]


@dataclass(frozen=True)
class AnswerCountCondition:
    count: int
    operator: NumericOperator


@dataclass(frozen=True)
class CorrectAnswerCountCondition:
    count: int
    operator: NumericOperator


@dataclass(frozen=True)
class LapseCountCondition:
    count: int
    operator: NumericOperator


@dataclass(frozen=True)
class CorrectAnswerRateCondition:
    percent: int
    operator: NumericOperator


@dataclass(frozen=True)
class OverdueCondition:
    days: int
    operator: NumericOperator


@dataclass(frozen=True)
class FsrsStabilityCondition:
    days: int
    operator: OrderedNumericOperator


@dataclass(frozen=True)
class FsrsDifficultyCondition:
    percent: int
    operator: OrderedNumericOperator


@dataclass(frozen=True)
class FsrsRetrievabilityCondition:
    percent: int
    operator: OrderedNumericOperator


@dataclass(frozen=True)
class Sm2EaseCondition:
    percent: int
    operator: NumericOperator


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
    | CardFlagCondition
    | AnswerCountCondition
    | CorrectAnswerCountCondition
    | LapseCountCondition
    | CorrectAnswerRateCondition
    | OverdueCondition
    | FsrsStabilityCondition
    | FsrsDifficultyCondition
    | FsrsRetrievabilityCondition
    | Sm2EaseCondition
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


@dataclass(frozen=True)
class SetFlagAction:
    flag: UserFlag


@dataclass(frozen=True)
class ClearFlagAction:
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
    | SetFlagAction
    | ClearFlagAction
)


def action_expands_to_siblings(action: Action) -> bool:
    """Whether one matching card extends this action to every card of its note."""
    return isinstance(action, DeleteNoteAction) or (
        isinstance(action, (SuspendAction, UnsuspendAction, MoveAction)) and action.target == "note"
    )


TriggerType: TypeAlias = Literal["daily", "on_open", "on_sync"]


@dataclass(frozen=True)
class Trigger:
    type: TriggerType


NumericOperator: TypeAlias = Literal["gt", "gte", "eq", "lte", "lt"]
OrderedNumericOperator: TypeAlias = Literal["gt", "gte", "lte", "lt"]
CardState: TypeAlias = Literal["new", "learning", "review", "relearning"]
CardFlag: TypeAlias = Literal[
    "none", "red", "orange", "green", "blue", "pink", "turquoise", "purple"
]
UserFlag: TypeAlias = Literal["red", "orange", "green", "blue", "pink", "turquoise", "purple"]


@dataclass(frozen=True)
class Policy:
    id: str
    name: str
    triggers: tuple[Trigger, ...]
    scope: Scope
    conditions: ConditionExpression
    actions: tuple[Action, ...]


@dataclass(frozen=True)
class AddonConfig:
    config_version: int
    automatic_cleanup_enabled: bool
    notify_after_automatic_run: bool
    warn_on_invalid_automatic_policies: bool
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


def _bounded_int(
    data: dict[str, Any], key: str, path: str, *, maximum: int, minimum: int = 0
) -> int:
    value = data.get(key)
    if not _is_int(value) or not minimum <= value <= maximum:
        raise ValueError(f"{path}.{key}: must be an integer between {minimum} and {maximum}")
    return value


def _numeric_operator(value: object, path: str, *, allow_equal: bool = True) -> str:
    valid = {"gt", "gte", "lte", "lt"} | ({"eq"} if allow_equal else set())
    if not isinstance(value, str) or value not in valid:
        choices = ", ".join(
            repr(item) for item in ("gt", "gte", "eq", "lte", "lt") if item in valid
        )
        raise ValueError(f"{path}: must be {choices}")
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
    if any(any(character.isspace() or character == "," for character in tag) for tag in normalized):
        raise ValueError(f"{path}.tags: individual tags must not contain whitespace or commas")
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
    | CardFlagCondition
    | AnswerCountCondition
    | CorrectAnswerCountCondition
    | LapseCountCondition
    | CorrectAnswerRateCondition
    | OverdueCondition
    | FsrsStabilityCondition
    | FsrsDifficultyCondition
    | FsrsRetrievabilityCondition
    | Sm2EaseCondition
    | ReviewHistoryCondition
    | TagCondition
    | SuspensionCondition
    | SiblingSuspensionCondition
    | SiblingReviewHistoryCondition
):
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    condition_type = value.get("type")
    if not isinstance(condition_type, str):
        raise ValueError(f"{path}.type: must be a string")
    if condition_type == "all_cards":
        _reject_unknown_keys(value, {"type"}, path)
        return AllCardsCondition()
    if condition_type == "age":
        _reject_unknown_keys(value, {"type", "days", "source", "operator"}, path)
        days = _bounded_int(value, "days", path, maximum=MAX_DAYS)
        source = value.get("source")
        if not isinstance(source, str) or source not in {
            "first_review",
            "last_review",
            "card_created",
        }:
            raise ValueError(
                f"{path}.source: must be 'first_review', 'last_review', or 'card_created'"
            )
        operator = _numeric_operator(value.get("operator"), f"{path}.operator")
        return AgeCondition(days=days, source=source, operator=operator)
    if condition_type == "interval":
        _reject_unknown_keys(value, {"type", "days", "operator"}, path)
        operator = _numeric_operator(value.get("operator"), f"{path}.operator")
        return IntervalCondition(
            days=_bounded_int(value, "days", path, maximum=MAX_DAYS), operator=operator
        )
    if condition_type == "card_state":
        _reject_unknown_keys(value, {"type", "states"}, path)
        states = value.get("states")
        valid_states = {"new", "learning", "review", "relearning"}
        if (
            not isinstance(states, list)
            or not states
            or any(not isinstance(state, str) or state not in valid_states for state in states)
            or len(states) != len(set(states))
        ):
            raise ValueError(
                f"{path}.states: must be a non-empty array of unique values containing "
                "'new', 'learning', 'review', or 'relearning'"
            )
        return CardStateCondition(tuple(states))
    if condition_type == "card_flag":
        _reject_unknown_keys(value, {"type", "flags"}, path)
        flags = value.get("flags")
        if (
            not isinstance(flags, list)
            or not flags
            or any(not isinstance(flag, str) or flag not in FLAG_NAMES for flag in flags)
            or len(flags) != len(set(flags))
        ):
            raise ValueError(f"{path}.flags: must be a non-empty array of unique card flag names")
        return CardFlagCondition(tuple(flags))
    count_conditions = {
        "answer_count": AnswerCountCondition,
        "correct_answer_count": CorrectAnswerCountCondition,
        "lapse_count": LapseCountCondition,
    }
    if condition_type in count_conditions:
        _reject_unknown_keys(value, {"type", "count", "operator"}, path)
        return count_conditions[condition_type](
            _bounded_int(value, "count", path, maximum=MAX_COUNT),
            _numeric_operator(value.get("operator"), f"{path}.operator"),
        )
    if condition_type == "correct_answer_rate":
        _reject_unknown_keys(value, {"type", "percent", "operator"}, path)
        return CorrectAnswerRateCondition(
            _bounded_int(value, "percent", path, maximum=100),
            _numeric_operator(value.get("operator"), f"{path}.operator"),
        )
    if condition_type == "overdue":
        _reject_unknown_keys(value, {"type", "days", "operator"}, path)
        return OverdueCondition(
            _bounded_int(value, "days", path, maximum=MAX_DAYS),
            _numeric_operator(value.get("operator"), f"{path}.operator"),
        )
    if condition_type == "fsrs_stability":
        _reject_unknown_keys(value, {"type", "days", "operator"}, path)
        return FsrsStabilityCondition(
            _bounded_int(value, "days", path, maximum=MAX_DAYS),
            _numeric_operator(value.get("operator"), f"{path}.operator", allow_equal=False),
        )
    fsrs_percent_conditions = {
        "fsrs_difficulty": FsrsDifficultyCondition,
        "fsrs_retrievability": FsrsRetrievabilityCondition,
    }
    if condition_type in fsrs_percent_conditions:
        _reject_unknown_keys(value, {"type", "percent", "operator"}, path)
        return fsrs_percent_conditions[condition_type](
            _bounded_int(value, "percent", path, maximum=100),
            _numeric_operator(value.get("operator"), f"{path}.operator", allow_equal=False),
        )
    if condition_type == "sm2_ease":
        _reject_unknown_keys(value, {"type", "percent", "operator"}, path)
        return Sm2EaseCondition(
            _bounded_int(value, "percent", path, maximum=MAX_EASE_PERCENT),
            _numeric_operator(value.get("operator"), f"{path}.operator"),
        )
    if condition_type == "review_history":
        _reject_unknown_keys(value, {"type", "operator"}, path)
        operator = value.get("operator")
        if not isinstance(operator, str) or operator not in {"exists", "not_exists"}:
            raise ValueError(f"{path}.operator: must be 'exists' or 'not_exists'")
        return ReviewHistoryCondition(operator)
    if condition_type == "tags":
        _reject_unknown_keys(value, {"type", "tags", "operator"}, path)
        operator = value.get("operator")
        if not isinstance(operator, str) or operator not in {
            "contains_any",
            "contains_all",
            "contains_none",
        }:
            raise ValueError(
                f"{path}.operator: must be 'contains_any', 'contains_all', or 'contains_none'"
            )
        return TagCondition(_tags(value, path), operator)
    if condition_type == "suspension":
        _reject_unknown_keys(value, {"type", "operator"}, path)
        operator = value.get("operator")
        if not isinstance(operator, str) or operator not in {"is_suspended", "is_not_suspended"}:
            raise ValueError(f"{path}.operator: must be 'is_suspended' or 'is_not_suspended'")
        return SuspensionCondition(operator)
    if condition_type in {"sibling_suspension", "sibling_review_history"}:
        _reject_unknown_keys(value, {"type", "operator"}, path)
        operator = value.get("operator")
        if not isinstance(operator, str) or operator not in {"all", "any", "none"}:
            raise ValueError(f"{path}.operator: must be 'all', 'any', or 'none'")
        return (
            SiblingSuspensionCondition(operator)
            if condition_type == "sibling_suspension"
            else SiblingReviewHistoryCondition(operator)
        )
    raise ValueError(f"{path}.type: unknown condition type {condition_type!r}")


def _parse_conditions(value: object, match: object, path: str) -> ConditionExpression:
    if not isinstance(match, str) or match not in {"all", "any"}:
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


def _parse_action(value: object, path: str) -> tuple[Action, ...]:  # noqa: PLR0911, PLR0912
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    action_type = value.get("type")
    if not isinstance(action_type, str):
        raise ValueError(f"{path}.type: must be a string")
    if action_type == "add_tags":
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
    if action_type == "set_flag":
        _reject_unknown_keys(value, {"type", "flag"}, path)
        flag = value.get("flag")
        if not isinstance(flag, str) or flag not in FLAG_NAMES[1:]:
            raise ValueError(f"{path}.flag: must be a named non-empty card flag")
        return (SetFlagAction(flag),)
    if action_type == "clear_flag":
        _reject_unknown_keys(value, {"type"}, path)
        return (ClearFlagAction(),)
    raise ValueError(f"{path}.type: unknown action type {action_type!r}")


def _parse_note_types(values: object, path: str) -> tuple[NoteTypeSelector, ...]:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{path}: must be a non-empty array")
    selectors: list[NoteTypeSelector] = []
    for index, value in enumerate(values):
        selector_path = f"{path}[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{selector_path}: must be a note type selector object")
        _reject_unknown_keys(value, {"name", "card_types"}, selector_path)
        name = _required_string(value, "name", selector_path)
        card_types_value = value.get("card_types")
        card_types = None
        if "card_types" in value:
            if (
                not isinstance(card_types_value, list)
                or not card_types_value
                or any(
                    not isinstance(card_type, str) or not card_type.strip()
                    for card_type in card_types_value
                )
            ):
                raise ValueError(f"{selector_path}.card_types: must be a non-empty array of names")
            card_types = tuple(
                sorted((card_type.strip() for card_type in card_types_value), key=str.casefold)
            )
            if len(card_types) != len({card_type.casefold() for card_type in card_types}):
                raise ValueError(f"{selector_path}.card_types: must not contain duplicates")
        selectors.append(NoteTypeSelector(name, card_types))
    selectors.sort(key=lambda selector: selector.name.casefold())
    if len(selectors) != len({selector.name.casefold() for selector in selectors}):
        raise ValueError(f"{path}: must not contain duplicates")
    return tuple(selectors)


def _parse_scope(value: object, path: str) -> Scope:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    _reject_unknown_keys(value, {"decks", "all_decks", "note_types"}, path)
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
    )


def _parse_triggers(value: object, path: str) -> tuple[Trigger, ...]:
    triggers_value = value
    if not isinstance(triggers_value, list):
        raise ValueError(f"{path}: must be an array")
    triggers = []
    for index, trigger in enumerate(triggers_value):
        trigger_path = f"{path}[{index}]"
        if not isinstance(trigger, dict):
            raise ValueError(f"{trigger_path}: must be an object")
        _reject_unknown_keys(trigger, {"type"}, trigger_path)
        kind = trigger.get("type")
        if not isinstance(kind, str) or kind not in {"daily", "on_open", "on_sync"}:
            raise ValueError(f"{trigger_path}.type: must be 'daily', 'on_open', or 'on_sync'")
        if any(item.type == kind for item in triggers):
            raise ValueError(f"{trigger_path}: duplicate trigger")
        triggers.append(Trigger(kind))
    return tuple(triggers)


def parse_policy(value: object, index: int = 0) -> Policy:
    path = f"policies[{index}]"
    if not isinstance(value, dict):
        raise ValueError(f"{path}: must be an object")
    _reject_unknown_keys(
        value,
        {"id", "name", "triggers", "scope", "match", "conditions", "actions"},
        path,
    )
    policy_id = _required_string(value, "id", path)
    name = _required_string(value, "name", path)
    triggers = _parse_triggers(value.get("triggers"), f"{path}.triggers")
    scope = _parse_scope(value.get("scope"), f"{path}.scope")
    conditions = _parse_conditions(value.get("conditions"), value.get("match"), path)
    if _contains_fsrs_condition(conditions) and _contains_sm2_condition(conditions):
        raise ValueError(f"{path}.conditions: FSRS and SM-2 conditions cannot be used together")
    actions_value = value.get("actions")
    if not isinstance(actions_value, list) or not actions_value:
        raise ValueError(f"{path}.actions: must be a non-empty array")
    actions = tuple(
        parsed_action
        for index, action in enumerate(actions_value)
        for parsed_action in _parse_action(action, f"{path}.actions[{index}]")
    )
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
    flag_values = {action.flag for action in actions if isinstance(action, SetFlagAction)}
    clear_flag = any(isinstance(action, ClearFlagAction) for action in actions)
    if len(flag_values) > 1 or (flag_values and clear_flag):
        raise ValueError(f"{path}.actions: a policy cannot request different card flags")
    return Policy(
        id=policy_id,
        name=name,
        triggers=tuple(triggers),
        scope=scope,
        conditions=conditions,
        actions=actions,
    )


def _contains_fsrs_condition(condition: ConditionExpression) -> bool:
    if isinstance(
        condition,
        (FsrsStabilityCondition, FsrsDifficultyCondition, FsrsRetrievabilityCondition),
    ):
        return True
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(_contains_fsrs_condition(child) for child in condition.conditions)
    return False


def _contains_sm2_condition(condition: ConditionExpression) -> bool:
    if isinstance(condition, Sm2EaseCondition):
        return True
    if isinstance(condition, (AllConditions, AnyConditions)):
        return any(_contains_sm2_condition(child) for child in condition.conditions)
    return False


def parse_config(value: object) -> ParsedConfig:
    issues: list[ConfigIssue] = []
    if not isinstance(value, dict):
        value = {
            "config_version": CONFIG_VERSION,
            "automatic_cleanup_enabled": True,
            "notify_after_automatic_run": True,
            "warn_on_invalid_automatic_policies": True,
            "debug_logging": False,
            "policies": [],
        }
        issues.append(ConfigIssue("config", "must be an object; no policies were loaded"))
    else:
        allowed = {
            "config_version",
            "automatic_cleanup_enabled",
            "notify_after_automatic_run",
            "warn_on_invalid_automatic_policies",
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

    automatic_enabled = value.get("automatic_cleanup_enabled", True)
    if not isinstance(automatic_enabled, bool):
        issues.append(ConfigIssue("automatic_cleanup_enabled", "must be a boolean"))
        automatic_enabled = False

    notify = value.get("notify_after_automatic_run")
    if not isinstance(notify, bool):
        issues.append(ConfigIssue("notify_after_automatic_run", "must be a boolean"))
        notify = True

    warn_invalid = value.get("warn_on_invalid_automatic_policies", True)
    if not isinstance(warn_invalid, bool):
        issues.append(ConfigIssue("warn_on_invalid_automatic_policies", "must be a boolean"))
        warn_invalid = True

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
            automatic_cleanup_enabled=automatic_enabled,
            notify_after_automatic_run=notify,
            warn_on_invalid_automatic_policies=warn_invalid,
            debug_logging=debug_logging,
            policies=tuple(policies),
        ),
        issues=tuple(issues),
        policy_records=tuple(records),
    )


def condition_to_dict(  # noqa: PLR0911, PLR0912
    condition: ConditionExpression,
) -> dict[str, Any]:
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
    if isinstance(condition, CardFlagCondition):
        return {"type": "card_flag", "flags": list(condition.flags)}
    if isinstance(condition, AnswerCountCondition):
        return {"type": "answer_count", "count": condition.count, "operator": condition.operator}
    if isinstance(condition, CorrectAnswerCountCondition):
        return {
            "type": "correct_answer_count",
            "count": condition.count,
            "operator": condition.operator,
        }
    if isinstance(condition, LapseCountCondition):
        return {"type": "lapse_count", "count": condition.count, "operator": condition.operator}
    if isinstance(condition, CorrectAnswerRateCondition):
        return {
            "type": "correct_answer_rate",
            "percent": condition.percent,
            "operator": condition.operator,
        }
    if isinstance(condition, OverdueCondition):
        return {"type": "overdue", "days": condition.days, "operator": condition.operator}
    if isinstance(condition, FsrsStabilityCondition):
        return {
            "type": "fsrs_stability",
            "days": condition.days,
            "operator": condition.operator,
        }
    if isinstance(condition, FsrsDifficultyCondition):
        return {
            "type": "fsrs_difficulty",
            "percent": condition.percent,
            "operator": condition.operator,
        }
    if isinstance(condition, FsrsRetrievabilityCondition):
        return {
            "type": "fsrs_retrievability",
            "percent": condition.percent,
            "operator": condition.operator,
        }
    if isinstance(condition, Sm2EaseCondition):
        return {"type": "sm2_ease", "percent": condition.percent, "operator": condition.operator}
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
        return {"type": "add_tags", "tags": [action.tag]}
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
    if isinstance(action, SetFlagAction):
        return {"type": "set_flag", "flag": action.flag}
    if isinstance(action, ClearFlagAction):
        return {"type": "clear_flag"}
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
        serialized_actions.append({"type": "add_tags", "tags": added_tags})
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
        "triggers": [{"type": trigger.type} for trigger in policy.triggers],
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
            **(
                {
                    "note_types": [
                        {
                            "name": selector.name,
                            **(
                                {"card_types": list(selector.card_types)}
                                if selector.card_types is not None
                                else {}
                            ),
                        }
                        for selector in policy.scope.note_types
                    ]
                }
                if policy.scope.note_types is not None
                else {}
            ),
        },
        "match": match,
        "conditions": [condition_to_dict(condition) for condition in conditions],
        "actions": serialized_actions,
    }
