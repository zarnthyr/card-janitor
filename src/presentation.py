# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from aqt.qt import QLabel, QWidget

from .models import (
    Action,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnyConditions,
    CardStateCondition,
    ConditionExpression,
    DeleteCardAction,
    DeleteNoteAction,
    IntervalCondition,
    MoveAction,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    Scope,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    SuspendAction,
    SuspensionCondition,
    TagAction,
    TagCondition,
    UnsuspendAction,
)

NUMERIC_OPERATOR_LABELS = (
    ("is greater than", "gt"),
    ("is at least", "gte"),
    ("is exactly", "eq"),
    ("is at most", "lte"),
    ("is less than", "lt"),
)
NUMERIC_OPERATOR_SYMBOLS = {"gt": ">", "gte": "≥", "eq": "=", "lte": "≤", "lt": "<"}
CARD_STATES = (
    ("New", "new"),
    ("Learning", "learning"),
    ("Review", "review"),
    ("Relearning", "relearning"),
)
CONDITION_HELP = {
    "all_cards": "Every card allowed by the selected scope",
    "age_first_review": (
        "Elapsed whole days since the card's earliest genuine answer in Anki's review log"
    ),
    "age_card_created": (
        "Elapsed whole days since the original creation timestamp stored in the card ID. "
        "Imported cards may appear much older."
    ),
    "interval": "The card's current Anki interval in days. New cards normally have interval 0.",
    "card_state": (
        "The card's current scheduling state. This does not indicate whether it has review history."
    ),
    "review_history": "Whether Anki's review log contains a genuine answer for the card",
    "tags": "Tags on the card's note. Sibling cards share the same tags.",
    "suspension": "Whether the card is currently suspended from Anki's schedule",
    "sibling_suspension": "Suspension across every card of the note, including the matching card and siblings outside scope",
    "sibling_review_history": "Genuine review history across every card of the note, including the matching card and siblings outside scope",
}


def configuration_error_text(issues: tuple[object, ...]) -> str:
    details = "\n".join(f"• {issue}" for issue in issues)
    return f"Card Janitor configuration has errors:\n\n{details}\n\nNo actions were applied."


def card_count_text(count: int) -> str:
    return "1 card" if count == 1 else f"{count} cards"


def applied_message(count: int) -> str:
    return f"Card Janitor cleaned up {card_count_text(count)}"


def describe_action(action: Action) -> str:  # noqa: PLR0911
    if isinstance(action, TagAction):
        return f"Add tag {action.tag!r}"
    if isinstance(action, RemoveTagAction):
        return f"Remove tag {action.tag!r}"
    if isinstance(action, ReplaceTagsAction):
        tags = ", ".join(repr(tag) for tag in action.tags) or "no tags"
        return f"Replace all tags with {tags}"
    if isinstance(action, SuspendAction):
        return "Suspend all cards of matching notes" if action.target == "note" else "Suspend cards"
    if isinstance(action, UnsuspendAction):
        return (
            "Unsuspend all cards of matching notes"
            if action.target == "note"
            else "Unsuspend cards"
        )
    if isinstance(action, MoveAction):
        subject = "all cards of matching notes" if action.target == "note" else "cards"
        return f"Move {subject} to {action.deck!r}"
    if isinstance(action, DeleteCardAction):
        return "Delete matching cards"
    if isinstance(action, DeleteNoteAction):
        return "Delete matching notes and all their cards"
    message = f"unknown cleanup action: {action!r}"
    raise AssertionError(message)


def describe_actions(actions: tuple[Action, ...]) -> str:
    return "\n".join(describe_action(action) for action in actions)


def _describe_decks(scope: Scope) -> str:
    if scope.all_decks:
        return "All decks"
    return ", ".join(
        selector.deck + (" + subdecks" if selector.include_subdecks else "")
        for selector in scope.selectors
    )


