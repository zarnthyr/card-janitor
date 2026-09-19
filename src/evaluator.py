# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import replace
from time import perf_counter, time
from typing import TYPE_CHECKING

from .engine import (
    CardFacts,
    NoteFacts,
    PolicyReport,
    ResolvedAction,
    action_is_satisfied,
    conditions_need_fsrs,
    conditions_need_history,
    conditions_need_siblings,
    conditions_need_sm2,
    evaluate_facts,
)
from .log import debug
from .models import MoveAction, Policy, action_expands_to_siblings

if TYPE_CHECKING:
    from anki.collection import Collection

REVIEW_CARD_TYPE = 2


def _resolve_deck_ids(col: Collection, policy: Policy) -> tuple[set[int], list[str]]:
    if policy.scope.all_decks:
        return {int(deck.id) for deck in col.decks.all_names_and_ids(include_filtered=False)}, []
    deck_ids: set[int] = set()
    errors: list[str] = []
    for selector in policy.scope.selectors:
        name = selector.deck
        deck_id = col.decks.id_for_name(name)
        if deck_id is None:
            errors.append(f"deck {name!r} not found")
            continue
        if selector.include_subdecks:
            deck_ids.update(int(value) for value in col.decks.deck_and_child_ids(deck_id))
        else:
            deck_ids.add(int(deck_id))
    return deck_ids, errors


def _resolve_actions(
    col: Collection, policy: Policy
) -> tuple[tuple[ResolvedAction, ...], list[str]]:
    resolved: list[ResolvedAction] = []
    errors: list[str] = []
    for action in policy.actions:
        if isinstance(action, MoveAction):
            deck_id = col.decks.id_for_name(action.deck)
            if deck_id is None:
                errors.append(f"destination deck {action.deck!r} not found")
                continue
            deck = col.decks.get(deck_id)
            if deck and deck.get("dyn"):
                errors.append(f"destination deck {action.deck!r} is filtered")
                continue
            resolved.append(ResolvedAction(action, int(deck_id)))
        else:
            resolved.append(ResolvedAction(action))
    return tuple(resolved), errors


def _resolve_note_type_card_types(
    col: Collection, policy: Policy
) -> tuple[dict[int, set[int] | None] | None, list[str]]:
    if policy.scope.note_types is None:
        return None, []
    resolved: dict[int, set[int] | None] = {}
    errors: list[str] = []
    for selector in policy.scope.note_types:
        note_type_id = col.models.id_for_name(selector.name)
        if note_type_id is None:
            errors.append(f"note type {selector.name!r} not found")
            continue
        if selector.card_types is None:
            resolved[int(note_type_id)] = None
            continue
        note_type = col.models.get(note_type_id)
        card_types = {
            str(card_type["name"]): int(card_type["ord"])
            for card_type in note_type.get("tmpls", [])
        }
        selected: set[int] = set()
        for name in selector.card_types:
            ordinal = card_types.get(name)
            if ordinal is None:
                qualified_name = f"{selector.name}::{name}"
                errors.append(f"card type {qualified_name!r} not found")
            else:
                selected.add(ordinal)
        resolved[int(note_type_id)] = selected
    return resolved, errors


def _fsrs_enabled(col: Collection, deck_ids: set[int]) -> bool:
    if not deck_ids:
        return False
    return bool(col.decks.get_deck_configs_for_update(next(iter(deck_ids))).fsrs)


def validate_policy_references(col: Collection, policy: Policy) -> tuple[str, ...]:
    """Check external references and scheduler compatibility without scanning cards."""
    deck_ids, errors = _resolve_deck_ids(col, policy)
    _, action_errors = _resolve_actions(col, policy)
    _, note_type_errors = _resolve_note_type_card_types(col, policy)
    errors.extend(action_errors)
    errors.extend(note_type_errors)
    needs_fsrs = conditions_need_fsrs(policy.conditions)
    needs_sm2 = conditions_need_sm2(policy.conditions)
    fsrs = _fsrs_enabled(col, deck_ids) if needs_fsrs or needs_sm2 else False
    if needs_fsrs and not fsrs:
        errors.append("FSRS conditions require FSRS to be enabled")
    if needs_sm2 and fsrs:
        errors.append("SM-2 ease conditions require FSRS to be disabled")
    return tuple(errors)


