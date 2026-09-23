# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from aqt.qt import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QDir,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QModelIndex,
    QPalette,
    QPushButton,
    QScrollArea,
    QSize,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextDocument,
    QTextOption,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import askUser, showWarning, tooltip

from .history_store import (
    HistoryRecord,
    HistoryStore,
    existing_source_id_for_profile,
)
from .log import exception
from .models import (
    AgeCondition,
    AllConditions,
    AnyConditions,
    CardStateCondition,
    IntervalCondition,
    ReviewHistoryCondition,
    SuspensionCondition,
    parse_policy,
)
from .presentation import (
    LAST_CLEANUP_KEY,
    TRIGGER_LABELS,
    describe_actions,
    describe_conditions,
    describe_scope,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .history_events import (
        CleanupEvent,
        ContributorRecord,
        EffectRecord,
        EntityCounts,
        EventAction,
        ExecutionStepRecord,
        PolicyResultRecord,
    )
    from .models import ConditionExpression, Policy

DEFAULT_PAGE_SIZE = 100
MAX_RAW_DISPLAY_LENGTH = 8_000
HISTORY_TRIGGER_COLUMN = 2
_DASH = "—"
_DETAIL_ITEM_VERTICAL_PADDING = 1


@dataclass(frozen=True)
class HistoryRow:
    time: str
    source: str
    trigger: str
    result: str
    cards: str
    policies: str
    tooltip: str = ""


@dataclass(frozen=True)
class _DisplayedCounts:
    cards: int
    notes: int
    matching_trigger_cards: int
    consequential_sibling_cards: int


@dataclass(frozen=True)
class _DisplayedEffect:
    action: EventAction
    counts: _DisplayedCounts
    contributor_key: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Why:
    text: str
    tooltip: str


@dataclass(frozen=True)
class _ChangeRow:
    change: str
    affected: str
    tooltip: str


def _local_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value).astimezone()
    except (TypeError, ValueError):
        return None


def _display_time(value: str) -> str:
    parsed = _local_time(value)
    if parsed is None:
        return "Unknown time"
    return parsed.strftime("%b %d, %Y %H:%M")


def _default_export_path(exported_at: datetime | None = None) -> str:
    local_time = (exported_at or datetime.now().astimezone()).astimezone()
    filename = f"card-janitor-cleanup-history-{local_time:%Y-%m-%d-%H%M}.jsonl"
    return QDir(QDir.homePath()).filePath(filename)


