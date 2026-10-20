"""Runs the Media Matcher dialogs under real Qt (PyQt6, offscreen) against a stand-in Calibre library and canned service answers.
Skipped when PyQt6 is not installed. Calibre itself is not involved: its database API is replaced by MediaDb below."""
import importlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_utils import BaseMockDb  # noqa: E402
from test_qt_dialogs import HAVE_QT, QtCase, QtCore, QtWidgets  # noqa: E402
from test_media import FIXTURES, JPEG, OL, MB, Scripted, id3_tag, id3_text, mp3_frames  # noqa: E402


class MediaDb(BaseMockDb):
    """Just enough of Calibre's db.new_api: books in memory, every write recorded."""

    def __init__(self, books):
        super().__init__(books=books)


def make_gui(db, selected):
    gui = QtWidgets.QWidget()  # the dialog needs a real parent widget; the rest of Calibre's window is a stand-in
    gui.current_db = MagicMock(new_api=db)
    gui.library_view = MagicMock()
    gui.library_view.get_selected_ids.return_value = selected
    gui.tags_view = MagicMock()
    return gui


def buttons(dialog):
    return {b.text(): b for b in dialog.findChildren(QtWidgets.QPushButton)}


def run_dialog(show, drive):
    original = QtWidgets.QDialog.exec
    QtWidgets.QDialog.exec = drive
    try:
        show()
    finally:
        QtWidgets.QDialog.exec = original


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class MediaDialogTests(QtCase):
    def setUp(self):
        super().setUp()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        self.package = self.load('media_matcher')
        self.sources = importlib.import_module(self.package + '.sources')
        self.action_mod = importlib.import_module(self.package + '.action')
        self.sources.MIN_INTERVAL = {}  # no real waiting between canned answers
        self.saved_fetch = self.sources.urllib_fetch

    def tearDown(self):
        self.sources.urllib_fetch = self.saved_fetch
        super().tearDown()

    def services(self):
        fetch = Scripted(routes={'musicbrainz.org/ws/2/recording': {'recordings': MB}, 'openlibrary.org/search.json': {'docs': OL},
                                 'coverartarchive.org': JPEG, 'covers.openlibrary.org': JPEG})
        self.sources.urllib_fetch = fetch
        return fetch

    def test_the_menu_is_built_with_both_commands(self):
        made = []

        class Base:
            def __init__(self):
                self.gui = QtWidgets.QWidget()
                self.qaction = QtWidgets.QWidgetAction(self.gui)

            def create_menu_action(self, menu, name, text, **kwargs):
                made.append((name, text, kwargs['triggered']))

        action = type('A', (Base, self.action_mod.MediaMatcherAction), {})()
        action.genesis()
        self.assertEqual([m[0] for m in made], ['media-matcher-add', 'media-matcher-match'])
        self.assertIsNotNone(action.qaction.menu())

    def test_add_files_from_a_folder_then_skip_them_the_second_time(self):
        with tempfile.TemporaryDirectory() as d:
            album = Path(d) / 'Beatles' / 'Help!'
            album.mkdir(parents=True)
            for n, title in ((1, 'Help!'), (2, 'Yesterday')):
                tag = id3_tag(3, [('TIT2', id3_text(0, title)), ('TPE1', id3_text(0, 'The Beatles')), ('TALB', id3_text(0, 'Help!')),
                                  ('TRCK', id3_text(0, f'{n}/2')), ('TYER', id3_text(0, '1965')), ('TCON', id3_text(0, 'Rock')),
                                  ('APIC', b'\x00image/jpeg\x00\x03\x00' + JPEG)])
                (album / f'0{n} - {title}.mp3').write_bytes(tag + mp3_frames())
            (album / 'untagged.mp3').write_bytes(mp3_frames())
            (Path(d) / 'Beatles' / 'notes.txt').write_text('not audio')
            (Path(d) / 'Beatles' / 'Book').mkdir()
            (Path(d) / 'Beatles' / 'Book' / 'Chapter 1.m4b').write_bytes(b'\x00\x00\x00\x08ftyp')

            db = MediaDb({})
            gui = make_gui(db, [])
            action = self.action_mod.MediaMatcherAction()
            action.gui = gui
            seen = {}
            original_dir = QtWidgets.QFileDialog.getExistingDirectory
            QtWidgets.QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: d)

            def drive(dialog):
                tree = dialog.findChild(QtWidgets.QTreeWidget)
                b = buttons(dialog)
                b['Choose a folder (includes subfolders)…'].click()
                seen['rows'] = [[tree.topLevelItem(i).text(c) for c in range(6)] for i in range(tree.topLevelItemCount())]
                self.assertTrue(b['Add ticked files'].isEnabled())
                b['Add ticked files'].click()
                seen['books_after_first'] = {i: dict(v) for i, v in db.books.items()}
                b['Choose a folder (includes subfolders)…'].click()  # the same files again
                b['Add ticked files'].click()
                seen['books_after_second'] = len(db.books)

            try:
                run_dialog(action.add_dialog, drive)
            finally:
                QtWidgets.QFileDialog.getExistingDirectory = original_dir

            names = [r[0] for r in seen['rows']]
            self.assertEqual(names, ['Chapter 1.m4b', '01 - Help!.mp3', '02 - Yesterday.mp3', 'untagged.mp3'], 'audio only, folders in order, tracks kept together')
            self.assertEqual(seen['rows'][1][1:], ['Help!', 'The Beatles', 'Help!', '1', 'music'])
            self.assertEqual(seen['rows'][3][5], 'music (from file name)')
            self.assertEqual(seen['rows'][0][5].split()[0], 'audiobook', 'an .m4b is an audiobook')
            books = seen['books_after_first']
            self.assertEqual(len(books), 4)
            self.assertEqual(books[3]['title'], 'Yesterday')
            self.assertEqual((books[3]['series'], books[3]['series_index']), ('Help!', 2.0))
            self.assertEqual(books[3]['tags'], ['Music', 'Rock'])
            self.assertEqual(books[3]['formats'], ['MP3'])
            self.assertEqual(books[1]['tags'], ['Audiobook'])
            self.assertEqual(books[1]['formats'], ['M4B'])
            self.assertEqual(set(db.covers), {2, 3}, 'embedded cover art becomes the book cover')
            self.assertEqual(db.covers[2], JPEG)
            self.assertEqual(db.added[1][1], {'MP3': str(album / '01 - Help!.mp3')}, 'the audio file itself is the format')
            self.assertEqual(seen['books_after_second'], 4, 'files already in the library are skipped')
            self.assertTrue(gui.tags_view.recount.called)

    def test_lookup_apply_and_undo_through_the_real_window(self):
        self.services()
        db = MediaDb({
            1: dict(title='yesterday', authors=['Beatles'], series='', series_index=1.0, tags=[], identifiers={}, formats=['MP3']),
            2: dict(title='Dune', authors=['Frank Herbert'], series='', series_index=1.0, tags=['Audiobook'], identifiers={}, formats=['M4B']),
            3: dict(title='dune', authors=['Frank Herbert'], series='', series_index=1.0, tags=[], identifiers={}, formats=['EPUB']),
        })
        gui = make_gui(db, [1, 2, 3])
        action = self.action_mod.MediaMatcherAction()
        action.gui = gui
        prefs = self.action_mod.prefs
        seen = {}

        def drive(dialog):
            b = buttons(dialog)
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            b['Look up'].click()
            seen['log'] = dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText()
            bar = dialog.findChild(QtWidgets.QProgressBar)
            seen['bar'] = (bar.value(), bar.maximum())
            T = QtCore.Qt.CheckState
            seen['rows'] = {tree.topLevelItem(i).text(0): (
                tree.topLevelItem(i).checkState(0),
                f"{tree.topLevelItem(i).text(3)}: {tree.topLevelItem(i).text(2)}",
                [f"{tree.topLevelItem(i).child(j).text(0)}:  {tree.topLevelItem(i).child(j).text(1)}  →  {tree.topLevelItem(i).child(j).text(2)}" if tree.topLevelItem(i).child(j).text(0) != 'Cover' else f"Cover:  {tree.topLevelItem(i).child(j).text(2)}" for j in range(tree.topLevelItem(i).childCount())]
            ) for i in range(tree.topLevelItemCount())}
            seen['T'] = T
            self.assertTrue(b['Apply ticked changes'].isEnabled())
            b['Apply ticked changes'].click()
            seen['writes'] = list(db.writes)
            seen['covers'] = dict(db.covers)
            seen['undo'] = dict(prefs['undo'])
            self.assertTrue(b['Undo last run'].isEnabled())
            b['Undo last run'].click()
            seen['restored'] = db.writes[len(seen['writes']):]

        run_dialog(action.match_dialog, drive)

        log = seen['log']
        self.assertIn('Reading 3 book(s) from your library', log)
        self.assertIn('1/3  yesterday — Beatles', log)
        self.assertIn('searching: yesterday / Beatles', log)
        self.assertIn('✔ MusicBrainz', log)
        self.assertIn('✔ Open Library', log)
        self.assertEqual(seen['bar'], (3, 3))
        rows = seen['rows']
        self.assertEqual(set(rows), {'yesterday', 'Dune', 'dune'})
        T = seen['T']
        state, summary, children = rows['yesterday']
        self.assertIn('MusicBrainz', summary)
        self.assertIn('Yesterday — The Beatles / Help!', summary)
        self.assertIn('Title:  yesterday  →  Yesterday', children)
        self.assertIn('Album:  (none)  →  Help! #13', children)
        self.assertTrue(any(c.startswith('Cover:  front cover from Cover Art Archive') for c in children))
        self.assertEqual(state, T.Unchecked, 'no length or album to confirm it, and several recordings differ: left for the user to tick')
        self.assertEqual(rows['dune'][0], T.Checked, 'a certain match starts ticked')

    def test_user_ticks_a_row_and_apply_writes_it_then_undo_restores(self):
        fetch = self.services()
        db = MediaDb({1: dict(title='yesterday', authors=['Beatles'], series='', series_index=1.0, tags=['Rock'], identifiers={'x': '1'}, formats=['MP3'])})
        gui = make_gui(db, [1])
        action = self.action_mod.MediaMatcherAction()
        action.gui = gui
        prefs = self.action_mod.prefs
        seen = {}

        def drive(dialog):
            b = buttons(dialog)
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            b['Look up'].click()
            top = tree.topLevelItem(0)
            top.setCheckState(0, QtCore.Qt.CheckState.Checked)
            b['Apply ticked changes'].click()
            seen['writes'] = list(db.writes)
            seen['covers'] = dict(db.covers)
            seen['undo'] = dict(prefs['undo'])
            b['Undo last run'].click()
            seen['restored'] = db.writes[len(seen['writes']):]
            seen['undo_after'] = dict(prefs['undo'])
            seen['covers_after'] = dict(db.covers)

        run_dialog(action.match_dialog, drive)
        writes = {name: list(m.values())[0] for name, m in seen['writes']}
        self.assertEqual(writes['title'], 'Yesterday')
        self.assertEqual(writes['authors'], ('The Beatles',))
        self.assertEqual(writes['series'], 'Help!')
        self.assertEqual(writes['series_index'], 13.0)
        self.assertEqual(writes['tags'], ('Rock', 'Music'))
        self.assertEqual(writes['identifiers']['x'], '1', 'existing identifiers are kept')
        self.assertIn('mb_recording', writes['identifiers'])
        self.assertEqual(str(writes['pubdate'].year), '1965')
        self.assertEqual(seen['covers'], {1: JPEG})
        self.assertEqual(seen['undo']['1']['title'], 'yesterday')
        self.assertIsNone(seen['undo']['1']['cover'])
        self.assertEqual(seen['covers_after'], {}, 'undo removes cover when book had none before')
        restored = {name: list(m.values())[0] for name, m in seen['restored']}
        self.assertEqual(restored['title'], 'yesterday')
        self.assertEqual(restored['authors'], ('Beatles',))
        self.assertIsNone(restored['series'])
        self.assertEqual(restored['tags'], ('Rock',))
        self.assertEqual(restored['identifiers'], {'x': '1'})
        self.assertIsNone(restored['pubdate'])
        self.assertEqual(seen['undo_after'], {}, 'a clean undo clears the saved run')

    def test_undo_restores_pre_matching_cover_image(self):
        import base64
        fetch = self.services()
        old_cover_bytes = b'OLD_COVER_DATA'
        db = MediaDb({1: dict(title='yesterday', authors=['Beatles'], series='', series_index=1.0, tags=['Rock'], identifiers={'x': '1'}, formats=['MP3'])})
        db.covers[1] = old_cover_bytes
        gui = make_gui(db, [1])
        action = self.action_mod.MediaMatcherAction()
        action.gui = gui
        prefs = self.action_mod.prefs
        seen = {}

        def drive(dialog):
            b = buttons(dialog)
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            b['Look up'].click()
            top = tree.topLevelItem(0)
            top.setCheckState(0, QtCore.Qt.CheckState.Checked)
            b['Apply ticked changes'].click()
            seen['covers_after_apply'] = dict(db.covers)
            seen['undo_payload'] = dict(prefs['undo'])
            b['Undo last run'].click()
            seen['covers_after_undo'] = dict(db.covers)

        run_dialog(action.match_dialog, drive)
        self.assertEqual(seen['covers_after_apply'], {1: JPEG})
        self.assertEqual(seen['undo_payload']['1']['cover'], base64.b64encode(old_cover_bytes).decode('ascii'))
        self.assertEqual(seen['covers_after_undo'], {1: old_cover_bytes}, 'undo restores original cover bytes')

    def test_cancel_during_a_run_keeps_the_window_usable(self):
        import time
        calls = []

        def slow(url, headers, data, timeout):
            calls.append(url)
            time.sleep(0.4)
            return b'{"recordings": []}'
        self.sources.urllib_fetch = slow
        db = MediaDb({i: dict(title=f'Song {i}', authors=['A'], series='', series_index=1.0, tags=[], identifiers={}, formats=['MP3']) for i in (1, 2, 3)})
        action = self.action_mod.MediaMatcherAction()
        action.gui = make_gui(db, [1, 2, 3])
        seen = {}

        def drive(dialog):
            b = buttons(dialog)
            QtCore.QTimer.singleShot(150, b['Cancel'].click)
            b['Look up'].click()
            seen['log'] = dialog.findChild(QtWidgets.QPlainTextEdit).toPlainText()
            seen['enabled'] = b['Look up'].isEnabled()

        run_dialog(action.match_dialog, drive)
        self.assertIn('Cancel pressed', seen['log'])
        self.assertLessEqual(len(calls), 2, 'later books are not looked up after Cancel')
        self.assertTrue(seen['enabled'])

    def test_settings_are_remembered_when_the_window_closes(self):
        db = MediaDb({1: dict(title='x', authors=['A'], series='', series_index=1.0, tags=[], identifiers={}, formats=['MP3'])})
        action = self.action_mod.MediaMatcherAction()
        action.gui = make_gui(db, [1])
        prefs = self.action_mod.prefs

        def drive(dialog):
            edits = [e for e in dialog.findChildren(QtWidgets.QLineEdit)]
            secret = next(e for e in edits if e.echoMode() == QtWidgets.QLineEdit.EchoMode.Password)
            secret.setText('  my-acoustid-key  ')
            contact = next(e for e in edits if 'User-Agent' in e.placeholderText())
            contact.setText('me@example.org')
            dialog.reject()  # closing the window is what saves the settings

        run_dialog(action.match_dialog, drive)
        self.assertEqual(prefs['acoustid_key'], 'my-acoustid-key')
        self.assertEqual(prefs['contact'], 'me@example.org')

    def test_fingerprint_option_without_a_key_explains_instead_of_running(self):
        fetch = self.services()
        db = MediaDb({1: dict(title='x', authors=['A'], series='', series_index=1.0, tags=[], identifiers={}, formats=['MP3'])})
        action = self.action_mod.MediaMatcherAction()
        action.gui = make_gui(db, [1])
        shown = []
        self.action_mod.error_dialog = lambda parent, title, text, show=False: shown.append(text)

        def drive(dialog):
            box = next(c for c in dialog.findChildren(QtWidgets.QCheckBox) if 'fingerprint' in c.text())
            box.setChecked(True)
            buttons(dialog)['Look up'].click()

        run_dialog(action.match_dialog, drive)
        self.assertTrue(any('AcoustID key' in t for t in shown))
        self.assertEqual(fetch.calls, [], 'nothing is sent')

    def test_apply_saves_metadata_when_cover_download_fails_due_to_http_error(self):
        import urllib.error
        fetch = self.services()
        db = MediaDb({1: dict(title='yesterday', authors=['Beatles'], series='', series_index=1.0, tags=['Rock'], identifiers={'x': '1'}, formats=['MP3'])})
        gui = make_gui(db, [1])
        action = self.action_mod.MediaMatcherAction()
        action.gui = gui

        def failing_fetch(url, headers, data, timeout):
            if 'coverartarchive.org' in url or 'covers.openlibrary.org' in url:
                raise urllib.error.HTTPError(url, 500, 'Internal Server Error', {}, None)
            return fetch(url, headers, data, timeout)

        self.sources.urllib_fetch = failing_fetch
        shown_info = []
        self.action_mod.info_dialog = lambda parent, title, text, show=False: shown_info.append(text)

        def drive(dialog):
            b = buttons(dialog)
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            b['Look up'].click()
            top = tree.topLevelItem(0)
            top.setCheckState(0, QtCore.Qt.CheckState.Checked)
            b['Apply ticked changes'].click()

        run_dialog(action.match_dialog, drive)
        writes = {name: list(m.values())[0] for name, m in db.writes}
        self.assertEqual(writes['title'], 'Yesterday')
        self.assertEqual(writes['authors'], ('The Beatles',))
        self.assertEqual(writes['series'], 'Help!')
        self.assertEqual(db.covers, {}, 'cover download failed, so no cover was set')
        self.assertTrue(shown_info, 'summary info dialog was shown')
        self.assertIn('Updated 1 book(s)', shown_info[0])
        self.assertIn('Problems:', shown_info[0])
        self.assertIn('cover download failed', shown_info[0])

    def test_apply_saves_metadata_when_cover_download_is_cancelled(self):
        fetch = self.services()
        db = MediaDb({1: dict(title='yesterday', authors=['Beatles'], series='', series_index=1.0, tags=['Rock'], identifiers={'x': '1'}, formats=['MP3'])})
        gui = make_gui(db, [1])
        action = self.action_mod.MediaMatcherAction()
        action.gui = gui

        def cancelling_fetch(url, headers, data, timeout):
            if 'coverartarchive.org' in url or 'covers.openlibrary.org' in url:
                raise self.sources.Cancelled()
            return fetch(url, headers, data, timeout)

        self.sources.urllib_fetch = cancelling_fetch
        shown_info = []
        self.action_mod.info_dialog = lambda parent, title, text, show=False: shown_info.append(text)

        def drive(dialog):
            b = buttons(dialog)
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            b['Look up'].click()
            top = tree.topLevelItem(0)
            top.setCheckState(0, QtCore.Qt.CheckState.Checked)
            b['Apply ticked changes'].click()

        run_dialog(action.match_dialog, drive)
        writes = {name: list(m.values())[0] for name, m in db.writes}
        self.assertEqual(writes['title'], 'Yesterday')
        self.assertEqual(writes['authors'], ('The Beatles',))
        self.assertEqual(db.covers, {})
        self.assertTrue(shown_info)
        self.assertIn('Updated 1 book(s)', shown_info[0])


if __name__ == '__main__':
    unittest.main()
