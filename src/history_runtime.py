# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from .history_events import (
    CleanupEvent,
    CleanupEventContext,
    FailureRecord,
    Invocation,
    PolicyActivation,
    Producer,
    TerminalState,
    build_cleanup_event,
    build_failure_event,
    build_success_event,
)
from .history_semantics import NOT_COLLECTED_EXECUTION_LEDGER, PlanSemantics
from .history_store import HistoryStore, source_id_for_profile
from .log import exception
from .version import CARD_JANITOR_VERSION

if TYPE_CHECKING:
    from pathlib import Path

    from .actions import CleanupError, ExecutionResult
    from .history_semantics import ExecutionLedger
    from .models import Policy

AUDIT_GAP_MESSAGE = (
    "Card Janitor could not record this run in local cleanup history. "
    "The cleanup result and Anki Undo state were not changed by this history error."
)


@dataclass
class HistorySession:
    policies: tuple[Policy, ...]
    context: CleanupEventContext | None
    store: HistoryStore | None
    error_message: str | None = None
    terminal_recorded: bool = False

    @property
    def collect_history(self) -> bool:
        return self.context is not None and self.store is not None

    def _finished_context(self) -> CleanupEventContext:
        if self.context is None:
            message = "cleanup-history session is unavailable"
            raise RuntimeError(message)
        return replace(self.context, finished_at=datetime.now().astimezone())

    def _append(self, event: CleanupEvent) -> None:
        if not self.collect_history or self.terminal_recorded:
            return
        self.terminal_recorded = True
        try:
            self.store.append(event)
        except Exception:
            self.error_message = AUDIT_GAP_MESSAGE
            exception("cleanup history event could not be recorded")

    def record_success(self, result: ExecutionResult) -> None:
        if not self.collect_history:
            return
        try:
            event = build_success_event(
                context=self._finished_context(),
                policies=self.policies,
                result=result,
            )
        except Exception:
            self.terminal_recorded = True
            self.error_message = AUDIT_GAP_MESSAGE
            exception("cleanup history success event could not be constructed")
            return
        self._append(event)

    def record_cleanup_error(
        self,
        error: CleanupError,
        *,
        code: str,
        recovery: str | None = None,
    ) -> None:
        if not self.collect_history:
            return
        try:
            event = build_failure_event(
                context=self._finished_context(),
                policies=self.policies,
                error=error,
                code=code,
                recovery=recovery,
            )
        except Exception:
            self.terminal_recorded = True
            self.error_message = AUDIT_GAP_MESSAGE
            exception("cleanup history failure event could not be constructed")
            return
        self._append(event)

    def record_terminal(
        self,
        *,
        semantics: PlanSemantics,
        ledger: ExecutionLedger = NOT_COLLECTED_EXECUTION_LEDGER,
        status: str,
        stage: str,
        code: str | None = None,
        message: str | None = None,
        recovery: str | None = None,
    ) -> None:
        if not self.collect_history:
            return
        failure = (
            FailureRecord(code or "cleanup_failed", message or "Cleanup did not complete", recovery)
            if status != "succeeded"
            else None
        )
        try:
            event = build_cleanup_event(
                context=self._finished_context(),
                policies=self.policies,
                semantics=semantics,
                ledger=ledger,
                terminal=TerminalState(status, stage, failure),
            )
        except Exception:
            self.terminal_recorded = True
            self.error_message = AUDIT_GAP_MESSAGE
            exception("cleanup history terminal event could not be constructed")
            return
        self._append(event)


def prepare_history_session(
    *,
    enabled: bool,
    profile: dict[str, object],
    policies: tuple[Policy, ...],
    invocation: Invocation,
    activations: tuple[PolicyActivation, ...],
    anki_version: str,
    root: Path | None = None,
    started_at: datetime | None = None,
) -> HistorySession | None:
    if not enabled:
        return None
    try:
        started = started_at or datetime.now().astimezone()
        source_id = source_id_for_profile(profile)
        context = CleanupEventContext(
            event_id=str(uuid4()),
            source_id=source_id,
            producer=Producer(CARD_JANITOR_VERSION, anki_version),
            started_at=started,
            finished_at=started,
            invocation=invocation,
            activations=activations,
        )
        return HistorySession(
            policies,
            context,
            HistoryStore(source_id, root=root),
        )
    except Exception:
        exception("cleanup history session could not be prepared")
        return HistorySession(policies, None, None, error_message=AUDIT_GAP_MESSAGE)
