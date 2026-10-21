import base64
from concurrent.futures import ThreadPoolExecutor
import os
import queue
import re
import threading
import time

try:
    from calibre.gui2 import error_dialog, info_dialog, question_dialog, warning_dialog
except ImportError:
    from calibre.gui2 import error_dialog, info_dialog, question_dialog
    warning_dialog = error_dialog
from qt.core import (QApplication, QBuffer, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QIODevice, QImage, QLabel,
                     QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSpinBox, QSplitter, Qt, QTimer, QTreeWidget,
                     QTreeWidgetItem, QVBoxLayout, QWidget)
try:
    from qt.core import _
except ImportError:
    _ = lambda s: s

from calibre_plugins.gemini_library_fixer import engine
from calibre_plugins.gemini_library_fixer.config import prefs
try:
    from calibre_plugins.gemini_library_fixer import providers
except ImportError:
    try:
        from . import providers
    except ImportError:
        try:
            import providers
        except ImportError:
            providers = None
try:
    from calibre_plugins.gemini_library_fixer.guikit import (
        DialogAction,
        EmptyStateOverlay,
        ThemeTokenEngine,
        append_log,
        chosen_fields as guikit_chosen_fields,
        collect_tree_changes,
        create_token_button,
        create_token_splitter,
        create_token_tree,
        run_task,
    )
except ImportError:
    from common.guikit import (
        DialogAction,
        EmptyStateOverlay,
        ThemeTokenEngine,
        append_log,
        chosen_fields as guikit_chosen_fields,
        collect_tree_changes,
        create_token_button,
        create_token_splitter,
        create_token_tree,
        run_task,
    )

FIELD_LABELS = [('title', _('Titles')), ('authors', _('Authors')), ('series', _('Series and volume number')), ('tags', _('Tags')),
                ('language', _('Language (only if empty)')), ('isbn', _('ISBN (read from the back cover or title page)'))]
SHOW = {'title': _('Title'), 'authors': _('Authors'), 'series': _('Series'), 'tags': _('Tags'), 'language': _('Language'), 'isbn': _('ISBN')}
PRIVACY = _('Your API key is stored unencrypted in Calibre\'s settings folder. Only book details (title, authors, series, tags, '
            'publisher, language, file names) are sent to Google; the opening text and the cover pictures of a book are sent only if you tick those options. '
            'Changing a title or author also renames the book\'s folder and files in your library. Nothing changes until you press Apply.')


class TemplateManagerDialog(QDialog):
    def __init__(self, parent, sample_vars_fn):
        super().__init__(parent)
        self.sample_vars_fn = sample_vars_fn
        self.setWindowTitle('Instruction Template Manager')
        self.resize(800, 550)

        main_layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel('Templates & Presets:'))

        self.list_widget = QListWidget()
        left_layout.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        self.new_btn = QPushButton('New')
        self.dup_btn = QPushButton('Duplicate')
        self.del_btn = QPushButton('Delete')
        btn_row.addWidget(self.new_btn)
        btn_row.addWidget(self.dup_btn)
        btn_row.addWidget(self.del_btn)
        left_layout.addLayout(btn_row)

        splitter.addWidget(left_widget)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.read_only_badge = QLabel('<b>Built-in Preset (Read-Only)</b>')
        self.read_only_badge.setStyleSheet('color: #666;')
        right_layout.addWidget(self.read_only_badge)

        row = QHBoxLayout()
        row.addWidget(QLabel('Template Name:'))
        self.name_edit = QLineEdit()
        row.addWidget(self.name_edit, 1)
        right_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel('Description:'))
        self.desc_edit = QLineEdit()
        row.addWidget(self.desc_edit, 1)
        right_layout.addLayout(row)

        right_layout.addWidget(QLabel('Template Content / Instructions:'))
        self.text_edit = QPlainTextEdit()
        right_layout.addWidget(self.text_edit, 1)

        ref_text = (
            "Available Variables:\n"
            "• {fields} — Active library fields being fixed\n"
            "• {min_confidence} — Minimum confidence threshold\n"
            "• {tag_policy} — Tag removal / addition policy guidelines\n"
            "• {batch_size} — Books per batch request\n"
            "• {model} — Gemini model name\n"
            "• {covers} — Cover inspection status\n"
            "• {excerpt} — EPUB excerpt status"
        )
        ref_label = QLabel(ref_text)
        ref_label.setWordWrap(True)
        ref_label.setStyleSheet("font-size: 11px; color: #555; background: #f0f0f0; padding: 4px; border-radius: 4px;")
        right_layout.addWidget(ref_label)

        action_row = QHBoxLayout()
        self.preview_btn = QPushButton('Preview Rendered Instructions')
        self.save_btn = QPushButton('Save Template')
        action_row.addWidget(self.preview_btn)
        action_row.addStretch(1)
        action_row.addWidget(self.save_btn)
        right_layout.addLayout(action_row)

        splitter.addWidget(right_widget)
        splitter.setSizes([220, 580])
        main_layout.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

        self.list_widget.currentItemChanged.connect(self.on_selection_changed)
        self.new_btn.clicked.connect(self.on_new)
        self.dup_btn.clicked.connect(self.on_duplicate)
        self.del_btn.clicked.connect(self.on_delete)
        self.save_btn.clicked.connect(self.on_save)
        self.preview_btn.clicked.connect(self.on_preview)

        self.refresh_list()

    def refresh_list(self, select_name=None):
        self.list_widget.clear()
        self.all_templates = engine.get_all_templates(prefs.get('custom_templates', {}))
        select_item = None
        for name, data in self.all_templates.items():
            label = f"{name} [Preset]" if data.get('read_only') else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list_widget.addItem(item)
            if select_name and name == select_name:
                select_item = item
        if select_item:
            self.list_widget.setCurrentItem(select_item)
        elif self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def get_current_data(self):
        curr = self.list_widget.currentItem()
        if not curr:
            return None
        name = curr.data(Qt.ItemDataRole.UserRole)
        return self.all_templates.get(name)

    def on_selection_changed(self, current, previous):
        data = self.get_current_data()
        if not data:
            self.name_edit.clear()
            self.desc_edit.clear()
            self.text_edit.clear()
            self.read_only_badge.setVisible(False)
            self.name_edit.setReadOnly(True)
            self.desc_edit.setReadOnly(True)
            self.text_edit.setReadOnly(True)
            self.save_btn.setEnabled(False)
            self.del_btn.setEnabled(False)
            return

        is_readonly = bool(data.get('read_only'))
        self.read_only_badge.setVisible(is_readonly)
        self.name_edit.setText(data.get('name', ''))
        self.desc_edit.setText(data.get('description', ''))
        self.text_edit.setPlainText(data.get('text', ''))

        self.name_edit.setReadOnly(is_readonly)
        self.desc_edit.setReadOnly(is_readonly)
        self.text_edit.setReadOnly(is_readonly)
        self.save_btn.setEnabled(not is_readonly)
        self.del_btn.setEnabled(not is_readonly)

    def on_new(self):
        self.list_widget.clearSelection()
        self.read_only_badge.setVisible(False)
        self.name_edit.setReadOnly(False)
        self.desc_edit.setReadOnly(False)
        self.text_edit.setReadOnly(False)
        self.save_btn.setEnabled(True)
        self.del_btn.setEnabled(False)

        self.name_edit.setText('Custom Template')
        self.desc_edit.setText('Custom system instructions for book metadata cleanup')
        self.text_edit.setPlainText(
            "You clean up ebook metadata for fields: {fields}.\n\nRules:\n"
            "- Fix titles, authors, series, and tags ({tag_policy}).\n"
            "- Set confidence to {min_confidence} when sure.\n"
            "- Return JSON array in order."
        )
        self.name_edit.setFocus()

    def on_duplicate(self):
        data = self.get_current_data()
        if not data:
            return
        new_name = f"{data.get('name', 'Template')} Copy"
        self.list_widget.clearSelection()
        self.read_only_badge.setVisible(False)
        self.name_edit.setReadOnly(False)
        self.desc_edit.setReadOnly(False)
        self.text_edit.setReadOnly(False)
        self.save_btn.setEnabled(True)
        self.del_btn.setEnabled(False)

        self.name_edit.setText(new_name)
        self.desc_edit.setText(data.get('description', ''))
        self.text_edit.setPlainText(data.get('text', ''))
        self.name_edit.setFocus()

    def on_save(self):
        name = self.name_edit.text().strip()
        desc = self.desc_edit.text().strip()
        text = self.text_edit.toPlainText().strip()

        if not name:
            return error_dialog(self, 'Template Manager', 'Please enter a template name.', show=True)
        if name in engine.BUILTIN_PRESETS:
            return error_dialog(self, 'Template Manager', f'"{name}" is a built-in preset name. Please pick a different name for your custom template.', show=True)
        if not text:
            return error_dialog(self, 'Template Manager', 'Template content cannot be empty.', show=True)

        try:
            sample_vars = self.sample_vars_fn()
            engine.render_template(text, sample_vars)
        except engine.TemplateError as exc:
            return error_dialog(self, 'Template Syntax Error', str(exc), show=True)

        customs = dict(prefs.get('custom_templates', {}))
        customs[name] = {'name': name, 'description': desc, 'text': text}
        prefs['custom_templates'] = customs
        self.refresh_list(select_name=name)
        info_dialog(self, 'Template Manager', f'Saved template "{name}".', show=True)

    def on_delete(self):
        data = self.get_current_data()
        if not data or data.get('read_only'):
            return
        name = data.get('name')
        if not question_dialog(self, 'Delete Template', f'Are you sure you want to delete custom template "{name}"?'):
            return
        customs = dict(prefs.get('custom_templates', {}))
        customs.pop(name, None)
        prefs['custom_templates'] = customs
        if prefs.get('active_template', 'Default Ebook Cleanup') == name:
            prefs['active_template'] = 'Default Ebook Cleanup'
        self.refresh_list()

    def on_preview(self):
        text = self.text_edit.toPlainText().strip()
        sample_vars = self.sample_vars_fn()
        try:
            rendered = engine.render_template(text, sample_vars)
            info_dialog(self, 'Rendered Instruction Preview', rendered, show=True)
        except engine.TemplateError as exc:
            error_dialog(self, 'Template Syntax Error', str(exc), show=True)



