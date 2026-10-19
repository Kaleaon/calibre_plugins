import base64
import os
import queue
import threading
import time

try:
    from calibre.gui2 import error_dialog, info_dialog, question_dialog, warning_dialog
except ImportError:
    from calibre.gui2 import error_dialog, info_dialog, question_dialog
    warning_dialog = error_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QBuffer, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QIODevice, QImage, QLabel,
                     QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSpinBox, QSplitter, Qt, QTreeWidget,
                     QTreeWidgetItem, QVBoxLayout, QWidget)
try:
    from qt.core import _
except ImportError:
    _ = lambda s: s

from calibre_plugins.gemini_library_fixer import engine
from calibre_plugins.gemini_library_fixer.config import prefs
from calibre_plugins.gemini_library_fixer.guikit import run_task

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


class GeminiFixerAction(InterfaceAction):
    name = 'Gemini Library Fixer'
    action_spec = (_('Gemini fix books'), None, _('Rename, sort and fix book metadata with the Google Gemini API'), None)

    def genesis(self):
        self.qaction.triggered.connect(self.show_dialog)

    # -- reading and writing the library ------------------------------------------------------------------------------
    def books_for(self, ids, with_excerpt, on_book=lambda index, total, book: None, should_stop=lambda: False, with_covers=False):
        db = self.gui.current_db.new_api
        books = []
        for i, book_id in enumerate(ids):
            mi = db.get_metadata(book_id, get_cover=False)
            fmts = list(db.formats(book_id) or [])
            names = []
            for fmt in fmts:
                path = db.format_abspath(book_id, fmt)
                if path:
                    names.append(os.path.basename(path))
            excerpt = ''
            if with_excerpt and 'EPUB' in fmts:
                excerpt = engine.epub_excerpt(db.format_abspath(book_id, 'EPUB'))
            images = []
            if with_covers:
                front = prepare_image(db.cover(book_id))
                if front:
                    images.append({'label': 'front cover', 'mime': 'image/jpeg', 'data': front})
                for fmt in fmts:
                    found = engine.find_back_cover(db.format_abspath(book_id, fmt), fmt) if fmt.upper() in ('EPUB', 'CBZ') else None
                    back = prepare_image(found[0]) if found else None
                    if back:
                        images.append({'label': found[1], 'mime': 'image/jpeg', 'data': back})
                        break
            books.append({'id': book_id, 'title': mi.title or '', 'authors': list(mi.authors or []),
                          'identifiers': dict(mi.identifiers or {}), 'images': images,
                          'series': mi.series or '', 'series_index': mi.series_index if mi.series else None,
                          'tags': list(mi.tags or []), 'languages': list(mi.languages or []), 'publisher': mi.publisher or '',
                          'filenames': names, 'excerpt': excerpt})
            on_book(i + 1, len(ids), books[-1])
            if should_stop():
                break
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
                    saved[field] = [old[0], old[1]] if field == 'series' else old
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
        row.addWidget(model, 1)
        models_btn = QPushButton(_('List models'))
        models_btn.setToolTip(_('Ask Google which models your key can use'))
        row.addWidget(models_btn)
        layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel(_('Instruction Template:')))
        template_combo = QComboBox()
        manage_templates_btn = QPushButton(_('Manage Templates...'))
        manage_templates_btn.setToolTip(_('Create, edit, or delete instruction templates and presets'))
        row.addWidget(template_combo, 1)
        row.addWidget(manage_templates_btn)
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
        settings_btn = QPushButton(_('Hide settings'))
        settings_btn.setToolTip(_('Hide the settings above to make room for the log and the suggested changes'))
        row.addWidget(settings_btn)
        bar = QProgressBar()
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setFormat('%v of %m')
        row.addWidget(bar, 1)
        stop_btn = QPushButton(_('Cancel'))
        stop_btn.setEnabled(False)
        row.addWidget(stop_btn)
        layout.addLayout(row)
        phase = QLabel(_('Idle.'))
        layout.addWidget(phase)

        # The log and the list of suggested changes share a splitter, so either can be dragged larger.
        splitter = QSplitter(Qt.Orientation.Vertical)
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
        all_btn = QPushButton(_('Check all'))
        none_btn = QPushButton(_('Check none'))
        safe_btn = QPushButton(_('Check all except big changes'))
        for w in (all_btn, none_btn, safe_btn):
            pick_row.addWidget(w)
        pick_row.addStretch(1)
        ticked = QLabel('')
        pick_row.addWidget(ticked)
        results_layout.addLayout(pick_row)
        tree = QTreeWidget()
        tree.setHeaderLabels([_('Suggested change'), _('Why')])
        tree.setColumnWidth(0, 700)
        tree.setAlternatingRowColors(True)
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
        check_btn = QPushButton(_('Check books'))
        compile_btn = QPushButton(_('Compile saved batches'))
        compile_btn.setToolTip(_('Compile separate batch output files from disk into the suggestions list'))
        open_batch_dir_btn = QPushButton(_('Open batch folder'))
        open_batch_dir_btn.setToolTip(_('Open the directory where separate batch outputs are saved'))
        apply_btn = QPushButton(_('Apply ticked changes'))
        apply_btn.setEnabled(False)
        undo_btn = QPushButton(_('Undo last run'))
        undo_btn.setEnabled(bool(prefs['undo']))
        buttons.addButton(check_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(compile_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(open_batch_dir_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(undo_btn, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def api_key():
            return key.text().strip() or os.environ.get('GEMINI_API_KEY', '') or os.environ.get('GOOGLE_API_KEY', '')

        def chosen_fields():
            return tuple(f for f, _ in FIELD_LABELS if boxes[f].isChecked())

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
            prefs['fields'] = list(chosen_fields())
            prefs['tags_remove'] = tags_remove.isChecked()
            prefs['excerpt'] = excerpt.isChecked()
            prefs['covers'] = covers.isChecked()
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

        def set_checks(choose):
            """choose(top_item) -> True/False. Children are set first and the (auto-tristate) parent last."""
            tree.blockSignals(True)
            for i in range(tree.topLevelItemCount()):
                top = tree.topLevelItem(i)
                state = Qt.CheckState.Checked if choose(top) else Qt.CheckState.Unchecked
                for j in range(top.childCount()):
                    top.child(j).setCheckState(0, state)
                top.setCheckState(0, state)
            tree.blockSignals(False)
            update_ticked()

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
            activity.appendPlainText(f'{time.strftime("%H:%M:%S")}  {text}')

        def set_busy(busy):
            run_busy[0] = busy
            for w in (all_btn, none_btn, safe_btn):
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
            ids = selected if scope_selected.isChecked() else sorted(self.gui.current_db.new_api.all_book_ids())
            if not ids:
                return info_dialog(dialog, _('Gemini'), _('No books found to check.'), show=True)

            b_dir = get_batch_dir()
            existing_batches = engine.list_batch_files(b_dir)
            resume_mode = False

            if existing_batches:
                completed_ids = engine.get_completed_batch_book_ids(b_dir)
                remaining_ids = [bid for bid in ids if bid not in completed_ids]
                if remaining_ids and completed_ids:
                    prompt_msg = _(
                        "Found {batch_count} saved batch output(s) from a previous run ({done_count} book(s) already checked).\n\n"
                        "• Click Yes to Resume: check the remaining {remaining_count} book(s) in batches and compile them together.\n"
                        "• Click No to Start Fresh: clear previous batches and start checking all {total_count} book(s)."
                    ).format(
                        batch_count=len(existing_batches),
                        done_count=len(completed_ids),
                        remaining_count=len(remaining_ids),
                        total_count=len(ids)
                    )
                    if question_dialog(dialog, _('Resume Previous Run?'), prompt_msg, yes_text=_('Resume'), no_text=_('Start Fresh')):
                        resume_mode = True
                        ids = remaining_ids
                    else:
                        engine.clear_batch_outputs(b_dir)
                else:
                    engine.clear_batch_outputs(b_dir)

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

                result, error = ask_gemini_batched(ids, b_dir, resume=resume_mode)
            finally:
                set_busy(False)
            return finish_run(result, len(ids), error)

        def ask_gemini_batched(target_ids, b_dir, resume=False):
            """Processes books in batches, saves each batch output separately to disk, then compiles them together."""
            messages = queue.Queue()
            box = {}
            total_books = len(target_ids)
            bsize = max(1, batch.value())
            per_request = min(bsize, 8) if covers.isChecked() else bsize
            chunks = [target_ids[i:i + per_request] for i in range(0, len(target_ids), per_request)]
            total_chunks = len(chunks)

            existing = engine.list_batch_files(b_dir) if resume else []
            start_batch_num = len(existing) + 1
            total_all_batches = total_chunks + len(existing)

            log(_('Processing {total_books} book(s) in {total_chunks} batch(es) of {per_request}. Each batch is saved separately to disk.').format(
                total_books=total_books, total_chunks=total_chunks, per_request=per_request))
            bar.setRange(0, max(1, total_books))
            bar.setValue(0)

            def work():
                try:
                    active_text = get_active_template_text()
                    tpl_vars = get_current_sample_vars()
                    books_done = 0

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
                            with_covers=covers.isChecked()
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
                            on_event=lambda kind, **info: messages.put((kind, info))
                        )

                        # 3. Save output SEPARATELY immediately!
                        saved_path = engine.save_batch_output(
                            b_dir, curr_batch_idx, batch_suggestions,
                            skipped=batch_skipped, book_ids=chunk_ids,
                            total_batches=total_all_batches
                        )
                        books_done += len(chunk_ids)
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
                    box['error'] = engine.GeminiPartialError(
                        engine.sanitize_error_message(exc, api_key()),
                        suggestions=suggs,
                        skipped=skips,
                        original_error=exc
                    )

            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            waiting = {'since': None, 'count': 0}

            def handle(kind, info):
                if kind == 'batch_start':
                    phase.setText(_('Batch {idx} of {total}: reading {count} books…').format(
                        idx=info['batch_idx'], total=info['total_batches'], count=info['count']))
                    log(_('→ Batch {idx}/{total}: reading {count} book(s)…').format(
                        idx=info['batch_idx'], total=info['total_batches'], count=info['count']))
                elif kind == 'read_progress':
                    index, total, book = info
                    log(_('  read {index}/{total}: {desc}').format(index=index, total=total, desc=engine.describe_book(book)))
                elif kind == 'batch_saved':
                    log(_('✔ Batch {idx}/{total} saved separately to {filename} ({s_count} suggestion(s), {sk_count} skipped)').format(
                        idx=info['batch_idx'], total=info['total_batches'],
                        filename=os.path.basename(info['path']),
                        s_count=info['suggestions_count'], sk_count=info['skipped_count']))
                    bar.setValue(info['done'])
                    phase.setText(_('Batch {idx} of {total} completed').format(
                        idx=info['batch_idx'], total=info['total_batches']))
                elif kind == 'compiling':
                    phase.setText(_('Compiling separate batch outputs together…'))
                    log(_('Compiling all separate batch output files together…'))
                elif kind == 'sending':
                    batch_books = info['books']
                    waiting['since'], waiting['count'] = time.time(), len(batch_books)
                    log(_('→ Sending {count} book(s) to Gemini:').format(count=len(batch_books)))
                    for b in batch_books:
                        log(f'     {engine.describe_book(b)}')
                elif kind == 'received':
                    log(_('← Gemini answered after {duration:.0f}s ({count} entries)').format(
                        duration=time.time() - (waiting["since"] or time.time()), count=info["count"]))
                    waiting['since'] = None
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
                    pass

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

            return box.get('result'), box.get('error')

        def finish_run(result, count, error=None):
            phase.setText(_('Idle.'))
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
            update_ticked()
            splitter.setSizes([120, 700])  # the changes are what needs reading now
            if partial_error:
                warning_dialog(dialog, _('Gemini'), _('Processing stopped early due to an error:\n\n{error}\n\nPartial results collected before the error are displayed below.').format(error=partial_error), show=True)

        def collect():
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
        dialog.exec()
