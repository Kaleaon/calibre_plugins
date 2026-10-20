"""Tests for Decoupled Task Queue Manager and UI components."""

import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6 import QtCore, QtGui, QtWidgets

    HAVE_QT = True
except ImportError:
    HAVE_QT = False
    QtCore = QtGui = QtWidgets = None

if HAVE_QT and "qt.core" not in sys.modules:
    qtcore = types.ModuleType("qt.core")
    for module in (QtWidgets, QtGui, QtCore):
        for name in dir(module):
            setattr(qtcore, name, getattr(module, name))
    sys.modules["qt"] = types.ModuleType("qt")
    sys.modules["qt.core"] = qtcore

from queue_ui import QueueManagerDialog
from task_queue import FailureCategory, QueueManager, TaskState, categorize_error

# Ensure QApplication instance for Qt GUI tests
app = (QtWidgets.QApplication.instance() or QtWidgets.QApplication([])) if HAVE_QT else None


class TaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cache_dir = self.tmp_dir.name
        self.qm = QueueManager(self.cache_dir, base_backoff=10.0, max_backoff=100.0)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_init_db_creates_tables_and_indexes(self):
        db_path = os.path.join(self.cache_dir, "queue.db")
        self.assertTrue(os.path.exists(db_path))

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        self.assertIn("task_queue", tables)
        self.assertIn("activity_log", tables)

    def test_startup_recovery_resets_processing_to_queued(self):
        # Insert a stuck 'PROCESSING' task directly
        conn = sqlite3.connect(os.path.join(self.cache_dir, "queue.db"))
        with conn:
            conn.execute("""
                INSERT INTO task_queue (task_id, task_type, payload, status, created_at, updated_at)
                VALUES ('stuck_task', 'check', '{}', 'PROCESSING', 100.0, 100.0)
            """)
        conn.close()

        # Re-initialize QueueManager
        new_qm = QueueManager(self.cache_dir)
        tasks = new_qm.get_tasks("QUEUED")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "stuck_task")
        self.assertEqual(tasks[0]["status"], TaskState.QUEUED)

    def test_task_state_machine_workflow(self):
        task_id = "test_story_1"
        payload = {"title": "Test Story", "url": "http://example.com"}

        # 1. Enqueue
        self.qm.enqueue_task(task_id, "check_follow", payload, max_attempts=3)
        tasks = self.qm.get_eligible_tasks("check_follow")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], TaskState.QUEUED)
        self.assertEqual(tasks[0]["payload"], payload)

        # 2. Mark Processing
        self.assertTrue(self.qm.mark_processing(task_id))
        tasks = self.qm.get_tasks("PROCESSING")
        self.assertEqual(len(tasks), 1)

        # 3. Mark Failure #1 -> BACKOFF
        self.assertTrue(self.qm.mark_failed(task_id, Exception("HTTP 429 Rate limited")))
        task = self.qm.get_tasks("BACKOFF")[0]
        self.assertEqual(task["status"], TaskState.BACKOFF)
        self.assertEqual(task["attempts"], 1)
        self.assertEqual(task["failure_category"], FailureCategory.RATE_LIMIT)
        self.assertGreater(task["next_run_at"], time.time())

        # 4. Mark Failure #2 -> BACKOFF
        self.assertTrue(self.qm.mark_failed(task_id, Exception("HTTP 429 Rate limited")))
        task = self.qm.get_tasks("BACKOFF")[0]
        self.assertEqual(task["attempts"], 2)

        # 5. Mark Failure #3 (max_attempts=3) -> DEAD_LETTER
        self.assertTrue(self.qm.mark_failed(task_id, Exception("HTTP 429 Rate limited")))
        task = self.qm.get_tasks("DEAD_LETTER")[0]
        self.assertEqual(task["status"], TaskState.DEAD_LETTER)
        self.assertEqual(task["attempts"], 3)

        # 6. Manual Retry -> QUEUED
        self.assertTrue(self.qm.retry_task(task_id))
        task = self.qm.get_tasks("QUEUED")[0]
        self.assertEqual(task["status"], TaskState.QUEUED)
        self.assertEqual(task["attempts"], 0)

        # 7. Mark Processing then Mark Completed -> COMPLETED
        self.qm.mark_processing(task_id)
        self.qm.mark_completed(task_id, "All chapters downloaded")
        task = self.qm.get_tasks("COMPLETED")[0]
        self.assertEqual(task["status"], TaskState.COMPLETED)

    def test_categorize_error_helper(self):
        self.assertEqual(categorize_error(Exception("HTTP 429 Too Many Requests")), FailureCategory.RATE_LIMIT)
        self.assertEqual(categorize_error("Rate limit exceeded"), FailureCategory.RATE_LIMIT)
        self.assertEqual(categorize_error(Exception("401 Unauthorized")), FailureCategory.AUTH_ERROR)
        self.assertEqual(categorize_error(Exception("403 Forbidden")), FailureCategory.AUTH_ERROR)
        self.assertEqual(categorize_error(Exception("Connection timeout")), FailureCategory.NETWORK_ERROR)
        self.assertEqual(categorize_error(Exception("JSONDecodeError invalid syntax")), FailureCategory.PARSE_ERROR)
        self.assertEqual(categorize_error(Exception("Permission denied")), FailureCategory.SYSTEM_ERROR)
        self.assertEqual(categorize_error(Exception("Something random")), FailureCategory.UNKNOWN)

    def test_log_retention_and_pruning(self):
        # Create 1100 log entries
        conn = sqlite3.connect(os.path.join(self.cache_dir, "queue.db"))
        now = time.time()
        with conn:
            for i in range(1100):
                conn.execute(
                    """
                    INSERT INTO activity_log (task_id, timestamp, from_status, to_status, message, failure_category)
                    VALUES (?, ?, 'QUEUED', 'PROCESSING', ?, 'UNKNOWN')
                """,
                    (f"task_{i}", now - (i * 10), f"Log message {i}"),
                )
        conn.close()

        # Trigger pruning
        self.qm.prune_activity_logs()
        logs = self.qm.get_activity_logs(limit=2000)
        self.assertLessEqual(len(logs), 1000)

        # Test timestamp pruning (> 30 days)
        conn = sqlite3.connect(os.path.join(self.cache_dir, "queue.db"))
        old_ts = now - (35 * 86400)
        with conn:
            conn.execute(
                """
                INSERT INTO activity_log (task_id, timestamp, from_status, to_status, message)
                VALUES ('old_task', ?, 'QUEUED', 'PROCESSING', 'ancient log')
            """,
                (old_ts,),
            )
        conn.close()

        self.qm.prune_activity_logs()
        logs = self.qm.get_activity_logs(limit=2000)
        task_ids = [l["task_id"] for l in logs]
        self.assertNotIn("old_task", task_ids)

    def test_calibre_restart_persistence(self):
        self.qm.enqueue_task("persistent_1", "type_a", {"a": 1})
        self.qm.mark_failed("persistent_1", Exception("Timeout"))

        # Re-instantiate QueueManager on same storage
        qm2 = QueueManager(self.cache_dir)
        tasks = qm2.get_tasks("BACKOFF")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task_id"], "persistent_1")

        logs = qm2.get_activity_logs()
        self.assertGreaterEqual(len(logs), 2)

    def test_concurrent_queue_access(self):
        def worker(worker_id):
            for i in range(10):
                task_id = f"worker_{worker_id}_task_{i}"
                self.qm.enqueue_task(task_id, "batch", {"i": i})
                self.qm.mark_processing(task_id)
                if i % 2 == 0:
                    self.qm.mark_completed(task_id)
                else:
                    self.qm.mark_failed(task_id, Exception("Simulated error"))

        threads = [threading.Thread(target=worker, args=(w,)) for w in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_tasks = self.qm.get_tasks()
        self.assertEqual(len(all_tasks), 50)

    @unittest.skipUnless(HAVE_QT, "PyQt6 is required for UI tests")
    def test_ui_dialog_widgets_and_filters(self):
        # Seed tasks and activity logs
        self.qm.enqueue_task("ui_task_1", "type_1", {"key": "val"})
        self.qm.mark_failed(
            "ui_task_1",
            Exception("Rate limit 429"),
            category=FailureCategory.RATE_LIMIT,
            diagnostic_trace="Traceback details...",
        )

        self.qm.enqueue_task("ui_task_2", "type_1", {"key": "val"}, max_attempts=1)
        self.qm.mark_failed("ui_task_2", Exception("Auth error 401"), category=FailureCategory.AUTH_ERROR)

        parent_widget = None
        run_now_mock = MagicMock()
        dialog = QueueManagerDialog(parent_widget, self.qm, run_now_callback=run_now_mock)
        dialog.suppress_msgboxes = True

        # Verify task rows
        self.assertEqual(dialog.tasks_table.rowCount(), 2)

        # Test State Filter
        dialog.task_filter_combo.setCurrentText(TaskState.DEAD_LETTER)
        self.assertEqual(dialog.tasks_table.rowCount(), 1)
        self.assertEqual(dialog.tasks_table.item(0, 0).text(), "ui_task_2")

        # Test Category Filter on History Tab
        dialog.category_filter_combo.setCurrentText(FailureCategory.RATE_LIMIT)
        self.assertEqual(dialog.history_table.rowCount(), 1)

        # Select history row and check trace
        dialog.history_table.selectRow(0)
        dialog.on_history_selection_changed()
        self.assertIn("Traceback details...", dialog.trace_edit.toPlainText())

        # Select DEAD_LETTER task and click Retry
        dialog.task_filter_combo.setCurrentText(TaskState.DEAD_LETTER)
        dialog.tasks_table.selectRow(0)
        dialog.on_retry_selected()

        queued_tasks = [t["task_id"] for t in self.qm.get_tasks("QUEUED")]
        self.assertIn("ui_task_2", queued_tasks)


if __name__ == "__main__":
    unittest.main()