def prepare_image(data, max_side=1000):
    """A cover or page as a smaller JPEG in base64 (Gemini reads cover text fine at this size and it costs far less), or None."""
    image = QImage()
    if not data or not image.loadFromData(data):
        return None
    if max(image.width(), image.height()) > max_side:
        image = image.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.convertToFormat(QImage.Format.Format_RGB32).save(buffer, 'JPEG', 80):
        return None
    return base64.b64encode(bytes(buffer.data())).decode('ascii')


def show_value(field, value):
    if field == 'series':
        name, index = value
        return f'{name} #{index:g}' if name and index is not None else (name or '(none)')
    if isinstance(value, list):
        return ', '.join(value) if value else '(none)'
    return value or '(none)'

def get_batch_dir():
    custom = prefs.get('batch_dir', '').strip()
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            return custom
        except Exception:
            pass
    try:
        from calibre.utils.config import config_dir
        base = os.path.join(config_dir, 'plugins', 'gemini_library_fixer_batches')
    except Exception:
        import tempfile
        base = os.path.join(tempfile.gettempdir(), 'gemini_library_fixer_batches')
    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return base


class _DummyDialog:
    def __init__(self, parent=None): pass
    def setWindowTitle(self, title): pass
    def resize(self, w, h): pass
    def accept(self): pass
    def reject(self): pass
    def exec(self): pass


_DialogBase = QDialog if isinstance(QDialog, type) else _DummyDialog


