# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aqt.qt import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
)

from .browsing import open_cards_in_browser
from .editor_utils import fit_initial_table_height
from .presentation import card_count_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from .actions import ExecutionPlan
    from .engine import PolicyReport


@dataclass(frozen=True)
class PreviewRow:
    card_id: int
    policies: tuple[str, ...]
    changes: tuple[str, ...]
    reasons: tuple[str, ...] = ()
    overlapping: bool = False
    expanded_sibling: bool = False


def build_preview_rows(  # noqa: PLR0912
    plan: ExecutionPlan,
    reports: tuple[PolicyReport, ...],
    deck_names: dict[int, str],
) -> tuple[PreviewRow, ...]:
    facts = {card.card_id: card for report in reports for card, _actions in report.card_actions}
    sources: dict[int, set[str]] = {}
    for report in reports:
        for card, _actions in report.card_actions:
            sources.setdefault(card.card_id, set()).add(report.policy.name)
    counts = Counter(card.card_id for report in reports for card in report.actionable)
    qualifying = {card.card_id for report in reports for card in report.qualifying}
    conflicts = {detail.card_id: detail for detail in plan.conflict_details}
    card_changes: dict[int, list[str]] = {}
    note_changes: dict[int, list[str]] = {}
    for groups, verb in ((plan.tags, "Add tag"), (plan.remove_tags, "Remove tag")):
        for tag, note_ids in groups:
            for note_id in note_ids:
                note_changes.setdefault(note_id, []).append(f"{verb} {tag!r}")
    for tags, note_ids in plan.replace_tags:
        description = ", ".join(repr(tag) for tag in tags) or "no tags"
        for note_id in note_ids:
            note_changes.setdefault(note_id, []).append(f"Replace all tags with {description}")
    for ids, description in (
        (plan.suspend_card_ids, "Suspend card"),
        (plan.unsuspend_card_ids, "Unsuspend card"),
        (plan.delete_card_ids, "Delete card"),
    ):
        for card_id in ids:
            card_changes.setdefault(card_id, []).append(description)
    for deck_id, card_ids in plan.moves:
        for card_id in card_ids:
            source = facts.get(card_id)
            destination = deck_names.get(deck_id, str(deck_id))
            origin = (
                deck_names.get(source.home_deck_id, str(source.home_deck_id))
                if source
                else "Unknown"
            )
            card_changes.setdefault(card_id, []).append(f"Move: {origin} → {destination}")
    for note_id in plan.delete_note_ids:
        note_changes.setdefault(note_id, []).append("Delete note and all its cards")
    rows = []
    for card_id in sorted(set(plan.planned_card_ids) | set(plan.conflicted_card_ids)):
        card = facts.get(card_id)
        conflict = conflicts.get(card_id)
        changes = card_changes.get(card_id, []) + note_changes.get(card.note_id if card else -1, [])
        rows.append(
            PreviewRow(
                card_id,
                conflict.policies if conflict else tuple(sorted(sources.get(card_id, ()))),
                () if conflict else tuple(changes),
                conflict.reasons if conflict else (),
                counts[card_id] > 1,
                card_id not in qualifying,
            )
        )
    return tuple(rows)


