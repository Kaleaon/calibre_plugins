"""Qt helpers shared by the plugins: a background-task runner and the download options panel."""
import threading
import time

from qt.core import (QApplication, QCheckBox, QEvent, QFrame, QHBoxLayout, QLabel, QLineEdit, QPalette, QProgressDialog, QPushButton,
                     Qt, QVBoxLayout, QWidget)

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


class EmptyStateOverlay(QWidget):
    """Centralized overlay component for empty list, table, tree, or text views."""

    def __init__(self, target_widget, icon='', title='', message='', action_text=None, on_action=None, actions=None):
        self.target_widget = target_widget
        self.parent_w = target_widget.viewport() if hasattr(target_widget, 'viewport') and callable(target_widget.viewport) else target_widget
        super().__init__(self.parent_w)

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if self.parent_w:
            self.parent_w.installEventFilter(self)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = QFrame(self)
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font_icon = self.icon_label.font()
        font_icon.setPointSize(font_icon.pointSize() + 16)
        self.icon_label.setFont(font_icon)

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font_title = self.title_label.font()
        font_title.setPointSize(font_title.pointSize() + 2)
        font_title.setBold(True)
        self.title_label.setFont(font_title)

        self.message_label = QLabel()
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)

        self.button_layout = QHBoxLayout()
        self.button_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card_layout.addWidget(self.icon_label)
        card_layout.addWidget(self.title_label)
        card_layout.addWidget(self.message_label)
        card_layout.addLayout(self.button_layout)

        main_layout.addWidget(self.card)

        self.set_message(title=title, message=message, icon=icon, action_text=action_text, on_action=on_action, actions=actions)
        self.update_geometry()
        self.hide()

    def update_geometry(self):
        if self.parent_w:
            self.setGeometry(self.parent_w.rect())

    def eventFilter(self, watched, event):
        if watched == self.parent_w and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.update_geometry()
        return super().eventFilter(watched, event)

    def set_message(self, title=None, message=None, icon=None, action_text=None, on_action=None, actions=None):
        if icon is not None:
            self.icon_label.setText(icon)
            self.icon_label.setVisible(bool(icon))
        if title is not None:
            self.title_label.setText(title)
            self.title_label.setVisible(bool(title))
        if message is not None:
            self.message_label.setText(message)
            self.message_label.setVisible(bool(message))

        while self.button_layout.count():
            item = self.button_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        btn_specs = []
        if actions:
            btn_specs.extend(actions)
        elif action_text and on_action:
            btn_specs.append((action_text, on_action))

        for text, cb in btn_specs:
            btn = QPushButton(text)
            btn.clicked.connect(cb)
            self.button_layout.addWidget(btn)

    def show_empty(self, title=None, message=None, icon=None, action_text=None, on_action=None, actions=None):
        if any(arg is not None for arg in (title, message, icon, action_text, on_action, actions)):
            self.set_message(title=title, message=message, icon=icon, action_text=action_text, on_action=on_action, actions=actions)

        palette = self.target_widget.palette()
        base_col = palette.color(QPalette.ColorRole.Base)
        text_col = palette.color(QPalette.ColorRole.Text)
        sub_col = palette.color(QPalette.ColorRole.PlaceholderText if hasattr(QPalette.ColorRole, 'PlaceholderText') else QPalette.ColorRole.WindowText)

        self.setStyleSheet(f"EmptyStateOverlay {{ background-color: {base_col.name()}; }}")
        self.title_label.setStyleSheet(f"color: {text_col.name()};")
        self.message_label.setStyleSheet(f"color: {sub_col.name()};")

        self.update_geometry()
        self.show()
        self.raise_()

    def hide_empty(self):
        self.hide()

    def update_visibility(self, item_count=None):
        if item_count is None:
            if hasattr(self.target_widget, 'topLevelItemCount'):
                item_count = self.target_widget.topLevelItemCount()
            elif hasattr(self.target_widget, 'rowCount'):
                item_count = self.target_widget.rowCount()
            elif hasattr(self.target_widget, 'toPlainText'):
                item_count = len(self.target_widget.toPlainText().strip())
            else:
                item_count = 0
        if item_count == 0:
            self.show_empty()
        else:
            self.hide_empty()

