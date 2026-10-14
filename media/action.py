import base64
import os
import queue
import threading
import time
from datetime import datetime

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                     QMenu, QPlainTextEdit, QProgressBar, QProgressDialog, QRadioButton, Qt, QToolButton, QTreeWidgetItem, QVBoxLayout)

from calibre_plugins.media_matcher import matching, sources, tags as audio
from calibre_plugins.media_matcher.config import prefs
try:
    from calibre_plugins.media_matcher.guikit import ThemeTokenEngine, create_token_button, create_token_tree, run_task
except ImportError:
    from common.guikit import ThemeTokenEngine, create_token_button, create_token_tree, run_task

KIND_CHOICES = [('auto', 'Detect from each file'), ('music', 'Music'), ('audiobook', 'Audiobook'), ('book', 'Book')]
FIELD_LABELS = [('title', 'Titles'), ('authors', 'Artists / authors'), ('album', 'Album (as series) and track number'),
                ('date', 'Release date'), ('tags', 'Music / Audiobook tag'), ('subjects', 'Open Library subjects as tags (can be noisy)'),
                ('identifiers', 'MusicBrainz / Open Library ids'), ('cover', 'Cover (only for books without one)')]
SHOW = {'title': 'Title', 'authors': 'By', 'album': 'Album', 'date': 'Date', 'tags': 'Tags', 'identifiers': 'Ids', 'cover': 'Cover'}
SOURCE_NAMES = {'musicbrainz': 'MusicBrainz', 'openlibrary': 'Open Library'}
PRIVACY = ('Lookups send only the title, artist, album and length of a track (or an audiobook\'s title and author) to MusicBrainz and '
           'Open Library; with audio fingerprints on, a short fingerprint of the audio goes to AcoustID. No file is uploaded. '
           'Nothing changes in your library until you press Apply, and "Undo last run" puts the old values back.')
ADD_NOTE = ('Each audio file becomes its own book, because Calibre keeps one file per format: the album is the series and the track '
            'number is the series number, so an album sorts and groups naturally. Files are copied into your library.')


def show_value(field, value):
    if field == 'album':
        name, index = value
        return (f'{name} #{index:g}' if name and index is not None else name) or '(none)'
    if field == 'date':
        return value[:10] if value else '(none)'
    if field == 'authors' or field == 'tags':
        return ', '.join(value) if value else '(none)'
    if field == 'identifiers':
        return ', '.join(f'{k}:{v}' for k, v in sorted(value.items())) or '(none)'
    if field == 'cover':
        return 'none' if value is None else ('front cover from ' + ('Cover Art Archive' if 'coverartarchive' in value[0] else 'Open Library'))
    return value or '(none)'


def split_artists(text):
    return [a.strip() for a in (text or '').split(' / ') if a.strip()]


