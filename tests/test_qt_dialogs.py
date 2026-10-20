"""Runs the real dialogs under real Qt (PyQt6, offscreen) with canned site pages. Skipped when PyQt6 is not installed.

Calibre itself is replaced by small stubs (its preferences store and plain modules); everything else, including Qt's
behaviour, is real. This is what caught the 'finished task looks cancelled' bug that left the tag search empty.
"""
import builtins
import http.server
import importlib
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    HAVE_QT = True
except ImportError:  # also raised when system libraries such as libEGL are missing
    HAVE_QT = False
    QtCore = QtGui = QtWidgets = None
import build_plugins  # noqa: E402
sys.path.insert(0, str(ROOT / 'tests'))
from harness_utils import BaseMockDb  # noqa: E402
from reddit_samples import entry, feed  # noqa: E402

TAG_INDEX = ('<p class="xy23R-sectionheaders">A</p>'
             '<a href="?list=tag&id=1">Alpha</a><a href="?list=tag&id=2">Beta</a><a href="?list=tag&id=90">All Tags</a>')


def story_row(sid, title, author='Writer'):
    return (f'<p class="xy23R-story-row-graf"><a href="/mbxy/site/story.php?id={sid}">{title}</a> by '
            f'<a href="/mbxy/site/archive.php?list=author&id=7">{author}</a></p>')


TAG_PAGES = {
    '1': story_row('one', 'Story One') + story_row('two', 'Story Two'),
    '2': story_row('two', 'Story Two') + story_row('three', 'Story Three'),
}


class StubMetadata:
    def __init__(self, title, authors):
        self.title, self.authors, self.ids, self.comments, self.tags, self.publisher = title, authors, {}, None, [], None

    def set_identifier(self, key, value):
        self.ids[key] = value


class StubPrefs(dict):
    def __init__(self):
        super().__init__()
        self.defaults = {}

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return self.defaults[key]