def _title(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _effective_trigger_text(event: CleanupEvent) -> str:
    if event.invocation.kind == "manual":
        return _DASH
    effective = {
        trigger for result in event.policy_results for trigger in result.activation.triggers
    }
    labels = [label for trigger, label in TRIGGER_LABELS.items() if trigger in effective]
    return _natural_list(labels) or _DASH


def history_row(record: HistoryRecord) -> HistoryRow:
    if record.event is not None:
        event = record.event
        if event.outcome.status == "succeeded":
            result = {
                "changed": "Changed",
                "no_change": "No change",
                "unknown": "Unknown",
            }[event.outcome.result]
        else:
            result = _title(event.outcome.status)
        cards = str(event.summary.distinct_cards_changed)
        if event.outcome.unknown_effects_possible:
            cards = f"≥ {cards}"
        source = "Manual" if event.invocation.kind == "manual" else "Automatic"
        return HistoryRow(
            _display_time(event.time.finished_at),
            source,
            _effective_trigger_text(event),
            result,
            cards,
            str(event.summary.policies),
        )
    if record.unsupported_schema is not None:
        return HistoryRow(
            f"Line {record.line_number}",
            "Unknown",
            _DASH,
            "Unsupported",
            _DASH,
            _DASH,
            f"Unsupported history schema: {record.unsupported_schema}",
        )
    return HistoryRow(
        f"Line {record.line_number}",
        "Unreadable",
        _DASH,
        "Corrupt",
        _DASH,
        _DASH,
        record.error or "Unreadable history record",
    )


def _quantity(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def _natural_list(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return "".join(values)
    first, *middle, last = values
    if not middle:
        return f"{first} and {last}"
    return ", ".join((first, *middle)) + f", and {last}"


def _policy_name_map(event: CleanupEvent) -> dict[tuple[str, str], str]:
    names = {}
    for snapshot in event.policies:
        definition = snapshot.definition
        policy_id = definition.get("id")
        if isinstance(policy_id, str):
            names[(policy_id, snapshot.definition_hash)] = str(definition.get("name", policy_id))
    return names


def _condition_paths(
    condition: ConditionExpression,
    *,
    path: str = "",
) -> dict[str, ConditionExpression]:
    paths = {path: condition}
    if isinstance(condition, (AllConditions, AnyConditions)):
        for index, child in enumerate(condition.conditions):
            paths.update(_condition_paths(child, path=f"{path}/conditions/{index}"))
    return paths


def _compact_condition(condition: ConditionExpression) -> str:
    return " ".join(describe_conditions(condition, nested=True).split())


def _summary_text(event: CleanupEvent) -> str:
    if event.outcome.result == "no_change":
        return (
            "No changes were needed."
            if event.outcome.status == "succeeded"
            else "No changes were recorded."
        )
    cards = _quantity(event.summary.distinct_cards_changed, "card")
    notes = _quantity(event.summary.distinct_notes_changed, "note")
    note_actions = {
        "add_tag",
        "remove_tag",
        "replace_tags",
        "suspend_note",
        "unsuspend_note",
        "move_note",
        "delete_note",
    }
    notes_are_explanatory = any(effect.action.type in note_actions for effect in event.effects)
    if event.outcome.result == "unknown" and event.summary.distinct_cards_changed == 0:
        return "Changes may have occurred, but their number is unknown."
    changed = cards + (
        f" across {notes}" if notes_are_explanatory and event.summary.distinct_notes_changed else ""
    )
    prefix = "At least " if event.outcome.unknown_effects_possible else ""
    return f"{prefix}{changed} changed."


def _event_explanation(event: CleanupEvent) -> str:
    ordinary_change = (
        event.outcome.status == "succeeded"
        and event.outcome.result == "changed"
        and event.outcome.effects_complete
        and not event.outcome.unknown_effects_possible
    )
    if ordinary_change:
        return ""
    paragraphs = [f"<p><b>{escape(_summary_text(event))}</b></p>"]
    if event.outcome.status != "succeeded":
        stage = {
            "validation": "before validation completed",
            "evaluation": "during policy evaluation",
            "planning": "while planning changes",
            "execution": "while applying changes",
            "undo_merge": "while integrating with Anki Undo",
            "complete": "after cleanup",
        }[event.outcome.stage]
        paragraphs.append(f"<p><b>Cleanup {event.outcome.status} {stage}.</b></p>")
    if event.outcome.failure is not None:
        failure = event.outcome.failure
        recovery = f"<br>{escape(failure.recovery)}" if failure.recovery else ""
        paragraphs.append(f"<p>{escape(failure.message)}{recovery}</p>")
    if event.outcome.unknown_effects_possible:
        paragraphs.append(
            "<p><b>The complete effect is unknown.</b> Additional changes may have occurred.</p>"
        )
    elif not event.outcome.effects_complete:
        paragraphs.append("<p><b>The recorded effects may be incomplete.</b></p>")
    return "".join(paragraphs)


def _sibling_shape(counts: EntityCounts | _DisplayedCounts) -> str:
    if counts.consequential_sibling_cards == 0:
        return "none"
    if counts.consequential_sibling_cards == counts.cards:
        return "all"
    return "mixed"


def _displayed_counts(record: EffectRecord) -> _DisplayedCounts:
    counts = record.counts
    return _DisplayedCounts(
        counts.cards,
        counts.notes,
        counts.matching_trigger_cards,
        counts.consequential_sibling_cards,
    )


def _sum_counts(first: _DisplayedCounts, second: _DisplayedCounts) -> _DisplayedCounts:
    return _DisplayedCounts(
        first.cards + second.cards,
        first.notes + second.notes,
        first.matching_trigger_cards + second.matching_trigger_cards,
        first.consequential_sibling_cards + second.consequential_sibling_cards,
    )


def _contributor_key(
    contributors: tuple[ContributorRecord, ...],
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (contributor.policy.id, contributor.policy.definition_hash) for contributor in contributors
    )


def _coalesced_effects(event: CleanupEvent) -> tuple[_DisplayedEffect, ...]:
    """Combine disjoint physical effects split only by provenance for display.

    The v1 builder deduplicates each action/target before aggregation. For an
    exact action and contributor-revision set, separate records therefore
    partition targets only by contributor signature descriptors. Sibling shape
    remains in the key because it changes the human explanation.
    """
    grouped: dict[
        tuple[EventAction, tuple[tuple[str, str], ...], str],
        _DisplayedEffect,
    ] = {}
    for effect in event.effects:
        counts = _displayed_counts(effect)
        contributor_key = _contributor_key(effect.contributors)
        key = effect.action, contributor_key, _sibling_shape(counts)
        existing = grouped.get(key)
        grouped[key] = _DisplayedEffect(
            effect.action,
            counts if existing is None else _sum_counts(existing.counts, counts),
            contributor_key,
        )
    return tuple(grouped.values())


def _sibling_suffix(effect: _DisplayedEffect) -> str:
    siblings = effect.counts.consequential_sibling_cards
    if not siblings:
        return ""
    if siblings == effect.counts.cards:
        return " All changed cards were siblings of matching cards."
    return f" This included {_quantity(siblings, 'consequential sibling card')}."


def _effect_text(effect: _DisplayedEffect) -> str:
    action = effect.action
    counts = effect.counts
    cards = _quantity(counts.cards, "card")
    notes = _quantity(counts.notes, "note")
    if action.type == "add_tag":
        text = f"Added tag {action.tag!r} to {notes}"
    elif action.type == "remove_tag":
        text = f"Removed tag {action.tag!r} from {notes}"
    elif action.type == "replace_tags":
        tags = ", ".join(repr(item) for item in action.tags) or "no tags"
        text = f"Replaced tags on {notes} with {tags}"
    elif action.type.startswith("suspend"):
        text = f"Suspended {cards}"
    elif action.type.startswith("unsuspend"):
        text = f"Unsuspended {cards}"
    elif action.type.startswith("move"):
        text = f"Moved {cards} to {action.target_deck!r}"
    elif action.type == "set_flag":
        text = f"Set the {action.flag} flag on {cards}"
    elif action.type == "clear_flag":
        text = f"Cleared the flag on {cards}"
    elif action.type == "delete_card":
        text = f"Deleted {cards}"
    else:
        text = f"Deleted {notes} ({cards})"
    return text + "." + _sibling_suffix(effect)


def _contributor_group_heading(
    key: tuple[tuple[str, str], ...],
    names: dict[tuple[str, str], str],
    event: CleanupEvent,
) -> str:
    order = {
        (snapshot.reference.id, snapshot.reference.definition_hash): index
        for index, snapshot in enumerate(event.policies)
    }
    references = sorted(key, key=order.__getitem__)
    return " + ".join(names.get(reference, reference[0]) for reference in references)


def _ordered_contributor_keys(
    keys: set[tuple[tuple[str, str], ...]], event: CleanupEvent
) -> list[tuple[tuple[str, str], ...]]:
    order = {
        (snapshot.reference.id, snapshot.reference.definition_hash): index
        for index, snapshot in enumerate(event.policies)
    }
    return sorted(keys, key=lambda key: tuple(order[reference] for reference in key))


def _policy_maps(
    event: CleanupEvent,
) -> tuple[
    dict[tuple[str, str], Policy],
    dict[tuple[str, str], PolicyResultRecord],
]:
    policies = {
        (snapshot.reference.id, snapshot.reference.definition_hash): parse_policy(
            snapshot.definition
        )
        for snapshot in event.policies
    }
    results = {
        (result.policy.id, result.policy.definition_hash): result for result in event.policy_results
    }
    return policies, results


def _condition_summary(condition: ConditionExpression, *, nested: bool = False) -> str:
    if not isinstance(condition, (AllConditions, AnyConditions)):
        return _concise_condition(condition)
    separator = " + " if isinstance(condition, AllConditions) else " OR "
    text = separator.join(_concise_condition(child) for child in condition.conditions)
    return f"({text})" if nested else text


def _concise_condition(condition: ConditionExpression) -> str:
    if isinstance(condition, (AllConditions, AnyConditions)):
        return _condition_summary(condition, nested=True)
    text = _compact_condition(condition)
    if isinstance(condition, CardStateCondition):
        states = ", ".join(state.capitalize() for state in condition.states)
        return states if len(condition.states) == 1 else f"State: {states}"
    if isinstance(condition, AgeCondition):
        source = {
            "first_review": "Age",
            "last_review": "Age since last review",
            "card_created": "Age since creation",
        }[condition.source]
        text = text.replace(
            {
                "first_review": "Age since first review",
                "last_review": "Age since last review",
                "card_created": "Age since creation",
            }[condition.source],
            source,
            1,
        )
    elif isinstance(condition, ReviewHistoryCondition):
        return "Studied" if condition.operator == "exists" else "Never studied"
    elif isinstance(condition, SuspensionCondition):
        return "Suspended" if condition.operator == "is_suspended" else "Active"
    return text.replace(" days", "d")


def _alternative_label(condition: ConditionExpression) -> str:
    if isinstance(condition, AgeCondition):
        return {
            "first_review": "Age",
            "last_review": "Last-review age",
            "card_created": "Creation age",
        }[condition.source]
    if isinstance(condition, IntervalCondition):
        return "Interval"
    if isinstance(condition, CardStateCondition):
        return _concise_condition(condition)
    if isinstance(condition, ReviewHistoryCondition):
        return _concise_condition(condition)
    if isinstance(condition, SuspensionCondition):
        return _concise_condition(condition)
    text = _concise_condition(condition)
    return "Tags" if text.startswith("Tags ") else text


def _selected_alternative_labels(
    node_path: str,
    matched_children: tuple[str, ...],
    paths: dict[str, ConditionExpression],
) -> list[str]:
    group = paths.get(node_path)
    all_conditions = group.conditions if isinstance(group, AnyConditions) else ()
    short_labels = [_alternative_label(condition) for condition in all_conditions]
    duplicate_labels = {label for label in short_labels if short_labels.count(label) > 1}
    labels = []
    for path in matched_children:
        condition = paths.get(path)
        if condition is None:
            continue
        short = _alternative_label(condition)
        labels.append(_concise_condition(condition) if short in duplicate_labels else short)
    return labels


def _provenance_lines(policy: Policy, result: PolicyResultRecord) -> list[str]:
    provenance = result.match_provenance
    if provenance.status == "unavailable":
        return ["⚠ Detailed match reasons unavailable"]
    if not any(signature.any_nodes for signature in provenance.signatures):
        return []

    paths = _condition_paths(policy.conditions)
    lines = []
    for signature in provenance.signatures:
        selections = []
        for node in signature.any_nodes:
            labels = _selected_alternative_labels(node.path, node.matched_children, paths)
            if not labels:
                continue
            selected = " + ".join(labels)
            if len(labels) == 1:
                selected += " only"
            selections.append(selected)
        if selections:
            lines.append(f"    ↳ {signature.trigger_cards} · {'; '.join(selections)}")
    return lines


def _why_for_contributors(
    event: CleanupEvent,
    key: tuple[tuple[str, str], ...],
    policies: dict[tuple[str, str], Policy],
    results: dict[tuple[str, str], PolicyResultRecord],
) -> _Why:
    multiple = len(key) > 1
    visible: list[str] = []
    tooltip: list[str] = []
    order = {
        (snapshot.reference.id, snapshot.reference.definition_hash): index
        for index, snapshot in enumerate(event.policies)
    }
    for reference in sorted(key, key=order.__getitem__):
        policy = policies[reference]
        result = results[reference]
        evaluation = result.evaluation
        if evaluation.status == "succeeded":
            explanation = _condition_summary(policy.conditions)
        elif evaluation.status == "not_reached":
            explanation = "Policy evaluation was not reached"
        else:
            explanation = "Matching-card count unavailable"
        prefix = f"{policy.name}: " if multiple else ""
        visible.append(prefix + explanation)
        visible.extend(_provenance_lines(policy, result))

        details = [
            f"{policy.name}",
            f"Scope:\n{describe_scope(policy.scope)}",
            f"Conditions:\n{describe_conditions(policy.conditions)}",
        ]
        if result.activation.triggers:
            details.append(
                "Automatic triggers: "
                + _natural_list([TRIGGER_LABELS[item] for item in result.activation.triggers])
            )
        if result.match_provenance.status == "unavailable":
            details.append(
                result.match_provenance.reason_message
                or "Complete match provenance could not be collected."
            )
        tooltip.append("\n\n".join(details))
    return _Why("\n".join(visible), "\n\n———\n\n".join(tooltip))


def _policy_tooltip_for_contributors(
    key: tuple[tuple[str, str], ...],
    policies: dict[tuple[str, str], Policy],
    results: dict[tuple[str, str], PolicyResultRecord],
) -> str:
    sections = []
    for reference in key:
        policy = policies[reference]
        result = results[reference]
        lines = [
            policy.name,
            f"Scope:\n{describe_scope(policy.scope)}",
            f"Conditions:\n{describe_conditions(policy.conditions)}",
            f"Actions:\n{describe_actions(policy.actions)}",
        ]
        if result.activation.triggers:
            lines.append(
                "Automatic triggers: "
                + _natural_list([TRIGGER_LABELS[item] for item in result.activation.triggers])
            )
        sections.append("\n\n".join(lines))
    return "\n\n———\n\n".join(sections)


def _effect_change(action: EventAction) -> str:
    if action.type in {"add_tag", "remove_tag"}:
        verb = "Added" if action.type == "add_tag" else "Removed"
        return f"{verb} tag {action.tag!r}"
    if action.type == "replace_tags":
        tags = ", ".join(repr(item) for item in action.tags) or "no tags"
        return f"Replaced tags with {tags}"
    if action.type.startswith("move"):
        return f"Moved to {action.target_deck!r}"
    if action.type == "set_flag":
        return f"Set {action.flag} flag"
    return {
        "suspend_card": "Suspended",
        "suspend_note": "Suspended",
        "unsuspend_card": "Unsuspended",
        "unsuspend_note": "Unsuspended",
        "clear_flag": "Cleared flag",
        "delete_card": "Deleted",
        "delete_note": "Deleted",
    }[action.type]


def _affected_text(action: EventAction, counts: _DisplayedCounts) -> str:
    if action.type in {"add_tag", "remove_tag", "replace_tags"}:
        text = _quantity(counts.notes, "note")
    elif action.type == "delete_note":
        text = f"{_quantity(counts.notes, 'note')} ({_quantity(counts.cards, 'card')})"
    else:
        text = _quantity(counts.cards, "card")
    siblings = counts.consequential_sibling_cards
    if siblings:
        if siblings == counts.cards and action.type not in {
            "add_tag",
            "remove_tag",
            "replace_tags",
            "delete_note",
        }:
            text = _quantity(siblings, "sibling card")
        else:
            text += f" ({siblings} {'sibling' if siblings == 1 else 'siblings'})"
    return text


def _change_row(effect: _DisplayedEffect) -> _ChangeRow:
    sibling = _sibling_suffix(effect).strip()
    tooltip_text = _effect_text(effect)
    if sibling:
        tooltip_text += " These cards changed because a different card of the same note matched."
    return _ChangeRow(
        _effect_change(effect.action),
        _affected_text(effect.action, effect.counts),
        tooltip_text,
    )


def _operation_text(step: ExecutionStepRecord, parameter: object) -> str:
    labels = {
        "add_tag": "Adding a tag",
        "remove_tag": "Removing a tag",
        "replace_tags": "Replacing tags",
        "move": "Moving cards",
        "set_flag": "Changing a card flag",
        "suspend": "Suspending cards",
        "unsuspend": "Unsuspending cards",
        "delete_card": "Deleting cards",
        "delete_note": "Deleting notes",
    }
    text = labels[step.operation]
    if step.operation == "move" and isinstance(parameter, str):
        text += f" to {parameter!r}"
    elif step.operation in {"add_tag", "remove_tag"} and isinstance(parameter, str):
        text += f" {parameter!r}"
    elif step.operation == "set_flag" and isinstance(parameter, str):
        text += f" to {parameter}"
    return text


def _execution_details(event: CleanupEvent) -> str:
    unusual = [step for step in event.execution.steps if step.status != "completed"]
    if event.outcome.status == "succeeded" and event.execution.status == "complete" and not unusual:
        return ""
    if event.execution.status == "not_reached":
        return "<h2>Execution</h2><p>No collection changes were attempted.</p>"

    items = []
    for step in unusual:
        for target in step.targets or (None,):
            parameter = target.parameter if target is not None else None
            action = _operation_text(step, parameter)
            count = target.targets if target is not None else 0
            entities = _quantity(count, step.target_kind)
            if step.status == "completed_undo_merge_failed":
                text = (
                    f"{action} changed {entities}, but Anki could not combine it with the "
                    "cleanup's Undo entry."
                )
            elif step.status == "failed_before_mutation":
                text = f"{action} failed before changing {entities}."
            elif step.status == "failed_unknown":
                pronoun = "it" if count == 1 else "they"
                text = f"{action} failed for {entities}; whether {pronoun} changed is unknown."
            else:
                text = f"{action} was not attempted for {entities} after an earlier failure."
            items.append(f"<li>{escape(text)}</li>")
    if items:
        return "<h2>Execution</h2><ul>" + "".join(items) + "</ul>"
    if event.execution.status in {"partial", "unavailable"}:
        return "<h2>Execution</h2><p>Complete execution details are unavailable.</p>"
    return ""


def _history_record_fallback(record: HistoryRecord) -> str:
    raw = record.raw_json
    suffix = ""
    if len(raw) > MAX_RAW_DISPLAY_LENGTH:
        raw = raw[:MAX_RAW_DISPLAY_LENGTH]
        suffix = "\n… truncated for display; export preserves the complete line"
    if record.unsupported_schema is not None:
        heading = "Unsupported history record"
        explanation = (
            f"Line {record.line_number} uses {record.unsupported_schema}. "
            "This Card Janitor version cannot interpret it, but export preserves it exactly."
        )
    else:
        heading = "Corrupt history record"
        explanation = (
            f"Line {record.line_number} could not be decoded: "
            f"{record.error or 'unknown error'}. Other records remain available."
        )
    return (
        f"<h2>{escape(heading)}</h2><p>{escape(explanation)}</p><pre>{escape(raw + suffix)}</pre>"
    )


def _plain_html(value: str) -> str:
    browser = QTextBrowser()
    browser.setHtml(value)
    return browser.toPlainText()


def _table_item(text: str, tooltip_text: str = "") -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    if tooltip_text:
        item.setToolTip(tooltip_text)
    return item


def _use_details_header_font(table: QTableWidget) -> None:
    font = table.font()
    font.setBold(False)
    table.horizontalHeader().setFont(font)


class _WrappedTextDelegate(QStyledItemDelegate):
    """Give wrapped cells a width-aware height hint on every Qt platform."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table

    def sizeHint(  # noqa: N802 - Qt override
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> QSize:
        sized_option = QStyleOptionViewItem(option)
        self.initStyleOption(sized_option, index)
        width = max(1, self._table.columnWidth(index.column()))
        sized_option.rect.setWidth(width)
        text_rect = self._table.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            sized_option,
            self._table,
        )
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(sized_option.font)
        text_option = QTextOption(document.defaultTextOption())
        text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(text_option)
        document.setPlainText(sized_option.text)
        document.setTextWidth(max(1, text_rect.width()))
        base = super().sizeHint(option, index)
        height = (
            math.ceil(document.size().height())
            + 2 * _DETAIL_ITEM_VERTICAL_PADDING
            + 1  # horizontal grid line
        )
        return QSize(base.width(), max(base.height(), height))


class _DetailsTable(QTableWidget):
    def __init__(self, headers: tuple[str, ...], parent: QWidget) -> None:
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        _use_details_header_font(self)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setWordWrap(True)
        self.verticalHeader().hide()
        self.verticalHeader().setMinimumSectionSize(self.fontMetrics().height() + 4)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(
            "QHeaderView::section { padding: 4px 6px; }QTableView::item { padding: 1px 5px; }"
        )
        self._wrapped_text_delegate = _WrappedTextDelegate(self)
        if "Change" in headers:
            self.setItemDelegateForColumn(headers.index("Change"), self._wrapped_text_delegate)
        self._column_proportions: tuple[float, ...] = ()
        self._minimum_widths: tuple[int, ...] = ()
        self._compact_groups: list[tuple[int, int]] = []

    def finish(
        self,
        column_proportions: tuple[float, ...],
        minimum_widths: tuple[int, ...],
    ) -> None:
        self._column_proportions = column_proportions
        self._minimum_widths = minimum_widths
        header = self.horizontalHeader()
        for column in range(self.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.setMinimumWidth(sum(minimum_widths) + self.frameWidth() * 2)
        self._size_columns()
        self._fit_height()
        QTimer.singleShot(0, self._refit)

    def shade_group(self, start_row: int, row_count: int, group_index: int) -> None:
        if group_index % 2 == 0:
            return
        background = self.palette().brush(QPalette.ColorRole.AlternateBase)
        for row in range(start_row, start_row + row_count):
            for column in range(self.columnCount()):
                item = self.item(row, column)
                if item is not None:
                    item.setBackground(background)

    def compact_group(self, start_row: int, row_count: int) -> None:
        if row_count > 1:
            self._compact_groups.append((start_row, row_count))

    def _compact_group_rows(self) -> None:
        compact_height = self.fontMetrics().height() + 6
        for start_row, row_count in self._compact_groups:
            items = [
                self.item(row, column)
                for row in range(start_row, start_row + row_count)
                for column in range(self.columnCount())
                if self.item(row, column) is not None
            ]
            if any("\n" in item.text() for item in items):
                continue
            if any(
                self.fontMetrics().horizontalAdvance(item.text())
                > self.columnWidth(item.column()) - 12
                for item in items
            ):
                continue
            for row in range(start_row, start_row + row_count):
                self.setRowHeight(row, compact_height)

    def _size_columns(self) -> None:
        if not self._column_proportions:
            return
        available = max(self.viewport().width(), sum(self._minimum_widths))
        widths = [
            max(minimum, round(available * proportion))
            for minimum, proportion in zip(
                self._minimum_widths, self._column_proportions, strict=True
            )
        ]
        excess = sum(widths) - available
        if excess > 0:
            reducible = [
                width - minimum for width, minimum in zip(widths, self._minimum_widths, strict=True)
            ]
            total_reducible = sum(reducible)
            if total_reducible:
                reductions = [round(excess * value / total_reducible) for value in reducible]
                widths = [
                    width - reduction for width, reduction in zip(widths, reductions, strict=True)
                ]
        widths[-1] += available - sum(widths)
        for column, width in enumerate(widths):
            self.setColumnWidth(column, width)

    def _refit(self) -> None:
        self._size_columns()
        self._fit_height()

    def _fit_height(self) -> None:
        self.resizeRowsToContents()
        self._compact_group_rows()
        height = self.horizontalHeader().height() + self.frameWidth() * 2
        height += sum(self.rowHeight(row) for row in range(self.rowCount()))
        fitted = height + 2
        if self.height() != fitted:
            self.setFixedHeight(fitted)

    def resizeEvent(self, event: object) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._refit)


class HistoryDetailsWidget(QScrollArea):
    """Human summary of a validated event, distinct from the complete JSONL audit."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setMinimumHeight(280)
        self._plain_text = ""

    def _replace_content(self) -> tuple[QWidget, QVBoxLayout]:
        old = self.takeWidget()
        if old is not None:
            old.deleteLater()
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(page)
        return page, layout

    def setHtml(self, html: str) -> None:  # noqa: N802 - mirrors QTextBrowser's API
        page, layout = self._replace_content()
        browser = QTextBrowser(page)
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.setHtml(html)
        layout.addWidget(browser)
        self._plain_text = browser.toPlainText()

    def toPlainText(self) -> str:  # noqa: N802 - preserves the old details API
        return self._plain_text

    def set_record(self, record: HistoryRecord) -> None:
        if record.event is None:
            self.setHtml(_history_record_fallback(record))
            return
        self.set_event(record.event)

    def set_event(self, event: CleanupEvent) -> None:
        page, layout = self._replace_content()
        plain_parts = []

        explanation = _event_explanation(event)
        if explanation:
            overview = QLabel(page)
            overview.setTextFormat(Qt.TextFormat.RichText)
            overview.setWordWrap(True)
            overview.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            overview.setText(explanation)
            layout.addWidget(overview)
            plain_parts.append(_plain_html(explanation))

        policies, results = _policy_maps(event)
        names = _policy_name_map(event)
        effects: dict[tuple[tuple[str, str], ...], list[_DisplayedEffect]] = {}
        for effect in _coalesced_effects(event):
            effects.setdefault(effect.contributor_key, []).append(effect)
        if effects:
            plain_parts.append("Changes made")
            heading = QLabel("<h2>Changes made</h2>", page)
            heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            layout.addWidget(heading)
            table = _DetailsTable(("Policy", "Change", "Affected", "Why"), page)
            table.setRowCount(sum(len(items) for items in effects.values()))
            row = 0
            for group_index, key in enumerate(_ordered_contributor_keys(set(effects), event)):
                items = effects[key]
                policy_name = _contributor_group_heading(key, names, event)
                policy_tip = _policy_tooltip_for_contributors(key, policies, results)
                why = _why_for_contributors(event, key, policies, results)
                table.setItem(row, 0, _table_item(policy_name, policy_tip))
                table.setItem(row, 3, _table_item(why.text, why.tooltip))
                if len(items) > 1:
                    table.setSpan(row, 0, len(items), 1)
                    table.setSpan(row, 3, len(items), 1)
                plain_parts.extend((policy_name, why.text))
                for offset, effect in enumerate(items):
                    displayed = _change_row(effect)
                    table.setItem(row + offset, 1, _table_item(displayed.change, displayed.tooltip))
                    table.setItem(
                        row + offset,
                        2,
                        _table_item(displayed.affected, displayed.tooltip),
                    )
                    plain_parts.extend((displayed.change, displayed.affected))
                table.compact_group(row, len(items))
                table.shade_group(row, len(items), group_index)
                row += len(items)
            table.finish((0.24, 0.24, 0.12, 0.40), (120, 130, 90, 180))
            layout.addWidget(table)

        if not effects:
            plain_parts.append("Policies evaluated")
            heading = QLabel("<h2>Policies evaluated</h2>", page)
            heading.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            layout.addWidget(heading)
            table = _DetailsTable(("Policy", "Why", "Intended action"), page)
            table.setRowCount(len(event.policies))
            for row, snapshot in enumerate(event.policies):
                key = ((snapshot.reference.id, snapshot.reference.definition_hash),)
                policy = policies[key[0]]
                why = _why_for_contributors(event, key, policies, results)
                policy_tip = _policy_tooltip_for_contributors(key, policies, results)
                actions = describe_actions(policy.actions)
                table.setItem(row, 0, _table_item(policy.name, policy_tip))
                table.setItem(row, 1, _table_item(why.text, why.tooltip))
                table.setItem(row, 2, _table_item(actions, actions))
                table.shade_group(row, 1, row)
                plain_parts.extend((policy.name, why.text, actions))
            table.finish((0.25, 0.45, 0.30), (130, 220, 160))
            layout.addWidget(table)

        execution = _execution_details(event)
        if execution:
            warning = QLabel(page)
            warning.setTextFormat(Qt.TextFormat.RichText)
            warning.setWordWrap(True)
            warning.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            warning.setText(execution)
            layout.addWidget(warning)
            plain_parts.append(_plain_html(execution))

        self._plain_text = "\n".join(part for part in plain_parts if part)


class CleanupHistoryDialog(QDialog):
    def __init__(
        self,
        profile: dict[str, object],
        parent: QWidget,
        *,
        store: HistoryStore | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
        on_cleared: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._store = store
        if store is None:
            source_id = existing_source_id_for_profile(profile)
            self._store = HistoryStore(source_id) if source_id is not None else None
        self._page_size = page_size
        self._on_cleared = on_cleared
        self._records: list[HistoryRecord] = []
        self._next_before_line: int | None = None

        self.setWindowTitle("Cleanup History")
        self.setModal(False)
        self.resize(850, 650)
        layout = QVBoxLayout(self)

        self.intro_layout = QHBoxLayout()
        self.intro_layout.addWidget(
            QLabel("Cleanup history is stored locally for this Anki profile and is not synced.")
        )
        self.intro_layout.addStretch()
        self.delete_button = QPushButton("Delete…", self)
        self.export_button = QPushButton("Export…", self)
        self.intro_layout.addWidget(self.delete_button)
        self.intro_layout.addWidget(self.export_button)
        layout.addLayout(self.intro_layout)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ("Time", "Source", "Trigger", "Result", "Cards", "Policies")
        )
        _use_details_header_font(self.table)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        for column in (0, 1, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        for column in (2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        metrics = self.table.fontMetrics()
        column_examples = {
            0: "Sep 23, 2026 19:50",
            1: "Automatic",
            4: "Cards",
            5: "Policies",
        }
        for column, example in column_examples.items():
            self.table.setColumnWidth(column, metrics.horizontalAdvance(example) + 24)
        self.table.setMinimumHeight(210)
        qconnect(self.table.currentCellChanged, self._selection_changed)
        layout.addWidget(self.table)

        self.details = HistoryDetailsWidget(self)
        layout.addWidget(self.details, 1)

        self.footer_layout = QHBoxLayout()
        self.load_older_button = QPushButton("Load Older", self)
        self.footer_layout.addWidget(self.load_older_button)
        self.footer_layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        qconnect(buttons.rejected, self.reject)
        qconnect(self.export_button.clicked, self._export)
        qconnect(self.delete_button.clicked, self._delete_history)
        qconnect(self.load_older_button.clicked, self._load_older)
        self.footer_layout.addWidget(buttons)
        layout.addLayout(self.footer_layout)

        self._load_initial()

    def _warn(self, message: str) -> None:
        try:
            showWarning(message, parent=self)
        except Exception:
            exception("Could not display cleanup-history warning")

    def _load_initial(self) -> None:
        self._records.clear()
        self.table.setRowCount(0)
        self._next_before_line = None
        if self._store is None:
            self._show_empty()
            self._update_controls()
            return
        self._append_page(None)
        if self._records:
            self.table.setCurrentCell(0, 0)
        else:
            self._show_empty()
        self._update_controls()

    def _append_page(self, before_line: int | None) -> None:
        store = self._store
        if store is None:
            return
        try:
            page = store.read_recent(limit=self._page_size, before_line=before_line)
        except Exception as exc:
            exception("Could not read cleanup history")
            self._warn(f"Could not read cleanup history:\n\n{exc}")
            return
        for record in page.records:
            row_number = self.table.rowCount()
            self.table.insertRow(row_number)
            row = history_row(record)
            for column, value in enumerate(
                (row.time, row.source, row.trigger, row.result, row.cards, row.policies)
            ):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    row.tooltip
                    or (value if column == HISTORY_TRIGGER_COLUMN and value != _DASH else "")
                )
                self.table.setItem(row_number, column, item)
            self._records.append(record)
        self._next_before_line = page.next_before_line

    def _load_older(self) -> None:
        if self._store is None or self._next_before_line is None:
            return
        self._append_page(self._next_before_line)
        self._update_controls()

    def _selection_changed(
        self, current_row: int, _current_column: int, _previous_row: int, _previous_column: int
    ) -> None:
        if 0 <= current_row < len(self._records):
            self.details.set_record(self._records[current_row])

    def _show_empty(self) -> None:
        self.details.setHtml(
            "<h2>No cleanup history</h2>"
            "<p>No cleanup events are available for this profile. History is recorded locally "
            "when cleanup runs while cleanup history is enabled.</p>"
        )

    def _update_controls(self) -> None:
        has_file = self._store is not None and self._store.path.is_file()
        self.export_button.setEnabled(has_file)
        self.delete_button.setEnabled(has_file)
        self.load_older_button.setEnabled(self._next_before_line is not None)

    def _export(self) -> None:
        if self._store is None:
            return
        try:
            filename, _selected_filter = QFileDialog.getSaveFileName(
                self,
                "Export Cleanup History",
                _default_export_path(),
                "JSON Lines (*.jsonl);;All Files (*)",
            )
            if not filename:
                return
            try:
                count = self._store.export(Path(filename))
            except Exception as exc:
                exception("Could not export cleanup history")
                self._warn(f"Could not export cleanup history:\n\n{exc}")
                return
            tooltip(f"Exported {count} cleanup history record(s).", parent=self)
        finally:
            self._restore_after_child_dialog()

    def _delete_history(self) -> None:
        if self._store is None:
            return
        try:
            if not askUser(
                "Permanently delete the complete cleanup history for this profile?\n\n"
                "This cannot be undone. Export first if you want to keep a copy.",
                parent=self,
                defaultno=True,
                title="Delete Cleanup History",
            ):
                return
            try:
                self._store.clear()
            except Exception as exc:
                exception("Could not clear cleanup history")
                self._warn(f"Could not clear cleanup history:\n\n{exc}")
                return
            self._profile.pop(LAST_CLEANUP_KEY, None)
            self._load_initial()
            if self._on_cleared is not None:
                try:
                    self._on_cleared()
                except Exception:
                    exception("Could not refresh cleanup status after clearing history")
        finally:
            self._restore_after_child_dialog()

    def _restore_after_child_dialog(self) -> None:
        self.raise_()
        self.activateWindow()
