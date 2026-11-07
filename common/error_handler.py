"""Central exception formatter and friendly recovery dialog helper."""

import json
import socket
import traceback
import zipfile
from urllib.error import URLError

try:
    from calibre.gui2 import error_dialog
except ImportError:
    error_dialog = None


class FriendlyError(Exception):
    """Structured exception containing category, explanation, recovery steps, and technical details."""

    def __init__(
        self, category, explanation, recovery, details=None, original_exc=None
    ):
        self.category = category
        self.explanation = explanation
        self.recovery = recovery
        self.details = details or ""
        self.original_exc = original_exc
        super().__init__(self.user_message)

    @property
    def user_message(self):
        msg = f"{self.explanation}\n\nTroubleshooting steps:\n"
        if isinstance(self.recovery, (list, tuple)):
            msg += "\n".join(f"• {step}" for step in self.recovery)
        else:
            msg += f"• {self.recovery}"
        return msg

    def __str__(self):
        return self.user_message


def format_exception(exc_or_msg, det_msg=None):
    """Formats an exception or string into a FriendlyError object."""
    if isinstance(exc_or_msg, FriendlyError):
        if det_msg and det_msg not in exc_or_msg.details:
            exc_or_msg.details = f"{exc_or_msg.details}\n\n{det_msg}".strip()
        return exc_or_msg

    original_exc = exc_or_msg if isinstance(exc_or_msg, BaseException) else None

    # Extract raw string and traceback details
    if original_exc is not None:
        raw_msg = str(original_exc)
        if hasattr(original_exc, "__traceback__") and original_exc.__traceback__:
            tb_lines = traceback.format_exception(
                type(original_exc), original_exc, original_exc.__traceback__
            )
            details = "".join(tb_lines)
        else:
            tb_str = traceback.format_exc()
            if tb_str and tb_str.strip() != "NoneType: None":
                details = tb_str
            else:
                details = f"{type(original_exc).__name__}: {raw_msg}"
    else:
        raw_msg = str(exc_or_msg) if exc_or_msg is not None else ""
        details = raw_msg

    if det_msg:
        details = f"{details}\n\nAdditional details:\n{det_msg}".strip()

    msg_lower = raw_msg.lower()

    # 1. Permission / File Access Errors
    if (
        isinstance(original_exc, PermissionError)
        or "errno 13" in msg_lower
        or "permission denied" in msg_lower
        or "access is denied" in msg_lower
    ):
        category = "File Access Error"
        explanation = (
            "A file or folder needed by this action is locked or access was denied."
        )
        recovery = [
            "Close any programs that might have the file open (e.g., e-book readers or text editors).",
            "Check folder permissions to ensure Calibre has write access.",
            "Try running Calibre as an administrator if permissions persist.",
        ]

    # 2. File Not Found
    elif (
        isinstance(original_exc, FileNotFoundError)
        or "errno 2" in msg_lower
        or "no such file or directory" in msg_lower
    ):
        category = "File Not Found"
        explanation = "A required file or directory could not be found."
        recovery = [
            "Verify that the file or directory exists at the specified path.",
            "Ensure the source files have not been moved, renamed, or deleted.",
        ]

    # 3. Disk Space Error
    elif "no space left on device" in msg_lower or "errno 28" in msg_lower:
        category = "Storage Error"
        explanation = (
            "There is not enough free disk space available to complete the operation."
        )
        recovery = [
            "Free up disk space on the destination drive.",
            "Empty the temporary folder or trash bin and try again.",
        ]

    # 4. HTTP 429 Rate Limit
    elif (
        "429" in msg_lower
        or "too many requests" in msg_lower
        or "rate limit" in msg_lower
    ):
        category = "Rate Limit Exceeded"
        explanation = "The remote server rate-limited requests because too many requests were made in a short period."
        recovery = [
            "Wait a few minutes before attempting the operation again.",
            "Increase the polling interval in the plugin settings to reduce request frequency.",
        ]

    # 5. HTTP 401 / 403 Access Denied
    elif (
        "401" in msg_lower
        or "403" in msg_lower
        or "unauthorized" in msg_lower
        or "forbidden" in msg_lower
    ):
        category = "Access Denied"
        explanation = "The remote server denied access or the authorization credentials were rejected."
        recovery = [
            "Check your account credentials or API keys in the plugin settings.",
            "Verify that your account has permission to access the requested resource.",
        ]

    # 6. General Network / Timeout / Connection Errors
    elif (
        isinstance(
            original_exc, (URLError, ConnectionError, TimeoutError, socket.error)
        )
        or "connection" in msg_lower
        or "timed out" in msg_lower
        or "network" in msg_lower
        or "http error" in msg_lower
    ):
        category = "Network Error"
        explanation = "A network error occurred while connecting to the remote service."
        recovery = [
            "Check your internet connection and network stability.",
            "Verify firewall or proxy settings if you are behind a restricted network.",
            "Try again in a few minutes.",
        ]

    # 7. Bad ZIP / Corrupted Archive / EPUB
    elif (
        isinstance(original_exc, (zipfile.BadZipFile, zipfile.LargeZipFile))
        or "not a zip file" in msg_lower
        or "bad zip" in msg_lower
    ):
        category = "Invalid Archive"
        explanation = (
            "The file is not a valid ZIP or EPUB archive, or the file is corrupted."
        )
        recovery = [
            "Verify that the file is an intact EPUB or ZIP archive.",
            "Try re-downloading or recovering a fresh copy of the file.",
        ]

    # 8. JSON or Data Parsing Error
    elif (
        isinstance(original_exc, (json.JSONDecodeError, ValueError))
        or "xml" in msg_lower
        or "parse" in msg_lower
    ):
        category = "Data Parsing Error"
        explanation = "The input file or server response contained invalid or unexpected formatting."
        recovery = [
            "Check that the input file is not corrupted.",
            "Verify that settings or input syntax match expected formats.",
        ]

    # 9. Dependency Missing
    elif isinstance(original_exc, (ImportError, ModuleNotFoundError)):
        category = "Dependency Missing"
        explanation = "A required Python component or dependency was not found."
        recovery = [
            "Ensure all plugin files and dependencies are properly installed in Calibre.",
            "Try re-installing the plugin zip.",
        ]

    # Fallback for general errors
    else:
        category = "Unexpected Error"
        explanation = raw_msg or "An unexpected error occurred during processing."
        recovery = [
            "Check the detailed error report below for technical details.",
            "If the problem persists, report the issue with the detailed error trace.",
        ]

    return FriendlyError(
        category, explanation, recovery, details=details, original_exc=original_exc
    )


def show_friendly_error_dialog(parent, title, exc_or_msg, det_msg=None, show=True):
    """Displays a friendly error dialog with plain explanation in main view and raw details in expandable view."""
    friendly = format_exception(exc_or_msg, det_msg=det_msg)
    primary_msg = friendly.user_message
    details_msg = friendly.details

    if error_dialog is not None:
        return error_dialog(parent, title, primary_msg, det_msg=details_msg, show=show)
    else:
        try:
            from qt.core import QMessageBox
        except ImportError:
            try:
                from PyQt6.QtWidgets import QMessageBox
            except ImportError:
                from PyQt5.QtWidgets import QMessageBox
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle(title)
        box.setText(f"{friendly.category}\n\n{primary_msg}")
        if details_msg:
            box.setDetailedText(details_msg)
        if show:
            box.exec()
        return box