class MediaMatcherAction(InterfaceAction):
    name = 'Media Matcher'
    action_spec = ('Music & audiobooks', None, 'Add music files and audiobooks, and match music, audiobooks and books against free databases', None)
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        self.create_menu_action(menu, 'media-matcher-add', 'Add music and audiobooks…', triggered=self.add_dialog)
        self.create_menu_action(menu, 'media-matcher-match', 'Match with MusicBrainz / Open Library…', triggered=self.match_dialog)

    # -- reading and writing the library ------------------------------------------------------------------------------
    def books_for(self, ids):
        db = self.gui.current_db.new_api
        books = []
        for book_id in ids:
            mi = db.get_metadata(book_id, get_cover=False)
            formats = [f.upper() for f in (db.formats(book_id) or [])]
            path = ''
            for fmt in formats:
                if fmt in audio.AUDIO_FORMATS:
                    path = db.format_abspath(book_id, fmt) or ''
                    if path:
                        break
            pub = mi.pubdate
            books.append({
                'id': book_id, 'title': mi.title or '', 'authors': list(mi.authors or []), 'series': mi.series or '',
                'series_index': mi.series_index if mi.series else None, 'tags': list(mi.tags or []), 'formats': formats,
                'identifiers': dict(mi.identifiers or {}), 'publisher': mi.publisher or '', 'path': path,
                'pubdate': pub.isoformat() if pub is not None and getattr(pub, 'year', 0) > 1900 else None,
                'has_cover': bool(db.field_for('cover', book_id))})
        return books

    def write_changes(self, items, covers):
        """items: [(book_id, {field: (old, new)})]; covers: {book_id: image bytes}. Returns (changed_ids, errors, undo)."""
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
                    elif field == 'album':
                        db.set_field('series', {book_id: new[0]})
                        if new[1] is not None:
                            db.set_field('series_index', {book_id: new[1]})
                    elif field == 'date':
                        db.set_field('pubdate', {book_id: datetime.fromisoformat(new)})
                    elif field == 'tags':
                        db.set_field('tags', {book_id: tuple(new)})
                    elif field == 'identifiers':
                        db.set_field('identifiers', {book_id: dict(new)})
                    elif field == 'cover':
                        if book_id in covers:
                            old_cover = db.cover(book_id) if hasattr(db, 'cover') else None
                            saved['cover'] = base64.b64encode(old_cover).decode('ascii') if old_cover else None
                            db.set_cover({book_id: covers[book_id]})
                        continue
                    saved[field] = old
                done.append(book_id)
            except Exception as exc:
                errors.append(f'book {book_id}: {exc}')
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
                    elif field == 'album':
                        db.set_field('series', {book_id: old[0] or None})
                        if old[0] and old[1] is not None:
                            db.set_field('series_index', {book_id: old[1]})
                    elif field == 'date':
                        db.set_field('pubdate', {book_id: datetime.fromisoformat(old) if old else None})
                    elif field == 'tags':
                        db.set_field('tags', {book_id: tuple(old)})
                    elif field == 'identifiers':
                        db.set_field('identifiers', {book_id: dict(old)})
                    elif field == 'cover':
                        if old is None:
                            if hasattr(db, 'remove_cover'):
                                db.remove_cover([book_id])
                            else:
                                db.set_cover({book_id: None})
                        else:
                            cover_bytes = base64.b64decode(old) if isinstance(old, str) else old
                            db.set_cover({book_id: cover_bytes})
                done.append(book_id)
            except Exception as exc:
                errors.append(f'book {book_id}: {exc}')
        return done, errors

    def refresh(self, ids, new=False):
        if new:
            self.gui.library_view.model().refresh()
        elif ids:
            self.gui.library_view.model().refresh_ids(ids)
        self.gui.tags_view.recount()

    def existing_keys(self):
        """(title, first author, album, format) of everything already in the library, to skip files that are already there."""
        db = self.gui.current_db.new_api
        ids = db.all_book_ids()
        titles, authors = db.all_field_for('title', ids), db.all_field_for('authors', ids)
        series, formats = db.all_field_for('series', ids), db.all_field_for('formats', ids)
        keys = set()
        for book_id in ids:
            first = (authors.get(book_id) or ('',))[0]
            for fmt in formats.get(book_id) or ():
                keys.add((matching.norm(titles.get(book_id)), matching.norm(first), matching.norm(series.get(book_id)), fmt.upper()))
        return keys

    # -- adding audio files -------------------------------------------------------------------------------------------
    def entry_for(self, path, forced):
        t = audio.tags_with_fallback(path)
        kind = forced if forced in ('music', 'audiobook') else ('audiobook' if audio.is_audiobook(t, path) else 'music')
        authors = split_artists(t['artist']) or split_artists(t['album_artist']) or ['Unknown']
        return {'path': path, 'tags': t, 'kind': kind, 'authors': authors, 'ext': os.path.splitext(path)[1].lstrip('.').upper()}

    def add_entries(self, entries, skip_existing, on_progress=lambda done, total: False):
        """Create one book per entry. Runs on the main thread, because Calibre's database events reach the window; on_progress
        keeps the window alive and returns True to stop. Returns (added ids, skipped count, errors)."""
        from calibre.ebooks.metadata.book.base import Metadata
        db = self.gui.current_db.new_api
        keys = self.existing_keys() if skip_existing else set()
        added, skipped, errors = [], 0, []
        for done, e in enumerate(entries):
            if on_progress(done, len(entries)):
                break
            t = e['tags']
            key = (matching.norm(t['title']), matching.norm(e['authors'][0]), matching.norm(t['album']), e['ext'])
            if key in keys:
                skipped += 1
                continue
            try:
                mi = Metadata(t['title'], e['authors'])
                if t['album']:
                    mi.series = t['album']
                    mi.series_index = float(t['track']) if t['track'] else 1.0
                mi.tags = [matching.KIND_TAGS[e['kind']]] + ([t['genre']] if t['genre'] and e['kind'] == 'music' else [])
                when = matching.parse_date(t['date'])
                if when:
                    mi.pubdate = when
                for ident, value in (('mb_recording', t['mb_recording']), ('mb_release', t['mb_release'])):
                    if value:
                        mi.set_identifier(ident, value)
                ids, _dupes = db.add_books([(mi, {e['ext']: e['path']})])
                if not ids:
                    raise ValueError('Calibre did not add the file')
                if t['cover']:
                    db.set_cover({ids[0]: t['cover'][1]})
                added.append(ids[0])
                keys.add(key)
            except Exception as exc:
                errors.append(f"{os.path.basename(e['path'])}: {exc}")
        return added, skipped, errors

    def add_dialog(self):
        dialog = QDialog(self.gui)
        ThemeTokenEngine.register_widget(dialog)
        dialog.setWindowTitle('Add music and audiobooks')
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        note = QLabel(ADD_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        files_btn = create_token_button('Choose files…')
        folder_btn = create_token_button('Choose a folder (includes subfolders)…')
        row.addWidget(files_btn)
        row.addWidget(folder_btn)
        row.addWidget(QLabel('Treat as:'))
        treat = QComboBox()
        for value, label in KIND_CHOICES[:3]:
            treat.addItem(label, value)
        treat.setCurrentIndex(max(0, treat.findData(prefs['add_kind'])))
        row.addWidget(treat)
        row.addStretch(1)
        layout.addLayout(row)
        tree = create_token_tree(header_labels=['File', 'Title', 'Artist', 'Album', '#', 'Kind'])
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 220)
        tree.setColumnWidth(1, 220)
        tree.setColumnWidth(2, 160)
        tree.setColumnWidth(3, 160)
        tree.setColumnWidth(4, 40)
        layout.addWidget(tree, 1)
        skip = QCheckBox('Skip files already in the library (same title, artist, album and format)')
        skip.setChecked(bool(prefs['skip_existing']))
        layout.addWidget(skip)
        lookup = QCheckBox('When added, look the new books up on MusicBrainz / Open Library')
        lookup.setChecked(bool(prefs['lookup_after_add']))
        layout.addWidget(lookup)
        status = QLabel('Choose audio files (MP3, FLAC, Ogg, Opus, M4A, M4B) or a folder of them.')
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QDialogButtonBox()
        add_btn = create_token_button('Add ticked files')
        add_btn.setEnabled(False)
        buttons.addButton(add_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        entries = {}
        counter = iter(range(1, 10 ** 9))

        def load(paths):
            known = {e['path'] for e in entries.values()}
            paths = [p for p in dict.fromkeys(paths) if p not in known]
            if not paths:
                return
            forced = treat.currentData()
            task = run_task(dialog, 'Reading tags…', lambda t: [self.entry_for(p, forced) if not t.cancelled() else None for p in paths])
            if task.error:
                return error_dialog(dialog, 'Media Matcher', str(task.error), show=True)
            for e in task.result or []:
                if e is None:
                    continue
                t = e['tags']
                note_from_path = ' (from file name)' if t['from_path'] else ''
                item = QTreeWidgetItem(tree, [os.path.basename(e['path']), t['title'], ', '.join(e['authors']), t['album'],
                                              str(t['track'] or ''), e['kind'] + note_from_path])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                key = next(counter)
                entries[key] = e
                item.setData(0, Qt.ItemDataRole.UserRole, key)
            add_btn.setEnabled(tree.topLevelItemCount() > 0)
            status.setText(f'{tree.topLevelItemCount()} file(s) ready. Untick any you do not want, then press Add.')

        def choose_files():
            exts = ' '.join(f'*.{e}' for e in sorted(audio.AUDIO_EXTENSIONS))
            paths, _ = QFileDialog.getOpenFileNames(dialog, 'Choose audio files', '', f'Audio files ({exts});;All files (*)')
            load(list(paths))

        def choose_folder():
            folder = QFileDialog.getExistingDirectory(dialog, 'Choose a folder of audio files')
            if not folder:
                return
            task = run_task(dialog, 'Looking for audio files…', lambda t: audio.scan_folder(folder, t.cancelled))
            if task.error:
                return error_dialog(dialog, 'Media Matcher', str(task.error), show=True)
            if not task.result:
                return info_dialog(dialog, 'Media Matcher', 'No audio files were found in that folder.', show=True)
            load(task.result)

        def do_add():
            chosen = []
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                if item.checkState(0) == Qt.CheckState.Checked:
                    e = dict(entries[item.data(0, Qt.ItemDataRole.UserRole)])
                    if treat.currentData() in ('music', 'audiobook'):
                        e['kind'] = treat.currentData()
                    chosen.append(e)
            if not chosen:
                return error_dialog(dialog, 'Media Matcher', 'Nothing is ticked.', show=True)
            prefs['add_kind'] = treat.currentData()
            prefs['skip_existing'] = skip.isChecked()
            prefs['lookup_after_add'] = lookup.isChecked()
            progress = QProgressDialog(f'Adding {len(chosen)} file(s) to your library…', 'Cancel', 0, len(chosen), dialog)
            progress.setWindowTitle('Media Matcher')
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)

            def on_progress(done, total):
                progress.setValue(done)
                progress.setLabelText(f'Adding file {done + 1} of {total}…')
                QApplication.processEvents()
                return progress.wasCanceled()

            added, skipped, errors = self.add_entries(chosen, skip.isChecked(), on_progress)
            progress.blockSignals(True)
            progress.close()
            if added:
                self.refresh(added, new=True)
            text = f'Added {len(added)} book(s).' + (f' Skipped {skipped} already in the library.' if skipped else '')
            if errors:
                text += '\n\nProblems:\n' + '\n'.join(errors[:10])
            info_dialog(self.gui, 'Media Matcher', text, show=True)
            tree.clear()
            entries.clear()
            add_btn.setEnabled(False)
            status.setText(text.split('\n')[0])
            if added and lookup.isChecked():
                dialog.accept()
                self.match_dialog(ids=added)

        files_btn.clicked.connect(choose_files)
        folder_btn.clicked.connect(choose_folder)
        add_btn.clicked.connect(do_add)
        dialog.exec()

    # -- matching -----------------------------------------------------------------------------------------------------
    def match_dialog(self, ids=None):
        selected = list(ids) if ids else list(self.gui.library_view.get_selected_ids())
        dialog = QDialog(self.gui)
        ThemeTokenEngine.register_widget(dialog)
        dialog.setWindowTitle('Match with MusicBrainz / Open Library')
        dialog.resize(860, 820)
        layout = QVBoxLayout(dialog)
        note = QLabel(PRIVACY)
        note.setWordWrap(True)
        layout.addWidget(note)
        scope_selected = QRadioButton(f'Selected books ({len(selected)})')
        scope_all = QRadioButton('Entire library (one lookup a second; start with a few books)')
        (scope_selected if selected else scope_all).setChecked(True)
        scope_selected.setEnabled(bool(selected))
        layout.addWidget(scope_selected)
        layout.addWidget(scope_all)

        row = QHBoxLayout()
        row.addWidget(QLabel('These are:'))
        kind = QComboBox()
        for value, label in KIND_CHOICES:
            kind.addItem(label, value)
        kind.setCurrentIndex(max(0, kind.findData(prefs['kind'])))
        kind.setToolTip('Music is matched on MusicBrainz; audiobooks and books on Open Library')
        row.addWidget(kind)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(QLabel('Fix:'))
        boxes = {}
        for field, label in FIELD_LABELS:
            boxes[field] = QCheckBox(label)
            boxes[field].setChecked(field in prefs['fields'])
            layout.addWidget(boxes[field])

        row = QHBoxLayout()
        row.addWidget(QLabel('Contact for MusicBrainz (optional email or web page):'))
        contact = QLineEdit(prefs['contact'])
        contact.setPlaceholderText('MusicBrainz asks apps to say who is calling; this is sent in the User-Agent')
        row.addWidget(contact, 1)
        layout.addLayout(row)
        fingerprint = QCheckBox('Identify music by audio fingerprint (AcoustID; needs the free key below and Chromaprint\'s fpcalc)')
        fingerprint.setChecked(bool(prefs['fingerprint']))
        layout.addWidget(fingerprint)
        row = QHBoxLayout()
        row.addWidget(QLabel('AcoustID key:'))
        acoustid = QLineEdit(prefs['acoustid_key'])
        acoustid.setEchoMode(QLineEdit.EchoMode.Password)
        acoustid.setPlaceholderText('Free application key from https://acoustid.org/new-application')
        row.addWidget(acoustid, 1)
        row.addWidget(QLabel('fpcalc:'))
        fpcalc = QLineEdit(prefs['fpcalc'])
        fpcalc.setPlaceholderText('Leave empty to search your PATH')
        row.addWidget(fpcalc, 1)
        layout.addLayout(row)

        row = QHBoxLayout()
        bar = QProgressBar()
        bar.setRange(0, 1)
        bar.setValue(0)
        bar.setFormat('%v of %m')
        row.addWidget(bar, 1)
        stop_btn = create_token_button('Cancel')
        stop_btn.setEnabled(False)
        row.addWidget(stop_btn)
        layout.addLayout(row)
        phase = QLabel('Idle.')
        layout.addWidget(phase)
        activity = QPlainTextEdit()
        activity.setReadOnly(True)
        activity.setMaximumBlockCount(5000)
        activity.setPlaceholderText('Each book as it is looked up, what was searched for, and what came back.')
        layout.addWidget(activity, 2)
        tree = create_token_tree(header_labels=['Suggested change', 'Match'])
        tree.setColumnWidth(0, 440)
        layout.addWidget(tree, 3)
        status = QLabel('Press Look up. Weak matches are not offered; uncertain ones are left unticked.')
        status.setWordWrap(True)
        layout.addWidget(status)

        buttons = QDialogButtonBox()
        check_btn = create_token_button('Look up')
        apply_btn = create_token_button('Apply ticked changes')
        apply_btn.setEnabled(False)
        undo_btn = create_token_button('Undo last run')
        undo_btn.setEnabled(bool(prefs['undo']))
        buttons.addButton(check_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(undo_btn, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        cancel_flag = threading.Event()
        suggestions = {}

        def chosen_fields():
            return tuple(f for f, _ in FIELD_LABELS if boxes[f].isChecked())

        def save_prefs():
            prefs['kind'] = kind.currentData()
            prefs['fields'] = list(chosen_fields())
            prefs['contact'] = contact.text().strip()
            prefs['acoustid_key'] = acoustid.text().strip()
            prefs['fpcalc'] = fpcalc.text().strip()
            prefs['fingerprint'] = fingerprint.isChecked()

        def log(text):
            activity.appendPlainText(f'{time.strftime("%H:%M:%S")}  {text}')

        def set_busy(busy):
            for w in (check_btn,):
                w.setEnabled(not busy)
            apply_btn.setEnabled(False)
            undo_btn.setEnabled(not busy and bool(prefs['undo']))
            stop_btn.setEnabled(busy)
            buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(not busy)

        stop_btn.clicked.connect(lambda: (cancel_flag.set(), log('Cancel pressed; stopping after the current lookup.')))
        dialog.finished.connect(lambda _result: cancel_flag.set())

        def options():
            return {'kind': kind.currentData(), 'fields': chosen_fields(), 'min_score': float(prefs['min_score']),
                    'acoustid_key': acoustid.text().strip(), 'fingerprint': fingerprint.isChecked(),
                    'fpcalc': sources.find_fpcalc(fpcalc.text()) or ''}

        def do_check():
            save_prefs()
            if not chosen_fields():
                return error_dialog(dialog, 'Media Matcher', 'Tick at least one thing to fix.', show=True)
            opts = options()
            if fingerprint.isChecked() and not (opts['acoustid_key'] and opts['fpcalc']):
                return error_dialog(dialog, 'Media Matcher', 'Audio fingerprints need an AcoustID key and the fpcalc program '
                                    '(Chromaprint). Enter the key and, if fpcalc is not on your PATH, its location, or untick the option.', show=True)
            book_ids = selected if scope_selected.isChecked() else sorted(self.gui.current_db.new_api.all_book_ids())
            if len(book_ids) > 50 and not question_dialog(
                    dialog, 'Media Matcher', f'{len(book_ids)} books will be looked up at about one a second '
                    f'(roughly {len(book_ids) // 60 + 1} minutes). You can cancel at any time and keep what was found. Continue?'):
                return
            tree.clear()
            suggestions.clear()
            activity.clear()
            cancel_flag.clear()
            set_busy(True)
            try:
                log(f'Reading {len(book_ids)} book(s) from your library…')
                books = self.books_for(book_ids)
                result = run_lookups(books, opts)
            finally:
                set_busy(False)
            finish_run(result, len(books))

        def run_lookups(books, opts):
            messages = queue.Queue()
            box = {}
            bar.setRange(0, max(1, len(books)))
            bar.setValue(0)
            http = sources.Http(contact=contact.text().strip(), cancelled=cancel_flag.is_set,
                                on_wait=lambda seconds, why: messages.put(('wait', {'seconds': seconds, 'why': why})))

            def work():
                try:
                    box['result'] = matching.plan_matches(books, http, opts, cancelled=cancel_flag.is_set,
                                                          on_event=lambda event, **info: messages.put((event, info)))
                except Exception as exc:  # shown to the user by the caller
                    box['error'] = exc

            worker = threading.Thread(target=work, daemon=True)
            worker.start()

            def handle(event, info):
                if event == 'book':
                    bar.setValue(info['done'] - 1)
                    phase.setText(f"Looking up {info['done']} of {info['total']}: {info['name']}")
                    log(f"{info['done']}/{info['total']}  {info['name']}")
                elif event == 'searching':
                    log(f"   searching: {info['query']}")
                elif event == 'fingerprint':
                    log('   fingerprinting the audio…')
                elif event == 'match':
                    c = info['candidate']
                    who = SOURCE_NAMES[c['source']]
                    what = f"{c['title']} — {', '.join(c['authors'])}" + (f" / {c['album']}" if c.get('album') else '')
                    extra = f" (ambiguous: {c['rivals']} similar)" if c.get('ambiguous') else ''
                    log(f"   ✔ {who} {c['score']:.0%}: {what}{extra}" + ('' if info['changes'] else ' — nothing to change'))
                elif event == 'nomatch':
                    log('   – no confident match' + (f" (best {info['best']:.0%})" if info.get('best') else ''))
                elif event in ('error', 'stopped', 'notice'):
                    log(f"   ⚠ {info.get('text')}")
                elif event == 'wait':
                    log(f"   waiting {info['seconds']:.0f}s: {info['why']}")
                    phase.setText(f"Waiting {info['seconds']:.0f}s: {info['why']}")

            while worker.is_alive() or not messages.empty():
                while not messages.empty():
                    handle(*messages.get())
                QApplication.processEvents()
                time.sleep(0.05)
            bar.setValue(bar.maximum() if not cancel_flag.is_set() else bar.value())
            return box.get('result'), box.get('error')

        def finish_run(result, count):
            result, error = result
            phase.setText('Idle.')
            if error:
                log(f'✖ Stopped: {error}')
                return error_dialog(dialog, 'Media Matcher', str(error), show=True)
            found, skipped = result
            note_cancel = ' (cancelled early)' if cancel_flag.is_set() else ''
            log(f'Done: {len(found)} of {count} book(s) have suggested changes{note_cancel}. Tick what you want, then press Apply.')
            for s in found:
                suggestions[s['id']] = s
                c = s['candidate']
                flags = ''
                if s['ambiguous']:
                    flags += f"; {c['rivals']} similar recording(s), check this one"
                if s['big']:
                    flags += '; BIG TITLE CHANGE'
                top = QTreeWidgetItem(tree, [s['title'] or f"book {s['id']}", f"{c['score']:.0%} {SOURCE_NAMES[c['source']]}: "
                                             f"{c['title']} — {', '.join(c['authors'])}" + (f" / {c['album']}" if c.get('album') else '') + flags])
                top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                sure = c['score'] >= matching.TICK_SCORE and not s['ambiguous'] and not s['big']
                want = Qt.CheckState.Checked if sure else Qt.CheckState.Unchecked
                top.setData(0, Qt.ItemDataRole.UserRole, s['id'])
                for field, (old, new) in s['changes'].items():
                    shown = f'{SHOW[field]}:  {show_value(field, old)}  →  {show_value(field, new)}' if field != 'cover' else \
                        f'Cover:  {show_value(field, new)}'
                    child = QTreeWidgetItem(top, [shown, ''])
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, want)
                    child.setData(0, Qt.ItemDataRole.UserRole, field)
                    child.setData(0, Qt.ItemDataRole.UserRole + 1, (old, new))
                top.setCheckState(0, want)
                top.setExpanded(True)
            text = f'{len(found)} of {count} book(s) have suggested changes{note_cancel}.'
            if skipped:
                text += f' {len(skipped)} problem(s): ' + '; '.join(skipped[:3]) + (' …' if len(skipped) > 3 else '')
            status.setText(text)
            apply_btn.setEnabled(bool(found))

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
                return error_dialog(dialog, 'Media Matcher', 'Nothing is ticked.', show=True)
            covers = {}
            wanted = {book_id: changes['cover'][1] for book_id, changes in items if 'cover' in changes}
            if wanted:
                http = sources.Http(contact=contact.text().strip())
                cache = {}

                def download(task):
                    for book_id, urls in wanted.items():
                        if task.cancelled():
                            break
                        task.status = f'Downloading cover art ({len(covers) + 1} of {len(wanted)})…'
                        key = tuple(urls)
                        if key not in cache:
                            try:
                                cache[key] = sources.fetch_image(http, urls)
                            except sources.ServiceError:
                                cache[key] = None
                        if cache[key]:
                            covers[book_id] = cache[key]

                task = run_task(dialog, 'Downloading cover art…', download)
                if task.error:
                    return error_dialog(dialog, 'Media Matcher', str(task.error), show=True)
            done, errors, undo = self.write_changes(items, covers)
            if undo:
                prefs['undo'] = undo
                undo_btn.setEnabled(True)
            self.refresh(done)
            missing = len(wanted) - len(covers)
            text = f'Updated {len(done)} book(s)' + (f' and {len(covers)} cover(s)' if wanted else '') + '. "Undo last run" can put the old values back.'
            if missing:
                text += f'\n\n{missing} ticked cover(s) could not be downloaded (the database has no front cover for them).'
            if errors:
                text += '\n\nProblems:\n' + '\n'.join(errors[:10])
            info_dialog(self.gui, 'Media Matcher', text, show=True)
            tree.clear()
            apply_btn.setEnabled(False)

        def do_undo():
            undo = dict(prefs['undo'])
            if not undo or not question_dialog(dialog, 'Media Matcher', f'Put back the old values for {len(undo)} book(s) changed in the last run?'):
                return
            done, errors = self.restore(undo)
            self.refresh(done)
            if not errors:
                prefs['undo'] = {}
                undo_btn.setEnabled(False)
            text = f'Restored {len(done)} book(s).' + ('\n\nProblems:\n' + '\n'.join(errors[:10]) if errors else '')
            info_dialog(self.gui, 'Media Matcher', text, show=True)

        for edit in (contact, acoustid, fpcalc):
            edit.editingFinished.connect(save_prefs)  # remembered as soon as typed, not only after a lookup
        dialog.finished.connect(lambda _result: save_prefs())
        check_btn.clicked.connect(do_check)
        apply_btn.clicked.connect(do_apply)
        undo_btn.clicked.connect(do_undo)
        dialog.exec()
