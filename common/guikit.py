"""Qt helpers shared by the plugins: a background-task runner, design token engine, widget factories, and options panel."""
import threading
import time

from qt.core import (QApplication, QCheckBox, QLineEdit, QObject, QPalette, QProgressDialog, QPushButton,
                     QSplitter, QTableWidget, QTreeWidget, Qt, QVBoxLayout, QWidget)

from .storykit import Cancelled


class ThemeTokenEngine(QObject):
    """Centralized Qt Design System Token Engine.

    Generates dynamic QSS stylesheet strings based on dark/light palette state,
    maintains widget registrations, and responds to application theme mode changes.
    """
    _instance = None

    def __init__(self):
        super().__init__()
        self._registered_widgets = set()
        self._forced_dark = None  # None = auto, True = dark, False = light

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def is_dark_mode(cls, app=None) -> bool:
        engine = cls.get_instance()
        if engine._forced_dark is not None:
            return engine._forced_dark
        if app is None:
            app = QApplication.instance()
        if not app:
            return False
        palette = app.palette()
        bg_color = palette.color(QPalette.ColorRole.Window)
        text_color = palette.color(QPalette.ColorRole.WindowText)
        return bg_color.lightness() < text_color.lightness() or bg_color.lightness() < 128

    @classmethod
    def is_high_contrast(cls, app=None) -> bool:
        if app is None:
            app = QApplication.instance()
        if not app:
            return False
        palette = app.palette()
        bg = palette.color(QPalette.ColorRole.Window).lightness()
        text = palette.color(QPalette.ColorRole.WindowText).lightness()
        contrast_diff = abs(bg - text)
        return contrast_diff > 220 and (bg < 10 or bg > 245)

    @classmethod
    def get_tokens(cls, dark_mode: bool = None) -> dict:
        if dark_mode is None:
            dark_mode = cls.is_dark_mode()

        if dark_mode:
            return {
                'bg_primary': '#1e1e2e',
                'bg_secondary': '#252538',
                'bg_alternate': '#2b2b40',
                'bg_input': '#181825',
                'text_primary': '#cdd6f4',
                'text_secondary': '#a6adc8',
                'text_muted': '#7f849c',
                'text_highlight': '#ffffff',
                'border_color': '#45475a',
                'border_focus': '#89b4fa',
                'highlight_bg': '#313244',
                'highlight_fg': '#89b4fa',
                'button_bg': '#313244',
                'button_hover': '#45475a',
                'button_pressed': '#585b70',
                'border_radius': '4px',
                'padding_sm': '4px',
                'padding_md': '8px',
            }
        else:
            return {
                'bg_primary': '#ffffff',
                'bg_secondary': '#f8f9fa',
                'bg_alternate': '#f1f3f5',
                'bg_input': '#ffffff',
                'text_primary': '#212529',
                'text_secondary': '#495057',
                'text_muted': '#6c757d',
                'text_highlight': '#000000',
                'border_color': '#ced4da',
                'border_focus': '#3b82f6',
                'highlight_bg': '#e7f5ff',
                'highlight_fg': '#1c7ed6',
                'button_bg': '#e9ecef',
                'button_hover': '#dee2e6',
                'button_pressed': '#ced4da',
                'border_radius': '4px',
                'padding_sm': '4px',
                'padding_md': '8px',
            }

    @classmethod
    def generate_stylesheet(cls, dark_mode: bool = None) -> str:
        if cls.is_high_contrast():
            return ""

        tokens = cls.get_tokens(dark_mode)

        return f"""
        QDialog, QWidget {{
            background-color: {tokens['bg_primary']};
            color: {tokens['text_primary']};
        }}
        QLabel, QCheckBox, QRadioButton {{
            color: {tokens['text_primary']};
        }}
        QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background-color: {tokens['bg_input']};
            color: {tokens['text_primary']};
            border: 1px solid {tokens['border_color']};
            border-radius: {tokens['border_radius']};
            padding: {tokens['padding_sm']};
        }}
        QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border: 1px solid {tokens['border_focus']};
        }}
        QPushButton {{
            background-color: {tokens['button_bg']};
            color: {tokens['text_primary']};
            border: 1px solid {tokens['border_color']};
            border-radius: {tokens['border_radius']};
            padding: {tokens['padding_sm']} {tokens['padding_md']};
            min-height: 22px;
        }}
        QPushButton:hover {{
            background-color: {tokens['button_hover']};
        }}
        QPushButton:pressed {{
            background-color: {tokens['button_pressed']};
        }}
        QPushButton:focus {{
            border: 1px solid {tokens['border_focus']};
        }}
        QPushButton:disabled {{
            color: {tokens['text_muted']};
            background-color: {tokens['bg_secondary']};
            border-color: {tokens['border_color']};
        }}
        QTreeWidget, QTableWidget {{
            background-color: {tokens['bg_input']};
            color: {tokens['text_primary']};
            border: 1px solid {tokens['border_color']};
            border-radius: {tokens['border_radius']};
            alternate-background-color: {tokens['bg_alternate']};
            gridline-color: {tokens['border_color']};
        }}
        QTreeWidget::item, QTableWidget::item {{
            padding: {tokens['padding_sm']};
        }}
        QTreeWidget::item:selected, QTableWidget::item:selected {{
            background-color: {tokens['highlight_bg']};
            color: {tokens['highlight_fg']};
        }}
        QHeaderView::section {{
            background-color: {tokens['bg_secondary']};
            color: {tokens['text_secondary']};
            padding: {tokens['padding_sm']};
            border: 1px solid {tokens['border_color']};
            font-weight: bold;
        }}
        QSplitter::handle {{
            background-color: {tokens['border_color']};
        }}
        QSplitter::handle:hover {{
            background-color: {tokens['border_focus']};
        }}
        QProgressBar {{
            border: 1px solid {tokens['border_color']};
            border-radius: {tokens['border_radius']};
            text-align: center;
            color: {tokens['text_primary']};
            background-color: {tokens['bg_input']};
        }}
        QProgressBar::chunk {{
            background-color: {tokens['border_focus']};
            border-radius: {tokens['border_radius']};
        }}
        QScrollArea {{
            border: none;
            background-color: transparent;
        }}
        """

    @classmethod
    def register_widget(cls, widget):
        engine = cls.get_instance()
        engine._registered_widgets.add(widget)
        def _on_destroyed(*_):
            try:
                cls.unregister_widget(widget)
            except Exception:
                pass
        try:
            widget.destroyed.connect(_on_destroyed)
        except Exception:
            pass
        cls.apply_theme(widget)

    @classmethod
    def unregister_widget(cls, widget):
        try:
            engine = cls.get_instance()
            if engine and hasattr(engine, '_registered_widgets'):
                engine._registered_widgets.discard(widget)
        except Exception:
            pass

    @classmethod
    def apply_theme(cls, widget=None):
        qss = cls.generate_stylesheet()
        if widget is not None:
            widget.setStyleSheet(qss)
        else:
            engine = cls.get_instance()
            dead_widgets = set()
            for w in list(engine._registered_widgets):
                try:
                    w.setStyleSheet(qss)
                except (RuntimeError, AttributeError):
                    dead_widgets.add(w)
            engine._registered_widgets -= dead_widgets

    @classmethod
    def set_forced_theme(cls, dark_mode):
        engine = cls.get_instance()
        engine._forced_dark = dark_mode
        cls.apply_theme()


def create_token_button(label='', parent=None, icon=None, tool_tip=None):
    btn = QPushButton(label, parent) if parent else QPushButton(label)
    if icon is not None:
        btn.setIcon(icon)
    if tool_tip is not None:
        btn.setToolTip(tool_tip)
    return btn


def create_token_tree(parent=None, header_labels=None):
    tree = QTreeWidget(parent) if parent else QTreeWidget()
    if header_labels:
        tree.setHeaderLabels(header_labels)
    tree.setAlternatingRowColors(True)
    return tree


def create_token_splitter(orientation=Qt.Orientation.Vertical, parent=None):
    return QSplitter(orientation, parent) if parent else QSplitter(orientation)


def create_token_table(rows=0, cols=0, parent=None, header_labels=None):
    table = QTableWidget(rows, cols, parent) if parent else QTableWidget(rows, cols)
    if header_labels:
        table.setHorizontalHeaderLabels(header_labels)
    table.setAlternatingRowColors(True)
    return table


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
