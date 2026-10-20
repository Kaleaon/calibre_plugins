import base64
import os
import queue
import threading
import time
from datetime import datetime

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                     QMenu, QPixmap, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QRadioButton, Qt, QTimer, QToolButton,
                     QTreeWidget, QTreeWidgetItem, QVBoxLayout)
try:
    from qt.core import _
except ImportError:
    _ = lambda s: s

from calibre_plugins.media_matcher import matching, sources, tags as audio
from calibre_plugins.media_matcher.config import prefs
from calibre_plugins.media_matcher.guikit import run_task

KIND_CHOICES = [('auto', _('Detect from each file')), ('music', _('Music')), ('audiobook', _('Audiobook')), ('book', _('Book'))]
FIELD_LABELS = [('title', _('Titles')), ('authors', _('Artists / authors')), ('album', _('Album (as series) and track number')),
                ('date', _('Release date')), ('tags', _('Music / Audiobook tag')), ('subjects', _('Open Library subjects as tags (can be noisy)')),
                ('identifiers', _('MusicBrainz / Open Library ids')), ('cover', _('Cover (only for books without one)'))]
SHOW = {'title': _('Title'), 'authors': _('By'), 'album': _('Album'), 'date': _('Date'), 'tags': _('Tags'), 'identifiers': _('Ids'), 'cover': _('Cover')}
SOURCE_NAMES = {'musicbrainz': 'MusicBrainz', 'openlibrary': 'Open Library'}
PRIVACY = _('Lookups send only the title, artist, album and length of a track (or an audiobook\'s title and author) to MusicBrainz and '
           'Open Library; with audio fingerprints on, a short fingerprint of the audio goes to AcoustID. No file is uploaded. '
           'Nothing changes in your library until you press Apply, and "Undo last run" puts the old values back.')
ADD_NOTE = _('Each audio file becomes its own book, because Calibre keeps one file per format: the album is the series and the track '
            'number is the series number, so an album sorts and groups naturally. Files are copied into your library.')


def show_value(field, value):
    if field == 'album':
        name, index = value
        return (f'{name} #{index:g}' if name and index is not None else name) or _('(none)')
    if field == 'date':
        return value[:10] if value else _('(none)')
    if field == 'authors' or field == 'tags':
        return ', '.join(value) if value else _('(none)')
    if field == 'identifiers':
        return ', '.join(f'{k}:{v}' for k, v in sorted(value.items())) or _('(none)')
    if field == 'cover':
        return _('none') if value is None else (_('front cover from ') + (_('Cover Art Archive') if 'coverartarchive' in value[0] else 'Open Library'))
    return value or _('(none)')


def split_artists(text):
    return [a.strip() for a in (text or '').split(' / ') if a.strip()]


