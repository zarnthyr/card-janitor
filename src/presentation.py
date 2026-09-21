# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Literal
from urllib.parse import quote

from aqt.qt import QLabel, QWidget

from .models import (
    Action,
    AgeCondition,
    AllCardsCondition,
    AllConditions,
    AnswerCountCondition,
    AnyConditions,
    CardFlagCondition,
    CardStateCondition,
    ClearFlagAction,
    ConditionExpression,
    CorrectAnswerCountCondition,
    CorrectAnswerRateCondition,
    DeleteCardAction,
    DeleteNoteAction,
    FsrsDifficultyCondition,
    FsrsRetrievabilityCondition,
    FsrsStabilityCondition,
    IntervalCondition,
    LapseCountCondition,
    MoveAction,
    NoteTypeSelector,
    OverdueCondition,
    Policy,
    RemoveTagAction,
    ReplaceTagsAction,
    ReviewHistoryCondition,
    Scope,
    SetFlagAction,
    SiblingReviewHistoryCondition,
    SiblingSuspensionCondition,
    Sm2EaseCondition,
    SuspendAction,
    SuspensionCondition,
    TagAction,
    TagCondition,
    Trigger,
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
    "age_first_review": (
        "Elapsed whole days since the card's earliest genuine answer in Anki's review log"
    ),
    "age_last_review": (
        "Elapsed whole days since the card's most recent genuine answer in Anki's review log"
    ),
    "age_card_created": (
        "Elapsed whole days since the original creation timestamp stored in the card ID. "
        "Imported cards may appear much older."
    ),
    "interval": "The card's current Anki interval in days. New cards normally have interval 0.",
    "overdue": "Whole days since a review card became due; only due review cards can match.",
    "card_state": (
        "The card's current scheduling state. This does not indicate whether it has review history."
    ),
    "card_flag": "The card's current Anki flag colour, including no flag",
    "answer_count": "Number of genuine answers in the card's review log (ratings 1-4)",
    "correct_answer_count": "Number of non-Again answers in the card's review log",
    "correct_answer_rate": (
        "Percentage of genuine answers that were not Again. Cards with no answers do not match."
    ),
    "lapse_count": "The card's cumulative lapse count stored by Anki",
    "sm2_ease": "The card's SM-2 ease percentage",
    "fsrs_stability": "The card's current FSRS stability in days",
    "fsrs_difficulty": "The card's current FSRS difficulty, normalized to 0-100%",
    "fsrs_retrievability": "The card's current estimated FSRS retrievability percentage",
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
    if isinstance(action, SetFlagAction):
        return f"Set {action.flag} card flag"
    if isinstance(action, ClearFlagAction):
        return "Clear card flag"
    message = f"unknown cleanup action: {action!r}"
    raise AssertionError(message)


def describe_actions(actions: tuple[Action, ...]) -> str:
    return "\n".join(describe_action(action) for action in actions)


def _describe_decks(scope: Scope, *, separator: str = ", ") -> str:
    if scope.all_decks:
        return "All decks"
    return separator.join(
        selector.deck + (" + subdecks" if selector.include_subdecks else "")
        for selector in scope.selectors
    )


def describe_scope(scope: Scope) -> str:
    decks = _describe_decks(scope, separator="\n")
    if scope.note_types is None:
        return decks
    types = (
        f"Note type: {_describe_note_type(scope.note_types[0])}"
        if len(scope.note_types) == 1
        else f"{len(scope.note_types)} note types"
    )
    return f"{decks}\n{types}"


def scope_tooltip(scope: Scope) -> str:
    return "\n".join(
        (
            f"Decks: {_describe_decks(scope)}",
            "Note types: "
            + (
                ", ".join(_describe_note_type(selector) for selector in scope.note_types)
                if scope.note_types is not None
                else "All note types"
            ),
        )
    )


def policy_tooltip(policy: Policy) -> str:
    decks = _describe_decks(policy.scope)
    types = (
        " with note types "
        + ", ".join(_describe_note_type(selector) for selector in policy.scope.note_types)
        if policy.scope.note_types is not None
        else ""
    )
    return (
        f"Cards in {decks}{types} will be cleaned up when:\n"
        f"{describe_conditions(policy.conditions)}\n\n"
        f"Actions:\n{describe_actions(policy.actions)}"
    )


def describe_conditions(  # noqa: PLR0911, PLR0912
    condition: ConditionExpression, *, nested: bool = False
) -> str:
    if isinstance(condition, AllCardsCondition):
        return "All cards"
    if isinstance(condition, AgeCondition):
        source = {
            "first_review": "Age since first review",
            "last_review": "Age since last review",
            "card_created": "Age since creation",
        }[condition.source]
        return f"{source} {NUMERIC_OPERATOR_SYMBOLS[condition.operator]} {condition.days} days"
    if isinstance(condition, IntervalCondition):
        return f"Interval {NUMERIC_OPERATOR_SYMBOLS[condition.operator]} {condition.days} days"
    if isinstance(condition, CardStateCondition):
        states = ", ".join(state.capitalize() for state in condition.states)
        return f"Card state is {states}"
    if isinstance(condition, CardFlagCondition):
        flags = ", ".join("No flag" if flag == "none" else flag.title() for flag in condition.flags)
        return f"Card flag is {flags}"
    numeric = (
        (AnswerCountCondition, "Answer count", "", "count"),
        (CorrectAnswerCountCondition, "Correct-answer count", "", "count"),
        (LapseCountCondition, "Lapse count", "", "count"),
        (CorrectAnswerRateCondition, "Correct-answer rate", "%", "percent"),
        (OverdueCondition, "Days overdue", " days", "days"),
        (FsrsStabilityCondition, "FSRS stability", " days", "days"),
        (FsrsDifficultyCondition, "FSRS difficulty", "%", "percent"),
        (FsrsRetrievabilityCondition, "FSRS retrievability", "%", "percent"),
        (Sm2EaseCondition, "SM-2 ease", "%", "percent"),
    )
    for condition_type, label, suffix, attribute in numeric:
        if isinstance(condition, condition_type):
            value = getattr(condition, attribute)
            return f"{label} {NUMERIC_OPERATOR_SYMBOLS[condition.operator]} {value}{suffix}"
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
        if nested:
            return "\n".join(_describe_condition_group(condition))
        operator = "AND" if isinstance(condition, AllConditions) else "OR"
        lines: list[str] = []
        for index, child in enumerate(condition.conditions):
            prefix = "" if index == 0 else f"{operator} "
            if isinstance(child, (AllConditions, AnyConditions)):
                group_lines = _describe_condition_group(child)
                group_lines[0] = prefix + group_lines[0]
                lines.extend(group_lines)
            else:
                lines.append(prefix + describe_conditions(child))
        return "\n".join(lines)
    message = f"unknown cleanup condition: {condition!r}"
    raise AssertionError(message)


def _describe_condition_group(condition: AllConditions | AnyConditions) -> list[str]:
    operator = "AND" if isinstance(condition, AllConditions) else "OR"
    lines = ["("]
    for index, child in enumerate(condition.conditions):
        prefix = "" if index == 0 else f"{operator} "
        description = describe_conditions(
            child,
            nested=isinstance(child, (AllConditions, AnyConditions)),
        ).splitlines()
        description[0] = prefix + description[0]
        lines.extend(f"  {line}" for line in description)
    lines.append(")")
    return lines


def _describe_note_type(selector: NoteTypeSelector) -> str:
    if selector.card_types is None:
        return selector.name
    return f"{selector.name} ({', '.join(selector.card_types)})"


TRIGGER_LABELS = {
    "daily": "Daily",
    "on_open": "On open",
    "on_sync": "On sync",
}
LAST_CLEANUP_KEY = "card_janitor_last_cleanup"


def record_cleanup(
    profile: dict,
    *,
    automatic: bool = True,
    affected_cards: int | None = 0,
    conflicts: int = 0,
    failure: str = "",
    policies: tuple[str, ...] = (),
    policy_ids: tuple[str, ...] = (),
    triggers: tuple[str, ...] = (),
) -> None:
    profile[LAST_CLEANUP_KEY] = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "affected_cards": affected_cards,
        "conflicts": conflicts,
        "failure": failure,
        "automatic": automatic,
        "policies": list(policies),
        "policy_ids": list(policy_ids),
        "triggers": list(triggers),
    }