class FakeFetcher:
    """Stands in for the site: serves the tag index and per-tag story lists."""

    def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
        self.cancelled = cancelled

    def get(self, url):
        time.sleep(0.05)  # long enough that the progress dialog really appears
        if 'list=tag&id=' in url:
            return TAG_PAGES[url.rsplit('=', 1)[1]]
        if 'list=tag' in url:
            return TAG_INDEX
        raise IOError('unexpected url ' + url)


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class QtCase(unittest.TestCase):
    """Shared setup: real Qt, stubbed Calibre, and the freshly built plugin zips extracted as packages."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.built = build_plugins.build_all(Path(cls.tmp.name) / 'dist')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.saved = {k: v for k, v in sys.modules.items() if k.startswith(('calibre', 'qt', 'calibre_plugins'))}
        qtcore = types.ModuleType('qt.core')
        for module in (QtWidgets, QtGui, QtCore):
            for name in dir(module):
                setattr(qtcore, name, getattr(module, name))
        sys.modules['qt'] = types.ModuleType('qt')
        sys.modules['qt.core'] = qtcore
        for name in ('calibre', 'calibre.customize', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils'):
            sys.modules[name] = MagicMock()
        for name in ('calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.book'):
            sys.modules[name] = MagicMock()
        base = types.ModuleType('calibre.ebooks.metadata.book.base')
        base.Metadata = StubMetadata
        sys.modules['calibre.ebooks.metadata.book.base'] = base
        config = types.ModuleType('calibre.utils.config')
        config.JSONConfig = lambda name: StubPrefs()
        sys.modules['calibre.utils.config'] = config
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins'].__path__ = []

    def tearDown(self):
        if hasattr(builtins, 'get_resources'):
            del builtins.get_resources
        for key in [k for k in sys.modules if k.startswith(('calibre', 'qt'))]:
            del sys.modules[key]
        sys.modules.update(self.saved)

    def load(self, import_name):
        target = Path(self.tmp.name) / import_name
        if not target.exists():
            zipfile.ZipFile(self.built[import_name]).extractall(target)
        package = types.ModuleType(f'calibre_plugins.{import_name}')
        package.__path__ = [str(target)]
        sys.modules[package.__name__] = package
        return package.__name__



@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class TaskAndTagSearchTests(QtCase):
    def test_finished_task_is_not_reported_as_cancelled(self):
        guikit = importlib.import_module(self.load('metabods_downloader') + '.guikit')
        task = guikit.run_task(None, 'Working…', lambda t: (time.sleep(0.3), ['done'])[1])
        self.assertEqual(task.result, ['done'])
        self.assertFalse(task.cancelled(), 'a run nobody cancelled must not look cancelled afterwards')

    def test_pressing_cancel_is_still_reported(self):
        guikit = importlib.import_module(self.load('metabods_downloader') + '.guikit')

        def press_cancel():
            for widget in QtWidgets.QApplication.topLevelWidgets():
                if isinstance(widget, QtWidgets.QProgressDialog) and widget.isVisible():
                    widget.findChild(QtWidgets.QPushButton).click()  # what a user does; QProgressDialog.cancel() alone emits nothing

        def work(task):
            for _ in range(200):
                if task.cancelled():
                    return 'stopped early'
                time.sleep(0.05)
            return 'ran to the end'

        QtCore.QTimer.singleShot(300, press_cancel)
        task = guikit.run_task(None, 'Working…', work)
        self.assertTrue(task.cancelled())
        self.assertEqual(task.result, 'stopped early')

    def test_metabods_tag_search_fills_the_story_list(self):
        package = self.load('metabods_downloader')
        core = importlib.import_module(package + '.core')
        core.Fetcher = FakeFetcher  # the dialog reaches the site only through core.Fetcher
        ui = importlib.import_module(package + '.ui')
        dialog = ui.TagSearchDialog(None, existing_ids={'three'})
        self.assertEqual(dialog.tag_list.count(), 2, 'the tag list should load (All Tags is skipped)')

        def check(*names):
            dialog.checked_tags.clear()
            for i in range(dialog.tag_list.count()):
                item = dialog.tag_list.item(i)
                if item.text().lstrip('★ ') in names:
                    item.setCheckState(QtCore.Qt.CheckState.Checked)
            self.assertEqual(len(dialog.checked_tags), len(names))

        check('Alpha', 'Beta')
        dialog.mode.setCurrentIndex(0)  # any checked tag
        dialog.search()
        titles = [dialog.result_list.item(i).text() for i in range(dialog.result_list.count())]
        self.assertEqual(len(titles), 3, titles)
        self.assertTrue(any('Story Three' in t and 'already in library' in t for t in titles))
        self.assertEqual(dialog.selected_ids(), ['one', 'two'], 'stories already in the library start unchecked')
        self.assertIn('3 stories found', dialog.summary.text())

        dialog.mode.setCurrentIndex(1)  # all checked tags: only the story in both lists
        dialog.search()
        self.assertEqual([dialog.result_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(dialog.result_list.count())], ['two'])


NIFTY_SECTION = '<a href="/nifty/gay/college/">College</a><a href="/nifty/gay/camping/">Camping</a>'
NIFTY_CATEGORY = ('<div class="ftr" role="row"><div>Dir</div><div>Oct 7 07:25</div><div><a href="campus-rivals/">campus-rivals/</a></div></div>'
                  '<div class="ftr" role="row"><div>7K</div><div>Oct 4 16:39</div><div><a href="my-story">my-story</a></div></div>')


class NiftyFakeFetcher(FakeFetcher):
    def get(self, url):
        if url.endswith('/nifty/gay/'):
            return NIFTY_SECTION
        if url.endswith('/gay/college/') or url.endswith('/gay/camping/'):
            return NIFTY_CATEGORY
        if url.endswith('/nifty/journal-entries/'):
            return NIFTY_CATEGORY
        raise IOError('unexpected url ' + url)


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class OtherDialogTests(QtCase):
    """Same real-Qt setup, for the remaining dialogs."""

    def test_metabods_link_dialog(self):
        ui = importlib.import_module(self.load('metabods_downloader') + '.ui')
        dialog = ui.DownloadDialog(None)
        dialog.text.setPlainText('bennet-3120\n\n  https://metabods.com/mbxy/site/archive.php?list=author&id=368  \n')
        self.assertEqual(len(dialog.lines()), 2)
        skip, combine, title, chapter_range = dialog.options.values()
        self.assertEqual((skip, combine, title, chapter_range), (True, False, '', ''))
        dialog.options.combine.setChecked(True)
        dialog.options.title.setText('  Omnibus ')
        dialog.options.chapter_range.setText('1-5')
        self.assertEqual(dialog.options.values(), (True, True, 'Omnibus', '1-5'))

    def test_nifty_browse_dialog_loads_and_selects(self):
        package = self.load('nifty_downloader')
        core = importlib.import_module(package + '.core')
        core.Fetcher = NiftyFakeFetcher
        ui = importlib.import_module(package + '.ui')
        dialog = ui.BrowseDialog(None, existing_ids={'gay/college/my-story'})
        self.assertEqual([dialog.category.itemData(i) for i in range(dialog.category.count())], ['gay/college', 'gay/camping'])
        dialog.load_stories()
        self.assertEqual(dialog.story_list.count(), 2)
        texts = [dialog.story_list.item(i).text() for i in range(dialog.story_list.count())]
        self.assertTrue(any('my-story'.replace('-', ' ').title() in t and 'already in library' in t for t in texts), texts)
        dialog.set_shown(True)
        self.assertEqual(sorted(r['id'] for r in dialog.selected_refs()), ['gay/college/campus-rivals', 'gay/college/my-story'])
        dialog.filter.setText('rivals')
        self.assertEqual(dialog.story_list.count(), 1)
        self.assertEqual(len(dialog.selected_refs()), 2, 'filtering must not lose checked stories')
        dialog.toggle_favorite()
        self.assertIn('gay/college', dialog.favorites)
        self.assertEqual(dialog.favs.count(), 2)

        # Direct section loads '[All Stories]' as category and displays stories
        dialog.filter.setText('')
        dialog.section.setCurrentText('journal-entries')
        self.assertEqual([dialog.category.itemData(i) for i in range(dialog.category.count())], ['journal-entries'])
        self.assertEqual(dialog.category.itemText(0), '[All Stories]')
        dialog.load_stories()
        self.assertEqual(dialog.story_list.count(), 2)
        self.assertEqual(sorted(r['id'] for r in dialog.refs), ['journal-entries/campus-rivals', 'journal-entries/my-story'])

    def test_nifty_link_dialog(self):
        ui = importlib.import_module(self.load('nifty_downloader') + '.ui')
        dialog = ui.LinksDialog(None)
        dialog.text.setPlainText('https://www.nifty.org/nifty/gay/college/\n\n')
        self.assertEqual(dialog.lines(), ['https://www.nifty.org/nifty/gay/college/'])

    def test_nifty_preview_dialog(self):
        ui = importlib.import_module(self.load('nifty_downloader') + '.ui')
        groups = [
            {
                'id': 'gay/college/saga',
                'title': 'Saga',
                'chapters': [
                    {'file': 'gay/college/saga-1', 'title': 'Chapter 1', 'enabled': True},
                    {'file': 'gay/college/saga-2', 'title': 'Chapter 2', 'enabled': True},
                    {'file': 'gay/college/saga-10', 'title': 'Chapter 10', 'enabled': True}
                ]
            },
            {
                'id': 'gay/college/epic',
                'title': 'Epic',
                'chapters': [
                    {'file': 'gay/college/epic-1', 'title': 'Epic 1', 'enabled': True}
                ]
            }
        ]
        dialog = ui.NiftyPreviewDialog(None, groups)
        self.assertEqual(dialog.tree.topLevelItemCount(), 2)

        saga_item = dialog.tree.topLevelItem(0)
        ch2_item = saga_item.child(1)
        dialog.tree.setCurrentItem(ch2_item)
        dialog.move_up()

        mod_groups = dialog.get_groups()
        self.assertEqual(mod_groups[0]['chapters'][0]['file'], 'gay/college/saga-2')

        saga_item = dialog.tree.topLevelItem(0)
        dialog.tree.setCurrentItem(saga_item)
        dialog.split_group()
        self.assertEqual(len(dialog.groups), 4)

        top0 = dialog.tree.topLevelItem(0)
        top1 = dialog.tree.topLevelItem(1)
        top0.setSelected(True)
        top1.setSelected(True)

        original_get_text = ui.QInputDialog.getText
        ui.QInputDialog.getText = staticmethod(lambda *args, **kwargs: ('Merged Saga Part 1', True))
        try:
            dialog.merge_groups()
        finally:
            ui.QInputDialog.getText = original_get_text

        final_groups = dialog.get_groups()
        self.assertEqual(final_groups[0]['title'], 'Merged Saga Part 1')


def _panel(anchor, name, *entries):
    lis = '\n'.join(f'<li><a href="{href}">{title}</a>' for href, title in entries)
    return (f'<div id="{anchor}" class="panel panel-default"><div class="panel-heading"><h4 class="panel-title">{name}</h4></div>'
            f'<div class="panel-body"><ul>\n{lis}\n</ul></div></div>')


AUTHORS_PAGE = (_panel('writer', 'Writer One',
                       ('/nifty/gay/college/texas-tails/', 'Texas Tails'),
                       ('/nifty/gay/highschool/jeremiah', 'Texas Tails: Jeremiah'),
                       ('/nifty/gay/camping/standalone', 'Standalone'),
                       ('/nifty/lesbian/beginnings/elsewhere', 'Elsewhere'))
                + _panel('solo', 'Solo Author', ('/nifty/gay/camping/lone-wolf', 'Lone Wolf')))


class AuthorsFakeFetcher(FakeFetcher):
    ranges = {
        'gay/college/texas-tails/texas-tails-1': 'Date: Mon, 5 Jan 2026 10:00:00 +0000\n\nTexas Tails chapter one',
        'gay/college/texas-tails/texas-tails-2': 'the end of Texas Tails',
        'gay/highschool/jeremiah': 'Subject: Texas Tails: Jeremiah\n\nJeremiah text',
        'gay/camping/standalone': 'Subject: Standalone\n\nContinued from Texas Tails\n\nmore text',
    }

    def get(self, url):
        if url.endswith('authors.html'):
            return AUTHORS_PAGE
        if url.endswith('prolific.html'):
            return ''
        if url.endswith('gay/college/texas-tails/'):
            return ('<table><tr><td>3K</td><td>Oct 3 1999</td><td><a href="texas-tails-1">texas-tails-1</a></td></tr>'
                    '<tr><td>3K</td><td>Oct 4 1999</td><td><a href="texas-tails-2">texas-tails-2</a></td></tr></table>')
        raise IOError('unexpected url ' + url)

    def get_range(self, url, length=3000, tail=False):
        key = url.split('/nifty/', 1)[1]
        if key not in self.ranges:
            raise IOError('unexpected range url ' + url)
        return self.ranges[key]


class YesBox:
    """Stands in for QMessageBox so the confirmation answers Yes without a modal dialog."""
    StandardButton = QtWidgets.QMessageBox.StandardButton if HAVE_QT else None

    @staticmethod
    def question(*args, **kwargs):
        return QtWidgets.QMessageBox.StandardButton.Yes


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class NiftyAuthorDialogTests(QtCase):
    def make(self, existing=()):
        package = self.load('nifty_downloader')
        core = importlib.import_module(package + '.core')
        core.Fetcher = AuthorsFakeFetcher
        ui = importlib.import_module(package + '.ui')
        return ui.AuthorDialog(None, existing_ids=set(existing)), core

    def titles(self, dialog):
        out = []
        for i in range(dialog.tree.topLevelItemCount()):
            parent = dialog.tree.topLevelItem(i)
            out.append((parent.text(0), [parent.child(j).text(0) for j in range(parent.childCount())]))
        return out

    def test_authors_list_filter_and_section(self):
        dialog, _ = self.make()
        self.assertEqual([dialog.author_list.item(i).text() for i in range(dialog.author_list.count())], ['Solo Author (1)', 'Writer One (3)'])
        dialog.filter.setText('writer')
        self.assertEqual(dialog.author_list.count(), 1)
        dialog.filter.setText('')
        dialog.section.setCurrentText('lesbian')
        self.assertEqual([dialog.author_list.item(i).text() for i in range(dialog.author_list.count())], ['Writer One (1)'])

    def test_suggested_set_spans_folders_and_selection_matches(self):
        dialog, core = self.make(existing={'gay/highschool/jeremiah'})
        dialog.filter.setText('writer')
        dialog.author_list.setCurrentRow(0)
        tree = self.titles(dialog)
        self.assertEqual(len(tree), 2)
        self.assertIn('Suggested set: Texas Tails', tree[0][0])
        self.assertIn('2 folder(s)', tree[0][0])
        self.assertEqual(len(tree[0][1]), 2)
        self.assertTrue(any('Jeremiah' in c and 'already in library' in c for c in tree[0][1]))
        self.assertTrue(tree[1][0].startswith('Other stories (1)'))
        self.assertEqual(dialog.selection(), [], 'nothing is ticked at first')

        dialog.check_suggested()
        parent = dialog.tree.topLevelItem(0)
        self.assertEqual([parent.child(i).checkState(0) for i in range(2)], [QtCore.Qt.CheckState.Checked] * 2, 'ticking a set ticks its stories')
        selection = dialog.selection()
        self.assertEqual([i['title'] for i in selection], ['Texas Tails'])
        self.assertEqual(selection[0]['addresses'], [core.SITE + 'gay/college/texas-tails/', core.SITE + 'gay/highschool/jeremiah'])

        parent.child(1).setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(parent.checkState(0), QtCore.Qt.CheckState.PartiallyChecked)
        self.assertEqual(dialog.selection(), [{'title': None, 'addresses': [core.SITE + 'gay/college/texas-tails/']}], 'one story left is just a story')

    def test_options_change_the_result(self):
        dialog, core = self.make()
        dialog.filter.setText('writer')
        dialog.author_list.setCurrentRow(0)
        dialog.check_all(True)
        self.assertEqual([i['title'] for i in dialog.selection()], ['Texas Tails', None])
        dialog.how.setCurrentIndex(2)  # separate books
        self.assertEqual([i['title'] for i in dialog.selection()], [None, None, None])
        dialog.how.setCurrentIndex(1)  # separate books grouped as a Calibre series
        series_items = dialog.selection()
        self.assertEqual([(i['title'], i.get('series')) for i in series_items[:1]], [(None, 'Texas Tails')])
        dialog.how.setCurrentIndex(0)
        dialog.combine_all.setChecked(True)
        dialog.combine_title.setText('Everything by Writer')
        selection = dialog.selection()
        self.assertEqual((len(selection), selection[0]['title'], len(selection[0]['addresses'])), (1, 'Everything by Writer', 3))

    def test_scanning_contents_finds_a_set_the_titles_missed_and_keeps_ticks(self):
        dialog, core = self.make()
        ui = sys.modules[type(dialog).__module__]
        ui.QMessageBox = YesBox
        dialog.filter.setText('writer')
        dialog.author_list.setCurrentRow(0)
        names = lambda: [(dialog.tree.topLevelItem(i).text(0), dialog.tree.topLevelItem(i).childCount()) for i in range(dialog.tree.topLevelItemCount())]
        before = names()
        self.assertEqual(before[0][1], 2, 'titles alone: Texas Tails and Texas Tails: Jeremiah')
        other = dialog.tree.topLevelItem(1)
        other.child(0).setCheckState(0, QtCore.Qt.CheckState.Checked)  # tick "Standalone" before scanning

        dialog.scan_contents()
        after = names()
        self.assertEqual(after[0][1], 3, 'the text of Standalone says it continues Texas Tails, so it joins the set')
        self.assertEqual(len(after), 1, 'nothing is left over, so there is no "Other stories" group')
        parent = dialog.tree.topLevelItem(0)
        self.assertIn('Found in the stories', parent.toolTip(0))
        self.assertIn('Standalone says', parent.toolTip(0))
        ticked = [parent.child(i).text(0) for i in range(parent.childCount()) if parent.child(i).checkState(0) == QtCore.Qt.CheckState.Checked]
        self.assertEqual(len(ticked), 1)
        self.assertIn('Standalone', ticked[0], 'the story ticked before the scan is still ticked')
        self.assertEqual(sorted(dialog.texts), sorted(['gay/college/texas-tails', 'gay/highschool/jeremiah', 'gay/camping/standalone']))
        self.assertIn('contents of 3 read', dialog.summary.text())
        # reading again finds nothing new to fetch
        dialog.scan_contents()
        self.assertIn('already been read', dialog.summary.text())


class RecordingBox:
    """Stands in for QMessageBox: answers Yes to questions and records warnings."""
    StandardButton = QtWidgets.QMessageBox.StandardButton if HAVE_QT else None
    warnings = []

    @staticmethod
    def question(*args, **kwargs):
        return QtWidgets.QMessageBox.StandardButton.Yes

    @staticmethod
    def warning(*args, **kwargs):
        RecordingBox.warnings.append(args[2] if len(args) > 2 else '')

    @staticmethod
    def information(*args, **kwargs):
        pass


class FakeDb(BaseMockDb):
    """The part of Calibre's library API the plugin uses, keeping books in memory."""

    def __init__(self):
        super().__init__()
        self.next_id = 1
        self.writes = 0

    def add_books(self, items, **kwargs):
        ids = []
        for mi, formats in items:
            self.books[self.next_id] = {'mi': mi, 'formats': {k: v.read() for k, v in formats.items()}}
            ids.append(self.next_id)
            self.next_id += 1
            self.writes += 1
        return ids, []

    def add_format(self, book_id, fmt, stream, replace=True):
        self.books[book_id]['formats'][fmt] = stream.read()
        self.writes += 1
        return True

    def set_field(self, name, mapping):
        for book_id, value in mapping.items():
            setattr(self.books[book_id]['mi'], name, value)