class ManualSearchDialog(QDialog):
    def __init__(self, parent, initial_metadata=None, http=None, options=None):
        super().__init__(parent)
        self.setWindowTitle(_('Manual Search / Lookup by ID'))
        self.resize(780, 580)
        self.http = http or sources.Http()
        self.options = options or {}
        self.selected_candidate = None
        self.accepted_candidate = None
        self.candidates = []

        initial = initial_metadata or {}
        title = initial.get('title', '')
        authors = initial.get('authors') or []
        artist = ', '.join(authors) if authors else ''
        album = initial.get('series') or initial.get('album', '')
        identifiers = initial.get('identifiers') or {}
        direct_id = ''
        for key in ('mb_recording', 'mb_release', 'olid', 'isbn'):
            if key in identifiers and identifiers[key]:
                direct_id = f"{key}:{identifiers[key]}"
                break

        layout = QVBoxLayout(self)

        fields_layout = QVBoxLayout()

        r1 = QHBoxLayout()
        r1.addWidget(QLabel(_('Title:')))
        self.title_edit = QLineEdit(title)
        r1.addWidget(self.title_edit, 1)

        r1.addWidget(QLabel(_('Artist / Author:')))
        self.artist_edit = QLineEdit(artist)
        r1.addWidget(self.artist_edit, 1)
        fields_layout.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(QLabel(_('Album:')))
        self.album_edit = QLineEdit(album)
        r2.addWidget(self.album_edit, 1)

        r2.addWidget(QLabel(_('Direct Identifier:')))
        self.id_edit = QLineEdit(direct_id)
        self.id_edit.setPlaceholderText(_('MusicBrainz MBID, Open Library OLID, or ISBN'))
        r2.addWidget(self.id_edit, 1)
        fields_layout.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(QLabel(_('Search in:')))
        self.kind_combo = QComboBox()
        self.kind_combo.addItem(_('Auto detect'), 'auto')
        self.kind_combo.addItem(_('Music (MusicBrainz)'), 'music')
        self.kind_combo.addItem(_('Audiobook (Open Library)'), 'audiobook')
        self.kind_combo.addItem(_('Book (Open Library)'), 'book')
        r3.addWidget(self.kind_combo)

        self.search_btn = QPushButton(_('Search'))
        r3.addWidget(self.search_btn)
        r3.addStretch(1)
        fields_layout.addLayout(r3)

        layout.addLayout(fields_layout)

        layout.addWidget(QLabel(_('Candidates / Direct ID Results:')))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_('Title'), _('Artist / Author'), _('Album / Series'), _('Score'), _('Source')])
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 180)
        self.tree.setColumnWidth(2, 160)
        self.tree.setColumnWidth(3, 70)
        layout.addWidget(self.tree, 1)

        preview_layout = QHBoxLayout()
        self.cover_label = QLabel()
        self.cover_label.setFixedSize(80, 110)
        self.cover_label.setStyleSheet('border: 1px solid #ccc;')
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setText(_('No cover'))
        preview_layout.addWidget(self.cover_label)

        self.details_label = QLabel(_('Select a candidate to view details.'))
        self.details_label.setWordWrap(True)
        preview_layout.addWidget(self.details_label, 1)
        layout.addLayout(preview_layout)

        self.buttons = QDialogButtonBox()
        self.accept_btn = QPushButton(_('Use Selected Candidate'))
        self.accept_btn.setEnabled(False)
        self.buttons.addButton(self.accept_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(QDialogButtonBox.StandardButton.Cancel)

        self.search_btn.clicked.connect(self.do_search)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.accept_btn.clicked.connect(self.do_accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        if title or artist or direct_id:
            self.do_search()

    def do_search(self):
        title = self.title_edit.text().strip()
        artist = self.artist_edit.text().strip()
        album = self.album_edit.text().strip()
        direct_id = self.id_edit.text().strip()
        media_kind = self.kind_combo.currentData()

        self.search_btn.setEnabled(False)
        self.tree.clear()
        self.details_label.setText(_('Searching…'))
        QApplication.processEvents()

        try:
            cands = matching.manual_search(
                self.http, options=self.options, title=title, artist=artist,
                album=album, direct_id=direct_id, media_kind=media_kind
            )
            self.candidates = cands
            for c in cands:
                track_str = f" #{c['track']}" if c.get('track') else ''
                album_str = (c.get('album') or '') + track_str
                score_str = _('Direct ID') if c.get('by_id') else f"{c['score']:.0%}"
                source_str = SOURCE_NAMES.get(c['source'], c['source'])
                authors_str = ', '.join(c.get('authors') or [])

                item = QTreeWidgetItem(self.tree, [c['title'], authors_str, album_str, score_str, source_str])
                item.setData(0, Qt.ItemDataRole.UserRole, c)

            if not cands:
                self.details_label.setText(_('No candidates found matching search criteria.'))
            else:
                top = self.tree.topLevelItem(0)
                self.tree.setCurrentItem(top)
                top.setSelected(True)
        except Exception as exc:
            self.details_label.setText(_('Search error: {exc}').format(exc=exc))
        finally:
            self.search_btn.setEnabled(True)

    def on_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            self.accept_btn.setEnabled(False)
            self.selected_candidate = None
            self.details_label.setText(_('Select a candidate to view details.'))
            self.cover_label.setText(_('No cover'))
            self.cover_label.setPixmap(QPixmap())
            return

        c = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.selected_candidate = c
        self.accept_btn.setEnabled(True)

        info = [
            f"<b>{c['title']}</b>",
            f"{_('By')}: {', '.join(c.get('authors') or [])}",
        ]
        if c.get('album'):
            info.append(f"{_('Album')}: {c['album']}" + (f" #{c['track']}" if c.get('track') else ''))
        if c.get('date'):
            info.append(f"{_('Date')}: {c['date']}")
        if c.get('source'):
            info.append(f"{_('Source')}: {SOURCE_NAMES.get(c['source'], c['source'])}")

        self.details_label.setText('<br>'.join(info))

        if c.get('cover_urls'):
            self.cover_label.setText(_('Loading…'))
            QApplication.processEvents()
            try:
                img_data = sources.fetch_image(self.http, c['cover_urls'])
                if img_data:
                    pm = QPixmap()
                    if pm.loadFromData(img_data):
                        self.cover_label.setPixmap(pm.scaled(80, 110, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                        return
            except Exception:
                pass
        self.cover_label.setText(_('No cover'))
        self.cover_label.setPixmap(QPixmap())

    def do_accept(self):
        if self.selected_candidate:
            self.accepted_candidate = self.selected_candidate
            self.accept()


class MediaMatcherAction(InterfaceAction):
    name = 'Media Matcher'
    action_spec = (_('Music & audiobooks'), None, _('Add music files and audiobooks, and match music, audiobooks and books against free databases'), None)
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        self.create_menu_action(menu, 'media-matcher-add', _('Add music and audiobooks…'), triggered=self.add_dialog)
        self.create_menu_action(menu, 'media-matcher-match', _('Match with MusicBrainz / Open Library…'), triggered=self.match_dialog)
        self.create_menu_action(menu, 'media-matcher-manual', _('Manual Search / Lookup by ID…'), triggered=self.manual_search_dialog)

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
        dialog.setWindowTitle(_('Add music and audiobooks'))
        dialog.resize(900, 620)
        layout = QVBoxLayout(dialog)
        note = QLabel(ADD_NOTE)
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        files_btn = QPushButton(_('Choose files…'))
        folder_btn = QPushButton(_('Choose a folder (includes subfolders)…'))
        row.addWidget(files_btn)
        row.addWidget(folder_btn)
        row.addWidget(QLabel(_('Treat as:')))
        treat = QComboBox()
        for value, label in KIND_CHOICES[:3]:
            treat.addItem(label, value)
        treat.setCurrentIndex(max(0, treat.findData(prefs['add_kind'])))
        row.addWidget(treat)
        row.addStretch(1)
        layout.addLayout(row)
        tree = QTreeWidget()
        tree.setRootIsDecorated(False)
        tree.setHeaderLabels([_('File'), _('Title'), _('Artist'), _('Album'), '#', _('Kind')])
        tree.setColumnWidth(0, 220)
        tree.setColumnWidth(1, 220)
        tree.setColumnWidth(2, 160)
        tree.setColumnWidth(3, 160)
        tree.setColumnWidth(4, 40)
        layout.addWidget(tree, 1)
        skip = QCheckBox(_('Skip files already in the library (same title, artist, album and format)'))
        skip.setChecked(bool(prefs['skip_existing']))
        layout.addWidget(skip)
        lookup = QCheckBox(_('When added, look the new books up on MusicBrainz / Open Library'))
        lookup.setChecked(bool(prefs['lookup_after_add']))
        layout.addWidget(lookup)
        status = QLabel(_('Choose audio files (MP3, FLAC, Ogg, Opus, M4A, M4B) or a folder of them.'))
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QDialogButtonBox()
        add_btn = QPushButton(_('Add ticked files'))
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
            task = run_task(dialog, _('Reading tags…'), lambda t: [self.entry_for(p, forced) if not t.cancelled() else None for p in paths])
            if task.error:
                return error_dialog(dialog, 'Media Matcher', str(task.error), show=True)
            for e in task.result or []:
                if e is None:
                    continue
                t = e['tags']
                note_from_path = _(' (from file name)') if t['from_path'] else ''
                item = QTreeWidgetItem(tree, [os.path.basename(e['path']), t['title'], ', '.join(e['authors']), t['album'],
                                              str(t['track'] or ''), e['kind'] + note_from_path])
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked)
                key = next(counter)
                entries[key] = e
                item.setData(0, Qt.ItemDataRole.UserRole, key)
            add_btn.setEnabled(tree.topLevelItemCount() > 0)
            status.setText(_('{count} file(s) ready. Untick any you do not want, then press Add.').format(count=tree.topLevelItemCount()))

        def choose_files():
            exts = ' '.join(f'*.{e}' for e in sorted(audio.AUDIO_EXTENSIONS))
            paths, _ = QFileDialog.getOpenFileNames(dialog, _('Choose audio files'), '', f'Audio files ({exts});;All files (*)')
            load(list(paths))

        def choose_folder():
            folder = QFileDialog.getExistingDirectory(dialog, _('Choose a folder of audio files'))
            if not folder:
                return
            task = run_task(dialog, _('Looking for audio files…'), lambda t: audio.scan_folder(folder, t.cancelled))
            if task.error:
                return error_dialog(dialog, 'Media Matcher', str(task.error), show=True)
            if not task.result:
                return info_dialog(dialog, 'Media Matcher', _('No audio files were found in that folder.'), show=True)
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
                return error_dialog(dialog, 'Media Matcher', _('Nothing is ticked.'), show=True)
            prefs['add_kind'] = treat.currentData()
            prefs['skip_existing'] = skip.isChecked()
            prefs['lookup_after_add'] = lookup.isChecked()
            progress = QProgressDialog(_('Adding {count} file(s) to your library…').format(count=len(chosen)), _('Cancel'), 0, len(chosen), dialog)
            progress.setWindowTitle('Media Matcher')
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)

            def on_progress(done, total):
                progress.setValue(done)
                progress.setLabelText(_('Adding file {done} of {total}…').format(done=done + 1, total=total))
                QApplication.processEvents()
                return progress.wasCanceled()

            added, skipped, errors = self.add_entries(chosen, skip.isChecked(), on_progress)
            progress.blockSignals(True)
            progress.close()
            if added:
                self.refresh(added, new=True)
            text = _('Added {count} book(s).').format(count=len(added)) + (_(' Skipped {count} already in the library.').format(count=skipped) if skipped else '')
            if errors:
                text += '\n\n' + _('Problems:') + '\n' + '\n'.join(errors[:10])
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
        dialog.setWindowTitle(_('Match with MusicBrainz / Open Library'))
        dialog.resize(860, 820)
        layout = QVBoxLayout(dialog)
        note = QLabel(PRIVACY)
        note.setWordWrap(True)
        layout.addWidget(note)
        scope_selected = QRadioButton(_('Selected books ({count})').format(count=len(selected)))
        scope_all = QRadioButton(_('Entire library (one lookup a second; start with a few books)'))
        (scope_selected if selected else scope_all).setChecked(True)
        scope_selected.setEnabled(bool(selected))
        layout.addWidget(scope_selected)
        layout.addWidget(scope_all)

        row = QHBoxLayout()
        row.addWidget(QLabel(_('These are:')))
        kind = QComboBox()
        for value, label in KIND_CHOICES:
            kind.addItem(label, value)
        kind.setCurrentIndex(max(0, kind.findData(prefs['kind'])))
        kind.setToolTip(_('Music is matched on MusicBrainz; audiobooks and books on Open Library'))
        row.addWidget(kind)
        row.addStretch(1)
        layout.addLayout(row)

        layout.addWidget(QLabel(_('Fix:')))
        boxes = {}
        for field, label in FIELD_LABELS:
            boxes[field] = QCheckBox(label)
            boxes[field].setChecked(field in prefs['fields'])
            layout.addWidget(boxes[field])

        row = QHBoxLayout()
        row.addWidget(QLabel(_('Contact for MusicBrainz (optional email or web page):')))
        contact = QLineEdit(prefs['contact'])
        contact.setPlaceholderText(_('MusicBrainz asks apps to say who is calling; this is sent in the User-Agent'))
        row.addWidget(contact, 1)
        layout.addLayout(row)
        fingerprint = QCheckBox(_('Identify music by audio fingerprint (AcoustID; needs the free key below and Chromaprint\'s fpcalc)'))
        fingerprint.setChecked(bool(prefs['fingerprint']))
        layout.addWidget(fingerprint)
        row = QHBoxLayout()
        row.addWidget(QLabel(_('AcoustID key:')))
        acoustid = QLineEdit(prefs['acoustid_key'])
        acoustid.setEchoMode(QLineEdit.EchoMode.Password)
        acoustid.setPlaceholderText(_('Free application key from https://acoustid.org/new-application'))
        row.addWidget(acoustid, 1)
        row.addWidget(QLabel(_('fpcalc:')))
        fpcalc = QLineEdit(prefs['fpcalc'])
        fpcalc.setPlaceholderText(_('Leave empty to search your PATH'))
        row.addWidget(fpcalc, 1)
        layout.addLayout(row)

        row = QHBoxLayout()
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
        activity = QPlainTextEdit()
        activity.setReadOnly(True)
        activity.setMaximumBlockCount(5000)
        activity.setPlaceholderText(_('Each book as it is looked up, what was searched for, and what came back.'))
        layout.addWidget(activity, 2)
        tree_toolbar = QHBoxLayout()
        tree_manual_btn = QPushButton(_('Manual Search / Lookup by ID…'))
        tree_toolbar.addWidget(tree_manual_btn)
        tree_toolbar.addStretch(1)
        layout.addLayout(tree_toolbar)

        tree = QTreeWidget()
        tree.setHeaderLabels([_('Suggested change'), _('Match')])
        tree.setColumnWidth(0, 440)
        layout.addWidget(tree, 3)
        status = QLabel(_('Press Look up. Weak matches are not offered; uncertain ones are left unticked.'))
        status.setWordWrap(True)
        layout.addWidget(status)

        buttons = QDialogButtonBox()
        check_btn = QPushButton(_('Look up'))
        apply_btn = QPushButton(_('Apply ticked changes'))
        apply_btn.setEnabled(False)
        undo_btn = QPushButton(_('Undo last run'))
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

        stop_btn.clicked.connect(lambda: (cancel_flag.set(), log(_('Cancel pressed; stopping after the current lookup.'))))
        dialog.finished.connect(lambda _result: cancel_flag.set())

        def options():
            return {'kind': kind.currentData(), 'fields': chosen_fields(), 'min_score': float(prefs['min_score']),
                    'acoustid_key': acoustid.text().strip(), 'fingerprint': fingerprint.isChecked(),
                    'fpcalc': sources.find_fpcalc(fpcalc.text()) or ''}

        def do_check():
            save_prefs()
            if not chosen_fields():
                return error_dialog(dialog, 'Media Matcher', _('Tick at least one thing to fix.'), show=True)
            opts = options()
            if fingerprint.isChecked() and not (opts['acoustid_key'] and opts['fpcalc']):
                return error_dialog(dialog, 'Media Matcher', _('Audio fingerprints need an AcoustID key and the fpcalc program '
                                    '(Chromaprint). Enter the key and, if fpcalc is not on your PATH, its location, or untick the option.'), show=True)
            book_ids = selected if scope_selected.isChecked() else sorted(self.gui.current_db.new_api.all_book_ids())
            if len(book_ids) > 50 and not question_dialog(
                    dialog, 'Media Matcher', _('{count} books will be looked up at about one a second '
                    '(roughly {mins} minutes). You can cancel at any time and keep what was found. Continue?').format(count=len(book_ids), mins=len(book_ids) // 60 + 1)):
                return
            tree.clear()
            suggestions.clear()
            activity.clear()
            cancel_flag.clear()
            set_busy(True)
            try:
                log(_('Reading {count} book(s) from your library…').format(count=len(book_ids)))
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
                    phase.setText(_('Looking up {done} of {total}: {name}').format(done=info['done'], total=info['total'], name=info['name']))
                    log(f"{info['done']}/{info['total']}  {info['name']}")
                elif event == 'searching':
                    log(f"   searching: {info['query']}")
                elif event == 'fingerprint':
                    log(_('   fingerprinting the audio…'))
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
                    log(_('   waiting {seconds:.0f}s: {why}').format(seconds=info['seconds'], why=info['why']))
                    phase.setText(_('Waiting {seconds:.0f}s: {why}').format(seconds=info['seconds'], why=info['why']))

            while worker.is_alive() or not messages.empty():
                while not messages.empty():
                    handle(*messages.get())
                QApplication.processEvents()
                time.sleep(0.05)
            bar.setValue(bar.maximum() if not cancel_flag.is_set() else bar.value())
            return box.get('result'), box.get('error')

        def finish_run(result, count):
            result, error = result
            phase.setText(_('Idle.'))
            if error:
                log(f'✖ Stopped: {error}')
                return error_dialog(dialog, 'Media Matcher', str(error), show=True)
            found, skipped = result
            note_cancel = _(' (cancelled early)') if cancel_flag.is_set() else ''
            log(_('Done: {found} of {count} book(s) have suggested changes{cancel}. Tick what you want, then press Apply.').format(found=len(found), count=count, cancel=note_cancel))
            for s in found:
                suggestions[s['id']] = s
                c = s['candidate']
                flags = ''
                if s['ambiguous']:
                    flags += _('; {rivals} similar recording(s), check this one').format(rivals=c['rivals'])
                if s['big']:
                    flags += _('; BIG TITLE CHANGE')
                book_str = s['title'] or _('book {id}').format(id=s['id'])
                top = QTreeWidgetItem(tree, [book_str, f"{c['score']:.0%} {SOURCE_NAMES[c['source']]}: "
                                             f"{c['title']} — {', '.join(c['authors'])}" + (f" / {c['album']}" if c.get('album') else '') + flags])
                top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                sure = c['score'] >= matching.TICK_SCORE and not s['ambiguous'] and not s['big']
                want = Qt.CheckState.Checked if sure else Qt.CheckState.Unchecked
                top.setData(0, Qt.ItemDataRole.UserRole, s['id'])
                for field, (old, new) in s['changes'].items():
                    shown = f'{SHOW[field]}:  {show_value(field, old)}  →  {show_value(field, new)}' if field != 'cover' else \
                        f'{_("Cover")}:  {show_value(field, new)}'
                    child = QTreeWidgetItem(top, [shown, ''])
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, want)
                    child.setData(0, Qt.ItemDataRole.UserRole, field)
                    child.setData(0, Qt.ItemDataRole.UserRole + 1, (old, new))
                top.setCheckState(0, want)
                top.setExpanded(True)
            text = _('{found} of {count} book(s) have suggested changes{cancel}.').format(found=len(found), count=count, cancel=note_cancel)
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
                return error_dialog(dialog, 'Media Matcher', _('Nothing is ticked.'), show=True)
            covers = {}
            wanted = {book_id: changes['cover'][1] for book_id, changes in items if 'cover' in changes}
            if wanted:
                http = sources.Http(contact=contact.text().strip())
                cache = {}

                def download(task):
                    for book_id, urls in wanted.items():
                        if task.cancelled():
                            break
                        task.status = _('Downloading cover art ({done} of {total})…').format(done=len(covers) + 1, total=len(wanted))
                        key = tuple(urls)
                        if key not in cache:
                            try:
                                cache[key] = sources.fetch_image(http, urls)
                            except sources.ServiceError:
                                cache[key] = None
                        if cache[key]:
                            covers[book_id] = cache[key]

                task = run_task(dialog, _('Downloading cover art…'), download)
                if task.error:
                    return error_dialog(dialog, 'Media Matcher', str(task.error), show=True)
            done, errors, undo = self.write_changes(items, covers)
            if undo:
                prefs['undo'] = undo
                undo_btn.setEnabled(True)
            self.refresh(done)
            missing = len(wanted) - len(covers)
            text = _('Updated {done} book(s)').format(done=len(done)) + (_(' and {covers} cover(s)').format(covers=len(covers)) if wanted else '') + _('. "Undo last run" can put the old values back.')
            if missing:
                text += '\n\n' + _('{missing} ticked cover(s) could not be downloaded (the database has no front cover for them).').format(missing=missing)
            if errors:
                text += '\n\n' + _('Problems:') + '\n' + '\n'.join(errors[:10])
            info_dialog(self.gui, 'Media Matcher', text, show=True)
            tree.clear()
            apply_btn.setEnabled(False)

        def do_undo():
            undo = dict(prefs['undo'])
            if not undo or not question_dialog(dialog, 'Media Matcher', _('Put back the old values for {count} book(s) changed in the last run?').format(count=len(undo))):
                return
            done, errors = self.restore(undo)
            self.refresh(done)
            if not errors:
                prefs['undo'] = {}
                undo_btn.setEnabled(False)
            text = _('Restored {count} book(s).').format(count=len(done)) + ('\n\n' + _('Problems:') + '\n' + '\n'.join(errors[:10]) if errors else '')
            info_dialog(self.gui, 'Media Matcher', text, show=True)

        def do_manual_search_for_item(item=None):
            book_id = None
            top = None
            if item:
                top = item.parent() or item
                book_id = top.data(0, Qt.ItemDataRole.UserRole)
            if not book_id and selected:
                book_id = selected[0]

            if not book_id:
                return error_dialog(dialog, 'Media Matcher', _('Please select an item in the tree to perform a manual search.'), show=True)

            books_found = self.books_for([book_id])
            if not books_found:
                return
            book = books_found[0]

            http = sources.Http(contact=contact.text().strip())
            opts = options()

            dlg = ManualSearchDialog(dialog, initial_metadata=book, http=http, options=opts)
            dlg.exec()
            if dlg.accepted_candidate:
                cand = dlg.accepted_candidate
                media_k = matching.classify(book, kind.currentData())
                chg = matching.plan_changes(book, media_k, cand, chosen_fields())

                suggestions[book_id] = {
                    'id': book_id, 'title': book.get('title', ''), 'kind': media_k,
                    'score': cand['score'], 'candidate': cand, 'changes': chg,
                    'big': False, 'ambiguous': False
                }

                if top is None:
                    for i in range(tree.topLevelItemCount()):
                        t = tree.topLevelItem(i)
                        if t.data(0, Qt.ItemDataRole.UserRole) == book_id:
                            top = t
                            break
                if top is None:
                    top = QTreeWidgetItem(tree)

                while top.childCount() > 0:
                    top.removeChild(top.child(0))

                book_str = book.get('title') or _('book {id}').format(id=book_id)
                top.setText(0, book_str)
                top.setText(1, f"{cand['score']:.0%} {SOURCE_NAMES.get(cand['source'], cand['source'])}: "
                               f"{cand['title']} — {', '.join(cand['authors'])}" + (f" / {cand['album']}" if cand.get('album') else ''))
                top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                top.setData(0, Qt.ItemDataRole.UserRole, book_id)

                for field, (old, new) in chg.items():
                    shown = f'{SHOW[field]}:  {show_value(field, old)}  →  {show_value(field, new)}' if field != 'cover' else \
                        f'{_("Cover")}:  {show_value(field, new)}'
                    child = QTreeWidgetItem(top, [shown, ''])
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(0, Qt.CheckState.Checked)
                    child.setData(0, Qt.ItemDataRole.UserRole, field)
                    child.setData(0, Qt.ItemDataRole.UserRole + 1, (old, new))

                top.setCheckState(0, Qt.CheckState.Checked)
                top.setExpanded(True)
                apply_btn.setEnabled(True)

        def show_tree_context_menu(pos):
            item = tree.itemAt(pos)
            menu = QMenu(tree)
            act = menu.addAction(_('Manual Search / Lookup by ID…'))
            act.triggered.connect(lambda: do_manual_search_for_item(item or tree.currentItem()))
            menu.exec(tree.mapToGlobal(pos))

        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.customContextMenuRequested.connect(show_tree_context_menu)
        tree_manual_btn.clicked.connect(lambda: do_manual_search_for_item(tree.currentItem()))

        for edit in (contact, acoustid, fpcalc):
            edit.editingFinished.connect(save_prefs)  # remembered as soon as typed, not only after a lookup
        dialog.finished.connect(lambda _result: save_prefs())
        check_btn.clicked.connect(do_check)
        apply_btn.clicked.connect(do_apply)
        undo_btn.clicked.connect(do_undo)
        dialog.exec()

    def manual_search_dialog(self, book_id=None):
        if book_id is None:
            selected = list(self.gui.library_view.get_selected_ids())
            if not selected:
                return info_dialog(self.gui, 'Media Matcher', _('Please select a book in Calibre first.'), show=True)
            book_id = selected[0]

        books = self.books_for([book_id])
        if not books:
            return error_dialog(self.gui, 'Media Matcher', _('Could not read metadata for selected book.'), show=True)
        book = books[0]

        http = sources.Http(contact=prefs['contact'])
        opts = {'kind': prefs['kind'], 'fields': tuple(prefs['fields'])}

        dlg = ManualSearchDialog(self.gui, initial_metadata=book, http=http, options=opts)
        dlg.exec()
        if dlg.accepted_candidate:
            cand = dlg.accepted_candidate
            media_k = matching.classify(book, prefs['kind'])
            chg = matching.plan_changes(book, media_k, cand, set(prefs['fields']))
            if not chg:
                return info_dialog(self.gui, 'Media Matcher', _('The selected candidate matches current metadata; nothing to change.'), show=True)

            covers = {}
            if 'cover' in chg and cand.get('cover_urls'):
                img_data = sources.fetch_image(http, cand['cover_urls'])
                if img_data:
                    covers[book['id']] = img_data

            done, errors, undo = self.write_changes([(book['id'], chg)], covers)
            if undo:
                prefs['undo'] = undo
            self.refresh(done)

            text = _('Updated book metadata successfully.') + _(' "Undo last run" can put the old values back.')
            if errors:
                text += '\n\n' + _('Problems:') + '\n' + '\n'.join(errors[:10])
            info_dialog(self.gui, 'Media Matcher', text, show=True)
