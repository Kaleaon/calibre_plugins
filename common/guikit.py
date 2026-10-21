"""Qt helpers shared by the plugins: background-task runner, download options, design token engine, widget factories, log/line utilities, and metadata helpers."""
import html
import re
import threading
import time
from pathlib import Path
from qt.core import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QEvent,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QObject,
    QPalette,
    QPlainTextEdit,
    QPushButton,
    QProgressDialog,
    QSplitter,
    Qt,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from .storykit import Cancelled, extract_epub_story, sort_by_posting_order
except (ImportError, ValueError):
    try:
        from storykit import Cancelled, extract_epub_story, sort_by_posting_order
    except ImportError:
        try:
            from common.storykit import Cancelled, extract_epub_story, sort_by_posting_order
        except ImportError:
            class Cancelled(Exception):
                pass

try:
    from .error_handler import format_exception
except ImportError:
    try:
        from error_handler import format_exception
    except ImportError:
        try:
            from common.error_handler import format_exception
        except ImportError:
            def format_exception(e):
                return str(e)

try:
    from calibre.gui2.actions import InterfaceAction
except ImportError:
    class InterfaceAction:
        pass


class DialogAction(InterfaceAction):
    """Shared base class for actions that open a single dialog when clicked."""

    def genesis(self):
        self.qaction.triggered.connect(self.show_dialog)


try:
    from calibre.gui2 import error_dialog, info_dialog
except ImportError:
    def info_dialog(gui, title, msg, show=True):
        pass

    def error_dialog(gui, title, msg, show=True):
        pass


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
            self.error = format_exception(exc)


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

def scan_library_books_for_merge(gui, book_ids):
    """Pre-flight extraction scan for library books before merger preview."""
    db = gui.current_db.new_api
    results = []
    for b_id in book_ids:
        title = f'Book ID {b_id}'
        try:
            mi = db.get_metadata(b_id)
            if mi and getattr(mi, 'title', None):
                title = mi.title
            epub_bytes = db.format(b_id, 'EPUB')
            if not epub_bytes:
                epub_bytes = db.format(b_id, 'ORIGINAL_EPUB')
            if not epub_bytes:
                results.append({
                    'book_id': b_id,
                    'title': title,
                    'status': 'error',
                    'error': f'Book ID {b_id} has no EPUB format in Calibre library.'
                })
                continue

            story = extract_epub_story(epub_bytes)
            if mi and getattr(mi, 'title', None):
                story['title'] = mi.title
            if mi and getattr(mi, 'authors', None):
                story['author'] = ' & '.join(mi.authors)

            results.append({
                'book_id': b_id,
                'title': title,
                'status': 'ok',
                'story': story
            })
        except Exception as exc:
            results.append({
                'book_id': b_id,
                'title': title,
                'status': 'error',
                'error': f'Failed to extract EPUB structure for Book ID {b_id}: {exc}'
            })
    return results


def scan_epub_files_for_merge(file_paths):
    """Pre-flight extraction scan for EPUB files on disk or raw bytes before merger preview."""
    results = []
    for idx, fp in enumerate(file_paths, 1):
        if isinstance(fp, (str, Path)):
            path_obj = Path(fp)
            title = path_obj.name
            try:
                data = path_obj.read_bytes()
            except Exception as exc:
                results.append({
                    'path': str(fp),
                    'title': title,
                    'status': 'error',
                    'error': f'Could not read EPUB file "{title}": {exc}'
                })
                continue
        else:
            title = f'EPUB Item {idx}'
            data = bytes(fp) if not isinstance(fp, bytes) else fp

        try:
            story = extract_epub_story(data)
            if story.get('title'):
                title = story['title']
            results.append({
                'path': str(fp) if isinstance(fp, (str, Path)) else f'Item {idx}',
                'title': title,
                'status': 'ok',
                'story': story
            })
        except Exception as exc:
            results.append({
                'path': str(fp) if isinstance(fp, (str, Path)) else f'Item {idx}',
                'title': title,
                'status': 'error',
                'error': f'Failed to extract EPUB file "{title}": {exc}'
            })
    return results