def describe_scope(scope: Scope) -> str:
    decks = _describe_decks(scope)
    if scope.note_types is None:
        return decks
    types = (
        f"Note type: {scope.note_types[0]}"
        if len(scope.note_types) == 1
        else f"{len(scope.note_types)} note types"
    )
    return f"{decks}\n{types}"


def scope_tooltip(scope: Scope) -> str:
    return "\n".join(
        (
            f"Decks: {_describe_decks(scope)}",
            f"Note types: {', '.join(scope.note_types) if scope.note_types is not None else 'All note types'}",
            f"Include suspended cards: {'Yes' if scope.include_suspended else 'No'}",
        )
    )


def policy_tooltip(policy: Policy) -> str:
    decks = _describe_decks(policy.scope)
    types = (
        f" with note types {', '.join(policy.scope.note_types)}"
        if policy.scope.note_types is not None
        else ""
    )
    return (
        f"Cards in {decks}{types} will be cleaned up when:\n"
        f"{describe_conditions(policy.conditions)}\n\n"
        f"Actions:\n{describe_actions(policy.actions)}"
    )


def describe_conditions(  # noqa: PLR0911
    condition: ConditionExpression, *, nested: bool = False
) -> str:
    if isinstance(condition, AllCardsCondition):
        return "All cards"
    if isinstance(condition, AgeCondition):
        source = (
            "Age since first review" if condition.source == "first_review" else "Age since creation"
        )
        return f"{source} {NUMERIC_OPERATOR_SYMBOLS[condition.operator]} {condition.days} days"
    if isinstance(condition, IntervalCondition):
        return f"Interval {NUMERIC_OPERATOR_SYMBOLS[condition.operator]} {condition.days} days"
    if isinstance(condition, CardStateCondition):
        states = ", ".join(state.capitalize() for state in condition.states)
        return f"Card state is {states}"
    if isinstance(condition, ReviewHistoryCondition):
        operator = "exists" if condition.operator == "exists" else "does not exist"
        return f"Review history {operator}"
    if isinstance(condition, SiblingSuspensionCondition):
        return f"Sibling cards: {condition.operator} suspended"
    if isinstance(condition, SiblingReviewHistoryCondition):
        return f"Sibling cards: {condition.operator} studied"
    if isinstance(condition, TagCondition):
        tags = ", ".join(repr(tag) for tag in condition.tags)
        operator = {
            "contains_any": "contain any of",
            "contains_all": "contain all of",
            "contains_none": "contain none of",
        }[condition.operator]
        return f"Tags {operator} {tags}"
    if isinstance(condition, SuspensionCondition):
        state = "suspended" if condition.operator == "is_suspended" else "not suspended"
        return f"Card is {state}"
    if isinstance(condition, (AllConditions, AnyConditions)):
        operator = "\nAND " if isinstance(condition, AllConditions) else "\nOR "
        description = operator.join(
            describe_conditions(child, nested=True) for child in condition.conditions
        )
        return f"({description})" if nested else description
    message = f"unknown cleanup condition: {condition!r}"
    raise AssertionError(message)


def configured_mode(mode: str) -> str:
    return "On demand" if mode == "on_demand" else "Automatic"


def mode_tooltip(mode: str) -> str:
    if mode == "on_demand":
        return "On demand: runs only when you click Clean Up in Card Janitor"
    return "Automatic: runs once per day without confirmation and can also be run on demand"


def warning_panel(text: str, parent: QWidget, *, destructive: bool = False) -> QLabel:
    panel = QLabel(f"⚠ {text}", parent)
    panel.setWordWrap(True)
    if destructive:
        background = "rgba(210, 45, 45, 42)"
        border = "rgba(210, 45, 45, 145)"
    else:
        background = "rgba(230, 160, 0, 35)"
        border = "rgba(230, 160, 0, 120)"
    panel.setStyleSheet(
        f"background-color: {background};"
        f"border: 1px solid {border};"
        "border-radius: 5px;"
        "padding: 8px;"
    )
    return panel