def make_gui():
    gui = QtWidgets.QWidget()
    gui.db = FakeDb()
    gui.current_db = MagicMock()
    gui.current_db.new_api = gui.db
    gui.library_view, gui.tags_view, gui.status_bar = MagicMock(), MagicMock(), MagicMock()
    return gui


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class RedditPluginTests(QtCase):
    def setUp(self):
        super().setUp()
        RecordingBox.warnings = []
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        self.package = self.load('reddit_follower')
        self.core = importlib.import_module(self.package + '.core')
        self.config = importlib.import_module(self.package + '.config')
        self.ui = importlib.import_module(self.package + '.ui')
        self.ui.QMessageBox = RecordingBox

    def make_action(self):
        action_module = importlib.import_module(self.package + '.action')
        action = action_module.FollowerAction()
        action.gui = make_gui()
        action.qaction = MagicMock()
        action.genesis()
        action.poll_timer.setInterval(30)
        self.tmp_cache = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_cache.cleanup)
        action._cache_dir = self.tmp_cache.name
        return action

    # -- dialogs
    def test_follow_dialog_validates_and_keeps_identity_when_edited(self):
        dialog = self.ui.FollowDialog(None)
        dialog.source.setText('hello world')
        self.assertIn('Could not tell', dialog.message.text())
        dialog.try_accept()
        self.assertEqual(dialog.result(), 0, 'an unusable address does not close the dialog')
        dialog.name.setText('Out of Cruel Space')
        dialog.source.setText('https://www.reddit.com/r/HFY/search/?q=Out+of+Cruel+Space&restrict_sr=1')
        dialog.title_filter.setText('Out of Cruel Space')
        self.assertIn('Will follow search', dialog.message.text())
        dialog.try_accept()
        self.assertEqual(dialog.result(), 1)
        follow = dialog.result_follow()
        self.assertEqual((follow['name'], follow['source']['kind'], follow['title_filter']), ('Out of Cruel Space', 'search', 'Out of Cruel Space'))
        follow['last_checked'] = 123.0
        edit = self.ui.FollowDialog(None, follow)
        edit.author_filter.setText('/u/Writer')
        edited = edit.result_follow()
        self.assertEqual((edited['id'], edited['author_filter'], edited['last_checked']), (follow['id'], 'Writer', 0.0), 'same series, checked again')

    def test_quick_start_preset_fills_the_dialog_for_out_of_cruel_space(self):
        dialog = self.ui.FollowDialog(None)
        self.assertEqual(dialog.preset.count(), 1 + len(self.core.PRESETS))
        dialog.preset.setCurrentIndex(1)
        self.assertEqual((dialog.name.text(), dialog.source.text(), dialog.title_filter.text(), dialog.author_filter.text()),
                         ('Out of Cruel Space', 'u/KyleKKent', self.core.PRESETS[0]['title_filter'], 'KyleKKent'))
        self.assertIn('Will follow u/KyleKKent', dialog.message.text())
        dialog.try_accept()
        self.assertEqual(dialog.result(), 1)
        follow = dialog.result_follow()
        self.assertEqual((follow['name'], follow['source']['user'], follow['author_filter']), ('Out of Cruel Space', 'KyleKKent', 'KyleKKent'))
        self.assertFalse(follow['author_note'], 'author comments are opt-in')
        self.assertIn('docs.google.com/spreadsheets', follow['index_url'], 'the preset brings the public chapter index')
        self.assertTrue(follow['title_from_body'], 'the preset names chapters from the title line each post opens with')
        bad = self.ui.FollowDialog(None)
        bad.source.setText('u/KyleKKent')
        bad.index_url.setText('https://example.com/nope')
        bad.try_accept()
        self.assertIn('Google Sheets', bad.message.text())
        self.assertEqual(bad.result(), 0, 'a bad index address does not close the dialog')
        self.assertTrue(self.core.matches({'title': 'OOCS, Into A Wider Galaxy, Part 800', 'author': 'KyleKKent', 'html': '<p>x</p>'}, follow),
                        'the preset keeps following after the series was renamed')
        dialog.author_note.setChecked(True)
        self.assertTrue(dialog.result_follow()['author_note'])
        edit = self.ui.FollowDialog(None, follow)
        self.assertFalse(hasattr(edit, 'preset'), 'editing an existing follow offers no presets')

    def test_settings_dialog_requires_a_client_id_for_the_api_and_clamps_the_interval(self):
        dialog = self.ui.SettingsDialog(None)
        dialog.api.setChecked(True)
        dialog.save()
        self.assertEqual(self.config.prefs['mode'], 'rss', 'nothing is saved without a client id')
        self.assertEqual(len(RecordingBox.warnings), 1)
        dialog.client_id.setText(' abc123 ')
        dialog.username.setText('Reader')
        dialog.hours.setValue(0.1)
        dialog.save()
        prefs = self.config.prefs
        self.assertEqual((prefs['mode'], prefs['client_id'], prefs['username']), ('api', 'abc123', 'Reader'))
        self.assertGreaterEqual(prefs['check_hours'], 1.0, 'never faster than hourly')

    def test_settings_dialog_logs_in_and_out_with_a_simulated_browser(self):
        import threading
        import urllib.request
        from urllib.parse import parse_qs, urlparse
        login = importlib.import_module(self.package + '.login')
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_reddit_login as stand
        stand.StandIn.log, stand.StandIn.token_reply = [], None
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), stand.StandIn)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_address[1]}'
        dialog = self.ui.SettingsDialog(None)
        self.assertEqual(dialog.login_status.text(), 'Not logged in')
        self.assertFalse(dialog.logout_button.isEnabled())
        dialog.api.setChecked(True)
        dialog.client_id.setText('test-client-id')
        dialog.login_endpoints = {'token_url': base + '/token', 'base': base, 'revoke_url': base + '/revoke'}
        dialog.login_endpoints['redirect_uri'] = login.REDIRECT_URI

        def browser(url):
            state = parse_qs(urlparse(url).query)['state'][0]
            threading.Thread(target=lambda: urllib.request.urlopen(f'{login.REDIRECT_URI}?code=abc&state={state}').read()).start()
        dialog.open_browser = browser
        dialog.log_in()
        self.assertEqual(RecordingBox.warnings, [])
        self.assertEqual(dialog.login_status.text(), 'Logged in as u/Reader')
        self.assertEqual((self.config.prefs['refresh_token'], self.config.prefs['account_name']), ('refresh1', 'Reader'))
        dialog.log_out()
        self.assertEqual(self.config.prefs['refresh_token'], '')
        self.assertEqual(dialog.login_status.text(), 'Not logged in')
        self.assertTrue(any(path == '/revoke' for path, _, _ in stand.StandIn.log))

    def test_each_post_becomes_its_own_book_and_the_dialog_offers_it(self):
        core = self.core
        dialog = self.ui.FollowDialog(None)
        preset_index = 1 + [i for i, p in enumerate(core.PRESETS) if p.get('layout') == 'each'][0]
        dialog.preset.setCurrentIndex(preset_index)
        dialog.flair_filter.setText('FICTION')
        dialog.try_accept()
        made = dialog.result_follow()
        self.assertEqual((made['layout'], made['flair_filter'], made['source']['subreddit']), ('each', 'FICTION', 'gayincest_stories'))
        edit = self.ui.FollowDialog(None, made)
        self.assertEqual((edit.layout_box.currentData(), edit.flair_filter.text()), ('each', 'FICTION'))

        class FeedFake:
            posts = []

            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                return feed(*FeedFake.posts)
        core.Fetcher = FeedFake
        action = self.make_action()
        db = action.gui.db
        follow = core.new_follow('Stories', 'r/gayincest_stories', layout='each')
        self.config.prefs['follows'] = [follow]
        FeedFake.posts = [entry('c', 'Weekend away, Part 2', '<p>two</p>'), entry('b', 'Camping [16M] and [40M]', '<p>skipped</p>'),
                          entry('a', 'Weekend away, Part 1', '<p>one</p>')]
        action.check_now([follow])
        titles = sorted((b['mi'].title, b['mi'].series, b['mi'].series_index) for b in db.books.values())
        self.assertEqual(titles, [('Weekend away, Part 1', 'Weekend away', 1.0), ('Weekend away, Part 2', 'Weekend away', 2.0)])
        self.assertTrue(all(b['formats'].get('EPUB') for b in db.books.values()))
        writes = db.writes
        action.check_now(list(self.config.prefs['follows']))
        self.assertEqual((len(db.books), db.writes), (2, writes), 'nothing is added twice')
        FeedFake.posts.insert(0, entry('d', 'Another story', '<p>three</p>'))
        action.check_now(list(self.config.prefs['follows']))
        self.assertEqual(len(db.books), 3)

    def test_find_stories_groups_series_and_follows_the_whole_thing(self):
        core = self.core

        class FeedFake:
            urls = []

            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                FeedFake.urls.append(url)
                return feed(entry('s3', 'Saga of Ash 3', '<p>three</p>', author='/u/Writer', stamp='2026-10-03T10:00:00+00:00'),
                            entry('s1', 'Saga of Ash 1', '<p>one</p>', author='/u/Writer', stamp='2026-10-01T10:00:00+00:00'),
                            entry('s2', '[OC] Saga of Ash, Part 2', '<p>two</p>', author='/u/Writer', stamp='2026-10-02T10:00:00+00:00'),
                            entry('x1', 'A lone tale', '<p>alone</p>', author='/u/Other', stamp='2026-10-04T10:00:00+00:00'))
        core.Fetcher = FeedFake
        action = self.make_action()
        action_module = importlib.import_module(self.package + '.action')

        class AutoFollow(self.ui.FollowDialog):
            def exec(self):
                self.try_accept()
                return self.result()
        self.ui.FollowDialog = AutoFollow
        dialog = self.ui.DiscoverDialog(None, action)
        dialog.words.setText('saga')
        dialog.search()
        self.assertTrue(any('/r/HFY/search.rss' in u and 'q=saga' in u for u in FeedFake.urls))
        names = [dialog.table.item(r, 0).text() for r in range(dialog.table.rowCount())]
        self.assertEqual(names, ['Saga of Ash', 'A lone tale  (single story)'], 'the series first, the lone story marked')
        self.assertEqual((dialog.table.item(0, 1).text(), dialog.table.item(0, 2).text(), dialog.table.item(0, 3).text()), ('Writer', '3', '1\u20133'))
        dialog.follow_selected()
        self.assertIn('Select one series', dialog.message.text())
        dialog.table.selectRow(0)
        dialog.follow_selected()
        chosen = dialog.chosen
        self.assertEqual((chosen['name'], chosen['source']['user'], chosen['author_filter']), ('Saga of Ash', 'Writer', 'Writer'))
        self.assertTrue(any('/user/Writer/submitted.rss' in u for u in FeedFake.urls), 'the author\'s own posts were read for every part')

        # end to end from the menu: the series is added and collected as one book
        class AutoDiscover(self.ui.DiscoverDialog):
            def exec(self):
                self.words.setText('saga')
                self.search()
                self.table.selectRow(0)
                self.follow_selected()
                return self.result()
        action_module.DiscoverDialog = AutoDiscover
        self.config.prefs['follows'] = []
        action.discover()
        self.assertEqual([f['name'] for f in self.config.prefs['follows']], ['Saga of Ash'])
        books = list(action.gui.db.books.values())
        self.assertEqual(len(books), 1)
        z = zipfile.ZipFile(io.BytesIO(books[0]['formats']['EPUB']))
        self.assertEqual(len([n for n in z.namelist() if n.startswith('OEBPS/s001_')]), 3, 'all three parts, whatever their title style')

    def test_add_top_rated_series_follows_several_at_once(self):
        core = self.core

        class FeedFake:
            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                if '/user/AlphaA/' in url:
                    return feed(*[entry(f'a{n}', f'Big Saga {n}', f'<p>a{n}</p>', author='/u/AlphaA', stamp=f'2026-10-0{n}T10:00:00+00:00') for n in (3, 2, 1)])
                if '/user/BravoB/' in url:
                    return feed(*[entry(f'b{n}', f'Mid Epic {n}', f'<p>b{n}</p>', author='/u/BravoB', stamp=f'2026-10-0{n}T11:00:00+00:00') for n in (4, 3, 2, 1)])
                return feed()
        core.Fetcher = FeedFake
        action = self.make_action()
        action_module = importlib.import_module(self.package + '.action')
        self.config.prefs['follows'] = []

        def post(i, title, author, score):
            return {'id': f't3_{i}', 'title': title, 'author': author, 'flair': 'OC', 'created': 1.7e9 + i, 'html': '<p>x</p>', 'link': '', 'score': score}
        entries = ([post(n, f'Big Saga {n}', 'AlphaA', 5000 - n) for n in (1, 2, 3)] + [post(10 + n, f'Mid Epic {n}', 'BravoB', 900 - n) for n in (1, 2, 3, 4)]
                   + [post(20, 'Lone', 'CeeC', 10 ** 6)])
        dialog = self.ui.DiscoverDialog(None, action)
        dialog.add_top()
        self.assertIn('Search or browse first', dialog.message.text())
        dialog.groups = core.group_series([dict(e, score=0) for e in entries])
        dialog.add_top()
        self.assertIn('Ratings need the official API', dialog.message.text())
        dialog.groups = core.group_series(entries)
        dialog.top_count.setValue(2)
        dialog.min_parts.setValue(3)
        dialog.add_top()
        self.assertEqual([f['name'] for f in dialog.chosen_many], ['Big Saga', 'Mid Epic'])
        self.assertEqual(dialog.result(), 1)

        class AutoTop(self.ui.DiscoverDialog):
            def exec(self):
                self.groups = core.group_series(entries)
                self.top_count.setValue(5)
                self.add_top()
                return self.result()
        action_module.DiscoverDialog = AutoTop
        action.discover()
        self.assertEqual([f['name'] for f in self.config.prefs['follows']], ['Big Saga', 'Mid Epic'])
        titles = sorted(b['mi'].title for b in action.gui.db.books.values())
        self.assertEqual(titles, ['Big Saga', 'Mid Epic'], 'one growing book per series, collected straight away')
        # a second time there is nothing new to add
        AutoTop.exec = lambda self: (setattr(self, 'groups', core.group_series(entries)), self.add_top(), self.result())[2]
        action.discover()
        self.assertEqual(len(self.config.prefs['follows']), 2)

    def test_followed_stories_list_shows_filters_in_words(self):
        core = self.core
        action = self.make_action()
        self.config.prefs['follows'] = [core.series_follow({'name': 'The Nature of Predators', 'author': 'SpacePaladin15'}),
                                        core.new_follow('Out of Cruel Space', 'u/KyleKKent', 'Out of Cruel Space', 'KyleKKent')]
        dialog = self.ui.ManageDialog(None, action)
        text = [dialog.table.item(r, 1).text() for r in range(dialog.table.rowCount())]
        self.assertEqual(text, ['u/SpacePaladin15  title starts with \u201cThe Nature of Predators\u201d, by SpacePaladin15',
                                'u/KyleKKent  title has \u201cOut of Cruel Space\u201d, by KyleKKent'])
        self.assertFalse(any('re:' in t or '\\' in t or '(?' in t for t in text), 'no regular expression is shown to the user')

    def test_manage_dialog_lists_and_removes_follows_and_their_cache(self):
        follow = self.core.new_follow('Series', 'u/writer', 'Chapter')
        self.config.prefs['follows'] = [follow]
        cache_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(cache_dir, ignore_errors=True))
        cache = self.core.ChapterCache(cache_dir, follow['id'])
        cache.posts['t3_a'] = {'id': 't3_a', 'title': 'Chapter 1', 'author': 'writer', 'created': 1.0, 'link': '', 'html': '<p>x</p>'}
        cache.save()

        class Act:
            def chapter_count(self, f):
                return len(self.core.ChapterCache(cache_dir, f['id']).posts)

            def cache_dir(self):
                return cache_dir
        act = Act()
        act.core = self.core
        dialog = self.ui.ManageDialog(None, act)
        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual([dialog.table.item(0, c).text() for c in (0, 2, 3)], ['Series', '1', 'never'])
        self.assertIn('u/writer', dialog.table.item(0, 1).text())
        self.assertIn('Chapter', dialog.table.item(0, 1).text())
        dialog.table.selectRow(0)
        dialog.remove()
        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertEqual(self.config.prefs['follows'], [])
        self.assertFalse(Path(cache.path).exists(), 'the cached chapters go with the follow')

    def test_settings_test_button_uses_the_values_on_screen_and_saves_nothing(self):
        core = self.core
        seen = []

        class FeedFake:
            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                seen.append(url)
                if FeedFake.limited:
                    raise core.RateLimited(url, 300)
                return feed(entry('a1', 'Chapter 1', '<p>One.</p>'), entry('a2', 'Chapter 2', '<p>Two.</p>'))
            limited = False
        core.Fetcher = FeedFake
        before = dict(self.config.prefs)
        dialog = self.ui.SettingsDialog(None)
        dialog.test_connection()
        self.assertTrue(dialog.test_result.text().startswith('OK: Reddit answered through the public feed: 2 post(s)'), dialog.test_result.text())
        self.assertEqual(len(seen), 1)
        self.assertIn('/r/HFY/new.rss', seen[0])
        FeedFake.limited = True
        dialog.test_connection()
        self.assertTrue(dialog.test_result.text().startswith('Problem: Reddit asked us to slow down'), dialog.test_result.text())
        dialog.api.setChecked(True)  # no client id typed: the test says so instead of crashing
        dialog.test_connection()
        self.assertIn('client id', dialog.test_result.text())
        self.assertEqual(dict(self.config.prefs), before, 'testing must not change saved settings')

    def test_test_selected_reports_how_many_posts_match_the_follow(self):
        core = self.core
        page = feed(self.chapter(2), self.chapter(1), entry('zz', 'Unrelated', '<p>no</p>'))

        class FeedFake:
            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                return page
        core.Fetcher = FeedFake
        action = self.make_action()
        follow = core.new_follow('Out of Cruel Space', 'r/HFY', 'Out of Cruel Space')
        self.config.prefs['follows'] = [follow]
        action_module = importlib.import_module(self.package + '.action')
        action_module.info_dialog.reset_mock()
        dialog = self.ui.ManageDialog(None, action)
        dialog.table.selectRow(0)
        dialog.test_selected()
        title, text = action_module.info_dialog.call_args[0][1], action_module.info_dialog.call_args[0][2]
        self.assertIn('Out of Cruel Space', title)
        self.assertIn('3 post(s)', text)
        self.assertIn('2 match your filters', text)
        self.assertEqual(self.config.prefs['follows'][0]['last_checked'], 0.0, 'a test is not a check')
        self.assertEqual(action.gui.db.books, {}, 'a test never touches the library')

    def test_manage_dialog_export_opml_saves_file(self):
        core = self.core
        action = self.make_action()
        f1 = core.new_follow('HFY Story', 'r/HFY')
        self.config.prefs['follows'] = [f1]
        dialog = self.ui.ManageDialog(None, action)
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
        out_file = os.path.join(tmp_dir, 'export.opml')

        with patch.object(self.ui.QFileDialog, 'getSaveFileName', return_value=(out_file, 'OPML Files (*.opml *.xml)')):
            dialog.export_opml()

        self.assertTrue(os.path.isfile(out_file))
        imported, skipped = core.import_opml(out_file, existing=[])
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]['name'], 'HFY Story')

    def test_manage_dialog_import_opml_adds_series_to_table(self):
        core = self.core
        action = self.make_action()
        f1 = core.new_follow('HFY Story', 'r/HFY')
        self.config.prefs['follows'] = [f1]
        dialog = self.ui.ManageDialog(None, action)

        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
        in_file = os.path.join(tmp_dir, 'import.opml')

        f2 = core.new_follow('New Author Series', 'u/NewAuthor')
        core.export_opml([f1, f2], in_file)

        with patch.object(self.ui.QFileDialog, 'getOpenFileName', return_value=(in_file, 'OPML Files (*.opml *.xml)')):
            dialog.import_opml()

        self.assertEqual(len(self.config.prefs['follows']), 2)
        self.assertEqual(dialog.table.rowCount(), 2)

    def test_manage_dialog_import_opml_handles_malformed_xml_gracefully(self):
        action = self.make_action()
        dialog = self.ui.ManageDialog(None, action)

        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
        bad_file = os.path.join(tmp_dir, 'bad.opml')
        with open(bad_file, 'w', encoding='utf-8') as f:
            f.write("<html>Malformed XML</html>")

        with patch.object(self.ui.QFileDialog, 'getOpenFileName', return_value=(bad_file, 'OPML Files (*.opml *.xml)')):
            with patch.object(self.ui, 'show_friendly_error_dialog') as mock_err:
                dialog.import_opml()
                self.assertTrue(mock_err.called)

    # -- the whole life of a followed series, against a stand-in library and feed
    def chapter(self, n, body=None):
        return entry(f'c{n}', f'Out of Cruel Space (Chapter {n})', body or f'<p>Chapter {n} text.</p>', author='/u/Writer',
                     stamp=f'2026-10-{n:02d}T10:00:00+00:00')

    def test_follow_creates_updates_and_survives_rate_limits(self):
        core = self.core

        class FeedFake:
            posts, limited = [], False

            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                if FeedFake.limited:
                    raise core.RateLimited(url, 77)
                return feed(*FeedFake.posts)
        core.Fetcher = FeedFake
        action = self.make_action()
        db = action.gui.db
        follow = core.new_follow('Out of Cruel Space', 'r/HFY', 'Out of Cruel Space')
        self.config.prefs['follows'] = [follow]
        FeedFake.posts = [self.chapter(3), self.chapter(2), entry('zz', 'Some unrelated story', '<p>no</p>'), self.chapter(1)]

        def epub_chapters(book_id):
            z = zipfile.ZipFile(io.BytesIO(db.books[book_id]['formats']['EPUB']))
            return sorted(n for n in z.namelist() if n.startswith('OEBPS/s001_'))

        action.check_now([follow])
        self.assertEqual(len(db.books), 1, 'the book was created')
        book_id = next(iter(db.books))
        mi = db.books[book_id]['mi']
        self.assertEqual((mi.title, mi.authors, mi.ids), ('Out of Cruel Space', ['Writer'], {'redditfollow': 'reddit-' + follow['id']}))
        self.assertEqual(len(epub_chapters(book_id)), 3, 'three matching chapters, the unrelated post left out')
        self.assertEqual(self.config.prefs['follows'][0]['last_status'], '3 new chapter(s)')
        self.assertGreater(self.config.prefs['follows'][0]['last_checked'], 0)

        # a new chapter arrives: the same book is updated in place
        FeedFake.posts.insert(0, self.chapter(4))
        action.check_now(list(self.config.prefs['follows']))
        self.assertEqual(len(db.books), 1)
        self.assertEqual(len(epub_chapters(book_id)), 4)

        # nothing new: the book is left alone
        writes = db.writes
        action.check_now(list(self.config.prefs['follows']))
        self.assertEqual(db.writes, writes)

        # the user deleted the book: the next check brings it back from the cached chapters
        del db.books[book_id]
        action.check_now(list(self.config.prefs['follows']))
        self.assertEqual(len(db.books), 1)
        self.assertEqual(len(epub_chapters(next(iter(db.books)))), 4)

        # Reddit says slow down: stop, remember, and leave the library alone
        FeedFake.limited = True
        action.check_now(list(self.config.prefs['follows']))
        self.assertGreater(action.backoff_until, time.time() + 14 * 60, 'at least a quarter of an hour of peace')
        self.assertIn('slow down', self.config.prefs['follows'][0]['last_status'])
        self.assertEqual(len(db.books), 1)

    def test_automatic_check_runs_in_the_background_and_respects_the_schedule(self):
        core = self.core

        class FeedFake:
            posts = []
            calls = 0

            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                FeedFake.calls += 1
                return feed(*FeedFake.posts)
        core.Fetcher = FeedFake
        action = self.make_action()
        follow = core.new_follow('Out of Cruel Space', 'r/HFY', 'Out of Cruel Space')
        self.config.prefs['follows'] = [follow]
        FeedFake.posts = [self.chapter(2), self.chapter(1)]

        def wait():
            deadline = time.time() + 15
            while action.busy and time.time() < deadline:
                QtWidgets.QApplication.processEvents()
                time.sleep(0.02)
            QtWidgets.QApplication.processEvents()
            self.assertFalse(action.busy, 'the background check should finish')

        self.config.prefs['auto_check'] = False
        action.auto_check()
        self.assertFalse(action.busy, 'automatic checking can be switched off')
        self.assertEqual(FeedFake.calls, 0)

        self.config.prefs['auto_check'] = True
        action.auto_check()
        self.assertTrue(action.busy)
        wait()
        self.assertEqual(len(action.gui.db.books), 1)
        self.assertTrue(action.gui.status_bar.show_message.called, 'a quiet note in the status bar')
        calls = FeedFake.calls
        action.auto_check()
        self.assertFalse(action.busy)
        self.assertEqual(FeedFake.calls, calls, 'just checked, so not due again yet')

        live = self.config.prefs['follows']
        live[0]['last_checked'] = 1.0
        self.config.prefs['follows'] = live
        action.backoff_until = time.time() + 3600
        action.auto_check()
        self.assertFalse(action.busy, 'a recent rate limit holds automatic checks back')
        action.backoff_until = 0
        FeedFake.posts.insert(0, self.chapter(3))
        action.auto_check()
        wait()
        book = next(iter(action.gui.db.books.values()))
        z = zipfile.ZipFile(io.BytesIO(book['formats']['EPUB']))
        self.assertEqual(len([n for n in z.namelist() if n.startswith('OEBPS/s001_')]), 3)

    def test_manage_dialog_live_backoff_countdown_and_reset(self):
        action = self.make_action()
        action.backoff_until = time.time() + 600
        dialog = self.ui.ManageDialog(None, action)
        self.assertFalse(dialog.backoff_banner.isHidden())
        self.assertIn('Backoff active: retrying in', dialog.backoff_label.text())

        dialog.reset_backoff_and_retry()
        self.assertEqual(action.backoff_until, 0)
        self.assertTrue(dialog.backoff_banner.isHidden())

    def test_manage_dialog_persistent_error_banner(self):
        action = self.make_action()
        action.last_background_error = 'HTTP 429 Rate Limit Exceeded'
        dialog = self.ui.ManageDialog(None, action)
        self.assertFalse(dialog.error_banner.isHidden())
        self.assertIn('HTTP 429 Rate Limit Exceeded', dialog.error_label.text())

        dialog.dismiss_error()
        self.assertIsNone(action.last_background_error)
        self.assertTrue(dialog.error_banner.isHidden())

    def test_settings_dialog_contextual_connection_guidance(self):
        core = self.core

        class FeedFake:
            def __init__(self, min_interval=0, cancelled=lambda: False, **kwargs):
                pass

            def get(self, url, headers=None, **kwargs):
                raise core.RateLimited(url, 300)
        core.Fetcher = FeedFake
        dialog = self.ui.SettingsDialog(None)
        dialog.test_connection()
        self.assertIn('HTTP 429', dialog.test_result.text())
        self.assertIn('Official API', dialog.test_result.text())
        self.assertFalse(dialog.switch_api_button.isHidden())
        dialog.switch_api_button.click()
        self.assertTrue(dialog.api.isChecked())
        self.assertTrue(dialog.switch_api_button.isHidden())


