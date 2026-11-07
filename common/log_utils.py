"""Logging and text parsing utilities shared across Calibre plugins."""

import time


def append_log(widget, text):
    """Format timestamped status message into a log widget.

    Appends "%H:%M:%S  <text>" to widgets supporting appendPlainText,
    append, or write, and returns the formatted text string.
    """
    formatted = f"{time.strftime('%H:%M:%S')}  {text}"
    if hasattr(widget, "appendPlainText"):
        widget.appendPlainText(formatted)
    elif hasattr(widget, "append"):
        widget.append(formatted)
    elif hasattr(widget, "write"):
        widget.write(formatted + "\n")
    return formatted


def clean_lines(text_or_widget):
    """Extract non-empty, whitespace-trimmed text lines from strings or text edit widgets."""
    if hasattr(text_or_widget, "toPlainText"):
        text = text_or_widget.toPlainText()
    elif isinstance(text_or_widget, str):
        text = text_or_widget
    elif text_or_widget is None:
        text = ""
    else:
        text = str(text_or_widget)
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_lines(text_or_widget):
    """Parse text or a widget with .toPlainText()/.text() into a list of non-empty stripped lines."""
    if text_or_widget is None:
        return []
    if hasattr(text_or_widget, "toPlainText"):
        raw = text_or_widget.toPlainText()
    elif hasattr(text_or_widget, "text") and callable(text_or_widget.text):
        raw = text_or_widget.text()
    else:
        raw = str(text_or_widget)
    return [line.strip() for line in raw.splitlines() if line.strip()]
