# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .engine import ResolvedAction

ProvenanceStatus = Literal["not_collected", "complete", "unavailable"]
DispositionCode = Literal[
    "planned",
    "already_satisfied",
    "conflict",
    "preview_no_longer_matching",
    "preview_newly_matching",
    "preview_note_boundary",
]
TargetKind = Literal["card", "note"]
SemanticsStatus = Literal["not_collected", "complete", "unavailable"]
LedgerStatus = Literal["not_collected", "complete", "partial", "unavailable"]
LedgerStepStatus = Literal[
    "completed",
    "completed_undo_merge_failed",
    "failed_before_mutation",
    "failed_unknown",
    "not_attempted_due_to_failure",
]


@dataclass(frozen=True)
class AnyNodeMatch:
    """All matching alternatives at one successful `any` condition node."""

    path: str
    matched_children: tuple[str, ...]


@dataclass(frozen=True)
class CardMatchProvenance:
    card_id: int
    any_nodes: tuple[AnyNodeMatch, ...]


@dataclass(frozen=True)
class PolicyMatchProvenance:
    status: ProvenanceStatus
    cards: tuple[CardMatchProvenance, ...] = ()
    reason_code: str | None = None


NOT_COLLECTED_PROVENANCE = PolicyMatchProvenance("not_collected")


@dataclass(frozen=True)
class BoundaryDisposition:
    """A Preview-to-execution candidate deliberately excluded before planning."""

    code: Literal[
        "preview_no_longer_matching",
        "preview_newly_matching",
        "preview_note_boundary",
    ]
    card_id: int
    note_id: int
    actions: tuple[ResolvedAction, ...]


@dataclass(frozen=True)
class LogicalIntention:
    policy_id: str
    action: ResolvedAction
    target_kind: TargetKind
    target_id: int
    target_card_id: int
    target_note_id: int
    trigger_card_ids: tuple[int, ...]
    disposition: DispositionCode
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectContributor:
    policy_id: str
    trigger_card_ids: tuple[int, ...]
    matches: tuple[CardMatchProvenance, ...]


@dataclass(frozen=True)
class LogicalEffect:
    """One planned logical effect on one card or note target."""

    action: ResolvedAction
    target_kind: TargetKind
    target_id: int
    affected_card_ids: tuple[int, ...]
    affected_card_ids_complete: bool
    matching_trigger_card_ids: tuple[int, ...]
    consequential_sibling_card_ids: tuple[int, ...]
    contributors: tuple[EffectContributor, ...]


@dataclass(frozen=True)
class PlanSemantics:
    status: SemanticsStatus
    intentions: tuple[LogicalIntention, ...] = ()
    effects: tuple[LogicalEffect, ...] = ()
    policy_provenance: tuple[tuple[str, PolicyMatchProvenance], ...] = ()
    reason_code: str | None = None

    @property
    def non_applied(self) -> tuple[LogicalIntention, ...]:
        return tuple(item for item in self.intentions if item.disposition != "planned")


NOT_COLLECTED_PLAN_SEMANTICS = PlanSemantics("not_collected")


@dataclass(frozen=True)
class ExecutionLedgerTarget:
    value: str | int | tuple[str, ...] | None
    target_ids: tuple[int, ...]


@dataclass(frozen=True)
class ExecutionLedgerStep:
    sequence: int
    operation: str
    target_kind: TargetKind
    targets: tuple[ExecutionLedgerTarget, ...]
    status: LedgerStepStatus


@dataclass(frozen=True)
class ExecutionLedger:
    status: LedgerStatus
    steps: tuple[ExecutionLedgerStep, ...]
    effects_complete: bool
    unknown_effects_possible: bool
    reason_code: str | None = None


EMPTY_EXECUTION_LEDGER = ExecutionLedger(
    status="complete",
    steps=(),
    effects_complete=True,
    unknown_effects_possible=False,
)

NOT_COLLECTED_EXECUTION_LEDGER = ExecutionLedger(
    status="not_collected",
    steps=(),
    effects_complete=False,
    unknown_effects_possible=False,
)
