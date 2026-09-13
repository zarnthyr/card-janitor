# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from .engine import CardFacts, PolicyReport, ResolvedAction, evaluate_facts
from .log import debug
from .models import MoveAction, Policy

if TYPE_CHECKING:
    from anki.collection import Collection


def _resolve_deck_ids(col: Collection, policy: Policy) -> tuple[set[int], list[str]]:
    deck_ids: set[int] = set()
    errors: list[str] = []
    for name in policy.scope.decks:
        deck_id = col.decks.id_for_name(name)
        if deck_id is None:
            errors.append(f"scope deck does not exist: {name!r}")
            continue
        if policy.scope.include_subdecks:
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


def _load_facts(col: Collection, deck_ids: set[int]) -> list[CardFacts]:
    if not deck_ids:
        return []
    placeholders = ",".join("?" for _ in deck_ids)
    ordered_ids = sorted(deck_ids)
    rows = col.db.all(
        f"""
select
    c.id,
    c.nid,
    c.did,
    c.odid,
    c.queue,
    c.ivl,
    c.reps,
    n.tags,
    min(case when r.ease between 1 and 4 then r.id end) as first_review,
    sum(case when r.ease between 2 and 4 then 1 else 0 end) as successful_answers
from cards c
join notes n on n.id = c.nid
left join revlog r on r.cid = c.id
where (case when c.odid != 0 then c.odid else c.did end) in ({placeholders})
group by c.id
""",
        *ordered_ids,
    )
    return [
        CardFacts(
            card_id=int(row[0]),
            note_id=int(row[1]),
            deck_id=int(row[2]),
            original_deck_id=int(row[3]),
            queue=int(row[4]),
            interval=int(row[5]),
            answer_count=int(row[6]),
            tags=frozenset(tag.casefold() for tag in str(row[7]).split()),
            created_at_ms=int(row[0]),
            first_review_ms=int(row[8]) if row[8] is not None else None,
            successful_answers=int(row[9] or 0),
        )
        for row in rows
    ]


def evaluate_policy(col: Collection, policy: Policy, *, now_ms: int | None = None) -> PolicyReport:
    started = perf_counter()
    deck_ids, errors = _resolve_deck_ids(col, policy)
    resolved_actions, action_errors = _resolve_actions(col, policy)
    errors.extend(action_errors)
    if errors:
        report = PolicyReport(
            policy=policy,
            qualifying=(),
            actionable=(),
            missing_first_review=0,
            resolved_actions=resolved_actions,
            errors=tuple(errors),
        )
        debug(
            "policy evaluation failed",
            policy_id=policy.id,
            errors=report.errors,
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
        )
        return report
    facts = _load_facts(col, deck_ids)
    report = evaluate_facts(
        policy,
        facts,
        deck_ids,
        resolved_actions,
        now_ms=now_ms,
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