def _load_facts_where(
    col: Collection,
    column: str,
    values: set[int],
    *,
    note_type_card_types: dict[int, set[int] | None] | None = None,
    load_history: bool = True,
    load_fsrs: bool = False,
    eligible_only: bool = False,
) -> list[CardFacts]:
    if not values or note_type_card_types == {}:
        return []
    placeholders = ",".join("?" for _ in values)
    ordered_values = sorted(values)
    note_filter = ""
    if note_type_card_types is not None:
        note_type_ids = set(note_type_card_types)
        note_filter = f"and n.mid in ({','.join('?' for _ in note_type_ids)})"
        ordered_values.extend(sorted(note_type_ids))

    first_review = "min(r.id)" if load_history else "null"
    last_review = "max(r.id)" if load_history else "null"
    answer_count = "count(r.id)" if load_history else "0"
    correct_count = (
        "coalesce(sum(case when r.ease between 2 and 4 then 1 else 0 end), 0)"
        if load_history
        else "0"
    )
    history_join = (
        "left join revlog r on r.cid = c.id and r.ease between 1 and 4" if load_history else ""
    )
    grouping = "group by c.id" if load_history else ""
    eligibility = "and c.odid = 0" if eligible_only else ""

    stability = "extract_fsrs_variable(c.data, 's')" if load_fsrs else "null"
    difficulty = "(extract_fsrs_variable(c.data, 'd') - 1.0) * 100.0 / 9.0" if load_fsrs else "null"
    retrievability = (
        "extract_fsrs_retrievability(c.data, c.due, c.ivl, ?, ?, ?) * 100.0"
        if load_fsrs
        else "null"
    )
    fsrs_args = [int(col.sched.today), int(col.sched.day_cutoff), int(time())] if load_fsrs else []
    rows = col.db.all(
        f"""
select
    c.id,
    c.nid,
    c.did,
    c.odid,
    c.queue,
    c.type,
    c.ivl,
    n.mid,
    c.ord,
    c.flags & 7,
    c.lapses,
    c.factor / 10.0,
    c.due,
    n.tags,
    {first_review} as first_review,
    {last_review} as last_review,
    {answer_count} as answer_count,
    {correct_count} as correct_answer_count,
    {stability} as fsrs_stability,
    {difficulty} as fsrs_difficulty,
    {retrievability} as fsrs_retrievability
from cards c
join notes n on n.id = c.nid
{history_join}
where {column} in ({placeholders}) {note_filter} {eligibility}
{grouping}
""",
        *fsrs_args,
        *ordered_values,
    )
    facts = [
        CardFacts(
            card_id=int(row[0]),
            note_id=int(row[1]),
            deck_id=int(row[2]),
            original_deck_id=int(row[3]),
            queue=int(row[4]),
            card_type=int(row[5]),
            interval=int(row[6]),
            note_type_id=int(row[7]),
            card_type_idx=int(row[8]),
            flag=int(row[9]),
            lapses=int(row[10]),
            sm2_ease_percent=float(row[11]) or None,
            overdue_days=(
                max(0, int(col.sched.today) - int(row[12]))
                if int(row[5]) == REVIEW_CARD_TYPE and int(row[12]) <= int(col.sched.today)
                else None
            ),
            tags=frozenset(tag.casefold() for tag in str(row[13]).split()),
            created_at_ms=int(row[0]),
            first_review_ms=int(row[14]) if row[14] is not None else None,
            last_review_ms=int(row[15]) if row[15] is not None else None,
            answer_count=int(row[16]),
            correct_answer_count=int(row[17]),
            fsrs_stability=float(row[18]) if row[18] is not None else None,
            fsrs_difficulty_percent=float(row[19]) if row[19] is not None else None,
            fsrs_retrievability_percent=float(row[20]) if row[20] is not None else None,
        )
        for row in rows
    ]
    if note_type_card_types is None:
        return facts
    return [
        card
        for card in facts
        if note_type_card_types[card.note_type_id] is None
        or card.card_type_idx in note_type_card_types[card.note_type_id]
    ]


