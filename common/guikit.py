"""Qt helpers shared by the plugins: a background-task runner and the download options panel."""

import threading
import time

from qt.core import (
    QApplication,
    QCheckBox,
    QLineEdit,
    QProgressDialog,
    Qt,
    QVBoxLayout,
    QWidget,
)

from .storykit import Cancelled

try:
    from .error_handler import format_exception
except ImportError:
    try:
        from error_handler import format_exception
    except ImportError:
        from common.error_handler import format_exception


class Task:
    """Runs func(task) in a thread; func polls task.cancelled() and may set task.status."""

    def __init__(self, func):
        self.func = func
        self.status = ""
        self.result = None
        self.error = None
        self._cancel = threading.Event()

    def cancelled(self):
        return self._cancel.is_set()

    def _run(self):
        try:
            self.result = self.func(self)
        except Cancelled:
            pass
        except Exception as exc:  # reported to the user by the caller
            self.error = format_exception(exc)


def run_task(parent, title, func):
    """Returns the finished Task; check task.error and task.cancelled(). The GUI stays responsive meanwhile."""
    task = Task(func)
    thread = threading.Thread(target=task._run, daemon=True)
    progress = QProgressDialog(title, "Cancel", 0, 0, parent)
    progress.setWindowTitle(title)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.canceled.connect(task._cancel.set)
    progress.show()
    thread.start()
    while thread.is_alive():
        progress.setLabelText(task.status or title)
        QApplication.processEvents()
        time.sleep(0.05)
    # Qt emits `canceled` whenever this dialog closes, including our own close() below, which would make every finished
    # task look cancelled. Only a Cancel pressed while the task ran should count, so silence the dialog before closing it.
    progress.blockSignals(True)
    progress.close()
    return task


class DownloadOptions(QWidget):
    """Skip-existing / combine-into-one-book checkboxes, remembered through the plugin's prefs."""

    def __init__(self, parent, prefs):
        super().__init__(parent)
        self.prefs = prefs
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.skip = QCheckBox("Skip stories already in this library")
        self.skip.setChecked(bool(prefs["skip_existing"]))
        layout.addWidget(self.skip)
        self.combine = QCheckBox(
            "Combine everything into one book (otherwise each story is its own book, with its parts as chapters)"
        )
        layout.addWidget(self.combine)
        self.title = QLineEdit()
        self.title.setPlaceholderText("Title for the combined book")
        self.title.setEnabled(False)
        self.combine.toggled.connect(self.title.setEnabled)
        layout.addWidget(self.title)
        self.chapter_range = QLineEdit()
        self.chapter_range.setPlaceholderText("Chapter range (e.g. 1-5, 8, 10+)")
        layout.addWidget(self.chapter_range)

    def values(self):
        self.prefs["skip_existing"] = self.skip.isChecked()
        return (
            self.skip.isChecked(),
            self.combine.isChecked(),
            self.title.text().strip(),
            self.chapter_range.text().strip(),
        )
