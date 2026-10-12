import base64
import os
import queue
import threading
import time

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                     QBuffer, QImage, QIODevice, QPlainTextEdit, QProgressBar, QRadioButton, QSpinBox, QSplitter, Qt, QTreeWidget,
                     QTreeWidgetItem, QVBoxLayout, QWidget)

from calibre_plugins.gemini_library_fixer import engine
from calibre_plugins.gemini_library_fixer.config import prefs
from calibre_plugins.gemini_library_fixer.guikit import run_task

FIELD_LABELS = [('title', 'Titles'), ('authors', 'Authors'), ('series', 'Series and volume number'), ('tags', 'Tags'),
                ('language', 'Language (only if empty)'), ('isbn', 'ISBN (read from the back cover or title page)')]
SHOW = {'title': 'Title', 'authors': 'Authors', 'series': 'Series', 'tags': 'Tags', 'language': 'Language', 'isbn': 'ISBN'}
PRIVACY = ('Your API key is stored unencrypted in Calibre\'s settings folder. Only book details (title, authors, series, tags, '
           'publisher, language, file names) are sent to Google; the opening text and the cover pictures of a book are sent only if you tick those options. '
           'Changing a title or author also renames the book\'s folder and files in your library. Nothing changes until you press Apply.')


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


class GeminiFixerAction(InterfaceAction):
    name = 'Gemini Library Fixer'
    action_spec = ('Gemini fix books', None, 'Rename, sort and fix book metadata with the Google Gemini API', None)

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
                        if old[0] and old[1] is not None:
                            db.set_field('series_index', {book_id: old[1]})
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
        dialog.setWindowTitle('Gemini Library Fixer')
        dialog.resize(1100, 940)
        main = QVBoxLayout(dialog)
        settings = QWidget()  # everything up to the progress bar; it can be hidden to give the results more room
        layout = QVBoxLayout(settings)
        layout.setContentsMargins(0, 0, 0, 0)
        main.addWidget(settings)
        note = QLabel(PRIVACY)
        note.setWordWrap(True)
        layout.addWidget(note)

        scope_selected = QRadioButton(f'Selected books ({len(selected)})')
        scope_all = QRadioButton('Entire library (uses many requests; start with a few selected books)')
        (scope_selected if selected else scope_all).setChecked(True)
        scope_selected.setEnabled(bool(selected))
        layout.addWidget(scope_selected)
        layout.addWidget(scope_all)

        row = QHBoxLayout()
        row.addWidget(QLabel('Google AI API key:'))
        key = QLineEdit(prefs['api_key'])
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText('Free key from https://aistudio.google.com/apikey — no billing needed (or set GEMINI_API_KEY)')
        row.addWidget(key, 1)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel('Model:'))
        model = QComboBox()
        model.setEditable(True)
        model.addItem(prefs['model'])
        model.setCurrentText(prefs['model'])
        row.addWidget(model, 1)
        models_btn = QPushButton('List models')
        models_btn.setToolTip('Ask Google which models your key can use')
        row.addWidget(models_btn)
        layout.addLayout(row)

        layout.addWidget(QLabel('Fix:'))
        boxes = {}
        for field, label in FIELD_LABELS:
            boxes[field] = QCheckBox(label)
            boxes[field].setChecked(field in prefs['fields'])
            layout.addWidget(boxes[field])
        tags_remove = QCheckBox('Let it remove or rename existing tags (otherwise it can only add tags)')
        tags_remove.setChecked(bool(prefs['tags_remove']))
        layout.addWidget(tags_remove)
        excerpt = QCheckBox('Also send the first ~1500 characters of each EPUB (helps with badly named books; sends book text to Google)')
        excerpt.setChecked(bool(prefs['excerpt']))
        layout.addWidget(excerpt)
        covers = QCheckBox('Also look at the cover pictures: the front cover, and a back cover or last page where the book has one (sends pictures to Google; uses more of a free key\'s limit)')
        covers.setChecked(bool(prefs['covers']))
        covers.setToolTip('Gemini reads the title, author, series, volume and ISBN printed on the pictures. Calibre keeps only a front cover, so a back cover is found only inside the file: an EPUB image named back/rear, or the last page of a CBZ.')
        layout.addWidget(covers)
        row = QHBoxLayout()
        row.addWidget(QLabel('Minimum confidence:'))
        confidence = QComboBox()
        confidence.addItems(['low', 'medium', 'high'])
        confidence.setCurrentText(prefs['min_confidence'])
        row.addWidget(confidence)
        row.addWidget(QLabel('Books per request:'))
        batch = QSpinBox()
        batch.setRange(1, 50)
        batch.setValue(int(prefs['batch_size']))
        row.addWidget(batch)
        row.addWidget(QLabel('Seconds between requests:'))
        interval = QSpinBox()
        interval.setRange(0, 120)
        interval.setValue(int(prefs['min_interval']))
        interval.setToolTip('Free Google AI keys allow only a few requests per minute. 7 keeps under about 8 a minute; use 0 with a paid key.')
        row.addWidget(interval)
        row.addStretch(1)
        layout.addLayout(row)

        layout = main
        row = QHBoxLayout()
        settings_btn = QPushButton('Hide settings')
        settings_btn.setToolTip('Hide the settings above to make room for the log and the suggested changes')
        row.addWidget(settings_btn)
        bar = QProgressBar()
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setFormat('%v of %m')
        row.addWidget(bar, 1)
        stop_btn = QPushButton('Cancel')
        stop_btn.setEnabled(False)
        row.addWidget(stop_btn)
        layout.addLayout(row)
        phase = QLabel('Idle.')
        layout.addWidget(phase)

        # The log and the list of suggested changes share a splitter, so either can be dragged larger.
        splitter = QSplitter(Qt.Orientation.Vertical)
        activity = QPlainTextEdit()
        activity.setReadOnly(True)
        activity.setMaximumBlockCount(5000)
        activity.setPlaceholderText('What is happening shows up here: each book as it is read, the exact data sent to Gemini, and every answer that comes back.')
        splitter.addWidget(activity)
        results = QWidget()
        results_layout = QVBoxLayout(results)
        results_layout.setContentsMargins(0, 0, 0, 0)
        pick_row = QHBoxLayout()
        pick_row.addWidget(QLabel('Suggested changes:'))
        all_btn = QPushButton('Check all')
        none_btn = QPushButton('Check none')
        safe_btn = QPushButton('Check all except big changes')
        for w in (all_btn, none_btn, safe_btn):
            pick_row.addWidget(w)
        pick_row.addStretch(1)
        ticked = QLabel('')
        pick_row.addWidget(ticked)
        results_layout.addLayout(pick_row)
        tree = QTreeWidget()
        tree.setHeaderLabels(['Suggested change', 'Why'])
        tree.setColumnWidth(0, 700)
        tree.setAlternatingRowColors(True)
        results_layout.addWidget(tree, 1)
        splitter.addWidget(results)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([180, 520])
        layout.addWidget(splitter, 1)
        status = QLabel('Press Check books to ask Gemini. Big changes (a very different title or author) are left unticked.')
        status.setWordWrap(True)
        layout.addWidget(status)

        buttons = QDialogButtonBox()
        check_btn = QPushButton('Check books')
        apply_btn = QPushButton('Apply ticked changes')
        apply_btn.setEnabled(False)
        undo_btn = QPushButton('Undo last run')
        undo_btn.setEnabled(bool(prefs['undo']))
        buttons.addButton(check_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(undo_btn, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def api_key():
            return key.text().strip() or os.environ.get('GEMINI_API_KEY', '') or os.environ.get('GOOGLE_API_KEY', '')

        def chosen_fields():
            return tuple(f for f, _ in FIELD_LABELS if boxes[f].isChecked())

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
            ticked.setText(f'{count} of {total} change(s) ticked' if total else '')
            apply_btn.setEnabled(count > 0 and not run_busy[0])

        run_busy = [False]
        all_btn.clicked.connect(lambda: set_checks(lambda top: True))
        none_btn.clicked.connect(lambda: set_checks(lambda top: False))
        safe_btn.clicked.connect(lambda: set_checks(lambda top: not top.data(0, Qt.ItemDataRole.UserRole + 2)))
        tree.itemChanged.connect(lambda item, column: update_ticked())

        def toggle_settings():
            settings.setHidden(not settings.isHidden())
            settings_btn.setText('Show settings' if settings.isHidden() else 'Hide settings')

        settings_btn.clicked.connect(toggle_settings)

        def log(text):
            activity.appendPlainText(f'{time.strftime("%H:%M:%S")}  {text}')

        def set_busy(busy):
            run_busy[0] = busy
            for w in (all_btn, none_btn, safe_btn):
                w.setEnabled(not busy)
            for w in (check_btn, apply_btn, undo_btn, models_btn):
                w.setEnabled(not busy and (w is not apply_btn) and (w is not undo_btn or bool(prefs['undo'])))
            stop_btn.setEnabled(busy)
            buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(not busy)

        stop_btn.clicked.connect(lambda: (cancel_flag.set(), log('Cancel pressed; stopping after the current step.')))
        dialog.finished.connect(lambda _result: cancel_flag.set())

        def do_list_models():
            save_prefs()
            if not api_key():
                return error_dialog(dialog, 'Gemini', 'Enter your API key first.', show=True)
            task = run_task(dialog, 'Asking Google for the model list…', lambda t: engine.list_models(api_key(), cancelled=t.cancelled))
            if task.error:
                return error_dialog(dialog, 'Gemini', str(task.error), show=True)
            if task.result:
                current = model.currentText()
                model.clear()
                model.addItems(task.result)
                model.setCurrentText(current)

        def do_check():
            save_prefs()
            if not api_key():
                return error_dialog(dialog, 'Gemini', 'Enter your Google AI API key first.', show=True)
            if not chosen_fields():
                return error_dialog(dialog, 'Gemini', 'Tick at least one thing to fix.', show=True)
            ids = selected if scope_selected.isChecked() else sorted(self.gui.current_db.new_api.all_book_ids())
            tree.clear()
            update_ticked()
            activity.clear()
            cancel_flag.clear()
            set_busy(True)
            if not settings.isHidden():
                toggle_settings()  # more room for the log; the button brings the settings back
            splitter.setSizes([520, 180])
            try:
                log(f'Reading {len(ids)} book(s) from your library' + (' (including the opening text of EPUBs)' if excerpt.isChecked() else '') + (' and their cover pictures' if covers.isChecked() else '') + '…')
                bar.setRange(0, max(1, len(ids)))
                bar.setValue(0)

                read_messages = queue.Queue()
                box = {}

                def read_work():
                    try:
                        box['books'] = self.books_for(
                            ids,
                            excerpt.isChecked(),
                            on_book=lambda index, total, book: read_messages.put(('progress', (index, total, book))),
                            should_stop=cancel_flag.is_set,
                            with_covers=covers.isChecked()
                        )
                    except Exception as exc:
                        box['error'] = exc

                reader = threading.Thread(target=read_work, daemon=True)
                reader.start()

                while reader.is_alive() or not read_messages.empty():
                    while not read_messages.empty():
                        kind, info = read_messages.get()
                        if kind == 'progress':
                            index, total, book = info
                            bar.setValue(index)
                            phase.setText(f'Reading your library: book {index} of {total}')
                            log(f'  read {index}/{total}: {engine.describe_book(book)}')
                    if cancel_flag.is_set():
                        phase.setText('Cancelling… stopping library scan')
                    else:
                        phase.setText('Reading your library…')
                    QApplication.processEvents()
                    time.sleep(0.02)

                if box.get('error'):
                    log(f'✖ Stopped: {box["error"]}')
                    return finish_run(None, 0, error=box['error'])

                books = box.get('books', [])
                if cancel_flag.is_set():
                    log('Cancelled while reading. Nothing was sent to Google.')
                    return finish_run(None, len(books))

                m_name = model.currentText().strip() or engine.DEFAULT_MODEL
                est = engine.estimate_batch_usage(
                    books,
                    batch_size=batch.value(),
                    covers=covers.isChecked(),
                    min_interval=interval.value(),
                    model=m_name
                )
                msg = (
                    f"Pre-Flight API Usage Estimate:\n\n"
                    f"• Selected Books: {est['books_count']}\n"
                    f"• Batch Requests: {est['batch_count']} ({est['per_request']} books/request)\n"
                    f"• Text Characters: {est['text_chars']:,}\n"
                    f"• Estimated Tokens: ~{est['prompt_tokens']:,} ({est['text_tokens']:,} text, {est['image_tokens']:,} image)\n"
                    f"• Estimated Duration: ~{est['estimated_duration']} seconds\n"
                    f"• Estimated Cost: ${est['estimated_cost']:.4f} USD (Model: {m_name})\n\n"
                    f"Do you want to proceed with sending these requests to Google Gemini?"
                )
                if not question_dialog(dialog, 'Gemini Pre-Flight Confirmation', msg):
                    log('Cancelled at pre-flight confirmation. Nothing was sent to Google.')
                    return finish_run(None, len(books))

                result, error = ask_gemini(books)
            finally:
                set_busy(False)
            return finish_run(result, len(books), error)

        def ask_gemini(books):
            """Runs the API calls in a thread; this loop shows what they report and keeps the window responsive."""
            messages = queue.Queue()
            box = {}
            total = len(books)
            per_request = min(batch.value(), 8) if covers.isChecked() else batch.value()  # pictures make requests heavy
            log(f'Sending {total} book(s) to Gemini ({model.currentText().strip() or engine.DEFAULT_MODEL}), {per_request} per request. Only the details shown above leave your computer.')
            bar.setRange(0, max(1, total))
            bar.setValue(0)

            def work():
                try:
                    box['result'] = engine.plan_fixes(
                        books, api_key(), model.currentText().strip() or engine.DEFAULT_MODEL, chosen_fields(), confidence.currentText(),
                        per_request, tags_remove.isChecked(), cancelled=cancel_flag.is_set, min_interval=interval.value(),
                        on_event=lambda kind, **info: messages.put((kind, info)))
                except Exception as exc:  # shown to the user by the caller
                    box['error'] = exc

            worker = threading.Thread(target=work, daemon=True)
            worker.start()
            waiting = {'since': None, 'count': 0}

            def handle(kind, info):
                if kind == 'sending':
                    batch_books = info['books']
                    waiting['since'], waiting['count'] = time.time(), len(batch_books)
                    log(f'→ Sending {len(batch_books)} book(s) to Gemini:')
                    for b in batch_books:
                        log(f'     {engine.describe_book(b)}')
                elif kind == 'received':
                    log(f'← Gemini answered after {time.time() - (waiting["since"] or time.time()):.0f}s ({info["count"]} entries)')
                    waiting['since'] = None
                elif kind == 'answer':
                    book, changes = info['book'], info['changes']
                    name = f"“{book.get('title') or book['id']}”"
                    if info.get('cover_text'):
                        log(f"   📖 read from the pictures for {name}: {info['cover_text']}")
                    if info['kept']:
                        log(f"   ✔ {name} [{info['confidence']}]: " + '; '.join(engine.describe_change(f, o, n) for f, (o, n) in changes.items())
                            + (f" — {info['reason']}" if info['reason'] else ''))
                    elif changes:
                        log(f"   ✖ {name}: suggestion dropped, {info['dropped_why']}")
                    else:
                        log(f'   – {name}: no change needed')
                elif kind == 'notice':
                    log(f"⚠ {info['text']}")
                elif kind == 'skipped':
                    log(f"✖ {info['text']}")
                elif kind == 'progress':
                    bar.setValue(info['done'])

            while worker.is_alive() or not messages.empty():
                while not messages.empty():
                    handle(*messages.get())
                if cancel_flag.is_set():
                    phase.setText('Cancelling… waiting for the request in progress to come back')
                elif waiting['since'] is not None:
                    phase.setText(f"Waiting for Gemini… {time.time() - waiting['since']:.0f}s so far ({waiting['count']} books in this request; usually 5–60s)")
                else:
                    phase.setText('Working…')
                QApplication.processEvents()
                time.sleep(0.05)
            return box.get('result'), box.get('error')

        def finish_run(result, count, error=None):
            phase.setText('Idle.')
            if error:
                log(f'✖ Stopped: {error}')
                return error_dialog(dialog, 'Gemini', str(error), show=True)
            if result is None:
                status.setText('Cancelled before anything was sent.')
                return
            suggestions, skipped = result
            cancelled_note = ' (cancelled early)' if cancel_flag.is_set() else ''
            log(f'Done: {len(suggestions)} of {count} book(s) have suggested changes{cancelled_note}. Tick what you want below, then press Apply.')
            for s in suggestions:
                top = QTreeWidgetItem(tree, [s['title'] or f"book {s['id']}", f"{s['confidence']} confidence" + ('; BIG CHANGE' if s['big'] else '')])
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
            text = f'{len(suggestions)} of {count} book(s) have suggested changes{cancelled_note}.'
            if skipped:
                text += f' {len(skipped)} could not be checked: ' + '; '.join(skipped[:3]) + (' …' if len(skipped) > 3 else '')
            status.setText(text)
            update_ticked()
            splitter.setSizes([120, 700])  # the changes are what needs reading now

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
                return error_dialog(dialog, 'Gemini', 'Nothing is ticked.', show=True)
            done, errors, undo = self.write_changes(items)
            if undo:
                prefs['undo'] = undo
                undo_btn.setEnabled(True)
            self.refresh(done)
            text = f'Updated {len(done)} book(s). "Undo last run" can put the old values back.'
            if errors:
                text += '\n\nProblems:\n' + '\n'.join(errors[:10])
            info_dialog(self.gui, 'Gemini', text, show=True)
            tree.clear()
            update_ticked()

        def do_undo():
            undo = dict(prefs['undo'])
            if not undo or not question_dialog(dialog, 'Gemini', f'Put back the old values for {len(undo)} book(s) changed in the last run?'):
                return
            done, errors = self.restore(undo)
            self.refresh(done)
            if not errors:
                prefs['undo'] = {}
                undo_btn.setEnabled(False)
            text = f'Restored {len(done)} book(s).' + ('\n\nProblems:\n' + '\n'.join(errors[:10]) if errors else '')
            info_dialog(self.gui, 'Gemini', text, show=True)

        key.editingFinished.connect(save_prefs)  # the key is remembered as soon as it is typed, not only after a check
        dialog.finished.connect(lambda _result: save_prefs())
        models_btn.clicked.connect(do_list_models)
        check_btn.clicked.connect(do_check)
        apply_btn.clicked.connect(do_apply)
        undo_btn.clicked.connect(do_undo)
        dialog.exec()