class MergePreviewDialog(QDialog):
    """Modal dialog presenting pre-scanned EPUB stories, interactive reordering, metadata editing, and corrupted file triage."""

    def __init__(self, parent, scan_results):
        super().__init__(parent)
        self.setWindowTitle('EPUB Merger - Preview & Customization')
        self.resize(850, 600)
        self.scan_results = scan_results

        layout = QVBoxLayout(self)

        # Header label
        header_label = QLabel(
            '<b>Review and Customize Omnibus:</b> Reorder books or chapters, edit omnibus metadata, '
            'and deselect any invalid or unwanted items before merging.'
        )
        header_label.setWordWrap(True)
        layout.addWidget(header_label)

        # Metadata Form Section
        metadata_widget = QWidget()
        form_layout = QFormLayout(metadata_widget)
        form_layout.setContentsMargins(0, 5, 0, 5)

        self.title_edit = QLineEdit()
        self.author_edit = QLineEdit()
        self.publisher_edit = QLineEdit()
        self.tags_edit = QLineEdit()
        self.desc_edit = QPlainTextEdit()
        self.desc_edit.setMaximumHeight(60)

        form_layout.addRow('Omnibus Title:', self.title_edit)
        form_layout.addRow('Author(s):', self.author_edit)
        form_layout.addRow('Publisher:', self.publisher_edit)
        form_layout.addRow('Tags (comma-separated):', self.tags_edit)
        form_layout.addRow('Description / Summary:', self.desc_edit)

        layout.addWidget(metadata_widget)

        # Splitter between tree and detail view
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Tree Widget Section
        tree_container = QWidget(splitter)
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)

        # Reorder / Action Toolbar above Tree
        tb_layout = QHBoxLayout()
        btn_up = QPushButton('▲ Move Up')
        btn_down = QPushButton('▼ Move Down')
        btn_sort = QPushButton('Auto-Sort')
        btn_select_all = QPushButton('Select All')
        btn_deselect_all = QPushButton('Deselect All')

        btn_up.clicked.connect(self.move_up)
        btn_down.clicked.connect(self.move_down)
        btn_sort.clicked.connect(self.auto_sort)
        btn_select_all.clicked.connect(self.select_all)
        btn_deselect_all.clicked.connect(self.deselect_all)

        tb_layout.addWidget(btn_up)
        tb_layout.addWidget(btn_down)
        tb_layout.addWidget(btn_sort)
        tb_layout.addWidget(btn_select_all)
        tb_layout.addWidget(btn_deselect_all)
        tb_layout.addStretch()

        tree_layout.addLayout(tb_layout)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Book / Chapter Sequence', 'Status / Category'])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        tree_layout.addWidget(self.tree)

        # Detail Pane on the right
        detail_widget = QWidget(splitter)
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        detail_title = QLabel('<b>Selected Item Details:</b>')
        detail_layout.addWidget(detail_title)

        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        detail_layout.addWidget(self.detail_edit)

        splitter.addWidget(tree_container)
        splitter.addWidget(detail_widget)
        splitter.setSizes([520, 330])

        layout.addWidget(splitter)

        # Bottom bar (summary + buttons)
        bottom_layout = QHBoxLayout()
        self.summary_label = QLabel()
        bottom_layout.addWidget(self.summary_label)
        bottom_layout.addStretch()

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText('Merge Books')
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        bottom_layout.addWidget(self.button_box)

        layout.addLayout(bottom_layout)

        # Connect signals
        self.tree.itemChanged.connect(self.on_item_changed)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)

        # Populate
        self.prepopulate_metadata()
        self.populate_tree()
        self.update_summary()

    def prepopulate_metadata(self):
        valid_stories = [res['story'] for res in self.scan_results if res.get('status') == 'ok' and res.get('story')]
        if not valid_stories:
            self.title_edit.setText('Merged Omnibus')
            self.author_edit.setText('Unknown')
            self.publisher_edit.setText('Merged Omnibus')
            self.tags_edit.setText('')
            self.desc_edit.setPlainText('')
            return

        combined = len(valid_stories) > 1
        title = (
            f"{valid_stories[0]['title']} and {len(valid_stories) - 1} more"
            if combined
            else valid_stories[0]['title']
        )
        authors = list(dict.fromkeys(s.get('author', 'Unknown') for s in valid_stories))
        author = ' & '.join(authors)
        publisher = 'Merged Omnibus'
        tags_set = list(dict.fromkeys(t for s in valid_stories for t in s.get('tags', []) + s.get('categories', [])))
        tags_str = ', '.join(tags_set)

        if combined:
            desc = '<ol>' + ''.join(f"<li>{html.escape(s['title'])} — {html.escape(s['author'])}</li>" for s in valid_stories) + '</ol>'
        else:
            desc = valid_stories[0].get('summary', '')

        self.title_edit.setText(title)
        self.author_edit.setText(author)
        self.publisher_edit.setText(publisher)
        self.tags_edit.setText(tags_str)
        self.desc_edit.setPlainText(desc)

    def populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        for res in self.scan_results:
            status = res.get('status', 'ok')
            title = res.get('title') or (res['story'].get('title') if res.get('story') else 'Untitled')

            if status == 'error':
                disp_title = f"⚠️ {title} [CORRUPTED]"
                book_item = QTreeWidgetItem(self.tree, [disp_title, 'Extraction Failure'])
                book_item.setFlags(book_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                book_item.setCheckState(0, Qt.CheckState.Unchecked)
                book_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'error', 'item': res})
            else:
                story = res['story']
                author = story.get('author', 'Unknown')
                sections = story.get('sections', [])
                disp_title = f"{title} (by {author})"
                book_item = QTreeWidgetItem(self.tree, [disp_title, f"{len(sections)} chapter(s)"])
                book_item.setFlags(book_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                book_item.setCheckState(0, Qt.CheckState.Checked)
                book_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'story', 'story': story, 'res': res})

                for heading, body in sections:
                    child_item = QTreeWidgetItem(book_item, [heading, 'Chapter'])
                    child_item.setFlags(child_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child_item.setCheckState(0, Qt.CheckState.Checked)
                    child_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'chapter', 'heading': heading, 'body': body, 'story': story})

                book_item.setExpanded(True)

        self.tree.blockSignals(False)

    def move_up(self):
        item = self.tree.currentItem()
        if not item:
            return
        parent = item.parent()
        if parent is None:
            idx = self.tree.indexOfTopLevelItem(item)
            if idx > 0:
                taken = self.tree.takeTopLevelItem(idx)
                self.tree.insertTopLevelItem(idx - 1, taken)
                self.tree.setCurrentItem(taken)
        else:
            idx = parent.indexOfChild(item)
            if idx > 0:
                taken = parent.takeChild(idx)
                parent.insertChild(idx - 1, taken)
                self.tree.setCurrentItem(taken)
        self.update_summary()

    def move_down(self):
        item = self.tree.currentItem()
        if not item:
            return
        parent = item.parent()
        if parent is None:
            idx = self.tree.indexOfTopLevelItem(item)
            if idx < self.tree.topLevelItemCount() - 1:
                taken = self.tree.takeTopLevelItem(idx)
                self.tree.insertTopLevelItem(idx + 1, taken)
                self.tree.setCurrentItem(taken)
        else:
            idx = parent.indexOfChild(item)
            if idx < parent.childCount() - 1:
                taken = parent.takeChild(idx)
                parent.insertChild(idx + 1, taken)
                self.tree.setCurrentItem(taken)
        self.update_summary()

    def auto_sort(self):
        top_items = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            top_items.append((item, data))

        valid_items = [t for t in top_items if isinstance(t[1], dict) and t[1].get('type') == 'story']
        error_items = [t for t in top_items if isinstance(t[1], dict) and t[1].get('type') == 'error']

        stories = [t[1]['story'] for t in valid_items]
        sorted_stories = sort_by_posting_order(stories)

        story_to_item = {id(t[1]['story']): t[0] for t in valid_items}

        self.tree.blockSignals(True)
        for _ in range(self.tree.topLevelItemCount()):
            self.tree.takeTopLevelItem(0)

        for st in sorted_stories:
            if id(st) in story_to_item:
                self.tree.addTopLevelItem(story_to_item[id(st)])

        for err_item, _ in error_items:
            self.tree.addTopLevelItem(err_item)

        self.tree.blockSignals(False)
        self.update_summary()

    def _set_children_check_state(self, item, state):
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
            self._set_children_check_state(child, state)

    def _update_parent_check_state(self, item):
        parent = item.parent()
        if parent is None:
            return
        checked_count = sum(1 for i in range(parent.childCount()) if parent.child(i).checkState(0) == Qt.CheckState.Checked)
        unchecked_count = sum(1 for i in range(parent.childCount()) if parent.child(i).checkState(0) == Qt.CheckState.Unchecked)
        total = parent.childCount()

        if checked_count == total:
            parent.setCheckState(0, Qt.CheckState.Checked)
        elif unchecked_count == total:
            parent.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            parent.setCheckState(0, Qt.CheckState.PartiallyChecked)

        self._update_parent_check_state(parent)

    def on_item_changed(self, item, column):
        self.tree.blockSignals(True)
        state = item.checkState(0)
        if item.childCount() > 0 and state != Qt.CheckState.PartiallyChecked:
            self._set_children_check_state(item, state)
        self._update_parent_check_state(item)
        self.tree.blockSignals(False)
        self.update_summary()

    def on_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            self.detail_edit.setPlainText('')
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            item_type = data.get('type')
            if item_type == 'error':
                res = data.get('item', {})
                self.detail_edit.setPlainText(
                    f"⚠️ CORRUPTED / UNPARSEABLE EPUB FILE ⚠️\n\n"
                    f"Title / Name: {res.get('title')}\n"
                    f"Identifier: {res.get('book_id') or res.get('path')}\n\n"
                    f"Error Details:\n{res.get('error')}\n\n"
                    f"Note: This file failed pre-flight parsing. Deselect this item to exclude it from the merged output."
                )
            elif item_type == 'story':
                st = data.get('story', {})
                self.detail_edit.setPlainText(
                    f"Story Title: {st.get('title')}\n"
                    f"Author: {st.get('author')}\n"
                    f"Total Chapters: {len(st.get('sections', []))}\n"
                    f"Tags: {', '.join(st.get('tags', []))}\n\n"
                    f"Summary:\n{st.get('summary', '(No summary)')}"
                )
            elif item_type == 'chapter':
                heading = data.get('heading', 'Chapter')
                body = data.get('body', '')
                snippet = re.sub(r'<[^>]+>', '', body)[:500].strip()
                self.detail_edit.setPlainText(
                    f"Chapter Heading: {heading}\n\n"
                    f"Content Excerpt:\n{snippet}..."
                )
            else:
                self.detail_edit.setPlainText(f"Selected: {item.text(0)}")
        else:
            self.detail_edit.setPlainText(f"Selected: {item.text(0)}")

    def select_all(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('type') == 'story':
                item.setCheckState(0, Qt.CheckState.Checked)
                self._set_children_check_state(item, Qt.CheckState.Checked)
        self.tree.blockSignals(False)
        self.update_summary()

    def deselect_all(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            self._set_children_check_state(item, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.update_summary()

    def get_active_stories(self):
        active_stories = []
        for i in range(self.tree.topLevelItemCount()):
            book_item = self.tree.topLevelItem(i)
            data = book_item.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(data, dict) or data.get('type') != 'story':
                continue

            if book_item.checkState(0) in (Qt.CheckState.Checked, Qt.CheckState.PartiallyChecked):
                story = data['story']
                active_sections = []
                for j in range(book_item.childCount()):
                    child = book_item.child(j)
                    if child.checkState(0) == Qt.CheckState.Checked:
                        cdata = child.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(cdata, dict) and cdata.get('type') == 'chapter':
                            active_sections.append((cdata['heading'], cdata['body']))

                if active_sections:
                    story_copy = dict(story)
                    story_copy['sections'] = active_sections
                    active_stories.append(story_copy)
        return active_stories

    def get_metadata(self):
        return {
            'title': self.title_edit.text().strip(),
            'author': self.author_edit.text().strip(),
            'publisher': self.publisher_edit.text().strip(),
            'tags': [t.strip() for t in self.tags_edit.text().split(',') if t.strip()],
            'description': self.desc_edit.toPlainText().strip(),
        }

    def update_summary(self):
        active_stories = self.get_active_stories()
        total_valid = sum(1 for r in self.scan_results if r.get('status') == 'ok')
        total_corrupted = sum(1 for r in self.scan_results if r.get('status') == 'error')

        msg = f"Active: {len(active_stories)} of {total_valid} valid story(ies)"
        if total_corrupted > 0:
            msg += f" | ⚠️ {total_corrupted} corrupted file(s) excluded"
        self.summary_label.setText(msg)

        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(len(active_stories) > 0)


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


class MergeSelectedActionMixin:
    """Action mixin providing a merge_selected handler for merging selected library books into an EPUB."""

    def merge_selected(self):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids or len(ids) < 2:
            info_dialog(self.gui, 'EPUB Merger', 'Select two or more books in Calibre first to merge.', show=True)
            return
        try:
            try:
                from .libkit import merge_library_books
            except (ImportError, ValueError):
                from libkit import merge_library_books
        except ImportError:
            try:
                from common.libkit import merge_library_books
            except ImportError:
                from calibre_plugins.epub_promo_scrubber.libkit import merge_library_books
        book_id, errors = merge_library_books(self.gui, ids)
        if book_id:
            info_dialog(self.gui, 'EPUB Merger', 'Successfully merged books into a new omnibus EPUB in your library.' +
                        (f'\nNotes: {"; ".join(errors)}' if errors else ''), show=True)
        else:
            error_dialog(self.gui, 'EPUB Merger', 'Could not merge books:\n' + '\n'.join(errors), show=True)


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




def parse_lines(text_or_widget):
    """Parse text or a widget with .toPlainText()/.text() into a list of non-empty stripped lines."""
    if text_or_widget is None:
        return []
    if hasattr(text_or_widget, 'toPlainText'):
        raw = text_or_widget.toPlainText()
    elif hasattr(text_or_widget, 'text') and callable(text_or_widget.text):
        raw = text_or_widget.text()
    else:
        raw = str(text_or_widget)
    return [line.strip() for line in raw.splitlines() if line.strip()]


def collect_tree_changes(tree):
    """Collect checked child items from a QTreeWidget into a list of (top_item_id, {field: new_val})."""
    items = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        changes = {}
        for j in range(top.childCount()):
            child = top.child(j)
            if child.checkState(0) == Qt.CheckState.Checked:
                changes[child.data(0, Qt.ItemDataRole.UserRole)] = child.data(0, Qt.ItemDataRole.UserRole + 1)
        if changes:
            items.append((top.data(0, Qt.ItemDataRole.UserRole), changes))
    return items


def append_log(widget, text):
    """Append a timestamped line ("%H:%M:%S  <text>") to a QPlainTextEdit or similar widget."""
    widget.appendPlainText(f'{time.strftime("%H:%M:%S")}  {text}')


def collect_checked_fields(boxes, labels=None):
    """Return tuple of field keys whose checkboxes in `boxes` dict are checked.
    If `labels` sequence of (field_key, label) is provided, preserves that order."""
    if labels:
        return tuple(f for f, _ in labels if f in boxes and boxes[f].isChecked())
    return tuple(f for f, cb in boxes.items() if cb.isChecked())


def format_field_value(field, value):
    """Format a field value into a human-readable string representation for display in UI tables/trees."""
    if field in ('series', 'album'):
        if isinstance(value, (tuple, list)) and len(value) == 2:
            name, index = value
            if name and index is not None:
                return f'{name} #{index:g}'
            return name or '(none)'
        return (value or '(none)') if isinstance(value, str) else '(none)'
    if field == 'date':
        return value[:10] if value else '(none)'
    if field == 'identifiers' and isinstance(value, dict):
        return ', '.join(f'{k}:{v}' for k, v in sorted(value.items())) or '(none)'
    if field == 'cover':
        if value is None:
            return 'none'
        if isinstance(value, (tuple, list)) and value:
            source = 'Cover Art Archive' if 'coverartarchive' in str(value[0]) else 'Open Library'
            return f'front cover from {source}'
        return str(value) if value else 'none'
    if field in ('authors', 'tags') or isinstance(value, list):
        return ', '.join(value) if value else '(none)'
    return value or '(none)'
