# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import contextlib
import itertools
from collections import Counter
from dataclasses import dataclass, replace
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import unquote
from uuid import uuid4

from aqt import mw
from aqt.operations import CollectionOp, QueryOp
from aqt.qt import (
    QAbstractItemView,
    QAction,
    QDialog,
    QDialogButtonBox,
    QEvent,
    QHBoxLayout,
    QHeaderView,
    QKeyEvent,
    QLabel,
    QPainter,
    QPushButton,
    QRect,
    QSignalBlocker,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionButton,
    Qt,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTimer,
    QVBoxLayout,
    QWidget,
    qconnect,
)
from aqt.utils import askUser, showText, showWarning, tooltip

from .actions import ExecutionResult, build_execution_plan
from .browsing import open_cards_in_browser
from .cleanup_preview import CleanupPreviewDialog, build_preview_rows
from .configuration import (
    ConfigWriteError,
    load_config,
    remove_policy,
    save_policy,
)
from .editor_utils import fit_initial_table_height
from .evaluator import evaluate_policies
from .execution import execute_approved_reports
from .json_editor import open_policy_json
from .log import configure as configure_logging
from .log import debug, error, exception
from .policy_editor import PolicyEditorDialog
from .presentation import (
    applied_message,
    card_count_text,
    configuration_error_text,
    configured_triggers,
    describe_actions,
    describe_conditions,
    describe_scope,
    last_cleanup,
    policy_tooltip,
    record_cleanup,
    scope_tooltip,
    triggers_tooltip,
    warning_panel,
)
from .settings_dialog import SettingsDialog

if TYPE_CHECKING:
    from anki.collection import Collection

    from .engine import PolicyReport
    from .models import ParsedConfig, Policy, PolicyRecord

MENU_ATTR = "_card_janitor_action"
ON_DEMAND_DIALOG_ATTR = "_card_janitor_dialog"


def _load_configured() -> ParsedConfig:
    parsed = load_config()
    configure_logging(debug_logging=parsed.config.debug_logging)
    return parsed


def _load_for_operation(parent: object = mw) -> ParsedConfig | None:
    parsed = _load_configured()
    if parsed.issues:
        error("invalid configuration", issues=tuple(str(issue) for issue in parsed.issues))
        showWarning(configuration_error_text(parsed.issues), parent=parent)
        return None
    return parsed


def _raw_policy_name(raw: object) -> str:
    if isinstance(raw, dict) and isinstance(raw.get("name"), str):
        return raw["name"]
    return ""


@dataclass(frozen=True)
class DashboardRow:
    record: PolicyRecord
    report: PolicyReport | None


class CheckBoxHeader(QHeaderView):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._check_state = Qt.CheckState.Unchecked
        self._check_enabled = False
        self.setSectionsClickable(True)

    @property
    def check_state(self) -> Qt.CheckState:
        return self._check_state

    def set_check_state(self, state: Qt.CheckState, *, enabled: bool) -> None:
        if self._check_state == state and self._check_enabled == enabled:
            return
        self._check_state = state
        self._check_enabled = enabled
        self.viewport().update()

    def paintSection(  # noqa: N802
        self,
        painter: QPainter,
        rect: QRect,
        logical_index: int,
    ) -> None:
        super().paintSection(painter, rect, logical_index)
        if logical_index != CardJanitorDialog.COLUMN_RUN:
            return

        option = QStyleOptionButton()
        option.initFrom(self)
        if not self._check_enabled:
            option.state &= ~QStyle.StateFlag.State_Enabled
        if self._check_state == Qt.CheckState.Checked:
            option.state |= QStyle.StateFlag.State_On
        elif self._check_state == Qt.CheckState.PartiallyChecked:
            option.state |= QStyle.StateFlag.State_NoChange
        else:
            option.state |= QStyle.StateFlag.State_Off

        style = self.style()
        width = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth, option, self)
        height = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorHeight, option, self)
        option.rect = QRect(
            rect.x() + (rect.width() - width) // 2,
            rect.y() + (rect.height() - height) // 2,
            width,
            height,
        )
        style.drawControl(QStyle.ControlElement.CE_CheckBox, option, painter, self)


