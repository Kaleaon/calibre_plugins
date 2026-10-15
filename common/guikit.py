"""Qt helpers shared by the plugins: background-task runner, download options, log/line utilities, and metadata helpers."""
import threading
import time

from qt.core import QApplication, QCheckBox, QLineEdit, QProgressDialog, Qt, QVBoxLayout, QWidget

from .storykit import Cancelled


class Task:
    """Runs func(task) in a thread; func polls task.cancelled() and may set task.status."""

    def __init__(self, func):
        self.func = func
        self.status = ''
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
            self.error = exc


def run_task(parent, title, func):
    """Returns the finished Task; check task.error and task.cancelled(). The GUI stays responsive meanwhile."""
    task = Task(func)
    thread = threading.Thread(target=task._run, daemon=True)
    progress = QProgressDialog(title, 'Cancel', 0, 0, parent)
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
        self.skip = QCheckBox('Skip stories already in this library')
        self.skip.setChecked(bool(prefs['skip_existing']))
        layout.addWidget(self.skip)
        self.combine = QCheckBox('Combine everything into one book (otherwise each story is its own book, with its parts as chapters)')
        layout.addWidget(self.combine)
        self.title = QLineEdit()
        self.title.setPlaceholderText('Title for the combined book')
        self.title.setEnabled(False)
        self.combine.toggled.connect(self.title.setEnabled)
        layout.addWidget(self.title)
        self.chapter_range = QLineEdit()
        self.chapter_range.setPlaceholderText('Chapter range (e.g. 1-5, 8, 10+)')
        layout.addWidget(self.chapter_range)

    def values(self):
        self.prefs['skip_existing'] = self.skip.isChecked()
        return self.skip.isChecked(), self.combine.isChecked(), self.title.text().strip(), self.chapter_range.text().strip()


def clean_lines(text_or_widget):
    """Extract non-empty, whitespace-trimmed text lines from strings or text edit widgets."""
    if hasattr(text_or_widget, 'toPlainText'):
        text = text_or_widget.toPlainText()
    elif isinstance(text_or_widget, str):
        text = text_or_widget
    elif text_or_widget is None:
        text = ''
    else:
        text = str(text_or_widget)
    return [line.strip() for line in text.splitlines() if line.strip()]


def chosen_fields(boxes, field_labels=None):
    """Query active metadata selection checkboxes.
    `boxes`: dict mapping field names to QCheckBox widgets or boolean values.
    `field_labels`: list of (field_key, label) tuples, list of field keys, or None.
    Returns a tuple of checked field keys.
    """
    if field_labels is None:
        if isinstance(boxes, dict):
            keys = list(boxes.keys())
        else:
            keys = []
    else:
        keys = [item[0] if isinstance(item, (tuple, list)) else item for item in field_labels]

    result = []
    for key in keys:
        box = boxes.get(key) if isinstance(boxes, dict) else getattr(boxes, key, None)
        if box is not None:
            if callable(getattr(box, 'isChecked', None)):
                checked = box.isChecked()
            else:
                checked = bool(box)
            if checked:
                result.append(key)
    return tuple(result)


def append_log(widget, text):
    """Format timestamped status message into a log widget."""
    formatted = f'{time.strftime("%H:%M:%S")}  {text}'
    if hasattr(widget, 'appendPlainText'):
        widget.appendPlainText(formatted)
    elif hasattr(widget, 'append'):
        widget.append(formatted)
    elif hasattr(widget, 'write'):
        widget.write(formatted + '\n')
    return formatted


def collect_tree_changes(tree):
    """Extract checked field items and metadata payloads from a tree widget.
    Returns list of (top_item_user_data, {child_user_data: child_user_data_plus_1}).
    """
    items = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        changes = {}
        for j in range(top.childCount()):
            child = top.child(j)
            checked_state = child.checkState(0)
            if checked_state == Qt.CheckState.Checked or checked_state == 2:
                changes[child.data(0, Qt.ItemDataRole.UserRole)] = child.data(0, Qt.ItemDataRole.UserRole + 1)
        if changes:
            items.append((top.data(0, Qt.ItemDataRole.UserRole), changes))
    return items