class CleanupPreviewDialog(QDialog):
    def __init__(
        self,
        rows: tuple[PreviewRow, ...],
        parent: QWidget,
        *,
        view: str = "planned",
        policy_name: str | None = None,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Card Janitor — Cleanup Preview")
        if policy_name is not None:
            self.setWindowTitle(
                f"Card Janitor — Cleanup Preview: {policy_name.strip() or 'Unnamed policy'}"
            )
        self.resize(960, 420)
        self._rows = rows
        self._single_policy = policy_name is not None
        self._is_current = is_current or (lambda: True)
        self._card_ids: set[int] = set()
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Preview of this policy. No changes have been applied."
            if policy_name is not None
            else "Preview of the checked policies. No changes have been applied.",
            self,
        )
        intro.setWordWrap(policy_name is None)
        controls = QHBoxLayout()
        if policy_name is None:
            layout.addWidget(intro)
            controls.addWidget(QLabel("View", self))
        else:
            controls.addWidget(intro)
        self.view = QComboBox(self)
        for label, key in (
            ("All affected", "all"),
            ("Planned changes", "planned"),
            ("Overlapping policies", "overlapping"),
            ("Conflicts", "conflicts"),
        ):
            self.view.addItem(label, key)
        if policy_name is None:
            controls.addWidget(self.view)
        else:
            self.view.hide()
        controls.addStretch()
        self.count = QLabel(self)
        controls.addWidget(self.count)
        layout.addLayout(controls)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["Card", "Policies", "Changes", "Status", "Reason"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        for column in (1, 2, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        metrics = self.table.fontMetrics()
        self.table.setColumnWidth(
            0,
            max(
                metrics.horizontalAdvance(text)
                for text in ("Card", *(str(row.card_id) for row in rows))
            )
            + 24,
        )
        self.table.setColumnWidth(
            3,
            max(metrics.horizontalAdvance(text) for text in ("Status", "Planned", "Skipped")) + 24,
        )
        qconnect(header.sectionResized, lambda *_args: self.table.resizeRowsToContents())
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self.browse_button = buttons.addButton("Browse", QDialogButtonBox.ButtonRole.ActionRole)
        self.browse_button.setToolTip(
            "Open selected cards, or all cards in this view if none are selected"
        )
        qconnect(self.browse_button.clicked, self._browse)
        qconnect(buttons.rejected, self.close)
        layout.addWidget(buttons)
        qconnect(self.view.currentIndexChanged, self._render)
        self.view.setCurrentIndex(self.view.findData(view if policy_name is None else "planned"))
        self._render()
        self._initial_table_height = fit_initial_table_height(self, self.table)
        self.setModal(False)
        self._context_timer = QTimer(self)
        qconnect(self._context_timer.timeout, self._check_context)
        self._context_timer.start(1000)
        qconnect(self.finished, self._context_timer.stop)
        if isinstance(parent, QDialog):
            qconnect(parent.finished, self.close)

    def _check_context(self) -> bool:
        if not self._is_current():
            self.close()
            return False
        return True

    def set_rows(self, rows: tuple[PreviewRow, ...]) -> None:
        self._rows = rows
        required_width = max(
            (self.table.fontMetrics().horizontalAdvance(str(row.card_id)) + 24 for row in rows),
            default=0,
        )
        if required_width > self.table.columnWidth(0):
            self.table.setColumnWidth(0, required_width)
        self._render()

    def _render(self, _index: int = 0) -> None:
        self.table.setColumnHidden(1, self._single_policy)
        self.table.setColumnHidden(3, self._single_policy)
        self.table.setColumnHidden(
            4,
            self._single_policy
            and not any(row.reasons or row.expanded_sibling for row in self._rows),
        )
        view = self.view.currentData()
        rows = [
            row
            for row in self._rows
            if (
                view == "all"
                or (view == "planned" and not row.reasons)
                or (view == "overlapping" and row.overlapping)
                or (view == "conflicts" and row.reasons)
            )
        ]
        self._card_ids = {row.card_id for row in rows}
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            status = "Skipped" if row.reasons else "Planned"
            reason = "\n".join(row.reasons)
            if row.expanded_sibling:
                reason = "\n".join(filter(None, (reason, "Included by note action")))
            for column, text in enumerate(
                (
                    str(row.card_id),
                    "\n".join(row.policies),
                    "\n".join(row.changes) or "None",
                    status,
                    reason,
                )
            ):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
        self.table.resizeRowsToContents()
        self.count.setText(card_count_text(len(rows)))
        self.browse_button.setEnabled(bool(rows))

    def _browse(self) -> None:
        if self._check_context() and self._card_ids:
            selected = {
                int(self.table.item(index.row(), 0).text())
                for index in self.table.selectionModel().selectedRows()
            }
            open_cards_in_browser(selected or self._card_ids, origin=self)