def last_cleanup(
    profile: dict, *, existing_policy_ids: set[str] | None = None
) -> tuple[str, str] | None:
    value = profile.get(LAST_CLEANUP_KEY)
    if not isinstance(value, dict):
        return None
    try:
        when = datetime.fromisoformat(value["time"]).astimezone().strftime("%Y-%m-%d %H:%M")
    except (KeyError, TypeError, ValueError):
        return None
    affected = value.get("affected_cards")
    conflicts = value.get("conflicts")
    failure = value.get("failure")
    if (
        (affected is not None and (type(affected) is not int or affected < 0))
        or type(conflicts) is not int
        or conflicts < 0
        or not isinstance(failure, str)
    ):
        return None
    outcome = "failed" if failure else card_count_text(affected or 0) + " cleaned up"
    summary = f"Last cleanup: {when} — {outcome}"
    rows = [
        ("Time", when),
        ("Source", "Automatic" if value.get("automatic") else "Manual"),
        (
            "Cards cleaned up",
            "Unknown; some changes may have been applied" if affected is None else str(affected),
        ),
        ("Cards skipped", str(conflicts)),
    ]
    for key, heading in (("triggers", "Triggers"), ("policies", "Policies")):
        if key == "triggers" and not value.get("automatic"):
            continue
        entries = value.get(key)
        if isinstance(entries, list) and entries and all(isinstance(item, str) for item in entries):
            rows.append((heading, "\n".join(entries)))
    policy_html = None
    ids = value.get("policy_ids")
    names = value.get("policies")
    if (
        existing_policy_ids is not None
        and isinstance(ids, list)
        and isinstance(names, list)
        and len(ids) == len(names)
        and all(isinstance(item, str) for item in (*ids, *names))
    ):
        policy_html = "<br>".join(
            f'<a href="policy:{quote(policy_id, safe="")}">{escape(name)}</a>'
            if policy_id in existing_policy_ids
            else escape(name)
            for policy_id, name in zip(ids, names, strict=True)
        )
    details = (
        '<table cellspacing="0" cellpadding="3">'
        + "".join(
            f'<tr><td valign="top"><b>{escape(label)}</b></td>'
            '<td width="12">&nbsp;</td>'
            f'<td valign="top">{policy_html if label == "Policies" and policy_html is not None else escape(text).replace(chr(10), "<br>")}</td></tr>'
            for label, text in rows
        )
        + "</table>"
    )
    if failure:
        details += "<p><b>Failure</b><br>" + escape(failure).replace("\n", "<br>") + "</p>"
    else:
        summary += f"; {conflicts} skipped due to conflicts" if conflicts else ""
    return summary + ".", details


