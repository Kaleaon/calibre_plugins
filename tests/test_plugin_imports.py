"""Loads each built plugin zip against stub calibre/Qt modules to catch import and name errors.

This does not exercise real Calibre or Qt behaviour; it only proves the packaged modules import and their pure helpers run.
"""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_plugins  # noqa: E402

STUBS = ['calibre', 'calibre.customize', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config',
         'calibre.utils.date', 'calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.book',
         'calibre.ebooks.metadata.book.base', 'qt', 'qt.core']


class Meta:
    def __init__(self, title, authors):
        self.title, self.authors, self.ids = title, authors, {}

    def set_identifier(self, key, value):
        self.ids[key] = value


class PluginImportTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: v for k, v in sys.modules.items() if k.startswith(('calibre', 'qt', 'calibre_plugins'))}
        for name in STUBS:
            sys.modules[name] = MagicMock()
        sys.modules['calibre.ebooks.metadata.book.base'].Metadata = Meta
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        sys.modules['calibre.customize'].InterfaceActionBase = object
        sys.modules['calibre.utils.config'].JSONConfig = lambda name: type('Prefs', (dict,), {'defaults': {}})()
        self.tmp = tempfile.TemporaryDirectory()
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins'].__path__ = []

    def tearDown(self):
        for k in [k for k in sys.modules if k.startswith(('calibre', 'qt'))]:
            del sys.modules[k]
        sys.modules.update(self.saved)
        self.tmp.cleanup()

    def load(self, import_name):
        """Extract the freshly built zip and register it as calibre_plugins.<import_name>."""
        if not hasattr(self, 'built'):
            self.built = build_plugins.build_all(Path(self.tmp.name) / 'dist')
        target = Path(self.tmp.name) / import_name
        zipfile.ZipFile(self.built[import_name]).extractall(target)
        package = f'calibre_plugins.{import_name}'
        module = types.ModuleType(package)
        module.__path__ = [str(target)]
        sys.modules[package] = module
        return package

    def test_every_plugin_module_imports(self):
        self.load('metabods_downloader')  # builds everything that can be built in this environment
        for import_name in self.built:
            package = self.load(import_name)
            modules = {'story_collection_tagger': ['config', 'action', 'rules'],
                       'gemini_library_fixer': ['config', 'action', 'engine'],
                       'media_matcher': ['config', 'action', 'tags', 'sources', 'matching'],
                       'fanfic_downloader': ['config', 'action', 'ui', 'engine', 'flaresolverr'],
                       'takeout_book_fixer': ['action', 'core', 'importer'],
                       'epub_promo_scrubber': ['action', 'ui', 'core']}.get(import_name, ['config', 'action', 'ui', 'core'])
            for module in modules:
                with self.subTest(plugin=import_name, module=module):
                    importlib.import_module(f'{package}.{module}')

    def test_library_metadata_helper(self):
        package = self.load('metabods_downloader')
        libkit = importlib.import_module(package + '.libkit')
        group = [{'id': 'a', 'title': 'T', 'author': 'Au', 'tags': ['x'], 'categories': ['c'], 'summary': 'S & s', 'pubdate': '2026-01-02'}]
        mi = libkit.metadata_for(group, None, 'metabods', 'Metabods')
        self.assertEqual((mi.title, mi.authors, mi.ids, mi.tags), ('T', ['Au'], {'metabods': 'a'}, ['x', 'c']))
        self.assertEqual(mi.comments, '<p>S &amp; s</p>')
        two = libkit.metadata_for(group + [dict(group[0], id='b', title='U')], 'Omni', 'metabods', 'Metabods')
        self.assertEqual((two.title, two.ids), ('Omni', {}))

    def test_library_metadata_carries_series_fields(self):
        package = self.load('metabods_downloader')
        libkit = importlib.import_module(package + '.libkit')
        story = {'id': 'a', 'title': 'Part B', 'author': 'Au', 'tags': [], 'categories': [], 'series': 'My Series', 'series_index': 2.0}
        mi = libkit.metadata_for([story], None, 'nifty', 'Nifty')
        self.assertEqual((mi.series, mi.series_index), ('My Series', 2.0))
        plain = libkit.metadata_for([{'id': 'b', 'title': 'Solo', 'author': 'Au', 'tags': [], 'categories': []}], None, 'nifty', 'Nifty')
        self.assertFalse(hasattr(plain, 'series'), 'a story outside a series gets no series fields')

    def test_downloader_action_existing_ids(self):
        for plugin, ident in (('metabods_downloader', 'metabods'), ('nifty_downloader', 'nifty')):
            package = self.load(plugin)
            libkit = importlib.import_module(package + '.libkit')
            action_mod = importlib.import_module(package + '.action')
            
            # Identify action class
            action_cls = action_mod.MetabodsAction if plugin == 'metabods_downloader' else action_mod.NiftyAction
            self.assertTrue(issubclass(action_cls, libkit.DownloaderAction))

            mock_gui = MagicMock()
            mock_gui.current_db.new_api.all_book_ids.return_value = [1, 2]
            mock_gui.current_db.new_api.all_field_for.return_value = {
                1: {ident: 'id1'},
                2: {ident: 'id2'}
            }

            action = action_cls()
            action.gui = mock_gui
            self.assertEqual(action.existing_ids(), {'id1', 'id2'})

    def test_epub_builder_and_core_build_epub(self):
        for plugin in ('metabods_downloader', 'nifty_downloader'):
            package = self.load(plugin)
            storykit = importlib.import_module(package + '.storykit')
            core = importlib.import_module(package + '.core')

            self.assertTrue(hasattr(storykit, 'epub_builder'))
            self.assertTrue(callable(core.build_epub))


if __name__ == '__main__':
    unittest.main()
