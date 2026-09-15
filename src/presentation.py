# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from aqt.qt import QLabel, QWidget

from .models import (
    Action,
    AgeCondition,
    AllConditions,
    AnyConditions,
    CardStateCondition,
    ConditionExpression,
    DeleteCardAction,
    IntervalCondition,
    MoveAction,
    Policy,
    ReviewHistoryCondition,
    Scope,
    SuspendAction,
    TagAction,
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
}


def configuration_error_text(issues: tuple[object, ...]) -> str:
    details = "\n".join(f"• {issue}" for issue in issues)
    return f"Card Janitor configuration has errors:\n\n{details}\n\nNo actions were applied."


def card_count_text(count: int) -> str:
    return "1 card" if count == 1 else f"{count} cards"


def applied_message(count: int) -> str:
    return f"Card Janitor cleaned up {card_count_text(count)}"


def describe_action(action: Action) -> str:
    if isinstance(action, TagAction):
        return f"Tag notes with {action.tag!r}"
    if isinstance(action, SuspendAction):
        return "Suspend cards"
    if isinstance(action, MoveAction):
        return f"Move cards to {action.deck!r}"
    if isinstance(action, DeleteCardAction):
        return "Delete cards"
    message = f"unknown cleanup action: {action!r}"
    raise AssertionError(message)


def describe_actions(actions: tuple[Action, ...]) -> str:
    return "\n".join(describe_action(action) for action in actions)


def describe_scope(scope: Scope) -> str:
    decks = ", ".join(scope.decks)
    return f"{decks} + subdecks" if scope.include_subdecks else decks


def scope_tooltip(scope: Scope) -> str:
    return "\n".join(
        (
            f"Decks: {', '.join(scope.decks)}",
            f"Include subdecks: {'Yes' if scope.include_subdecks else 'No'}",
            f"Include suspended cards: {'Yes' if scope.include_suspended else 'No'}",
        )
    )


def policy_tooltip(policy: Policy) -> str:
    decks = ", ".join(policy.scope.decks)
    if policy.scope.include_subdecks:
        decks += " and its subdecks" if len(policy.scope.decks) == 1 else " and their subdecks"
    return (
        f"Cards in {decks} will be cleaned up when:\n"
        f"{describe_conditions(policy.conditions)}\n\n"
        f"Actions:\n{describe_actions(policy.actions)}"
    )


def describe_conditions(condition: ConditionExpression, *, nested: bool = False) -> str:
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