TRIGGER_HELP = {
    "daily": "Applies this policy once per Anki day, either on open or on day change.",
    "on_open": "Applies this policy when Anki opens.",
    "on_sync": "Applies this policy after collection sync.",
}


def configured_triggers(policy: Policy) -> str:
    return "\n".join(TRIGGER_LABELS[trigger.type] for trigger in policy.triggers) or "None"


def trigger_summary(triggers: tuple[Trigger, ...]) -> str:
    return "; ".join(TRIGGER_LABELS[trigger.type] for trigger in triggers) or "None"


def triggers_tooltip(policy: Policy) -> str:
    return trigger_tooltip(policy.triggers)


def trigger_tooltip(triggers: tuple[Trigger, ...]) -> str:
    return "\n".join(TRIGGER_HELP[trigger.type] for trigger in triggers) or (
        "No automatic triggers; this policy is applied only when started manually"
    )


def warning_panel(
    text: str,
    parent: QWidget,
    *,
    kind: Literal["info", "warning", "error"] = "warning",
) -> QLabel:
    icon = "ⓘ" if kind == "info" else "⚠"
    panel = QLabel(f"{icon} {text}", parent)
    panel.setWordWrap(True)
    background, border = {
        "info": ("rgba(45, 125, 210, 32)", "rgba(45, 125, 210, 115)"),
        "warning": ("rgba(230, 160, 0, 35)", "rgba(230, 160, 0, 120)"),
        "error": ("rgba(210, 45, 45, 42)", "rgba(210, 45, 45, 145)"),
    }[kind]
    panel.setStyleSheet(
        f"background-color: {background};"
        f"border: 1px solid {border};"
        "border-radius: 5px;"
        "padding: 8px;"
    )
    return panel