class GeminiDb(BaseMockDb):
    """Just enough of Calibre's db.new_api for the Gemini fixer: books in memory, every write recorded."""

    def __init__(self):
        books = {
            1: dict(title='dune messiah [epub]', authors=['Herbert, Frank'], series='', series_index=1.0, tags=['Sci-Fi'], languages=[]),
            2: dict(title='Fine Book', authors=['A B'], series='', series_index=1.0, tags=[], languages=['eng']),
            3: dict(title='Cooking Today', authors=['C D'], series='', series_index=1.0, tags=[], languages=['eng']),
        }
        super().__init__(books=books)
        self.cover_bytes = None


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class GeminiDialogTests(QtCase):
    def setUp(self):
        super().setUp()
        self.tmp_batch_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_batch_dir.cleanup)

    def test_check_apply_and_undo_through_the_real_window(self):
        import json
        package = self.load('gemini_library_fixer')
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        engine = importlib.import_module(package + '.engine')
        action_mod = importlib.import_module(package + '.action')
        action_mod.prefs['batch_dir'] = self.tmp_batch_dir.name
        answers = [
            {'id': 1, 'title': 'Dune Messiah', 'authors': ['Frank Herbert'], 'series': 'Dune', 'series_index': 2, 'tags': ['Science Fiction'],
             'language': 'eng', 'confidence': 'high', 'reason': 'junk in title'},
            {'id': 2, 'confidence': 'high', 'reason': 'already fine'},
            {'id': 3, 'title': 'Totally Different Novel', 'confidence': 'high', 'reason': 'guess'},
        ]
        canned = json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps(answers)}]}}]}).encode()
        engine._post = lambda url, headers, body, timeout: canned  # Google is replaced; the key never leaves this process

        db = GeminiDb()
        gui = QtWidgets.QWidget()  # the dialog needs a real parent widget; the rest of Calibre's window is a stand-in
        gui.current_db = MagicMock(new_api=db)
        gui.library_view = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1, 2, 3]
        gui.tags_view = MagicMock()
        action = action_mod.GeminiFixerAction()
        action.gui = gui
        config = importlib.import_module(package + '.config')
        action_mod.prefs['api_key'] = 'test-key'
        seen = {}

        def drive(dialog):
            def button(text):
                return next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == text)
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            button('Check books').click()
            bar = dialog.findChild(QtWidgets.QProgressBar)
            seen['log'] = next(e for e in dialog.findChildren(QtWidgets.QPlainTextEdit) if e.isReadOnly()).toPlainText()
            seen['bar'] = (bar.value(), bar.maximum())
            seen['cancel_enabled_after'] = button('Cancel').isEnabled()
            seen['rows'] = [(tree.topLevelItem(i).text(0), tree.topLevelItem(i).checkState(0), tree.topLevelItem(i).childCount())
                            for i in range(tree.topLevelItemCount())]
            self.assertTrue(button('Apply ticked changes').isEnabled())
            button('Apply ticked changes').click()
            seen['undo'] = dict(action_mod.prefs['undo'])
            self.assertTrue(button('Undo last run').isEnabled())
            seen['writes_after_apply'] = list(db.writes)
            button('Undo last run').click()

        original_exec = QtWidgets.QDialog.exec
        QtWidgets.QDialog.exec = drive
        try:
            action.show_dialog()
        finally:
            QtWidgets.QDialog.exec = original_exec


        log = seen['log']
        self.assertTrue('reading 3 book(s)' in log.lower() or 'read 1/3' in log)
        self.assertIn('read 1/3', log)
        self.assertIn('“dune messiah [epub]” | by Herbert, Frank | tags: Sci-Fi | files: 1.epub', log, 'the data being read is shown')
        self.assertIn('Sending 3 book(s) to Gemini', log)
        self.assertIn('→ Sending 3 book(s) to Gemini:', log)
        self.assertIn('✔ “dune messiah [epub]” [high]: title “dune messiah [epub]” → “Dune Messiah”', log, 'each answer is shown')
        self.assertIn('– “Fine Book”: no change needed', log)
        self.assertIn('Done: 2 of 3 book(s) have suggested changes', log)
        self.assertEqual(seen['bar'], (3, 3), 'the bar ends full')
        self.assertFalse(seen['cancel_enabled_after'], 'Cancel is only live while work is running')
        T = QtCore.Qt.CheckState
        rows = {r[0]: r[1] for r in seen['rows']}  # books are asked about grouped by author, so the order is not the library's
        self.assertEqual(set(rows), {'dune messiah [epub]', 'Cooking Today'})
        self.assertEqual(rows['dune messiah [epub]'], T.Checked, 'a tidy-up starts ticked')
        self.assertEqual(rows['Cooking Today'], T.Unchecked, 'a very different title is a BIG CHANGE and starts unticked')
        writes = dict(((n, tuple(m.items())) for n, m in seen['writes_after_apply']))
        self.assertEqual(writes['title'], ((1, 'Dune Messiah'),))
        self.assertEqual(writes['authors'], ((1, ('Frank Herbert',)),))
        self.assertEqual(writes['series'], ((1, 'Dune'),))
        self.assertEqual(writes['series_index'], ((1, 2.0),))
        self.assertEqual(writes['tags'], ((1, ('Sci-Fi', 'Science Fiction')),))
        self.assertEqual(writes['languages'], ((1, ('eng',)),))
        self.assertFalse(any(list(m)[0] == 3 for _, m in seen['writes_after_apply']), 'the unticked book must not be written')
        self.assertEqual(seen['undo']['1']['title'], 'dune messiah [epub]')
        restored = {n: list(m.values())[0] for n, m in db.writes[len(seen['writes_after_apply']):]}
        self.assertEqual(restored['title'], 'dune messiah [epub]')
        self.assertEqual(restored['authors'], ('Herbert, Frank',))
        self.assertIsNone(restored['series'])
        self.assertEqual(restored['tags'], ('Sci-Fi',))
        self.assertEqual(restored['languages'], ())
        self.assertEqual(dict(action_mod.prefs['undo']), {}, 'a clean undo clears the saved run')

    def test_gemini_system_instructions_ui_and_reset(self):
        package = self.load('gemini_library_fixer')
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        action_mod = importlib.import_module(package + '.action')

        db = GeminiDb()
        gui = QtWidgets.QWidget()
        gui.current_db = MagicMock(new_api=db)
        gui.library_view = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1]
        gui.tags_view = MagicMock()

        action = action_mod.GeminiFixerAction()
        action.gui = gui

        action_mod.prefs['system_instruction_mode'] = 'override'
        action_mod.prefs['custom_system_instructions'] = 'Custom test instruction prompt'

        seen = {}

        def drive(dialog):
            mode_combo = next(c for c in dialog.findChildren(QtWidgets.QComboBox)
                              if 'Append' in c.itemText(0) or 'Override' in c.itemText(0))
            custom_edit = next(e for e in dialog.findChildren(QtWidgets.QPlainTextEdit)
                               if 'placeholderText' in dir(e) and 'Always prefix' in e.placeholderText())
            reset_btn = next(b for b in dialog.findChildren(QtWidgets.QPushButton)
                             if 'Reset' in b.text())

            seen['initial_mode_index'] = mode_combo.currentIndex()
            seen['initial_text'] = custom_edit.toPlainText()

            pixmap = dialog.grab()
            pixmap.save('/tmp/gemini_settings.png')

            reset_btn.click()

            seen['after_reset_mode_index'] = mode_combo.currentIndex()
            seen['after_reset_text'] = custom_edit.toPlainText()
            seen['after_reset_pref_mode'] = action_mod.prefs['system_instruction_mode']
            seen['after_reset_pref_text'] = action_mod.prefs['custom_system_instructions']

            pixmap_reset = dialog.grab()
            pixmap_reset.save('/tmp/gemini_settings_reset.png')

            dialog.reject()

        original_exec = QtWidgets.QDialog.exec
        QtWidgets.QDialog.exec = drive
        try:
            action.show_dialog()
        finally:
            QtWidgets.QDialog.exec = original_exec

        self.assertEqual(seen['initial_mode_index'], 1, 'Started in override mode index 1')
        self.assertEqual(seen['initial_text'], 'Custom test instruction prompt')
        self.assertEqual(seen['after_reset_mode_index'], 0, 'Reset set mode index back to 0 (append)')
        self.assertEqual(seen['after_reset_text'], '', 'Reset cleared custom instructions text')
        self.assertEqual(seen['after_reset_pref_mode'], 'append')
        self.assertEqual(seen['after_reset_pref_text'], '')

    def _window(self, db, canned_answers, selected):
        """A Gemini window over a stand-in library with Google replaced by canned answers; returns (action, dialog_runner, sent_bodies)."""
        import json
        package = self.load('gemini_library_fixer')
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        engine = importlib.import_module(package + '.engine')
        action_mod = importlib.import_module(package + '.action')
        sent = []
        canned = json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps(canned_answers)}]}}]}).encode()

        def post(url, headers, body, timeout):
            sent.append(json.loads(body))
            return canned

        engine._post = post
        gui = QtWidgets.QWidget()
        gui.current_db = MagicMock(new_api=db)
        gui.library_view = MagicMock()
        gui.library_view.get_selected_ids.return_value = selected
        gui.tags_view = MagicMock()
        action = action_mod.GeminiFixerAction()
        action.gui = gui
        action_mod.prefs['api_key'] = 'k'
        action_mod.prefs['min_interval'] = 0
        action_mod.prefs['fields'] = list(engine.FIELDS)
        action_mod.prefs['batch_dir'] = self.tmp_batch_dir.name

        def run(drive):
            original_exec = QtWidgets.QDialog.exec
            QtWidgets.QDialog.exec = drive
            try:
                action.show_dialog()
            finally:
                QtWidgets.QDialog.exec = original_exec
        return action_mod, run, sent

    @staticmethod
    def _button(dialog, text):
        return next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == text)

    def test_check_all_none_and_the_count_label(self):
        db = GeminiDb()
        answers = [{'id': 1, 'title': 'Dune Messiah', 'confidence': 'high'}, {'id': 3, 'title': 'Totally Different Novel', 'confidence': 'high'}]
        action_mod, run, _ = self._window(db, answers, [1, 2, 3])
        seen = {}

        def labels(dialog):
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            text = next(l.text() for l in dialog.findChildren(QtWidgets.QLabel) if 'change(s) ticked' in l.text())
            states = {tree.topLevelItem(i).text(0): tree.topLevelItem(i).child(0).checkState(0) for i in range(tree.topLevelItemCount())}
            return text, states, self._button(dialog, 'Apply ticked changes').isEnabled()

        def drive(dialog):
            self._button(dialog, 'Check books').click()
            seen['start'] = labels(dialog)
            self._button(dialog, 'Check none').click()
            seen['none'] = labels(dialog)
            self._button(dialog, 'Check all').click()
            seen['all'] = labels(dialog)
            self._button(dialog, 'Check all except big changes').click()
            seen['safe'] = labels(dialog)
            tree = dialog.findChild(QtWidgets.QTreeWidget)
            tree.topLevelItem(0).child(0).setCheckState(0, QtCore.Qt.CheckState.Unchecked)  # a single click updates the count too
            seen['one'] = labels(dialog)
            seen['settings_hidden'] = self._button(dialog, 'Show settings') is not None
            self._button(dialog, 'Show settings').click()
            seen['settings_back'] = self._button(dialog, 'Hide settings') is not None
            splitter = dialog.findChild(QtWidgets.QSplitter)
            seen['sizes'] = splitter.sizes()

        run(drive)
        T = QtCore.Qt.CheckState
        self.assertEqual(seen['start'][0], '1 of 2 change(s) ticked')
        self.assertEqual(seen['none'][0:3:2], ('0 of 2 change(s) ticked', False), 'nothing ticked, so Apply is off')
        self.assertEqual(seen['all'][0], '2 of 2 change(s) ticked')
        self.assertEqual(set(seen['all'][1].values()), {T.Checked})
        self.assertTrue(seen['all'][2])
        self.assertEqual(seen['safe'][1], {'Cooking Today': T.Unchecked, 'dune messiah [epub]': T.Checked}, 'the big change stays unticked')
        self.assertEqual(seen['safe'][0], '1 of 2 change(s) ticked')
        self.assertTrue(seen['settings_hidden'], 'the settings fold away during a run, leaving the results room')
        self.assertTrue(seen['settings_back'], 'and the button brings them back')
        self.assertGreater(seen['sizes'][1], seen['sizes'][0], 'the list of changes is the larger part once results are in')

    def test_covers_are_sent_as_pictures_and_the_isbn_is_applied_and_undone(self):
        import json
        db = GeminiDb()
        # a real PNG for book 1's front cover, and an EPUB for book 2 that holds a back-cover picture
        image = QtGui.QImage(1500, 2000, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor('navy'))
        buffer = QtCore.QBuffer()
        buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        db.cover_bytes = bytes(buffer.data())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        epub = Path(tmp.name) / 'two.epub'
        with zipfile.ZipFile(epub, 'w') as z:
            z.writestr('OEBPS/back-cover.png', bytes(buffer.data()))
        db.paths[2] = str(epub)
        db.identifiers[1] = {'isbn': '9999999999999', 'goodreads': '5'}
        answers = [{'id': 1, 'title': 'Dune Messiah', 'isbn': '978-0-306-40615-7', 'cover_text': 'DUNE MESSIAH Frank Herbert',
                    'confidence': 'high', 'reason': 'printed on the cover'},
                   {'id': 2, 'isbn': '0306406152', 'confidence': 'high'}]
        action_mod, run, sent = self._window(db, answers, [1, 2])
        action_mod.prefs['covers'] = True
        action_mod.prefs['batch_size'] = 15
        seen = {}

        def drive(dialog):
            self._button(dialog, 'Check books').click()
            seen['log'] = next(e for e in dialog.findChildren(QtWidgets.QPlainTextEdit) if e.isReadOnly()).toPlainText()
            self._button(dialog, 'Apply ticked changes').click()
            seen['after_apply'] = list(db.writes)
            self._button(dialog, 'Undo last run').click()

        run(drive)
        parts = sent[0]['contents'][0]['parts']
        pictures = [(parts[i - 1]['text'], p['inlineData']['mimeType']) for i, p in enumerate(parts) if 'inlineData' in p]
        self.assertEqual(sorted(pictures), [('Image for book id 1 (front cover):', 'image/jpeg'),
                                           ('Image for book id 2 (back cover (back-cover.png)):', 'image/jpeg')])
        raw = __import__('base64').b64decode(parts[2]['inlineData']['data'])
        probe = QtGui.QImage()
        self.assertTrue(probe.loadFromData(raw))
        self.assertEqual(max(probe.width(), probe.height()), 1000, 'big covers are shrunk before upload')
        self.assertIn('picture: front cover', seen['log'])
        self.assertIn('📖 read from the pictures for “dune messiah [epub]”: DUNE MESSIAH Frank Herbert', seen['log'])
        self.assertIn('ISBN 9999999999999 → 9780306406157', seen['log'])
        written = {(n, tuple(m)): list(m.values())[0] for n, m in seen['after_apply']}
        self.assertEqual(written[('identifiers', (1,))], {'isbn': '9780306406157', 'goodreads': '5'}, 'other identifiers are kept')
        self.assertEqual(written[('identifiers', (2,))], {'isbn': '0306406152'})
        restored = [list(m.values())[0] for n, m in db.writes[len(seen['after_apply']):] if n == 'identifiers']
        self.assertIn({'isbn': '9999999999999', 'goodreads': '5'}, restored)
        self.assertIn({}, restored, 'a book that had no ISBN gets none again')

    def test_cancel_during_a_run_stops_further_requests_and_says_so(self):
        import json
        package = self.load('gemini_library_fixer')
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        engine = importlib.import_module(package + '.engine')
        action_mod = importlib.import_module(package + '.action')
        calls = []

        def slow_post(url, headers, body, timeout):
            calls.append(1)
            time.sleep(0.4)
            return json.dumps({'candidates': [{'content': {'parts': [{'text': '[]'}]}}]}).encode()

        engine._post = slow_post
        gui = QtWidgets.QWidget()
        gui.current_db = MagicMock(new_api=GeminiDb())
        gui.library_view = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1, 2, 3]
        gui.tags_view = MagicMock()
        action = action_mod.GeminiFixerAction()
        action.gui = gui
        action_mod.prefs['api_key'] = 'k'
        action_mod.prefs['batch_size'] = 1
        action_mod.prefs['min_interval'] = 0
        seen = {}

        def drive(dialog):
            cancel = next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == 'Cancel')
            QtCore.QTimer.singleShot(150, cancel.click)  # pressed while the first request is still in flight
            next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == 'Check books').click()
            seen['log'] = next(e for e in dialog.findChildren(QtWidgets.QPlainTextEdit) if e.isReadOnly()).toPlainText()
            seen['check_enabled'] = next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == 'Check books').isEnabled()

        original_exec = QtWidgets.QDialog.exec
        QtWidgets.QDialog.exec = drive
        try:
            action.show_dialog()
        finally:
            QtWidgets.QDialog.exec = original_exec
        self.assertEqual(len(calls), 1, 'the second and third requests never start')
        self.assertIn('Cancel pressed', seen['log'])
        self.assertTrue(seen['check_enabled'], 'the window is usable again afterwards')

    def test_partial_error_renders_suggestions_and_shows_warning_dialog(self):
        package = self.load('gemini_library_fixer')
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        engine = importlib.import_module(package + '.engine')
        action_mod = importlib.import_module(package + '.action')

        partial_suggestions = [{'id': 1, 'title': 'Book 1', 'changes': {'title': ('Old Title', 'New Title')}, 'confidence': 'high', 'reason': 'fix junk', 'big': False}]
        partial_err = engine.GeminiPartialError('Batch 2 failed due to network error', suggestions=partial_suggestions, skipped=['Book 2: network error'])

        db = GeminiDb()
        gui = QtWidgets.QWidget()
        gui.current_db = MagicMock(new_api=db)
        gui.library_view = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1, 2]
        gui.tags_view = MagicMock()
        action = action_mod.GeminiFixerAction()
        action.gui = gui
        action_mod.prefs['api_key'] = 'k'
        action_mod.prefs['batch_dir'] = self.tmp_batch_dir.name

        warnings_shown = []
        action_mod.warning_dialog = lambda parent, title, msg, show=False: warnings_shown.append((title, msg))

        seen = {}

        def drive(dialog):
            with unittest.mock.patch.object(engine, 'plan_fixes', side_effect=partial_err):
                next(b for b in dialog.findChildren(QtWidgets.QPushButton) if b.text() == 'Check books').click()
            seen['tree_count'] = dialog.findChild(QtWidgets.QTreeWidget).topLevelItemCount()
            seen['log'] = next(e for e in dialog.findChildren(QtWidgets.QPlainTextEdit) if e.isReadOnly()).toPlainText()

        original_exec = QtWidgets.QDialog.exec
        QtWidgets.QDialog.exec = drive
        try:
            action.show_dialog()
        finally:
            QtWidgets.QDialog.exec = original_exec

        self.assertEqual(seen['tree_count'], 1, 'partial suggestion for book 1 rendered in results tree')
        self.assertIn('Stopped early due to an error', seen['log'])
        self.assertEqual(len(warnings_shown), 1)
        self.assertIn('Batch 2 failed due to network error', warnings_shown[0][1])

    def test_api_key_is_saved_when_the_window_closes_without_checking(self):
        package = self.load('gemini_library_fixer')
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        action_mod = importlib.import_module(package + '.action')
        gui = QtWidgets.QWidget()
        gui.current_db = MagicMock(new_api=GeminiDb())
        gui.library_view = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1]
        action = action_mod.GeminiFixerAction()
        action.gui = gui
        action_mod.prefs['api_key'] = ''

        def drive(dialog):
            edit = next(e for e in dialog.findChildren(QtWidgets.QLineEdit) if e.echoMode() == QtWidgets.QLineEdit.EchoMode.Password)
            edit.setText('  my-secret-key  ')
            dialog.reject()  # closed without pressing Check books

        original_exec = QtWidgets.QDialog.exec
        QtWidgets.QDialog.exec = drive
        try:
            action.show_dialog()
        finally:
            QtWidgets.QDialog.exec = original_exec
        self.assertEqual(action_mod.prefs['api_key'], 'my-secret-key')


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class FanficDialogTests(QtCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if 'fanfic_downloader' not in cls.built:
            raise unittest.SkipTest('fanfic zip needs FanFicFare installed to build')

    def setUp(self):
        super().setUp()
        package = self.load('fanfic_downloader')
        self.path = str(Path(self.tmp.name) / 'fanfic_downloader')
        sys.path.insert(0, self.path)  # what `with plugin:` does for the bundled third-party libraries
        zf = zipfile.ZipFile(self.built['fanfic_downloader'])
        builtins.get_resources = lambda name: zf.read(name)  # Calibre injects this into plugin modules
        self.package = package

    def tearDown(self):
        sys.path.remove(self.path)
        for key in [k for k in sys.modules if k.startswith(('fanficfare', 'cloudscraper', 'requests_file', 'requests_toolbelt', 'brotlidecpy'))]:
            del sys.modules[key]
        super().tearDown()

    def test_download_dialog_and_sites_listing(self):
        ui = importlib.import_module(self.package + '.ui')
        dialog = ui.DownloadDialog(None)
        dialog.urls.setPlainText('https://www.royalroad.com/fiction/21220\n\n')
        values = dialog.values()
        self.assertEqual((values['lines'], values['allow_adult'], values['skip']), (['https://www.royalroad.com/fiction/21220'], False, True))
        dialog.adult.setChecked(True)
        dialog.extra.setPlainText('Custom.net # note')
        values = dialog.values()
        self.assertEqual((values['allow_adult'], values['extra_adult']), (True, ['custom.net']))
        sites = ui.SitesDialog(dialog)
        self.assertIn('www.royalroad.com', sites.findChild(QtWidgets.QPlainTextEdit).toPlainText())

    def test_royal_road_setup_dialog(self):
        ui = importlib.import_module(self.package + '.ui')
        flaresolverr = importlib.import_module(self.package + '.flaresolverr')
        advanced = QtWidgets.QPlainTextEdit('[archiveofourown.org]\nis_adult:true\n')
        dialog = ui.RoyalRoadSetupDialog(None, advanced)
        self.assertIn('FlareSolverr', dialog.findChild(QtWidgets.QTextBrowser).toPlainText(), 'the bundled guide should be shown')
        self.assertIn('not switched on', dialog.status.text())
        self.assertFalse(dialog.start_button.isEnabled(), 'no program chosen yet')
        self.assertFalse(dialog.stop_button.isEnabled())
        dialog.enable()
        self.assertIn('[www.royalroad.com]\nuse_flaresolverr_proxy:true', advanced.toPlainText())
        self.assertIn('is_adult:true', advanced.toPlainText())
        self.assertIn('switched on', dialog.status.text())
        dialog.copy_docker()
        self.assertEqual(QtWidgets.QApplication.clipboard().text(), flaresolverr.DOCKER_COMMAND)
        advanced.setPlainText(advanced.toPlainText() + '\n[defaults]\nflaresolverr_proxy_port:1\n')  # nothing listens on port 1
        dialog.test_server()
        self.assertTrue(dialog.status.text().startswith('Problem: Nothing answered'), dialog.status.text())
        self.assertIn('localhost:1/v1', dialog.status.text(), 'the port from Advanced settings should be used')

    def test_flaresolverr_wizard_pages(self):
        ui = importlib.import_module(self.package + '.ui')
        advanced = QtWidgets.QPlainTextEdit('[www.royalroad.com]\n')
        wizard = ui.FlareSolverrSetupWizard(None, advanced)
        self.assertEqual(wizard.pageCount(), 4)

        # Page 1: Startup method selection
        p1 = wizard.currentPage()
        self.assertIsInstance(p1, ui.StartupMethodPage)
        p1.rb_docker.setChecked(True)
        p1.validatePage()
        self.assertEqual(wizard.method, 1)

        # Page 2: Configuration page
        wizard.next()
        p2 = wizard.currentPage()
        self.assertIsInstance(p2, ui.MethodConfigPage)

        # Page 3: Health checks
        wizard.next()
        p3 = wizard.currentPage()
        self.assertIsInstance(p3, ui.HealthCheckPage)

        # Page 4: Apply settings
        wizard.next()
        p4 = wizard.currentPage()
        self.assertIsInstance(p4, ui.ApplySettingsPage)

        self.assertIn('[www.royalroad.com]\nuse_flaresolverr_proxy:true', advanced.toPlainText())



def make_dirty_epub():
    xhtml = '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>%s</body></html>'
    container = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
    opf = '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Book Title - OceanofPDF.com</dc:title></metadata>
    <manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
    <spine><itemref idref="c1"/></spine></package>'''
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container)
        z.writestr('OEBPS/content.opf', opf)
        z.writestr('OEBPS/c1.xhtml', xhtml % '<p>Downloaded from OceanofPDF.com</p><p>Story text.</p>')
    return b.getvalue()


class ScrubberFakeDb:
    def __init__(self):
        self.books = {}

    def formats(self, book_id):
        if book_id not in self.books:
            return ()
        return tuple(self.books[book_id]['formats'].keys())

    def format(self, book_id, fmt):
        return self.books[book_id]['formats'].get(fmt)

    def save_original_format(self, book_id, fmt):
        data = self.format(book_id, fmt)
        if data is not None:
            self.books[book_id]['formats']['ORIGINAL_' + fmt] = data

    def remove_formats(self, mapping):
        for book_id, fmts in mapping.items():
            for fmt in fmts:
                self.books[book_id]['formats'].pop(fmt, None)

    def add_format(self, book_id, fmt, stream, replace=True):
        self.books[book_id]['formats'][fmt] = stream.read()
        return True

    def get_metadata(self, book_id):
        return self.books[book_id]['mi']

    def set_field(self, field, mapping):
        for book_id, val in mapping.items():
            setattr(self.books[book_id]['mi'], field, val)


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class ScrubberPluginTests(QtCase):
    def setUp(self):
        super().setUp()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        self.package = self.load('epub_promo_scrubber')
        self.action_module = importlib.import_module(self.package + '.action')

    def make_action(self, selected_ids):
        action = self.action_module.ScrubAction()
        action.gui = make_gui()
        action.gui.db = ScrubberFakeDb()
        action.gui.current_db.new_api = action.gui.db
        action.gui.library_view.get_selected_ids.return_value = selected_ids
        action.qaction = MagicMock()
        action.genesis()
        self.action_module.prefs['show_preview'] = False
        return action

    def test_backup_disabled_user_declines_deletion_preserves_backup(self):
        self.action_module.prefs['backup'] = False
        action = self.make_action([1])
        db = action.gui.db
        dirty_epub = make_dirty_epub()
        original_epub = b'original_backup_bytes'
        db.books[1] = {
            'mi': StubMetadata('Book Title', ['An Author']),
            'formats': {'EPUB': dirty_epub, 'ORIGINAL_EPUB': original_epub}
        }
        with unittest.mock.patch.object(self.action_module.QMessageBox, 'question',
                                       return_value=QtWidgets.QMessageBox.StandardButton.No) as mock_q:
            action.scrub_selected()
            mock_q.assert_called_once()
            self.assertIn('Delete existing backups?', mock_q.call_args[0][1])
        self.assertIn('ORIGINAL_EPUB', db.formats(1))
        self.assertEqual(db.format(1, 'ORIGINAL_EPUB'), original_epub)

    def test_backup_disabled_user_confirms_deletion_removes_backup(self):
        self.action_module.prefs['backup'] = False
        action = self.make_action([1])
        db = action.gui.db
        dirty_epub = make_dirty_epub()
        original_epub = b'original_backup_bytes'
        db.books[1] = {
            'mi': StubMetadata('Book Title', ['An Author']),
            'formats': {'EPUB': dirty_epub, 'ORIGINAL_EPUB': original_epub}
        }
        with unittest.mock.patch.object(self.action_module.QMessageBox, 'question',
                                       return_value=QtWidgets.QMessageBox.StandardButton.Yes) as mock_q:
            action.scrub_selected()
            mock_q.assert_called_once()
        self.assertNotIn('ORIGINAL_EPUB', db.formats(1))

    def test_backup_enabled_saves_original_and_does_not_prompt(self):
        self.action_module.prefs['backup'] = True
        action = self.make_action([1])
        db = action.gui.db
        dirty_epub = make_dirty_epub()
        db.books[1] = {
            'mi': StubMetadata('Book Title', ['An Author']),
            'formats': {'EPUB': dirty_epub}
        }
        with unittest.mock.patch.object(self.action_module.QMessageBox, 'question') as mock_q:
            action.scrub_selected()
            mock_q.assert_not_called()
        self.assertIn('ORIGINAL_EPUB', db.formats(1))

    def test_backup_disabled_multiple_books_prompts_once(self):
        self.action_module.prefs['backup'] = False
        action = self.make_action([1, 2])
        db = action.gui.db
        dirty_epub = make_dirty_epub()
        for bid in (1, 2):
            db.books[bid] = {
                'mi': StubMetadata(f'Book {bid}', ['Author']),
                'formats': {'EPUB': dirty_epub, 'ORIGINAL_EPUB': b'backup'}
            }
        with unittest.mock.patch.object(self.action_module.QMessageBox, 'question',
                                       return_value=QtWidgets.QMessageBox.StandardButton.Yes) as mock_q:
            action.scrub_selected()
            self.assertEqual(mock_q.call_count, 1)
        self.assertNotIn('ORIGINAL_EPUB', db.formats(1))
        self.assertNotIn('ORIGINAL_EPUB', db.formats(2))

    def test_backup_disabled_no_existing_backups_does_not_prompt(self):
        self.action_module.prefs['backup'] = False
        action = self.make_action([1])
        db = action.gui.db
        dirty_epub = make_dirty_epub()
        db.books[1] = {
            'mi': StubMetadata('Book Title', ['Author']),
            'formats': {'EPUB': dirty_epub}
        }
        with unittest.mock.patch.object(self.action_module.QMessageBox, 'question') as mock_q:
            action.scrub_selected()
            mock_q.assert_not_called()

    def test_backup_disabled_preview_mode_removes_backup_when_accepted(self):
        self.action_module.prefs['backup'] = False
        action = self.make_action([1])
        self.action_module.prefs['show_preview'] = True
        db = action.gui.db
        dirty_epub = make_dirty_epub()
        original_epub = b'original_backup_bytes'
        db.books[1] = {
            'mi': StubMetadata('Book Title', ['An Author']),
            'formats': {'EPUB': dirty_epub, 'ORIGINAL_EPUB': original_epub}
        }
        _, report = self.action_module.scrub_epub(dirty_epub, *action.options(), dry_run=True)
        change_ids = {c['id'] for c in report.get('changes', [])}
        scrub_ui = importlib.import_module(self.package + '.ui')
        mock_dialog_class = MagicMock()
        mock_dialog_inst = mock_dialog_class.return_value
        mock_dialog_inst.exec.return_value = QtWidgets.QDialog.DialogCode.Accepted
        mock_dialog_inst.get_selected_change_ids.return_value = change_ids

        with unittest.mock.patch.object(self.action_module.QMessageBox, 'question',
                                       return_value=QtWidgets.QMessageBox.StandardButton.Yes) as mock_q, \
             unittest.mock.patch.object(scrub_ui, 'ScrubPreviewDialog', mock_dialog_class):
            action.scrub_selected()
            mock_q.assert_called_once()
        self.assertNotIn('ORIGINAL_EPUB', db.formats(1))


@unittest.skipUnless(HAVE_QT, 'PyQt6 (with its system libraries) is not installed')
class RedditDialogTests(QtCase):
    def setUp(self):
        super().setUp()
        package = self.load('reddit_follower')
        self.ui = importlib.import_module(package + '.ui')
        self.core = importlib.import_module(package + '.core')
        self.subscription = importlib.import_module(package + '.subscription')
        self.ui.QMessageBox = RecordingBox
        self.ui.prefs['follows'] = [
            self.core.new_follow('Out of Cruel Space', 'u/KyleKKent', 'Out of Cruel Space', 'KyleKKent'),
            self.core.new_follow('r/HFY', 'r/HFY')
        ]

    def test_subscription_exchange_dialog_import_and_export(self):
        dialog = self.ui.SubscriptionExchangeDialog(None)
        self.assertEqual(dialog.tabs.count(), 2)
        self.assertEqual(dialog.export_table.rowCount(), 2)

        # Create temporary OPML file
        opml_text = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <body>
    <outline text="New Series" title="New Series" type="rss" xmlUrl="https://www.reddit.com/r/HFY/search.rss?q=New"/>
    <outline text="Out of Cruel Space" title="Out of Cruel Space" type="rss" xmlUrl="https://www.reddit.com/user/KyleKKent/submitted.rss"/>
  </body>
</opml>"""
        tmp_opml = tempfile.NamedTemporaryFile(suffix='.opml', delete=False)
        try:
            tmp_opml.write(opml_text.encode('utf-8'))
            tmp_opml.close()

            dialog.load_import_file(tmp_opml.name)
            self.assertEqual(dialog.import_table.rowCount(), 2)
            self.assertEqual(dialog.import_table.item(0, 4).text(), 'New')
            self.assertIn('Duplicate', dialog.import_table.item(1, 4).text())

            # Execute import
            dialog.execute_import()
            self.assertGreaterEqual(len(self.ui.prefs['follows']), 3)
        finally:
            os.unlink(tmp_opml.name)


if __name__ == '__main__':
    unittest.main()
