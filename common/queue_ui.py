"""Queue Manager Dialog UI for Calibre Plugins.

Provides a multi-tabbed interface to monitor tasks, filter by state or failure category,
inspect diagnostic stack traces, and trigger manual task retries or purges.
"""

import time
from datetime import datetime

try:
    from qt.core import (
        QAbstractItemView,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMessageBox,
        QPushButton,
        QSplitter,
        Qt,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QMessageBox,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

try:
    from task_queue import FailureCategory, TaskState
except ImportError:
    from calibre_plugins.reddit_follower.task_queue import FailureCategory, TaskState


def format_timestamp(ts):
    if not ts:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def format_next_run(ts):
    if not ts or ts <= 0:
        return "Immediate"
    now = time.time()
    diff = ts - now
    if diff <= 0:
        return "Ready"
    if diff < 60:
        return f"In {int(diff)}s"
    if diff < 3600:
        return f"In {int(diff // 60)}m {int(diff % 60)}s"
    return format_timestamp(ts)


class QueueManagerDialog(QDialog):
    """Multi-tabbed Queue Manager Dialog."""

    def __init__(self, parent, queue_manager, run_now_callback=None):
        super().__init__(parent)
        self.queue_manager = queue_manager
        self.run_now_callback = run_now_callback

        self.setWindowTitle("Task Queue Manager & Diagnostics")
        self.resize(900, 600)

        self.init_ui()
        self.refresh_all()

    def init_ui(self):
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        # Tab 1: Queue Tasks
        self.tasks_tab = QWidget()
        self.init_tasks_tab()
        self.tabs.addTab(self.tasks_tab, "Task Queue")

        # Tab 2: Activity History & Diagnostics
        self.history_tab = QWidget()
        self.init_history_tab()
        self.tabs.addTab(self.history_tab, "Activity History & Diagnostics")

        # Bottom Button Box
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        button_box.rejected.connect(self.accept)
        layout.addWidget(button_box)

    def init_tasks_tab(self):
        tab_layout = QVBoxLayout(self.tasks_tab)

        # Top Controls: Filter dropdown & action buttons
        controls_layout = QHBoxLayout()

        controls_layout.addWidget(QLabel("Filter State:"))
        self.task_filter_combo = QComboBox(self)
        self.task_filter_combo.addItems(["ALL"] + TaskState.ALL_STATES)
        self.task_filter_combo.currentTextChanged.connect(self.refresh_tasks)
        controls_layout.addWidget(self.task_filter_combo)

        controls_layout.addStretch()

        self.btn_retry = QPushButton("Retry Selected", self)
        self.btn_retry.setToolTip(
            "Reset attempts and move selected task(s) to QUEUED state"
        )
        self.btn_retry.clicked.connect(self.on_retry_selected)
        controls_layout.addWidget(self.btn_retry)

        self.btn_purge = QPushButton("Purge Selected", self)
        self.btn_purge.setToolTip("Purge selected task(s) from the queue")
        self.btn_purge.clicked.connect(self.on_purge_selected)
        controls_layout.addWidget(self.btn_purge)

        if self.run_now_callback:
            self.btn_run_now = QPushButton("Run Queue Now", self)
            self.btn_run_now.clicked.connect(self.on_run_now)
            controls_layout.addWidget(self.btn_run_now)

        self.btn_refresh_tasks = QPushButton("Refresh", self)
        self.btn_refresh_tasks.clicked.connect(self.refresh_tasks)
        controls_layout.addWidget(self.btn_refresh_tasks)

        tab_layout.addLayout(controls_layout)

        # Tasks Table
        self.tasks_table = QTableWidget(self)
        self.tasks_table.setColumnCount(7)
        self.tasks_table.setHorizontalHeaderLabels(
            [
                "Task ID",
                "Type",
                "Status",
                "Attempts",
                "Next Run",
                "Updated",
                "Last Error",
            ]
        )
        self.tasks_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.tasks_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tasks_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.tasks_table.horizontalHeader().setStretchLastSection(True)
        tab_layout.addWidget(self.tasks_table)

    def init_history_tab(self):
        tab_layout = QVBoxLayout(self.history_tab)

        # Top Controls
        controls_layout = QHBoxLayout()

        controls_layout.addWidget(QLabel("Filter Category:"))
        self.category_filter_combo = QComboBox(self)
        self.category_filter_combo.addItems(["ALL"] + FailureCategory.ALL_CATEGORIES)
        self.category_filter_combo.currentTextChanged.connect(self.refresh_history)
        controls_layout.addWidget(self.category_filter_combo)

        controls_layout.addStretch()

        self.btn_clear_history = QPushButton("Clear History", self)
        self.btn_clear_history.clicked.connect(self.on_clear_history)
        controls_layout.addWidget(self.btn_clear_history)

        self.btn_refresh_history = QPushButton("Refresh", self)
        self.btn_refresh_history.clicked.connect(self.refresh_history)
        controls_layout.addWidget(self.btn_refresh_history)

        tab_layout.addLayout(controls_layout)

        # Splitter dividing History Table and Diagnostic Detail view
        splitter = QSplitter(Qt.Orientation.Vertical, self)

        # History Table
        self.history_table = QTableWidget(self)
        self.history_table.setColumnCount(5)
        self.history_table.setHorizontalHeaderLabels(
            ["Timestamp", "Task ID", "Transition", "Category", "Message"]
        )
        self.history_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.itemSelectionChanged.connect(
            self.on_history_selection_changed
        )
        splitter.addWidget(self.history_table)

        # Diagnostic Detail Text View
        detail_widget = QWidget(self)
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(QLabel("Diagnostic Log & Stack Trace Detail:"))

        self.trace_edit = QTextEdit(self)
        self.trace_edit.setReadOnly(True)
        self.trace_edit.setPlaceholderText(
            "Select a history log entry above to view full trace details..."
        )
        detail_layout.addWidget(self.trace_edit)

        splitter.addWidget(detail_widget)
        splitter.setSizes([300, 200])

        tab_layout.addWidget(splitter)

    def refresh_all(self):
        self.refresh_tasks()
        self.refresh_history()

    def refresh_tasks(self):
        state_filter = self.task_filter_combo.currentText()
        tasks = self.queue_manager.get_tasks(status_filter=state_filter)

        self.tasks_table.setRowCount(len(tasks))
        for row_idx, task in enumerate(tasks):
            self.tasks_table.setItem(row_idx, 0, QTableWidgetItem(str(task["task_id"])))
            self.tasks_table.setItem(
                row_idx, 1, QTableWidgetItem(str(task["task_type"]))
            )
            self.tasks_table.setItem(row_idx, 2, QTableWidgetItem(str(task["status"])))
            self.tasks_table.setItem(
                row_idx,
                3,
                QTableWidgetItem(f"{task['attempts']}/{task['max_attempts']}"),
            )
            self.tasks_table.setItem(
                row_idx, 4, QTableWidgetItem(format_next_run(task["next_run_at"]))
            )
            self.tasks_table.setItem(
                row_idx, 5, QTableWidgetItem(format_timestamp(task["updated_at"]))
            )
            self.tasks_table.setItem(
                row_idx, 6, QTableWidgetItem(str(task.get("last_error") or ""))
            )

    def refresh_history(self):
        cat_filter = self.category_filter_combo.currentText()
        logs = self.queue_manager.get_activity_logs(category_filter=cat_filter)

        self.history_table.setRowCount(len(logs))
        for row_idx, log in enumerate(logs):
            item_ts = QTableWidgetItem(format_timestamp(log["timestamp"]))
            item_ts.setData(Qt.ItemDataRole.UserRole, log)  # Store row data dictionary
            self.history_table.setItem(row_idx, 0, item_ts)
            self.history_table.setItem(
                row_idx, 1, QTableWidgetItem(str(log["task_id"]))
            )
            self.history_table.setItem(
                row_idx,
                2,
                QTableWidgetItem(f"{log['from_status']} -> {log['to_status']}"),
            )
            self.history_table.setItem(
                row_idx, 3, QTableWidgetItem(str(log.get("failure_category") or "N/A"))
            )
            self.history_table.setItem(
                row_idx, 4, QTableWidgetItem(str(log.get("message") or ""))
            )

        self.trace_edit.clear()

    def on_history_selection_changed(self):
        selected_rows = self.history_table.selectedItems()
        if not selected_rows:
            self.trace_edit.clear()
            return

        # Get stored log dict from column 0 item
        row_idx = self.history_table.currentRow()
        item = self.history_table.item(row_idx, 0)
        if not item:
            return
        log = item.data(Qt.ItemDataRole.UserRole)
        if not log:
            return

        trace_parts = [
            "=== Activity Log Details ===",
            f"Timestamp: {format_timestamp(log.get('timestamp'))}",
            f"Task ID: {log.get('task_id')}",
            f"Transition: {log.get('from_status')} -> {log.get('to_status')}",
            f"Category: {log.get('failure_category') or 'N/A'}",
            f"Message: {log.get('message') or ''}",
            "",
        ]
        if log.get("diagnostic_trace"):
            trace_parts.append("=== Diagnostic Trace / Exception Stack ===")
            trace_parts.append(str(log["diagnostic_trace"]))

        self.trace_edit.setText("\n".join(trace_parts))

    def get_selected_task_ids(self):
        selected_rows = self.tasks_table.selectionModel().selectedRows()
        task_ids = []
        for index in selected_rows:
            item = self.tasks_table.item(index.row(), 0)
            if item:
                task_ids.append(item.text())
        return task_ids

    def on_retry_selected(self):
        task_ids = self.get_selected_task_ids()
        if not task_ids:
            if not getattr(self, "suppress_msgboxes", False):
                QMessageBox.information(
                    self, "Retry Tasks", "Please select one or more tasks to retry."
                )
            return

        for task_id in task_ids:
            self.queue_manager.retry_task(task_id)

        self.refresh_all()
        if not getattr(self, "suppress_msgboxes", False):
            QMessageBox.information(
                self,
                "Retry Tasks",
                f"Successfully moved {len(task_ids)} task(s) to QUEUED state.",
            )

    def on_purge_selected(self):
        task_ids = self.get_selected_task_ids()
        if not task_ids:
            if not getattr(self, "suppress_msgboxes", False):
                QMessageBox.information(
                    self, "Purge Tasks", "Please select one or more tasks to purge."
                )
            return

        if not getattr(self, "suppress_msgboxes", False):
            reply = QMessageBox.question(
                self,
                "Confirm Purge",
                f"Are you sure you want to purge {len(task_ids)} selected task(s) from the queue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        for task_id in task_ids:
            self.queue_manager.purge_task(task_id)
        self.refresh_all()

    def on_run_now(self):
        if self.run_now_callback:
            self.run_now_callback()
            self.refresh_all()

    def on_clear_history(self):
        if not getattr(self, "suppress_msgboxes", False):
            reply = QMessageBox.question(
                self,
                "Clear History",
                "Are you sure you want to clear all activity log history?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.queue_manager.clear_activity_logs()
        self.refresh_history()