class GeminiRecoveryDialog(_DialogBase):
    """Interactive dialog presented when batch scanning is interrupted by rate limit or quota errors."""

    def __init__(self, parent, paused_error):
        super().__init__(parent)
        self.setWindowTitle('Gemini - Scan Paused (Rate Limit Reached)')
        self.resize(500, 320)
        self.choice = 'cancel'
        self.paused_error = paused_error
        state = getattr(paused_error, 'state', None)

        layout = QVBoxLayout(self)

        title = QLabel('<b>Batch Scan Interrupted</b>')
        layout.addWidget(title)

        completed_count = len(state.completed_ids) if state else 0
        pending_count = len(state.pending_books) if state else 0
        info_text = (
            f"The Gemini API rate limit or quota was reached.\n\n"
            f"• Completed books: {completed_count}\n"
            f"• Pending books: {pending_count}\n\n"
            f"Select a recovery action below:"
        )
        msg_label = QLabel(info_text)
        msg_label.setWordWrap(True)
        layout.addWidget(msg_label)

        # Settings group
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Fallback / Retry Model:'))
        self.model_combo = QComboBox()
        valid_models = list(engine.MODEL_COST_PER_1M.keys())
        self.model_combo.addItems(valid_models)
        current_model = (state.model_config.get('model') if state else '') or engine.DEFAULT_MODEL
        default_selection = 'gemini-2.5-flash-lite' if 'gemini-2.5-flash-lite' in valid_models else current_model
        if default_selection in valid_models:
            self.model_combo.setCurrentText(default_selection)
        elif current_model in valid_models:
            self.model_combo.setCurrentText(current_model)
        row1.addWidget(self.model_combo, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Request delay (seconds):'))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(0, 120)
        current_interval = int(state.model_config.get('min_interval', 7) if state else 7)
        self.interval_spin.setValue(max(10, current_interval))
        row2.addWidget(self.interval_spin)
        row2.addStretch(1)
        layout.addLayout(row2)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_resume = QPushButton('Resume Scanning')
        btn_resume.setDefault(True)
        btn_resume.clicked.connect(self._on_resume)
        btn_save = QPushButton('Save for Later')
        btn_save.clicked.connect(self._on_save)
        btn_cancel = QPushButton('Cancel')
        btn_cancel.clicked.connect(self._on_cancel)

        btn_layout.addWidget(btn_resume)
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def _on_resume(self):
        self.choice = 'resume'
        self.accept()

    def _on_save(self):
        self.choice = 'save'
        self.accept()

    def _on_cancel(self):
        self.choice = 'cancel'
        self.reject()

    def get_action(self):
        return self.choice

    def get_settings(self):
        return self.model_combo.currentText().strip(), self.interval_spin.value()


class GeminiFixerAction(DialogAction):
    name = 'Gemini Library Fixer'
    action_spec = (_('Gemini fix books'), None, _('Rename, sort and fix book metadata with the Google Gemini API'), None)

    # -- reading and writing the library ------------------------------------------------------------------------------
    def books_for(self, ids, with_excerpt, on_book=lambda index, total, book: None, should_stop=lambda: False, with_covers=False, max_workers=None, with_providers=True):
        db = self.gui.current_db.new_api
        if not ids:
            return []

        if max_workers is None:
            max_workers = min(8, max(1, os.cpu_count() or 4))

        books = []
        finished_queue = queue.Queue()
        futures_map = {}

        def process_book(book_id):
            if should_stop():
                return None
            try:
                mi = db.get_metadata(book_id, get_cover=False)
                fmts = list(db.formats(book_id) or [])
                names = []
                for fmt in fmts:
                    path = db.format_abspath(book_id, fmt)
                    if path:
                        names.append(os.path.basename(path))
                excerpt = ''
                if with_excerpt and 'EPUB' in fmts:
                    try:
                        epub_path = db.format_abspath(book_id, 'EPUB')
                        if epub_path:
                            excerpt = engine.epub_excerpt(epub_path)
                    except Exception:
                        excerpt = ''
                images = []
                if with_covers:
                    has_front = False
                    try:
                        if hasattr(db, 'has_cover'):
                            has_front = db.has_cover(book_id)
                        else:
                            has_front = bool(db.cover(book_id))
                    except Exception:
                        has_front = False

                    if has_front:
                        images.append({
                            'type': 'front',
                            'label': 'front cover',
                            'mime': 'image/jpeg',
                            'book_id': book_id,
                            'loader': lambda bid=book_id: db.cover(bid)
                        })

                    for fmt in fmts:
                        if fmt.upper() in ('EPUB', 'CBZ'):
                            try:
                                fmt_path = db.format_abspath(book_id, fmt)
                                if fmt_path:
                                    desc = engine.find_back_cover_descriptor(fmt_path, fmt)
                                    if desc:
                                        images.append(desc)
                                        break
                            except Exception:
                                continue
                book_data = {
                    'id': book_id,
                    'title': (getattr(mi, 'title', '') if mi else '') or '',
                    'authors': list(getattr(mi, 'authors', []) or []) if mi else [],
                    'identifiers': dict(getattr(mi, 'identifiers', {}) or {}) if mi else {},
                    'images': images,
                    'series': (getattr(mi, 'series', '') if mi else '') or '',
                    'series_index': (getattr(mi, 'series_index', None) if mi and getattr(mi, 'series', '') else None),
                    'tags': list(getattr(mi, 'tags', []) or []) if mi else [],
                    'languages': list(getattr(mi, 'languages', []) or []) if mi else [],
                    'publisher': (getattr(mi, 'publisher', '') if mi else '') or '',
                    'filenames': names,
                    'excerpt': excerpt,
                    'comments': (getattr(mi, 'comments', '') if mi else '') or ''
                }
                if with_providers and providers:
                    try:
                        providers.enrich_books_with_providers([book_data], timeout=4)
                    except Exception:
                        pass
                return book_data
            except Exception:
                return {
                    'id': book_id,
                    'title': '',
                    'authors': [],
                    'identifiers': {},
                    'images': [],
                    'series': '',
                    'series_index': None,
                    'tags': [],
                    'languages': [],
                    'publisher': '',
                    'filenames': [],
                    'excerpt': '',
                    'comments': ''
                }

        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            for book_id in ids:
                if should_stop():
                    break
                fut = executor.submit(process_book, book_id)
                futures_map[fut] = book_id
                fut.add_done_callback(lambda f: finished_queue.put(f))

            completed_count = 0
            total_submitted = len(futures_map)

            while completed_count < total_submitted:
                if should_stop():
                    for f in futures_map:
                        f.cancel()
                    break
                try:
                    fut = finished_queue.get(timeout=0.02)
                except queue.Empty:
                    continue

                if fut.cancelled():
                    continue

                try:
                    book_res = fut.result()
                except Exception:
                    book_res = None

                if book_res is None:
                    continue

                books.append(book_res)
                completed_count += 1
                on_book(completed_count, len(ids), book_res)

                if should_stop():
                    for f in futures_map:
                        f.cancel()
                    break
        finally:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

        return books

    def write_changes(self, items):
        """items: [(book_id, {field: (old, new)})]. Returns (changed_ids, errors, undo)."""
        db = self.gui.current_db.new_api
        done, errors, undo = [], [], {}
        for book_id, changes in items:
            saved = {}
            try:
                for field, (old, new) in changes.items():
                    if field == 'title':
                        db.set_field('title', {book_id: new})
                    elif field == 'authors':
                        db.set_field('authors', {book_id: tuple(new)})
                    elif field == 'series':
                        db.set_field('series', {book_id: new[0]})
                        db.set_field('series_index', {book_id: new[1]})
                    elif field == 'tags':
                        db.set_field('tags', {book_id: tuple(new)})
                    elif field == 'language':
                        db.set_field('languages', {book_id: tuple(new)})
                    elif field == 'isbn':
                        db.set_field('identifiers', {book_id: dict(db.field_for('identifiers', book_id) or {}, isbn=new)})
                    saved[field] = [old[0], old[1] if old[1] is not None else 1.0] if field == 'series' else old
                done.append(book_id)
            except Exception as exc:
                errors.append(f'{changes.get("title", ("", ""))[0] or book_id}: {exc}')
            if saved:
                undo[str(book_id)] = saved
        return done, errors, undo

    def restore(self, undo):
        db = self.gui.current_db.new_api
        done, errors = [], []
        for key, saved in undo.items():
            book_id = int(key)
            try:
                if not db.has_id(book_id):
                    continue
                for field, old in saved.items():
                    if field == 'title':
                        db.set_field('title', {book_id: old})
                    elif field == 'authors':
                        db.set_field('authors', {book_id: tuple(old)})
                    elif field == 'series':
                        db.set_field('series', {book_id: old[0] or None})
                        db.set_field('series_index', {book_id: old[1] if old[1] is not None else 1.0})
                    elif field == 'tags':
                        db.set_field('tags', {book_id: tuple(old)})
                    elif field == 'language':
                        db.set_field('languages', {book_id: tuple(old)})
                    elif field == 'isbn':
                        ids = dict(db.field_for('identifiers', book_id) or {})
                        if old:
                            ids['isbn'] = old
                        else:
                            ids.pop('isbn', None)
                        db.set_field('identifiers', {book_id: ids})
                done.append(book_id)
            except Exception as exc:
                errors.append(f'book {book_id}: {exc}')
        return done, errors

    def refresh(self, ids):
        if ids:
            self.gui.library_view.model().refresh_ids(ids)
            self.gui.tags_view.recount()

    # -- the window ---------------------------------------------------------------------------------------------------
    def show_dialog(self):
        selected = list(self.gui.library_view.get_selected_ids())
        dialog = QDialog(self.gui)
        ThemeTokenEngine.register_widget(dialog)
        dialog.setWindowTitle(_('Gemini Library Fixer'))
        dialog.resize(1000, 700)
        main = QVBoxLayout(dialog)
        settings = QWidget()  # everything up to the progress bar; it can be hidden to give the results more room
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(settings)
        main.addWidget(scroll)
        note = QLabel(PRIVACY)
        note.setWordWrap(True)
        layout.addWidget(note)

        scope_selected = QRadioButton(_('Selected books ({count})').format(count=len(selected)))
        scope_all = QRadioButton(_('Entire library (uses many requests; start with a few selected books)'))
        (scope_selected if selected else scope_all).setChecked(True)
        scope_selected.setEnabled(bool(selected))
        layout.addWidget(scope_selected)
        layout.addWidget(scope_all)

        row = QHBoxLayout()
        row.addWidget(QLabel(_('Google AI API key:')))
        key = QLineEdit(prefs['api_key'])
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText(_('Free key from https://aistudio.google.com/apikey — no billing needed (or set GEMINI_API_KEY)'))
        row.addWidget(key, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel(_('Model:')))
        model = QComboBox()
        model.setEditable(True)
        model.addItem(prefs['model'])
        model.setCurrentText(prefs['model'])
        models_btn = create_token_button(_('List models'), tool_tip=_('Ask Google which models your key can use'))
        row.addWidget(models_btn)
        layout.addLayout(row)

        def _create_template_row(settings_layout, settings_prefs):
            row = QHBoxLayout()
            row.addWidget(QLabel(_('Instruction template:')))
            template_combo = QComboBox()
            row.addWidget(template_combo, 1)
            manage_templates_btn = QPushButton(_('Manage templates...'))
            manage_templates_btn.setToolTip(_('Create, edit, or delete instruction templates and presets'))
            row.addWidget(manage_templates_btn)
            settings_layout.addLayout(row)
            return template_combo, manage_templates_btn

        template_combo, manage_templates_btn = _create_template_row(layout, prefs)

        row = QHBoxLayout()
        enable_fallback = QCheckBox(_('Enable dynamic fallback on rate limit'))
        enable_fallback.setChecked(bool(prefs.get('enable_fallback', True)))
        row.addWidget(enable_fallback)
        row.addWidget(QLabel(_('Fallback model:')))
        fallback_model = QComboBox()
        fallback_model.setEditable(True)
        fallback_model.addItem(prefs.get('fallback_model', 'gemini-2.5-flash-lite'))
        fallback_model.setCurrentText(prefs.get('fallback_model', 'gemini-2.5-flash-lite'))
        fallback_model.setEnabled(enable_fallback.isChecked())
        enable_fallback.toggled.connect(fallback_model.setEnabled)
        row.addWidget(fallback_model, 1)
        layout.addLayout(row)
        layout.addWidget(QLabel(_('Fix:')))
        boxes = {}
        for field, label in FIELD_LABELS:
            boxes[field] = QCheckBox(label)
            boxes[field].setChecked(field in prefs['fields'])
            layout.addWidget(boxes[field])
        tags_remove = QCheckBox(_('Let it remove or rename existing tags (otherwise it can only add tags)'))
        tags_remove.setChecked(bool(prefs['tags_remove']))
        layout.addWidget(tags_remove)
        excerpt = QCheckBox(_('Also send the first ~1500 characters of each EPUB (helps with badly named books; sends book text to Google)'))
        excerpt.setChecked(bool(prefs['excerpt']))
        layout.addWidget(excerpt)
        covers = QCheckBox(_('Also look at the cover pictures: the front cover, and a back cover or last page where the book has one (sends pictures to Google; uses more of a free key\'s limit)'))
        covers.setChecked(bool(prefs['covers']))
        covers.setToolTip(_('Gemini reads the title, author, series, volume and ISBN printed on the pictures. Calibre keeps only a front cover, so a back cover is found only inside the file: an EPUB image named back/rear, or the last page of a CBZ.'))
        layout.addWidget(covers)
        provider_enrich = QCheckBox(_('Query metadata providers (FanFicFare, Nifty, Metabods) when metadata is sparse'))
        provider_enrich.setChecked(bool(prefs.get('provider_metadata', True)))
        provider_enrich.setToolTip(_('If a book lacks metadata or is from a known web novel/fanfic/archive source (such as Royal Road, AO3, FanFiction.net, ScribbleHub, Nifty, or Metabods), query the source site or providers directly to enrich title, author, series, tags, and summary before asking Gemini.'))
        layout.addWidget(provider_enrich)
        row = QHBoxLayout()
        row.addWidget(QLabel(_('Minimum confidence:')))
        confidence = QComboBox()
        confidence.addItems(['low', 'medium', 'high'])
        confidence.setCurrentText(prefs['min_confidence'])
        row.addWidget(confidence)
        row.addWidget(QLabel(_('Batch size (books per batch):')))
        batch = QSpinBox()
        batch.setRange(1, 50)
        batch.setValue(int(prefs['batch_size']))
        batch.setToolTip(_('Number of books processed in each separate batch request (default 10). Each batch is saved separately.'))
        row.addWidget(batch)
        row.addWidget(QLabel(_('Seconds between requests:')))
        interval = QSpinBox()
        interval.setRange(0, 120)
        interval.setValue(int(prefs['min_interval']))
        interval.setToolTip(_('Free Google AI keys allow only a few requests per minute. 7 keeps under about 8 a minute; use 0 with a paid key.'))
        row.addWidget(interval)
        row.addStretch(1)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel('System instructions mode:'))
        instruction_mode = QComboBox()
        instruction_mode.addItem('Append custom rules to default instructions', 'append')
        instruction_mode.addItem('Override default instructions completely', 'override')
        current_mode = prefs.get('system_instruction_mode', 'append')
        if current_mode == 'override':
            instruction_mode.setCurrentIndex(1)
        else:
            instruction_mode.setCurrentIndex(0)
        row.addWidget(instruction_mode)

        reset_instructions_btn = QPushButton('Reset to default instructions')
        reset_instructions_btn.setToolTip('Clear custom instruction text and restore mode to append')
        row.addWidget(reset_instructions_btn)
        layout.addLayout(row)

        layout.addWidget(QLabel('Custom system instructions / guidelines:'))
        custom_instructions_edit = QPlainTextEdit()
        custom_instructions_edit.setMaximumHeight(80)
        custom_instructions_edit.setPlaceholderText('e.g. Always prefix genre tags with "Genre: " or preserve custom series names.')
        custom_instructions_edit.setPlainText(prefs.get('custom_system_instructions', ''))
        layout.addWidget(custom_instructions_edit)
        layout = main
        row = QHBoxLayout()
        settings_btn = create_token_button(_('Hide settings'), tool_tip=_('Hide the settings above to make room for the log and the suggested changes'))
        row.addWidget(settings_btn)
        bar = QProgressBar()
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setFormat('%v of %m')
        row.addWidget(bar, 1)
        stop_btn = create_token_button(_('Cancel'))
        stop_btn.setEnabled(False)
        row.addWidget(stop_btn)
        layout.addLayout(row)
        phase = QLabel(_('Idle.'))
        layout.addWidget(phase)

        # The log and the list of suggested changes share a splitter, so either can be dragged larger.
        splitter = create_token_splitter(Qt.Orientation.Vertical)
        activity = QPlainTextEdit()
        activity.setReadOnly(True)
        activity.setMaximumBlockCount(5000)
        activity.setPlaceholderText(_('What is happening shows up here: each book as it is read, the exact data sent to Gemini, and every answer that comes back.'))
        splitter.addWidget(activity)
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel(_('Suggested changes:')))
        all_btn = create_token_button(_('Check all'))
        none_btn = create_token_button(_('Check none'))
        safe_btn = create_token_button(_('Check all except big changes'))
        field_btn = create_token_button(_('Check by field...'))
        field_menu = QMenu(dialog)
        field_btn.setMenu(field_menu)
        for w in (all_btn, none_btn, safe_btn, field_btn):
            pick_row.addWidget(w)
        pick_row.addStretch(1)
        ticked = QLabel('')
        pick_row.addWidget(ticked)
        results_layout.addLayout(pick_row)
        tree = create_token_tree(header_labels=[_('Suggested change'), _('Why')])
        tree.setColumnWidth(0, 700)
        tree.setAlternatingRowColors(True)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        results_layout.addWidget(tree, 1)
        splitter.addWidget(results)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([180, 520])
        layout.addWidget(splitter, 1)
        status = QLabel(_('Press Check books to ask Gemini. Big changes (a very different title or author) are left unticked.'))
        status.setWordWrap(True)
        layout.addWidget(status)

        buttons = QDialogButtonBox()
        check_btn = create_token_button(_('Check books'))
        compile_btn = create_token_button(_('Compile saved batches'), tool_tip=_('Compile separate batch output files from disk into the suggestions list'))
        open_batch_dir_btn = create_token_button(_('Open batch folder'), tool_tip=_('Open the directory where separate batch outputs are saved'))
        apply_btn = create_token_button(_('Apply ticked changes'))
        apply_btn.setEnabled(False)
        undo_btn = create_token_button(_('Undo last run'))
        undo_btn.setEnabled(bool(prefs['undo']))
        buttons.addButton(check_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(compile_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(open_batch_dir_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(undo_btn, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        overlay = EmptyStateOverlay(
            tree,
            icon='✨',
            title='Check your books with Gemini',
            message='Select books and click "Check books" to ask Gemini for metadata suggestions.',
            action_text='Check books',
            on_action=check_btn.click
        )
        overlay.show_empty()

        def api_key():
            return key.text().strip() or os.environ.get('GEMINI_API_KEY', '') or os.environ.get('GOOGLE_API_KEY', '')

        def chosen_fields():
            return guikit_chosen_fields(boxes, FIELD_LABELS)

        def update_template_combo():
            all_tpls = engine.get_all_templates(prefs.get('custom_templates', {}))
            current = template_combo.currentText().strip() or prefs.get('active_template', 'Default Ebook Cleanup')
            template_combo.clear()
            for tname in all_tpls:
                template_combo.addItem(tname)
            if current in all_tpls:
                template_combo.setCurrentText(current)
            elif 'Default Ebook Cleanup' in all_tpls:
                template_combo.setCurrentText('Default Ebook Cleanup')

        update_template_combo()

        def get_current_sample_vars():
            return engine.get_default_template_vars(
                fields=chosen_fields(),
                min_confidence=confidence.currentText(),
                tags_remove=tags_remove.isChecked(),
                batch_size=batch.value(),
                model=model.currentText().strip() or engine.DEFAULT_MODEL,
                covers=covers.isChecked(),
                excerpt=excerpt.isChecked()
            )

        def get_active_template_text():
            all_tpls = engine.get_all_templates(prefs.get('custom_templates', {}))
            active_name = template_combo.currentText().strip()
            tdata = all_tpls.get(active_name, all_tpls.get('Default Ebook Cleanup', {}))
            return tdata.get('text', engine.INSTRUCTIONS)

        def do_manage_templates():
            save_prefs()
            dlg = TemplateManagerDialog(dialog, get_current_sample_vars)
            dlg.exec()
            update_template_combo()

        manage_templates_btn.clicked.connect(do_manage_templates)

        def save_prefs():
            prefs['api_key'] = key.text().strip()
            prefs['model'] = model.currentText().strip() or engine.DEFAULT_MODEL
            prefs['enable_fallback'] = enable_fallback.isChecked()
            prefs['fallback_model'] = fallback_model.currentText().strip() or 'gemini-2.5-flash-lite'
            prefs['fields'] = list(chosen_fields())
            prefs['tags_remove'] = tags_remove.isChecked()
            prefs['excerpt'] = excerpt.isChecked()
            prefs['covers'] = covers.isChecked()
            prefs['provider_metadata'] = provider_enrich.isChecked()
            prefs['min_confidence'] = confidence.currentText()
            prefs['batch_size'] = batch.value()
            prefs['min_interval'] = interval.value()
            prefs['active_template'] = template_combo.currentText().strip() or 'Default Ebook Cleanup'
            mode_data = instruction_mode.currentData()
            if not mode_data:
                mode_data = 'override' if instruction_mode.currentIndex() == 1 else 'append'
            prefs['system_instruction_mode'] = mode_data
            prefs['custom_system_instructions'] = custom_instructions_edit.toPlainText()

        def do_reset_instructions():
            custom_instructions_edit.setPlainText('')
            instruction_mode.setCurrentIndex(0)
            save_prefs()

        reset_instructions_btn.clicked.connect(do_reset_instructions)

        cancel_flag = threading.Event()

        def set_checks(choose_top=None, choose_child_fn=None):
            """choose_top(top_item) -> True/False or choose_child_fn(child_item, top_item) -> True/False/CheckState."""
            tree.blockSignals(True)
            for i in range(tree.topLevelItemCount()):
                top = tree.topLevelItem(i)
                if choose_child_fn is not None:
                    for j in range(top.childCount()):
                        child = top.child(j)
                        res = choose_child_fn(child, top)
                        if isinstance(res, bool):
                            child.setCheckState(0, Qt.CheckState.Checked if res else Qt.CheckState.Unchecked)
                        else:
                            child.setCheckState(0, res)
                elif choose_top is not None:
                    state = Qt.CheckState.Checked if choose_top(top) else Qt.CheckState.Unchecked
                    for j in range(top.childCount()):
                        top.child(j).setCheckState(0, state)

                child_count = top.childCount()
                if child_count > 0:
                    checked_count = sum(1 for j in range(child_count) if top.child(j).checkState(0) == Qt.CheckState.Checked)
                    if checked_count == child_count:
                        top.setCheckState(0, Qt.CheckState.Checked)
                    elif checked_count == 0:
                        top.setCheckState(0, Qt.CheckState.Unchecked)
                    else:
                        top.setCheckState(0, Qt.CheckState.PartiallyChecked)
            tree.blockSignals(False)
            update_ticked()

        def get_present_fields():
            seen = set()
            fields = []
            for i in range(tree.topLevelItemCount()):
                top = tree.topLevelItem(i)
                for j in range(top.childCount()):
                    f = top.child(j).data(0, Qt.ItemDataRole.UserRole)
                    if f and f not in seen:
                        seen.add(f)
                        fields.append(f)
            ordered = [f for f, _ in FIELD_LABELS if f in seen]
            for f in fields:
                if f not in ordered:
                    ordered.append(f)
            return ordered

        def update_field_menu():
            field_menu.clear()
            fields = get_present_fields()
            if not fields:
                act = field_menu.addAction(_('No fields available'))
                act.setEnabled(False)
                return

            for f in fields:
                flabel = SHOW.get(f, f.title())
                act = field_menu.addAction(_('Check only {field}').format(field=flabel))
                act.triggered.connect(lambda _, field=f: set_checks(choose_child_fn=lambda c, t: c.data(0, Qt.ItemDataRole.UserRole) == field))

            field_menu.addSeparator()

            for f in fields:
                flabel = SHOW.get(f, f.title())
                act = field_menu.addAction(_('Check all {field}').format(field=flabel))
                act.triggered.connect(lambda _, field=f: set_checks(choose_child_fn=lambda c, t: Qt.CheckState.Checked if c.data(0, Qt.ItemDataRole.UserRole) == field else c.checkState(0)))

            field_menu.addSeparator()

            for f in fields:
                flabel = SHOW.get(f, f.title())
                act = field_menu.addAction(_('Uncheck all {field}').format(field=flabel))
                act.triggered.connect(lambda _, field=f: set_checks(choose_child_fn=lambda c, t: Qt.CheckState.Unchecked if c.data(0, Qt.ItemDataRole.UserRole) == field else c.checkState(0)))

        field_menu.aboutToShow.connect(update_field_menu)

        def on_tree_context_menu(pos):
            item = tree.itemAt(pos)
            if not item or item.parent() is None:
                return
            field = item.data(0, Qt.ItemDataRole.UserRole)
            if not field:
                return
            flabel = SHOW.get(field, field.title())
            ctx_menu = QMenu(tree)

            act_only = ctx_menu.addAction(_("Check only '{field}' across all books").format(field=flabel))
            act_only.triggered.connect(lambda _, f=field: set_checks(choose_child_fn=lambda c, t: c.data(0, Qt.ItemDataRole.UserRole) == f))

            act_all = ctx_menu.addAction(_("Check all '{field}' across all books").format(field=flabel))
            act_all.triggered.connect(lambda _, f=field: set_checks(choose_child_fn=lambda c, t: Qt.CheckState.Checked if c.data(0, Qt.ItemDataRole.UserRole) == f else c.checkState(0)))

            act_none = ctx_menu.addAction(_("Uncheck all '{field}' across all books").format(field=flabel))
            act_none.triggered.connect(lambda _, f=field: set_checks(choose_child_fn=lambda c, t: Qt.CheckState.Unchecked if c.data(0, Qt.ItemDataRole.UserRole) == f else c.checkState(0)))

            ctx_menu.exec(tree.viewport().mapToGlobal(pos))

        tree.customContextMenuRequested.connect(on_tree_context_menu)

        def update_ticked():
            total = count = 0
            for i in range(tree.topLevelItemCount()):
                top = tree.topLevelItem(i)
                for j in range(top.childCount()):
                    total += 1
                    count += top.child(j).checkState(0) == Qt.CheckState.Checked
            ticked.setText(_('{count} of {total} change(s) ticked').format(count=count, total=total) if total else '')
            apply_btn.setEnabled(count > 0 and not run_busy[0])

        run_busy = [False]
        all_btn.clicked.connect(lambda: set_checks(lambda top: True))
        none_btn.clicked.connect(lambda: set_checks(lambda top: False))
        safe_btn.clicked.connect(lambda: set_checks(lambda top: not top.data(0, Qt.ItemDataRole.UserRole + 2)))
        tree.itemChanged.connect(lambda item, column: update_ticked())

        def toggle_settings():
            scroll.setHidden(not scroll.isHidden())
            settings_btn.setText(_('Show settings') if scroll.isHidden() else _('Hide settings'))

        settings_btn.clicked.connect(toggle_settings)

        def log(text):
            append_log(activity, text)

        def set_busy(busy):
            run_busy[0] = busy
            for w in (all_btn, none_btn, safe_btn, field_btn):
                w.setEnabled(not busy)
            for w in (check_btn, apply_btn, undo_btn, models_btn, compile_btn, open_batch_dir_btn):
                w.setEnabled(not busy and (w is not apply_btn) and (w is not undo_btn or bool(prefs['undo'])))
            stop_btn.setEnabled(busy)
            buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(not busy)

        stop_btn.clicked.connect(lambda: (cancel_flag.set(), log(_('Cancel pressed; stopping after the current step.'))))
        dialog.finished.connect(lambda _result: cancel_flag.set())

        def do_list_models():
            save_prefs()
            if not api_key():
                return error_dialog(dialog, _('Gemini'), _('Enter your API key first.'), show=True)
            task = run_task(dialog, _('Asking Google for the model list…'), lambda t: engine.list_models(api_key(), cancelled=t.cancelled))
            if task.error:
                return error_dialog(dialog, _('Gemini'), str(task.error), show=True)
            if task.result:
                current = model.currentText()
                model.clear()
                model.addItems(task.result)
                model.setCurrentText(current)

                current_fb = fallback_model.currentText()
                fallback_model.clear()
                fallback_model.addItems(task.result)
                fallback_model.setCurrentText(current_fb)

        def do_compile_saved():
            b_dir = get_batch_dir()
            files = engine.list_batch_files(b_dir)
            if not files:
                return info_dialog(dialog, _('Gemini Batches'), _('No saved batch output files found in:\n{dir}').format(dir=b_dir), show=True)
            compiled = engine.compile_batch_outputs(files)
            if not compiled.suggestions and not compiled.skipped:
                return info_dialog(dialog, _('Gemini Batches'), _('The saved batch files did not contain any suggestions or skipped items.'), show=True)
            finish_run((compiled.suggestions, compiled.skipped), compiled.metadata.get('total_books', len(compiled.suggestions)))
            log(_('Compiled {s_count} suggestion(s) from {b_count} saved batch file(s) ({books_count} book(s) total).').format(
                s_count=len(compiled.suggestions), b_count=len(files),
                books_count=compiled.metadata.get('total_books', len(compiled.suggestions))))

        def do_open_batch_dir():
            b_dir = get_batch_dir()
            try:
                from qt.core import QDesktopServices, QUrl
                QDesktopServices.openUrl(QUrl.fromLocalFile(b_dir))
            except Exception:
                try:
                    os.startfile(b_dir)
                except Exception as exc:
                    error_dialog(dialog, _('Gemini'), str(exc), show=True)

        def do_check():
            save_prefs()
            if not api_key():
                return error_dialog(dialog, _('Gemini'), _('Enter your Google AI API key first.'), show=True)
            if not chosen_fields():
                return error_dialog(dialog, _('Gemini'), _('Tick at least one thing to fix.'), show=True)
            all_lib_ids = sorted(self.gui.current_db.new_api.all_book_ids())
            ids = selected if scope_selected.isChecked() else all_lib_ids
            current_scope = 'selected' if scope_selected.isChecked() else 'all'
            if not ids:
                return info_dialog(dialog, _('Gemini'), _('No books found to check.'), show=True)

            b_dir = get_batch_dir()
            existing_batches = engine.list_batch_files(b_dir)
            scan_state = engine.load_scan_state(b_dir)
            resume_mode = False
            all_scan_ids = list(ids)

            if existing_batches:
                completed_ids = engine.get_completed_batch_book_ids(b_dir)

                # Check if there was an interrupted full library scan, but scope_selected is currently active
                if scan_state and scan_state.get('scope') == 'all' and scope_selected.isChecked():
                    interrupted_rem = [bid for bid in all_lib_ids if bid not in completed_ids]
                    if interrupted_rem and completed_ids:
                        msg = _(
                            "A previous complete library scan was interrupted with {done_count} of {total_count} books completed ({rem_count} remaining).\n\n"
                            "You currently have 'Selected books' chosen in the dialog.\n\n"
                            "Would you like to switch to 'Entire library' and resume the interrupted complete library scan?"
                        ).format(
                            done_count=len(completed_ids),
                            total_count=len(all_lib_ids),
                            rem_count=len(interrupted_rem)
                        )
                        if question_dialog(dialog, _('Resume Complete Library Scan?'), msg, yes_text=_('Resume Library Scan'), no_text=_('Check Selected Books Only')):
                            scope_all.setChecked(True)
                            ids = all_lib_ids
                            current_scope = 'all'
                            all_scan_ids = list(ids)

                remaining_ids = [bid for bid in ids if bid not in completed_ids]
                already_done_in_scope = [bid for bid in ids if bid in completed_ids]

                if already_done_in_scope:
                    if remaining_ids:
                        prompt_msg = _(
                            "Found {batch_count} saved batch output(s) from a previous run ({done_count} of {total_count} books in this scope already checked).\n\n"
                            "• Click Yes to Resume: check the remaining {remaining_count} book(s) in batches and compile them together.\n"
                            "• Click No to Start Fresh: clear previous batches and start checking all {total_count} book(s)."
                        ).format(
                            batch_count=len(existing_batches),
                            done_count=len(already_done_in_scope),
                            remaining_count=len(remaining_ids),
                            total_count=len(ids)
                        )
                        if question_dialog(dialog, _('Resume Previous Run?'), prompt_msg, yes_text=_('Resume'), no_text=_('Start Fresh')):
                            resume_mode = True
                            all_scan_ids = list(ids)
                            ids = remaining_ids
                        else:
                            engine.clear_batch_outputs(b_dir)
                            all_scan_ids = list(ids)
                    else:
                        # All books in this scope were already completed!
                        prompt_msg = _(
                            "All {total_count} book(s) in the chosen scope have already been checked in {batch_count} saved batch(es).\n\n"
                            "• Click Review Suggestions: compile and display the results collected so far.\n"
                            "• Click Start Fresh: clear saved batches and check these books again from scratch."
                        ).format(total_count=len(ids), batch_count=len(existing_batches))
                        if question_dialog(dialog, _('Books Already Checked'), prompt_msg, yes_text=_('Review Suggestions'), no_text=_('Start Fresh')):
                            do_compile_saved()
                            return
                        else:
                            engine.clear_batch_outputs(b_dir)
                            all_scan_ids = list(ids)
                else:
                    # Previous batches exist, but for different books
                    prompt_msg = _(
                        "Found {batch_count} saved batch output(s) from another run ({done_count} other books checked).\n\n"
                        "Starting a new check will clear previous batches.\n\n"
                        "Do you want to clear previous batches and continue?"
                    ).format(batch_count=len(existing_batches), done_count=len(completed_ids))
                    if question_dialog(dialog, _('Clear Previous Batches?'), prompt_msg, yes_text=_('Clear & Continue'), no_text=_('Cancel')):
                        engine.clear_batch_outputs(b_dir)
                        all_scan_ids = list(ids)
                    else:
                        return

            tree.clear()
            update_ticked()
            activity.clear()
            cancel_flag.clear()
            set_busy(True)
            if not scroll.isHidden():
                toggle_settings()
            splitter.setSizes([520, 180])
            try:
                m_name = model.currentText().strip() or engine.DEFAULT_MODEL
                active_text = get_active_template_text()
                tpl_vars = get_current_sample_vars()
                bsize = max(1, batch.value())
                per_req = min(bsize, 8) if covers.isChecked() else bsize

                est = engine.estimate_library_summary(
                    len(ids),
                    batch_size=per_req,
                    covers=covers.isChecked(),
                    excerpt=excerpt.isChecked(),
                    min_interval=interval.value(),
                    model=m_name,
                    instructions=active_text,
                    template_vars=tpl_vars,
                    system_instruction_mode=prefs['system_instruction_mode'],
                    custom_instructions=prefs['custom_system_instructions'],
                )
                msg = (
                    _("Pre-Flight API Usage Estimate:\n\n"
                      "• Books to Check: {books_count}\n"
                      "• Batch Requests: {batch_count} ({per_request} books/batch)\n"
                      "• Text Characters: ~{text_chars:,}\n"
                      "• Estimated Tokens: ~{prompt_tokens:,} ({text_tokens:,} text, {image_tokens:,} image)\n"
                      "• Estimated Duration: ~{estimated_duration} seconds\n"
                      "• Estimated Cost: ${estimated_cost:.4f} USD (Model: {model})\n"
                      "• Batch Save Directory: {batch_dir}\n\n"
                      "Each batch of {per_request} books will be processed and saved separately, then compiled together.\n\n"
                      "Do you want to proceed with sending these requests to Google Gemini?")
                ).format(
                    books_count=est['books_count'],
                    batch_count=est['batch_count'],
                    per_request=est['per_request'],
                    text_chars=est['text_chars'],
                    prompt_tokens=est['prompt_tokens'],
                    text_tokens=est['text_tokens'],
                    image_tokens=est['image_tokens'],
                    estimated_duration=est['estimated_duration'],
                    estimated_cost=est['estimated_cost'],
                    model=m_name,
                    batch_dir=b_dir
                )
                if not question_dialog(dialog, _('Gemini Pre-Flight Confirmation'), msg):
                    log(_('Cancelled at pre-flight confirmation. Nothing was sent to Google.'))
                    return finish_run(None, len(ids))

                while True:
                    result, error = ask_gemini_batched(ids, b_dir, resume=resume_mode, all_scan_ids=all_scan_ids, scan_scope=current_scope)
                    if isinstance(error, engine.GeminiBatchPausedError):
                        rec_dialog = GeminiRecoveryDialog(dialog, paused_error=error)
                        rec_dialog.exec()
                        choice = rec_dialog.get_action()
                        if choice == 'resume':
                            new_model, new_interval = rec_dialog.get_settings()
                            model.setCurrentText(new_model)
                            interval.setValue(new_interval)
                            save_prefs()
                            completed_ids = engine.get_completed_batch_book_ids(b_dir)
                            remaining_ids = [bid for bid in all_scan_ids if bid not in completed_ids]
                            if remaining_ids:
                                ids = remaining_ids
                                resume_mode = True
                                log(f"⚠ Resuming scan for {len(ids)} remaining book(s) with model '{new_model}' and {new_interval}s delay...")
                                cancel_flag.clear()
                                continue
                            else:
                                break
                        elif choice == 'save':
                            log("Scan state saved on disk. Retaining current suggestions.")
                            break
                        else:  # cancel
                            log("Recovery cancelled. Retaining current suggestions.")
                            break
                    else:
                        break
            finally:
                set_busy(False)
            return finish_run(result, len(ids), error)

        def ask_gemini_batched(target_ids, b_dir, resume=False, all_scan_ids=None, scan_scope='selected'):
            """Processes books in batches, saves each batch output separately to disk, then compiles them together."""
            messages = queue.Queue()
            box = {}
            total_books = len(target_ids)
            total = total_books
            if all_scan_ids is None:
                all_scan_ids = list(target_ids)
            bsize = max(1, batch.value())
            per_request = min(bsize, 8) if covers.isChecked() else bsize
            chunks = [target_ids[i:i + per_request] for i in range(0, len(target_ids), per_request)]
            total_chunks = len(chunks)

            existing = engine.list_batch_files(b_dir) if resume else []
            existing_indices = []
            for f in existing:
                m = re.search(r'batch_(\d+)', os.path.basename(f))
                if m:
                    try:
                        existing_indices.append(int(m.group(1)))
                    except ValueError:
                        pass
            start_batch_num = (max(existing_indices) + 1) if existing_indices else 1
            total_all_batches = total_chunks + len(existing)

            log(_('Processing {total_books} book(s) in {total_chunks} batch(es) of {per_request}. Each batch is saved separately to disk.').format(
                total_books=total_books, total_chunks=total_chunks, per_request=per_request))
            bar.setRange(0, max(1, total_books))
            bar.setValue(0)

            fb_model = (fallback_model.currentText().strip() or 'gemini-2.5-flash-lite') if enable_fallback.isChecked() else None

            def work():
                try:
                    active_text = get_active_template_text()
                    tpl_vars = get_current_sample_vars()
                    books_done = 0
                    completed_ids_set = set(engine.get_completed_batch_book_ids(b_dir)) if resume else set()
                    engine.save_scan_state(b_dir, all_scan_ids, completed_ids_set, scope=scan_scope, status='running')

                    for offset, chunk_ids in enumerate(chunks):
                        if cancel_flag.is_set():
                            break
                        curr_batch_idx = start_batch_num + offset
                        messages.put(('batch_start', {
                            'batch_idx': curr_batch_idx,
                            'total_batches': total_all_batches,
                            'count': len(chunk_ids),
                            'ids': chunk_ids
                        }))

                        # 1. Read ONLY this batch of books
                        batch_books = self.books_for(
                            chunk_ids,
                            excerpt.isChecked(),
                            on_book=lambda index, total, book: messages.put(('read_progress', (index, total, book))),
                            should_stop=cancel_flag.is_set,
                            with_covers=covers.isChecked(),
                            with_providers=provider_enrich.isChecked()
                        )
                        if cancel_flag.is_set():
                            break

                        # 2. Process this batch with Gemini
                        batch_suggestions, batch_skipped = engine.plan_fixes(
                            batch_books, api_key(), model.currentText().strip() or engine.DEFAULT_MODEL,
                            chosen_fields(), confidence.currentText(),
                            per_request, tags_remove.isChecked(),
                            cancelled=cancel_flag.is_set, min_interval=interval.value(),
                            instructions=active_text, template_vars=tpl_vars,
                            system_instruction_mode=prefs['system_instruction_mode'],
                            custom_instructions=prefs['custom_system_instructions'],
                            on_event=lambda kind, **info: messages.put((kind, info)),
                            image_hydrator=prepare_image, db=self.gui.current_db.new_api,
                            fallback_model=fb_model
                        )

                        # 3. Save output SEPARATELY immediately!
                        saved_path = engine.save_batch_output(
                            b_dir, curr_batch_idx, batch_suggestions,
                            skipped=batch_skipped, book_ids=chunk_ids,
                            total_batches=total_all_batches
                        )
                        books_done += len(chunk_ids)
                        completed_ids_set.update(chunk_ids)
                        engine.save_scan_state(b_dir, all_scan_ids, completed_ids_set, scope=scan_scope, status='running')

                        messages.put(('batch_saved', {
                            'batch_idx': curr_batch_idx,
                            'total_batches': total_all_batches,
                            'path': saved_path,
                            'suggestions_count': len(batch_suggestions),
                            'skipped_count': len(batch_skipped),
                            'done': books_done
                        }))

                        # Free memory immediately
                        del batch_books
                        del batch_suggestions
                        del batch_skipped

                    # 4. Compile it together!
                    messages.put(('compiling', {}))
                    compiled = engine.compile_batch_outputs(b_dir)
                    box['result'] = (compiled.suggestions, compiled.skipped)
                    if not cancel_flag.is_set():
                        engine.save_scan_state(b_dir, all_scan_ids, completed_ids_set, scope=scan_scope, status='completed')
                except Exception as exc:
                    compiled = engine.compile_batch_outputs(b_dir)
                    suggs = list(compiled.suggestions)
                    skips = list(compiled.skipped)
                    if getattr(exc, 'suggestions', None):
                        seen_ids = {s['id'] for s in suggs if 'id' in s}
                        for s in exc.suggestions:
                            if s.get('id') not in seen_ids:
                                suggs.append(s)
                    if getattr(exc, 'skipped', None):
                        for sk in exc.skipped:
                            if sk not in skips:
                                skips.append(sk)
                    if isinstance(exc, engine.GeminiBatchPausedError):
                        box['error'] = exc
                    else:
                        box['error'] = engine.GeminiPartialError(
                            engine.sanitize_error_message(exc, api_key()),
                            suggestions=suggs,
                            skipped=skips,
                            original_error=exc
                        )

            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            waiting = {'since': None, 'count': 0}
            completed_books = 0

            def handle(kind, info):
                nonlocal completed_books
                if kind == 'batch_start':
                    phase.setText(_('Batch {idx} of {total}: reading {count} books…').format(
                        idx=info['batch_idx'], total=info['total_batches'], count=info['count']))
                    log(_('→ Batch {idx}/{total}: reading {count} book(s)…').format(
                        idx=info['batch_idx'], total=info['total_batches'], count=info['count']))
                elif kind == 'read_progress':
                    index, tot, book = info
                    log(_('  read {index}/{total}: {desc}').format(index=index, total=tot, desc=engine.describe_book(book)))
                elif kind == 'batch_saved':
                    log(_('✔ Batch {idx}/{total} saved separately to {filename} ({s_count} suggestion(s), {sk_count} skipped)').format(
                        idx=info['batch_idx'], total=info['total_batches'],
                        filename=os.path.basename(info['path']),
                        s_count=info['suggestions_count'], sk_count=info['skipped_count']))
                    completed_books = info['done']
                    bar.setValue(info['done'])
                    phase.setText(_('Batch {idx} of {total} completed').format(
                        idx=info['batch_idx'], total=info['total_batches']))
                elif kind == 'compiling':
                    phase.setText(_('Compiling separate batch outputs together…'))
                    log(_('Compiling all separate batch output files together…'))
                elif kind == 'sending':
                    batch_books = info['books']
                    waiting['since'], waiting['count'] = time.time(), len(batch_books)
                    bar.setRange(0, 0)
                    log(_('→ Sending {count} book(s) to Gemini:').format(count=len(batch_books)))
                    for b in batch_books:
                        log(f'     {engine.describe_book(b)}')
                elif kind == 'received':
                    log(_('← Gemini answered after {duration:.0f}s ({count} entries)').format(
                        duration=time.time() - (waiting["since"] or time.time()), count=info["count"]))
                    waiting['since'] = None
                    bar.setRange(0, max(1, total_books))
                    bar.setValue(completed_books)
                elif kind == 'answer':
                    book, changes = info['book'], info['changes']
                    name = f"“{book.get('title') or book['id']}”"
                    if info.get('cover_text'):
                        log(_('   📖 read from the pictures for {name}: {text}').format(name=name, text=info['cover_text']))
                    if info['kept']:
                        log(_('   ✔ {name} [{confidence}]: ').format(name=name, confidence=info['confidence'])
                            + '; '.join(engine.describe_change(f, o, n) for f, (o, n) in changes.items())
                            + (f" — {info['reason']}" if info['reason'] else ''))
                    elif changes:
                        log(_('   ✖ {name}: suggestion dropped, {why}').format(name=name, why=info['dropped_why']))
                    else:
                        log(_('   – {name}: no change needed').format(name=name))
                elif kind == 'notice':
                    log(_('⚠ {text}').format(text=info['text']))
                elif kind == 'skipped':
                    log(_('✖ {text}').format(text=info['text']))
                elif kind == 'progress':
                    completed_books = info['done']
                    bar.setRange(0, max(1, total_books))
                    bar.setValue(completed_books)

            while worker.is_alive() or not messages.empty():
                while not messages.empty():
                    handle(*messages.get())
                if cancel_flag.is_set():
                    phase.setText(_('Cancelling… compiling saved batches so far'))
                elif waiting['since'] is not None:
                    phase.setText(_('Waiting for Gemini… {duration:.0f}s so far ({count} books in this request; usually 5–60s)').format(
                        duration=time.time() - waiting['since'], count=waiting['count']))
                else:
                    phase.setText(_('Working…'))
                QApplication.processEvents()
                time.sleep(0.05)
            bar.setRange(0, max(1, total_books))
            bar.setValue(completed_books)
            return box.get('result'), box.get('error')

        def finish_run(result, count, error=None):
            phase.setText(_('Idle.'))
            bar.setRange(0, max(1, count))
            partial_error = None
            if error:
                if hasattr(error, 'suggestions') and getattr(error, 'suggestions') is not None:
                    partial_error = error
                    result = (getattr(error, 'suggestions', []), getattr(error, 'skipped', []))
                    log(_('⚠ Stopped early due to an error: {error}').format(error=error))
                else:
                    log(_('✖ Stopped: {error}').format(error=error))
                    return error_dialog(dialog, _('Gemini'), str(error), show=True)
            if result is None:
                status.setText(_('Cancelled before anything was sent.'))
                return
            suggestions, skipped = result
            cancelled_note = _(' (cancelled early)') if cancel_flag.is_set() else (
                _(' (stopped early due to an error)') if partial_error else ''
            )
            log(_('Done: {count} of {total} book(s) have suggested changes{note}. Tick what you want below, then press Apply.').format(
                count=len(suggestions), total=count, note=cancelled_note))
            seen_ids = set()
            for s in suggestions:
                if s['id'] in seen_ids:
                    continue
                seen_ids.add(s['id'])
                top = QTreeWidgetItem(tree, [
                    s['title'] or _('book {id}').format(id=s['id']),
                    _('{confidence} confidence{big}').format(confidence=s['confidence'], big=_('; BIG CHANGE') if s['big'] else '')
                ])
                top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                want = Qt.CheckState.Unchecked if s['big'] else Qt.CheckState.Checked  # adding a child resets an auto-tristate parent, so set the parent last
                top.setData(0, Qt.ItemDataRole.UserRole, s['id'])
                top.setData(0, Qt.ItemDataRole.UserRole + 2, bool(s['big']))
                for field, (old, new) in s['changes'].items():
                    child = QTreeWidgetItem(top, [f'{SHOW[field]}:  {show_value(field, old)}  →  {show_value(field, new)}', s['reason'] if field == 'title' else ''])
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, want)
                    child.setData(0, Qt.ItemDataRole.UserRole, field)
                    child.setData(0, Qt.ItemDataRole.UserRole + 1, (old, new))
                top.setCheckState(0, want)
                top.setExpanded(True)
            text = _('{count} of {total} book(s) have suggested changes{note}.').format(
                count=len(suggestions), total=count, note=cancelled_note)
            if skipped:
                text += _(' {count} could not be checked: ').format(count=len(skipped)) + '; '.join(skipped[:3]) + (' …' if len(skipped) > 3 else '')
            status.setText(text)
            if suggestions:
                overlay.hide_empty()
            else:
                overlay.show_empty(
                    title='No suggested changes found',
                    message='Gemini did not find any metadata changes for the selected books. You can adjust confidence thresholds or choose different fields.',
                    icon='🔍',
                    action_text='Check books',
                    on_action=check_btn.click
                )
            update_ticked()
            splitter.setSizes([120, 700])  # the changes are what needs reading now
            if partial_error:
                warning_dialog(dialog, _('Gemini'), _('Processing stopped early due to an error:\n\n{error}\n\nPartial results collected before the error are displayed below.').format(error=partial_error), show=True)

        def collect():
            return collect_tree_changes(tree)

        def do_apply():
            items = collect()
            if not items:
                return error_dialog(dialog, _('Gemini'), _('Nothing is ticked.'), show=True)
            done, errors, undo = self.write_changes(items)
            if undo:
                prefs['undo'] = undo
                undo_btn.setEnabled(True)
            self.refresh(done)
            text = _('Updated {count} book(s). "Undo last run" can put the old values back.').format(count=len(done))
            if errors:
                text += _('\n\nProblems:\n') + '\n'.join(errors[:10])
            info_dialog(self.gui, _('Gemini'), text, show=True)
            tree.clear()
            overlay.show_empty(
                title='Check your books with Gemini',
                message='Select books and click "Check books" to ask Gemini for metadata suggestions.',
                icon='✨',
                action_text='Check books',
                on_action=check_btn.click
            )
            update_ticked()

        def do_undo():
            undo = dict(prefs['undo'])
            if not undo or not question_dialog(dialog, _('Gemini'), _('Put back the old values for {count} book(s) changed in the last run?').format(count=len(undo))):
                return
            done, errors = self.restore(undo)
            self.refresh(done)
            if not errors:
                prefs['undo'] = {}
                undo_btn.setEnabled(False)
            text = _('Restored {count} book(s).').format(count=len(done)) + (_('\n\nProblems:\n') + '\n'.join(errors[:10]) if errors else '')
            info_dialog(self.gui, _('Gemini'), text, show=True)

        key.editingFinished.connect(save_prefs)  # the key is remembered as soon as it is typed, not only after a check
        dialog.finished.connect(lambda _result: save_prefs())
        models_btn.clicked.connect(do_list_models)
        compile_btn.clicked.connect(do_compile_saved)
        open_batch_dir_btn.clicked.connect(do_open_batch_dir)
        check_btn.clicked.connect(do_check)
        apply_btn.clicked.connect(do_apply)
        undo_btn.clicked.connect(do_undo)

        # Check if there is a crashed / interrupted scan state from a previous session
        b_dir = get_batch_dir()
        initial_scan_state = engine.load_scan_state(b_dir)
        if initial_scan_state and initial_scan_state.get('status') == 'running':
            rem = initial_scan_state.get('remaining_ids', [])
            all_bids = initial_scan_state.get('all_ids', [])
            saved_scope = initial_scan_state.get('scope', 'all')
            if rem and all_bids:
                if saved_scope == 'all':
                    scope_all.setChecked(True)
                log(_('⚠ Detected an interrupted {scope} scan ({done} of {total} books completed, {rem} remaining). Click "Check books" to resume where it stopped.').format(
                    scope=_('complete library') if saved_scope == 'all' else _('selected books'),
                    done=len(initial_scan_state.get('completed_ids', [])),
                    total=len(all_bids),
                    rem=len(rem)
                ))

        dialog.exec()
