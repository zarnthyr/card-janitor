# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import TYPE_CHECKING

from .engine import (
    CardFacts,
    NoteFacts,
    PolicyReport,
    ResolvedAction,
    action_is_satisfied,
    conditions_need_history,
    conditions_need_siblings,
    evaluate_facts,
)
from .log import debug
from .models import MoveAction, Policy, action_expands_to_siblings

if TYPE_CHECKING:
    from anki.collection import Collection


def _resolve_deck_ids(col: Collection, policy: Policy) -> tuple[set[int], list[str]]:
    if policy.scope.all_decks:
        return {int(deck.id) for deck in col.decks.all_names_and_ids(include_filtered=False)}, []
    deck_ids: set[int] = set()
    errors: list[str] = []
    for selector in policy.scope.selectors:
        name = selector.deck
        deck_id = col.decks.id_for_name(name)
        if deck_id is None:
            errors.append(f"scope deck does not exist: {name!r}")
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
                errors.append(f"move destination does not exist: {action.deck!r}")
                continue
            deck = col.decks.get(deck_id)
            if deck and deck.get("dyn"):
                errors.append(f"move destination is a filtered deck: {action.deck!r}")
                continue
            resolved.append(ResolvedAction(action, int(deck_id)))
        else:
            resolved.append(ResolvedAction(action))
    return tuple(resolved), errors


def _load_facts_where(
    col: Collection,
    column: str,
    values: set[int],
    *,
    note_type_ids: set[int] | None = None,
    load_history: bool = True,
    eligible_only: bool = False,
    include_suspended: bool = False,
) -> list[CardFacts]:
    if not values or note_type_ids == set():
        return []
    placeholders = ",".join("?" for _ in values)
    ordered_values = sorted(values)
    note_filter = ""
    if note_type_ids is not None:
        note_filter = f"and n.mid in ({','.join('?' for _ in note_type_ids)})"
        ordered_values.extend(sorted(note_type_ids))
    history_column = (
        "min(case when r.ease between 1 and 4 then r.id end)" if load_history else "null"
    )
    history_join = "left join revlog r on r.cid = c.id" if load_history else ""
    grouping = "group by c.id" if load_history else ""
    eligibility = "and c.odid = 0" if eligible_only else ""
    if eligible_only and not include_suspended:
        eligibility += " and c.queue != -1"
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
    n.tags,
    {history_column} as first_review
from cards c
join notes n on n.id = c.nid
{history_join}
where {column} in ({placeholders}) {note_filter} {eligibility}
{grouping}
""",
        *ordered_values,
    )
    return [
        CardFacts(
            card_id=int(row[0]),
            note_id=int(row[1]),
            deck_id=int(row[2]),
            original_deck_id=int(row[3]),
            queue=int(row[4]),
            card_type=int(row[5]),
            interval=int(row[6]),
            tags=frozenset(tag.casefold() for tag in str(row[7]).split()),
            created_at_ms=int(row[0]),
            first_review_ms=int(row[8]) if row[8] is not None else None,
        )
        for row in rows
    ]


def _load_deck_facts(
    col: Collection,
    deck_ids: set[int],
    note_type_ids: set[int] | None = None,
    *,
    load_history: bool = True,
    include_suspended: bool = False,
) -> list[CardFacts]:
    return _load_facts_where(
        col,
        "c.did",
        deck_ids,
        note_type_ids=note_type_ids,
        load_history=load_history,
        eligible_only=True,
        include_suspended=include_suspended,
    )


def evaluate_policy(col: Collection, policy: Policy, *, now_ms: int | None = None) -> PolicyReport:
    started = perf_counter()
    deck_ids, errors = _resolve_deck_ids(col, policy)
    resolved_actions, action_errors = _resolve_actions(col, policy)
    errors.extend(action_errors)
    note_type_ids = None
    if policy.scope.note_types is not None:
        note_type_ids = set()
        for name in policy.scope.note_types:
            note_type_id = col.models.id_for_name(name)
            if note_type_id is None:
                errors.append(f"scope note type does not exist: {name!r}")
            else:
                note_type_ids.add(int(note_type_id))
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
    facts = _load_deck_facts(
        col,
        deck_ids,
        note_type_ids,
        load_history=load_history,
        include_suspended=policy.scope.include_suspended,
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