class CardJanitorDialog(QDialog):
    COLUMN_RUN = 0
    COLUMN_POLICY = 1
    COLUMN_TRIGGERS = 2
    COLUMN_SCOPE = 3
    COLUMN_CONDITIONS = 4
    COLUMN_ACTIONS = 5
    COLUMN_COUNT = 6

    def __init__(self, parsed: ParsedConfig, reports: tuple[PolicyReport, ...]) -> None:
        super().__init__(mw)
        self._rows: tuple[DashboardRow, ...] = ()
        self._parsed = parsed
        self._running = False
        self._columns_initialized = False
        self._policy_editor: PolicyEditorDialog | None = None
        self._preview_dialog: CleanupPreviewDialog | None = None
        self._preview_collection = mw.col
        self._preview_profile = mw.pm.profile
        self._cleanup_details: QDialog | None = None
        self._editor_widget_states: list[tuple[QWidget, bool]] = []
        self.setWindowTitle("Card Janitor")
        self.resize(1050, 420)

        layout = QVBoxLayout(self)
        intro = QHBoxLayout()
        intro.addWidget(
            QLabel("Manage policies for cleaning up cards automatically or on demand", self)
        )
        intro.addStretch()
        self.add_button = QPushButton("Add…", self)
        self.add_button.setToolTip("Create a cleanup policy")
        self.edit_button = QPushButton("Edit…", self)
        self.edit_button.setToolTip("Edit the highlighted policy")
        self.duplicate_button = QPushButton("Duplicate…", self)
        self.duplicate_button.setToolTip("Create a copy of the highlighted policy")
        qconnect(self.duplicate_button.clicked, self._duplicate_policy)
        self.remove_button = QPushButton("Remove…", self)
        self.remove_button.setToolTip("Remove the highlighted policy")
        intro.addWidget(self.add_button)
        intro.addWidget(self.edit_button)
        intro.addWidget(self.duplicate_button)
        intro.addWidget(self.remove_button)
        layout.addLayout(intro)

        self.table = QTableWidget(0, 7, self)
        self.table_header = CheckBoxHeader(self.table)
        self.table.setHorizontalHeader(self.table_header)
        self.table.setHorizontalHeaderLabels(
            ("", "Policy", "Trigger", "Scope", "Conditions", "Actions", "Cards")
        )
        self.table.horizontalHeaderItem(self.COLUMN_RUN).setToolTip(
            "Include or exclude all valid policies"
        )
        self.table.horizontalHeaderItem(self.COLUMN_COUNT).setToolTip(
            "Cards requiring an action, including affected siblings outside scope"
        )
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setTabKeyNavigation(False)
        self.table.installEventFilter(self)
        self.table.viewport().installEventFilter(self)
        vertical_header = self.table.verticalHeader()
        vertical_header.setVisible(False)
        vertical_header.setMinimumSectionSize(self.fontMetrics().height() * 2 + 12)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COLUMN_COUNT, QHeaderView.ResizeMode.Interactive)
        qconnect(self.table.itemChanged, self._on_item_changed)
        qconnect(self.table_header.sectionClicked, self._on_header_clicked)
        qconnect(self.table.itemSelectionChanged, self._update_buttons)
        qconnect(self.table.itemDoubleClicked, self._on_item_double_clicked)
        qconnect(self.add_button.clicked, self._add_policy)
        qconnect(self.edit_button.clicked, self._edit_policy)
        qconnect(self.remove_button.clicked, self._remove_policy)

        self.empty_page = QWidget(self)
        empty_layout = QVBoxLayout(self.empty_page)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.addStretch()
        empty_title = QLabel("<b>No policies yet</b>", self.empty_page)
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_description = QLabel(
            "Add a policy to define which cards Card Janitor should clean up",
            self.empty_page,
        )
        empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_description.setWordWrap(True)
        empty_layout.addWidget(empty_description)
        empty_layout.addSpacing(8)
        self.empty_add_button = QPushButton("Add Policy…", self.empty_page)
        self.empty_add_button.setToolTip("Create a cleanup policy")
        qconnect(self.empty_add_button.clicked, self._add_policy)
        empty_layout.addWidget(
            self.empty_add_button,
            alignment=Qt.AlignmentFlag.AlignHCenter,
        )
        empty_layout.addStretch()

        self.content_stack = QStackedWidget(self)
        self.content_stack.addWidget(self.table)
        self.content_stack.addWidget(self.empty_page)
        layout.addWidget(self.content_stack)

        summary_panel = QWidget(self)
        summary_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        summary_layout = QVBoxLayout(summary_panel)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(0)
        self.summary = QLabel(summary_panel)
        self.summary.setWordWrap(True)
        summary_layout.addWidget(self.summary)
        self.conflict_summary = QLabel(summary_panel)
        summary_layout.addWidget(self.conflict_summary)
        self.cleanup_status = QLabel(summary_panel)
        self.cleanup_status.setContentsMargins(0, 6, 0, 0)
        self.cleanup_status.setWordWrap(True)
        self.cleanup_status.setOpenExternalLinks(False)
        qconnect(self.cleanup_status.linkActivated, self._show_cleanup_status)
        summary_layout.addWidget(self.cleanup_status)
        self._cleanup_status_timer = QTimer(self)
        qconnect(self._cleanup_status_timer.timeout, self._update_cleanup_status)
        self._cleanup_status_timer.start(1000)
        qconnect(self.finished, lambda _result: self._cleanup_status_timer.stop())
        footer = QWidget(self)
        footer.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(12)
        footer_layout.addWidget(summary_panel)
        layout.addWidget(footer)
        self.automatic_disabled = warning_panel("Automatic cleanup is disabled", self)
        layout.insertWidget(1, self.automatic_disabled)
        qconnect(self.finished, lambda _result: self._close_preview())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self.settings_button = buttons.addButton(
            "Settings…",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.settings_button.setToolTip("Configure Card Janitor")
        self.json_button = buttons.addButton(
            "Edit as JSON…",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.json_button.setToolTip("Edit this collection's policies as JSON")
        self.refresh_button = buttons.addButton(
            "Refresh",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.refresh_button.setToolTip("Recalculate the card counts")
        self.view_button = buttons.addButton(
            "Browse",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        self.view_button.setToolTip("Open cards from the checked policies in Anki's Browser")
        self.preview_button = QPushButton("Preview…", self)
        self.preview_button.setAutoDefault(False)
        self.preview_button.setToolTip("Preview merged changes from the checked policies")
        qconnect(self.preview_button.clicked, lambda: self._show_preview("planned"))
        self.run_button = QPushButton("Clean Up", self)
        self.run_button.setToolTip("Apply the actions from the checked policies")
        if isinstance(self.run_button, QPushButton):
            self.run_button.setDefault(True)
        qconnect(self.settings_button.clicked, self._open_settings)
        qconnect(self.json_button.clicked, self._open_policy_json)
        qconnect(self.refresh_button.clicked, self._refresh)
        qconnect(self.view_button.clicked, self._view_included)
        qconnect(self.run_button.clicked, self._run)
        qconnect(buttons.rejected, self.close)
        button_row = QHBoxLayout()
        button_row.addWidget(buttons, stretch=1)
        button_row.addWidget(self.preview_button)
        button_row.addWidget(self.run_button)
        footer_layout.addLayout(button_row)
        self.close_button = buttons.button(QDialogButtonBox.StandardButton.Close)

        tab_widgets = (
            self.add_button,
            self.edit_button,
            self.duplicate_button,
            self.remove_button,
            self.table,
            self.empty_add_button,
            self.settings_button,
            self.json_button,
            self.refresh_button,
            self.view_button,
            self.close_button,
            self.preview_button,
            self.run_button,
        )
        for current, following in itertools.pairwise(tab_widgets):
            QWidget.setTabOrder(current, following)

        self.set_dashboard(parsed, reports)
        self._initial_table_height = fit_initial_table_height(self, self.table)
        QTimer.singleShot(0, self._focus_initial)

    def _focus_initial(self) -> None:
        if self._rows:
            self.table.setFocus()
        else:
            self.empty_add_button.setFocus()

    def _enable_column_resizing(self) -> None:
        header = self.table.horizontalHeader()
        columns = (
            self.COLUMN_POLICY,
            self.COLUMN_TRIGGERS,
            self.COLUMN_SCOPE,
            self.COLUMN_CONDITIONS,
        )
        widths = {column: header.sectionSize(column) for column in columns}
        for column in columns:
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            header.resizeSection(column, widths[column])
        self.table.resizeRowsToContents()

    def _reset_column_sizing(self) -> None:
        header = self.table.horizontalHeader()
        self.table.setColumnWidth(
            self.COLUMN_COUNT, self.table.fontMetrics().horizontalAdvance("Cards") + 28
        )
        for column in (self.COLUMN_RUN, self.COLUMN_TRIGGERS):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (
            self.COLUMN_POLICY,
            self.COLUMN_SCOPE,
            self.COLUMN_CONDITIONS,
            self.COLUMN_ACTIONS,
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        QTimer.singleShot(0, self._enable_column_resizing)

    def set_dashboard(
        self,
        parsed: ParsedConfig,
        reports: tuple[PolicyReport, ...],
        checked_keys: set[str] | None = None,
        known_keys: set[str] | None = None,
    ) -> None:
        definitions_changed = not self._columns_initialized or (
            tuple(record.raw for record in self._parsed.policy_records)
            != tuple(record.raw for record in parsed.policy_records)
        )
        self._parsed = parsed
        report_by_id = {report.policy.id: report for report in reports}
        self._rows = tuple(
            DashboardRow(
                record,
                report_by_id.get(record.policy.id) if record.policy is not None else None,
            )
            for record in parsed.policy_records
        )
        signal_blocker = QSignalBlocker(self.table)
        self.table.setRowCount(len(self._rows))
        for row, dashboard_row in enumerate(self._rows):
            record = dashboard_row.record
            policy = record.policy
            use_config_default = checked_keys is None or (
                known_keys is not None and record.key not in known_keys
            )
            included = policy is not None if use_config_default else record.key in checked_keys
            run_item = QTableWidgetItem()
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if policy is not None:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            run_item.setFlags(flags)
            run_item.setCheckState(Qt.CheckState.Checked if included else Qt.CheckState.Unchecked)
            self.table.setItem(row, self.COLUMN_RUN, run_item)
            if policy is None:
                raw = record.raw if isinstance(record.raw, dict) else {}
                values = (
                    _raw_policy_name(raw) or f"Invalid policy {record.index + 1}",
                    "Invalid",
                    "—",
                    "—",
                    "—",
                    "Error",
                )
            else:
                report = dashboard_row.report
                values = (
                    policy.name,
                    configured_triggers(policy),
                    describe_scope(policy.scope),
                    describe_conditions(policy.conditions),
                    describe_actions(policy.actions),
                    "Error" if report is None or report.errors else str(len(report.actionable)),
                )
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                if column == self.COLUMN_COUNT:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)
            if policy is None:
                error_text = "\n".join(str(issue) for issue in record.issues)
                self.table.item(row, self.COLUMN_COUNT).setToolTip(error_text)
                self.table.item(row, self.COLUMN_POLICY).setToolTip(error_text)
            else:
                self.table.item(row, self.COLUMN_POLICY).setToolTip(policy_tooltip(policy))
                self.table.item(row, self.COLUMN_TRIGGERS).setToolTip(triggers_tooltip(policy))
                self.table.item(row, self.COLUMN_SCOPE).setToolTip(scope_tooltip(policy.scope))
                if dashboard_row.report and dashboard_row.report.errors:
                    error_text = "\n".join(dashboard_row.report.errors)
                    self.table.item(row, self.COLUMN_COUNT).setToolTip(error_text)
        del signal_blocker
        if definitions_changed:
            self._reset_column_sizing()
            self._columns_initialized = True
        self.table.resizeRowsToContents()
        QTimer.singleShot(0, self.table.resizeRowsToContents)
        if self._rows:
            self.table.selectRow(0)
        self.content_stack.setCurrentWidget(self.table if self._rows else self.empty_page)
        self._sync_header_check_state()
        self._update_summary()
        self._update_cleanup_status()
        self.automatic_disabled.setVisible(not parsed.config.automatic_cleanup_enabled)
        self._update_buttons()

    def checked_keys(self) -> set[str]:
        return {
            dashboard_row.record.key
            for row, dashboard_row in enumerate(self._rows)
            if self.table.item(row, self.COLUMN_RUN).checkState() == Qt.CheckState.Checked
        }

    def row_keys(self) -> set[str]:
        return {row.record.key for row in self._rows}

    def checked_reports(self) -> tuple[PolicyReport, ...]:
        checked = self.checked_keys()
        return tuple(
            row.report for row in self._rows if row.record.key in checked and row.report is not None
        )

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == self.COLUMN_RUN:
            self._sync_header_check_state()
            self._update_summary()

    def _on_header_clicked(self, logical_index: int) -> None:
        if logical_index != self.COLUMN_RUN or not self._rows:
            return
        target = (
            Qt.CheckState.Unchecked
            if self.table_header.check_state == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        signal_blocker = QSignalBlocker(self.table)
        for row in range(len(self._rows)):
            item = self.table.item(row, self.COLUMN_RUN)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(target)
        del signal_blocker
        self._sync_header_check_state()
        self._update_summary()

    def _sync_header_check_state(self) -> None:
        items = [
            self.table.item(row, self.COLUMN_RUN)
            for row in range(len(self._rows))
            if self.table.item(row, self.COLUMN_RUN).flags() & Qt.ItemFlag.ItemIsUserCheckable
        ]
        checked = sum(item.checkState() == Qt.CheckState.Checked for item in items)
        if not checked:
            state = Qt.CheckState.Unchecked
        elif checked == len(items):
            state = Qt.CheckState.Checked
        else:
            state = Qt.CheckState.PartiallyChecked
        self.table_header.set_check_state(state, enabled=bool(items))

    def _on_item_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() != self.COLUMN_RUN:
            self._edit_policy()

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if (
            watched not in (self.table, self.table.viewport())
            or event.type() != QEvent.Type.KeyPress
            or not isinstance(event, QKeyEvent)
        ):
            return super().eventFilter(watched, event)
        if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return):
            self._edit_policy()
            return True
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._remove_policy()
            return True
        if event.key() == Qt.Key.Key_Space:
            row = self.table.currentRow()
            if 0 <= row < len(self._rows):
                item = self.table.item(row, self.COLUMN_RUN)
                if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    checked = item.checkState() == Qt.CheckState.Checked
                    item.setCheckState(
                        Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
                    )
            return True
        return super().eventFilter(watched, event)

    def _update_summary(self) -> None:
        self.preview_button.setEnabled(False)
        self.conflict_summary.hide()
        self.summary.setVisible(True)
        reports = self.checked_reports()
        if self._parsed.issues:
            self._close_preview()
            self.summary.setText(
                "Configuration needs repair before cleaning up:\n"
                + "\n".join(f"• {issue}" for issue in self._parsed.issues)
            )
            self.view_button.setEnabled(False)
            self.run_button.setEnabled(False)
            return
        if not self._rows:
            self._close_preview()
            self.summary.setVisible(False)
            self.view_button.setEnabled(False)
            self.run_button.setEnabled(False)
            return
        if not reports:
            self._close_preview()
            messages = ["No policies are selected for cleanup"]
            if any(row.record.policy is None for row in self._rows):
                messages.append("Edit policies marked Invalid to repair them")
            self.summary.setText(messages[0] if len(messages) == 1 else ". ".join(messages) + ".")
            self.view_button.setEnabled(False)
            self.run_button.setEnabled(False)
            return
        errors = [error for report in reports for error in report.errors]
        match_counts = Counter(card.card_id for report in reports for card in report.actionable)
        candidate_ids = set(match_counts)
        overlap_count = sum(count > 1 for count in match_counts.values())
        plan = build_execution_plan(reports, mw.col)
        text = f"{card_count_text(plan.card_count).capitalize()} would be cleaned up."
        messages = [text]
        if overlap_count:
            verb = "is" if overlap_count == 1 else "are"
            text = f"{card_count_text(overlap_count).capitalize()} {verb} affected by more than one policy."
            messages.append(text)
        if plan.conflicted_card_ids:
            conflict_count = len(plan.conflicted_card_ids)
            text = f"{card_count_text(conflict_count).capitalize()} with conflicting actions would be skipped."
            self.conflict_summary.setText(text)
            self.conflict_summary.show()
        if errors:
            messages.append(
                "A checked policy has an error. Uncheck it or fix the configuration before cleaning up."
            )
            self._close_preview()
        elif self._preview_dialog is not None:
            self._preview_dialog.set_rows(build_preview_rows(plan, reports, self._preview_decks()))
        self.summary.setText("\n".join(messages))
        self.preview_button.setEnabled(not errors)
        self.view_button.setEnabled(bool(candidate_ids))
        self.run_button.setEnabled(plan.card_count > 0 and not errors)

    def _close_preview(self) -> None:
        if self._preview_dialog is not None:
            self._preview_dialog.close()
            self._preview_dialog.deleteLater()
            self._preview_dialog = None

    def _show_preview(self, _link: str) -> None:
        if mw.col is not self._preview_collection or mw.pm.profile is not self._preview_profile:
            self._close_preview()
            return
        if self._policy_editor is not None:
            return
        if self._preview_dialog is not None and self._preview_dialog.isVisible():
            self._preview_dialog.view.setCurrentIndex(self._preview_dialog.view.findData(_link))
            self._preview_dialog.raise_()
            self._preview_dialog.activateWindow()
            return
        self._close_preview()
        plan = build_execution_plan(self.checked_reports(), mw.col)
        if any(report.errors for report in self.checked_reports()) or self._parsed.issues:
            return
        self._preview_dialog = CleanupPreviewDialog(
            build_preview_rows(plan, self.checked_reports(), self._preview_decks()),
            self,
            view=_link,
            is_current=lambda: (
                mw.col is self._preview_collection and mw.pm.profile is self._preview_profile
            ),
        )
        self._preview_dialog.show()
        self._preview_dialog.raise_()
        self._preview_dialog.activateWindow()

    def _preview_decks(self) -> dict[int, str]:
        return {int(deck.id): deck.name for deck in mw.col.decks.all_names_and_ids()}

    def _view_included(self) -> None:
        card_ids = {card.card_id for report in self.checked_reports() for card in report.actionable}
        if card_ids:
            open_cards_in_browser(card_ids, origin=self)

    def _update_cleanup_status(self) -> None:
        status = last_cleanup(mw.pm.profile or {})
        self.cleanup_status.setVisible(status is not None)
        if status:
            self.cleanup_status.setText(f'<a href="cleanup">{escape(status[0])}</a>')

    def _show_cleanup_status(self, _link: str) -> None:
        if self._cleanup_details is not None and self._cleanup_details.isVisible():
            self._cleanup_details.raise_()
            self._cleanup_details.activateWindow()
            return
        parsed = _load_configured()
        ids = {record.policy.id for record in parsed.policy_records if record.policy is not None}
        status = last_cleanup(mw.pm.profile or {}, existing_policy_ids=ids)
        if status:
            result = showText(
                status[1], parent=self, type="html", title="Last Cleanup", minHeight=300, run=False
            )
            if result:
                dialog, _buttons = result
                self._cleanup_details = dialog
                qconnect(self.finished, dialog.close)
                browser = dialog.findChild(QTextBrowser)
                browser.setOpenLinks(False)
                browser.setOpenExternalLinks(False)

                def open_policy(url: object) -> None:
                    link = url.toString()
                    if not link.startswith("policy:"):
                        return
                    policy_id = unquote(link.removeprefix("policy:"))
                    record = next(
                        (
                            item
                            for item in _load_configured().policy_records
                            if item.policy is not None and item.policy.id == policy_id
                        ),
                        None,
                    )
                    if record is not None:
                        self._open_editor(record)
                        if self._policy_editor is not None:

                            def return_to_details(_result: int) -> None:
                                if dialog.isVisible() and self.isVisible():
                                    dialog.raise_()
                                    dialog.activateWindow()

                            qconnect(self._policy_editor.finished, return_to_details)

                qconnect(browser.anchorClicked, open_policy)
                dialog.setModal(False)
                dialog.show()
                dialog.raise_()
                dialog.activateWindow()

    def _refresh(self) -> None:
        refresh_on_demand_dialog(self)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._parsed.config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _open_policy_json(self) -> None:
        open_policy_json(parent=self, on_close=self._refresh)

    def _run(self) -> None:
        if self._running:
            return
        self._running = True
        self.run_button.setEnabled(False)
        execute_on_demand_reports(self, self.checked_reports())

    def _selected_record(self) -> PolicyRecord | None:
        row = self.table.currentRow()
        return self._rows[row].record if 0 <= row < len(self._rows) else None

    def _update_buttons(self) -> None:
        record = self._selected_record()
        has_selection = record is not None
        self.duplicate_button.setEnabled(record is not None and record.policy is not None)
        self.edit_button.setEnabled(has_selection)
        self.remove_button.setEnabled(has_selection)

    def _add_policy(self) -> None:
        self._open_editor(None)

    def _edit_policy(self) -> None:
        record = self._selected_record()
        if record is not None:
            self._open_editor(record)

    def _duplicate_policy(self) -> None:
        record = self._selected_record()
        if record is not None and record.policy is not None:
            policy = replace(record.policy, id=uuid4().hex, name=record.policy.name + " (copy)")
            self._open_editor(None, initial_policy=policy)

    def _remove_policy(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        raw = record.raw if isinstance(record.raw, dict) else {}
        name = record.policy.name if record.policy else _raw_policy_name(raw)
        name = name or f"Invalid policy {record.index + 1}"
        if not askUser(
            f"Remove {name!r}?\n\nThis will not change any cards.",
            parent=self,
            defaultno=True,
            title="Card Janitor",
        ):
            return
        try:
            remove_policy(record=record)
        except ConfigWriteError as exc:
            error("failed to remove policy", policy_name=name, reason=str(exc))
            showWarning(str(exc), parent=self)
            return
        debug("policy removed", policy_name=name)
        self._refresh()

    def _open_editor(
        self, record: PolicyRecord | None, *, initial_policy: Policy | None = None
    ) -> None:
        if self._policy_editor is not None and self._policy_editor.isVisible():
            self._policy_editor.raise_()
            self._policy_editor.activateWindow()
            return
        excluded_id = record.policy.id.casefold() if record and record.policy else None
        existing_ids = {
            item.policy.id.casefold()
            for item in self._parsed.policy_records
            if item.policy is not None and item.policy.id.casefold() != excluded_id
        }
        editor = PolicyEditorDialog(record, existing_ids, self, initial_policy=initial_policy)
        self._policy_editor = editor
        self._set_editor_controls_enabled(enabled=False)
        qconnect(
            editor.finished,
            lambda result: self._finish_editor(editor, record, result),
        )
        editor.setModal(False)
        editor.show()
        editor.raise_()
        editor.activateWindow()

    def _set_editor_controls_enabled(self, enabled: bool) -> None:
        widgets = (
            self.table,
            self.conflict_summary,
            self.add_button,
            self.edit_button,
            self.duplicate_button,
            self.remove_button,
            self.empty_add_button,
            self.settings_button,
            self.json_button,
            self.refresh_button,
            self.view_button,
            self.preview_button,
            self.close_button,
            self.run_button,
        )
        if not enabled:
            self._editor_widget_states = [(widget, widget.isEnabled()) for widget in widgets]
            for widget, _was_enabled in self._editor_widget_states:
                widget.setEnabled(False)
            return
        for widget, was_enabled in self._editor_widget_states:
            widget.setEnabled(was_enabled)
        self._editor_widget_states = []

    def _finish_editor(
        self,
        editor: PolicyEditorDialog,
        record: PolicyRecord | None,
        result: int,
    ) -> None:
        if self._policy_editor is editor:
            self._policy_editor = None
        self._set_editor_controls_enabled(enabled=True)
        policy = editor.result_policy
        editor.deleteLater()
        if result != QDialog.DialogCode.Accepted or policy is None:
            self.raise_()
            self.activateWindow()
            return
        try:
            save_policy(policy, record=record)
        except ConfigWriteError as exc:
            error(
                "failed to save policy",
                policy_id=policy.id,
                reason=str(exc),
            )
            showWarning(str(exc), parent=self)
            return
        debug(
            "policy saved",
            policy_id=policy.id,
            operation="updated" if record is not None else "added",
        )
        self._refresh()


def _show_on_demand_dialog(parsed: ParsedConfig, reports: tuple[PolicyReport, ...]) -> None:
    dialog = CardJanitorDialog(parsed, reports)
    setattr(mw, ON_DEMAND_DIALOG_ATTR, dialog)

    def clear_reference(_result: int) -> None:
        if getattr(mw, ON_DEMAND_DIALOG_ATTR, None) is dialog:
            setattr(mw, ON_DEMAND_DIALOG_ATTR, None)

    qconnect(dialog.finished, clear_reference)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


def open_card_janitor() -> None:
    existing = getattr(mw, ON_DEMAND_DIALOG_ATTR, None)
    if isinstance(existing, CardJanitorDialog) and existing.isVisible():
        existing.raise_()
        existing.activateWindow()
        return
    parsed = _load_configured()
    policies = parsed.config.policies
    collection = mw.col

    def on_success(reports: tuple[PolicyReport, ...]) -> None:
        if mw.col is collection:
            _show_on_demand_dialog(parsed, reports)

    QueryOp(
        parent=mw,
        op=lambda col: evaluate_policies(col, policies),
        success=on_success,
    ).run_in_background()


def refresh_on_demand_dialog(dialog: CardJanitorDialog) -> None:
    parsed = _load_configured()
    checked = dialog.checked_keys()
    known = dialog.row_keys()
    policies = parsed.config.policies

    def on_success(reports: tuple[PolicyReport, ...]) -> None:
        if getattr(mw, ON_DEMAND_DIALOG_ATTR, None) is dialog and dialog.isVisible():
            dialog.set_dashboard(parsed, reports, checked, known)

    QueryOp(
        parent=dialog,
        op=lambda col: evaluate_policies(col, policies),
        success=on_success,
    ).run_in_background()


def close_card_janitor() -> None:
    dialog = getattr(mw, ON_DEMAND_DIALOG_ATTR, None)
    if isinstance(dialog, CardJanitorDialog):
        dialog.close()
    setattr(mw, ON_DEMAND_DIALOG_ATTR, None)


def execute_on_demand_reports(
    dialog: CardJanitorDialog,
    reports: tuple[PolicyReport, ...],
) -> None:
    if not reports or any(report.errors for report in reports):
        return
    approved = {
        report.policy.id: {card.card_id for card in report.actionable} for report in reports
    }
    profile = mw.pm.profile
    collection = mw.col
    policy_names = tuple(report.policy.name for report in reports)
    policy_ids = tuple(report.policy.id for report in reports)
    dialog.close()

    def execute_fresh(col: Collection) -> ExecutionResult:
        if col is not collection or mw.col is not collection or mw.pm.profile is not profile:
            message = "Cleanup cancelled because the collection changed."
            raise RuntimeError(message)
        return execute_approved_reports(
            col,
            reports,
            approved,
            "Card Janitor: Clean Up",
        )

    def on_applied(result: ExecutionResult) -> None:
        if mw.pm.profile is not profile or mw.col is not collection:
            return
        record_cleanup(
            profile,
            automatic=False,
            affected_cards=result.affected_cards,
            conflicts=result.conflicts,
            policies=policy_names,
            policy_ids=policy_ids,
            triggers=("Manual",),
        )
        debug(
            "on-demand run complete",
            affected_cards=result.affected_cards,
            conflicts=result.conflicts,
        )
        messages = [
            applied_message(result.affected_cards)
            if result.affected_cards
            else "No cards needed cleanup"
        ]
        if result.conflicts:
            messages.append(f"{result.conflicts} conflicting cards were skipped")
        message = messages[0] if len(messages) == 1 else ". ".join(messages) + "."
        tooltip(message, parent=mw)

    def on_failure(exc: Exception) -> None:
        if mw.pm.profile is not profile or mw.col is not collection:
            return
        error("manual cleanup failed", reason=str(exc))
        record_cleanup(
            profile,
            automatic=False,
            affected_cards=None,
            failure=str(exc),
            policies=policy_names,
            policy_ids=policy_ids,
            triggers=("Manual",),
        )
        showWarning(str(exc), parent=mw)

    CollectionOp(parent=mw, op=execute_fresh).success(on_applied).failure(
        on_failure
    ).run_in_background()


def install_menu() -> None:
    existing = getattr(mw, MENU_ATTR, None)
    if existing is not None:
        with contextlib.suppress(RuntimeError):
            mw.form.menuTools.removeAction(existing)

    action = QAction("Card Janitor…", mw)
    action.setStatusTip("Manage policies and clean up your collection")
    qconnect(action.triggered, open_card_janitor)
    mw.form.menuTools.addAction(action)
    setattr(mw, MENU_ATTR, action)


def safe_install_menu() -> None:
    try:
        install_menu()
    except Exception:
        exception("failed to install Tools menu")