def _load_deck_facts(
    col: Collection,
    deck_ids: set[int],
    note_type_card_types: dict[int, set[int] | None] | None = None,
    *,
    load_history: bool = True,
    load_fsrs: bool = False,
) -> list[CardFacts]:
    return _load_facts_where(
        col,
        "c.did",
        deck_ids,
        note_type_card_types=note_type_card_types,
        load_history=load_history,
        load_fsrs=load_fsrs,
        eligible_only=True,
    )


def evaluate_policy(col: Collection, policy: Policy, *, now_ms: int | None = None) -> PolicyReport:
    started = perf_counter()
    deck_ids, errors = _resolve_deck_ids(col, policy)
    resolved_actions, action_errors = _resolve_actions(col, policy)
    errors.extend(action_errors)
    note_type_card_types, note_type_errors = _resolve_note_type_card_types(col, policy)
    errors.extend(note_type_errors)
    needs_fsrs = conditions_need_fsrs(policy.conditions)
    needs_sm2 = conditions_need_sm2(policy.conditions)
    fsrs = _fsrs_enabled(col, deck_ids) if needs_fsrs or needs_sm2 else False
    if needs_fsrs and not fsrs:
        errors.append("FSRS conditions require FSRS to be enabled")
    if needs_sm2 and fsrs:
        errors.append("SM-2 ease conditions require FSRS to be disabled")
    if errors:
        report = PolicyReport(
            policy=policy,
            qualifying=(),
            actionable=(),
            missing_first_review=0,
            resolved_actions=resolved_actions,
            card_actions=(),
            errors=tuple(errors),
        )
        debug(
            "policy evaluation failed",
            policy_id=policy.id,
            errors=report.errors,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )
        return report

    load_history = conditions_need_history(policy.conditions)
    load_fsrs = conditions_need_fsrs(policy.conditions)
    facts = _load_deck_facts(
        col,
        deck_ids,
        note_type_card_types,
        load_history=load_history,
        load_fsrs=load_fsrs,
    )
    siblings = None
    note_facts = None
    if conditions_need_siblings(policy.conditions):
        siblings = _load_facts_where(
            col, "c.nid", {card.note_id for card in facts}, load_history=load_history
        )
        grouped: dict[int, list[CardFacts]] = {}
        for card in siblings:
            grouped.setdefault(card.note_id, []).append(card)
        note_facts = {
            note_id: NoteFacts(
                len(cards),
                sum(card.queue == -1 for card in cards),
                sum(card.first_review_ms is not None for card in cards),
            )
            for note_id, cards in grouped.items()
        }
    report = evaluate_facts(
        policy,
        facts,
        deck_ids,
        resolved_actions,
        now_ms=now_ms,
        note_facts=note_facts,
    )
    if any(action_expands_to_siblings(action.action) for action in resolved_actions):
        note_ids = {card.note_id for card in report.qualifying}
        siblings = (
            _load_facts_where(col, "c.nid", note_ids, load_history=load_history)
            if siblings is None
            else [card for card in siblings if card.note_id in note_ids]
        )
        trigger_ids = {card.card_id for card in report.qualifying}
        card_actions = tuple(
            (
                card,
                tuple(
                    action
                    for action in resolved_actions
                    if action_expands_to_siblings(action.action) or card.card_id in trigger_ids
                ),
            )
            for card in siblings
        )
        report = replace(
            report,
            card_actions=card_actions,
            actionable=tuple(
                card
                for card, actions in card_actions
                if any(not action_is_satisfied(action, card) for action in actions)
            ),
        )
    debug(
        "policy evaluated",
        policy_id=policy.id,
        deck_count=len(deck_ids),
        loaded_cards=len(facts),
        qualifying_cards=len(report.qualifying),
        actionable_cards=len(report.actionable),
        missing_first_review=report.missing_first_review,
        elapsed_ms=round((perf_counter() - started) * 1000, 2),
    )
    return report


def evaluate_policies(col: Collection, policies: tuple[Policy, ...]) -> tuple[PolicyReport, ...]:
    return tuple(evaluate_policy(col, policy) for policy in policies)
