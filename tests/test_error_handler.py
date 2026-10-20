"""Tests for common/error_handler.py exception formatting and friendly error dialogs."""

import json
import sys
import types
import unittest
import zipfile
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

# Set up qt.core mapping if running in standalone test environment
if "qt.core" not in sys.modules:
    try:
        import PyQt6.QtCore as _qtcore
        import PyQt6.QtWidgets as _qtwidgets

        qt = types.ModuleType("qt")
        qtcore = types.ModuleType("qt.core")
        for k, v in _qtcore.__dict__.items():
            setattr(qtcore, k, v)
        for k, v in _qtwidgets.__dict__.items():
            setattr(qtcore, k, v)
        sys.modules["qt"] = qt
        sys.modules["qt.core"] = qtcore
    except ImportError:
        qt = types.ModuleType("qt")
        qt.core = MagicMock()
        sys.modules["qt"] = qt
        sys.modules["qt.core"] = qt.core

from common.error_handler import (
    FriendlyError,
    format_exception,
    show_friendly_error_dialog,
)
from common.guikit import Task


class ErrorHandlerTests(unittest.TestCase):
    def test_permission_error_formatting(self):
        exc = PermissionError(13, "Permission denied", "test_file.epub")
        friendly = format_exception(exc)
        self.assertIsInstance(friendly, FriendlyError)
        self.assertEqual(friendly.category, "File Access Error")
        self.assertIn("locked or access was denied", friendly.explanation)
        self.assertTrue(any("Close any programs" in step for step in friendly.recovery))
        self.assertIn("Permission denied", friendly.details)

    def test_file_not_found_error_formatting(self):
        exc = FileNotFoundError(2, "No such file or directory", "missing.epub")
        friendly = format_exception(exc)
        self.assertEqual(friendly.category, "File Not Found")
        self.assertIn("could not be found", friendly.explanation)
        self.assertTrue(
            any(
                "Verify that the file or directory exists" in step
                for step in friendly.recovery
            )
        )

    def test_http_429_rate_limit_formatting(self):
        exc = HTTPError("http://example.com", 429, "Too Many Requests", {}, None)
        friendly = format_exception(exc)
        self.assertEqual(friendly.category, "Rate Limit Exceeded")
        self.assertIn("rate-limited requests", friendly.explanation)
        self.assertTrue(any("Wait a few minutes" in step for step in friendly.recovery))

    def test_url_error_formatting(self):
        exc = URLError("Connection refused")
        friendly = format_exception(exc)
        self.assertEqual(friendly.category, "Network Error")
        self.assertIn("network error occurred", friendly.explanation)
        self.assertTrue(
            any("Check your internet connection" in step for step in friendly.recovery)
        )

    def test_bad_zip_file_formatting(self):
        exc = zipfile.BadZipFile("File is not a zip file")
        friendly = format_exception(exc)
        self.assertEqual(friendly.category, "Invalid Archive")
        self.assertIn("not a valid ZIP or EPUB archive", friendly.explanation)
        self.assertTrue(any("intact EPUB" in step for step in friendly.recovery))

    def test_json_decode_error_formatting(self):
        try:
            json.loads("{invalid json}")
        except json.JSONDecodeError as exc:
            friendly = format_exception(exc)
            self.assertEqual(friendly.category, "Data Parsing Error")
            self.assertIn("invalid or unexpected formatting", friendly.explanation)

    def test_import_error_formatting(self):
        exc = ModuleNotFoundError("No module named 'nonexistent'")
        friendly = format_exception(exc)
        self.assertEqual(friendly.category, "Dependency Missing")
        self.assertIn("component or dependency was not found", friendly.explanation)

    def test_string_error_message_formatting(self):
        msg = "HTTP Error 429: Too Many Requests"
        friendly = format_exception(msg)
        self.assertEqual(friendly.category, "Rate Limit Exceeded")
        self.assertIn("429", friendly.details)

    def test_det_msg_preserves_details(self):
        exc = ValueError("Invalid parameter value")
        friendly = format_exception(exc, det_msg="Extra context information")
        self.assertIn("Extra context information", friendly.details)

    def test_show_friendly_error_dialog_calls_calibre_error_dialog(self):
        captured = {}

        def mock_error_dialog(parent, title, msg, det_msg="", show=True):
            captured["parent"] = parent
            captured["title"] = title
            captured["msg"] = msg
            captured["det_msg"] = det_msg
            captured["show"] = show
            return "dialog_instance"

        with patch("common.error_handler.error_dialog", mock_error_dialog):
            exc = PermissionError(13, "Permission denied")
            res = show_friendly_error_dialog(
                "parent_widget", "Test Title", exc, det_msg="Det text"
            )
            self.assertEqual(res, "dialog_instance")
            self.assertEqual(captured["title"], "Test Title")
            self.assertIn("locked or access was denied", captured["msg"])
            self.assertIn("Troubleshooting steps:", captured["msg"])
            self.assertIn("Permission denied", captured["det_msg"])

    def test_task_runner_formats_worker_exceptions(self):
        def failing_worker(task):
            raise PermissionError(13, "Permission denied in worker")

        task = Task(failing_worker)
        task._run()

        self.assertIsNotNone(task.error)
        self.assertIsInstance(task.error, FriendlyError)
        self.assertEqual(task.error.category, "File Access Error")
        self.assertIn("Permission denied in worker", task.error.details)
        self.assertIn("Troubleshooting steps:", str(task.error))


if __name__ == "__main__":
    unittest.main()
