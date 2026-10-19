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
         'calibre.utils.date', 'calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.meta',
         'calibre.ebooks.metadata.book', 'calibre.ebooks.metadata.book.base', 'qt', 'qt.core']


class Meta:
    def __init__(self, title, authors):
        self.title, self.authors, self.ids = title, authors, {}

    def set_identifier(self, key, value):
        self.ids[key] = value


class PluginImportTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: v for k, v in sys.modules.items() if k.startswith(('calibre', 'qt', 'calibre_plugins'))}
        for name in STUBS + ['lxml', 'lxml.etree']:
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
            modules = {'takeout_book_fixer': ['action', 'core'],
                       'epub_promo_scrubber': ['action', 'core'],
                       'story_collection_tagger': ['config', 'action', 'rules'],
                       'gemini_library_fixer': ['config', 'action', 'engine'],
                       'media_matcher': ['config', 'action', 'tags', 'sources', 'matching'],
                       'epub_promo_scrubber': ['action', 'ui', 'core'],
                       'takeout_book_fixer': ['config', 'action', 'ui', 'core', 'importer', 'queue_db', 'daemon', 'watcher'],
                       'fanfic_downloader': ['config', 'action', 'ui', 'engine', 'flaresolverr']}.get(import_name, ['config', 'action', 'ui', 'core'])
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

    def test_import_books_chunked_duplicate_checking(self):
        from plugin import importer

        content_a = b'A' * 70000  # larger than 64 KB chunk size
        content_b = b'B' * 70000

        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tf:
            tf.write(content_a)
            tf.flush()
            path_a = Path(tf.name)

        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tf_diff:
            tf_diff.write(content_b)
            tf_diff.flush()
            path_b = Path(tf_diff.name)

        try:
            # 1. Exact duplicate (title match, size match, SHA-256 hash match)
            mock_gui = MagicMock()
            mock_db = MagicMock()
            mock_gui.current_db.new_api = mock_db
            mock_db.all_book_ids.return_value = [10]
            mock_db.all_field_for.return_value = {10: 'Test Book'}
            mock_db.format_metadata.return_value = {'size': len(content_a)}
            mock_db.format_abspath.return_value = None
            mock_db.format.return_value = content_a

            row_dup = {'output': str(path_a), 'format': 'epub', 'source': 'Test Book.epub', 'metadata': {'title': 'Test Book'}}
            imported, errors = importer.import_books(mock_gui, [row_dup])

            self.assertEqual(imported, [])
            self.assertEqual(row_dup.get('import_status'), 'duplicate skipped')

            # 2. Size mismatch (title match, size mismatch -> duplicate check skips)
            mock_db.reset_mock()
            mock_db.all_book_ids.return_value = [10]
            mock_db.all_field_for.return_value = {10: 'Test Book'}
            mock_db.format_metadata.return_value = {'size': 1234}  # different size
            mock_db.create_book_entry.return_value = 11
            mock_db.add_format.return_value = True

            row_size_mismatch = {'output': str(path_a), 'format': 'epub', 'source': 'Test Book.epub', 'metadata': {'title': 'Test Book'}}
            imported, errors = importer.import_books(mock_gui, [row_size_mismatch])

            self.assertEqual(imported, [11])
            self.assertEqual(row_size_mismatch.get('import_status'), 'imported')
            self.assertEqual(row_size_mismatch.get('calibre_id'), 11)
            # Verify mock_db.format was NOT called because size mismatch aborted hash calculation
            mock_db.format.assert_not_called()

            # 3. Content mismatch (title match, size match, SHA-256 hash mismatch)
            mock_db.reset_mock()
            mock_db.all_book_ids.return_value = [10]
            mock_db.all_field_for.return_value = {10: 'Test Book'}
            mock_db.format_metadata.return_value = {'size': len(content_b)}
            mock_db.format.return_value = content_a  # content_a != content_b
            mock_db.create_book_entry.return_value = 12
            mock_db.add_format.return_value = True

            row_content_mismatch = {'output': str(path_b), 'format': 'epub', 'source': 'Test Book.epub', 'metadata': {'title': 'Test Book'}}
            imported, errors = importer.import_books(mock_gui, [row_content_mismatch])

            self.assertEqual(imported, [12])
            self.assertEqual(row_content_mismatch.get('import_status'), 'imported')

        finally:
            path_a.unlink(missing_ok=True)
            path_b.unlink(missing_ok=True)

    def test_normalize_isbn(self):
        from plugin import importer

        # ISBN-10 to ISBN-13
        self.assertEqual(importer.normalize_isbn('0-306-40615-2'), '9780306406157')
        self.assertEqual(importer.normalize_isbn('0306406152'), '9780306406157')
        self.assertEqual(importer.normalize_isbn('0-8044-2957-X'), '9780804429573')

        # ISBN-13 normalization
        self.assertEqual(importer.normalize_isbn('978-0-306-40615-7'), '9780306406157')
        self.assertEqual(importer.normalize_isbn('  978 0 306 40615 7  '), '9780306406157')

        # Invalid / Empty inputs
        self.assertIsNone(importer.normalize_isbn(None))
        self.assertIsNone(importer.normalize_isbn(''))
        self.assertIsNone(importer.normalize_isbn('   '))

    def test_import_books_multi_index_deduplication(self):
        from plugin import importer

        content_1 = b'Binary Content One' * 5000
        content_2 = b'Binary Content Two' * 5000

        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tf1:
            tf1.write(content_1)
            tf1.flush()
            path_1 = Path(tf1.name)

        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tf2:
            tf2.write(content_2)
            tf2.flush()
            path_2 = Path(tf2.name)

        with tempfile.NamedTemporaryFile(suffix='.epub', delete=False) as tf3:
            tf3.write(content_2)
            tf3.flush()
            path_3 = Path(tf3.name)

        try:
            # Setup mock Calibre DB
            mock_gui = MagicMock()
            mock_db = MagicMock()
            mock_gui.current_db.new_api = mock_db

            mock_db.all_book_ids.return_value = [101, 102]

            def mock_all_field_for(field, book_ids, default=None):
                if field == 'title':
                    return {101: 'Existing Title A', 102: 'Pride and Prejudice'}
                if field == 'identifiers':
                    return {101: {'isbn': '978-1-111-11111-1'}, 102: {'isbn': '0-306-40615-2'}}
                if field == 'formats':
                    return {101: ('EPUB',), 102: ('EPUB',)}
                return default or {}

            mock_db.all_field_for.side_effect = mock_all_field_for

            def mock_format_metadata(book_id, fmt):
                if book_id == 101:
                    return {'size': len(content_1)}
                if book_id == 102:
                    return {'size': 999999}
                return None

            mock_db.format_metadata.side_effect = mock_format_metadata
            mock_db.format_abspath.return_value = None

            def mock_format(book_id, fmt):
                if book_id == 101:
                    return content_1
                if book_id == 102:
                    return b'Different Content'
                return None

            mock_db.format.side_effect = mock_format
            mock_db.create_book_entry.return_value = 200
            mock_db.add_format.return_value = True

            # 1. Stage 1: Exact binary content hash match with DIFFERENT title string
            row_binary_dup = {
                'output': str(path_1),
                'format': 'epub',
                'source': 'Misnamed.epub',
                'metadata': {'title': 'Completely Different Title'}
            }
            imported, errors = importer.import_books(mock_gui, [row_binary_dup])
            self.assertEqual(imported, [])
            self.assertEqual(row_binary_dup.get('import_status'), 'duplicate skipped')

            # 2. Stage 2: Normalized ISBN match with DIFFERENT title string (ISBN-10 vs ISBN-13)
            row_isbn_dup = {
                'output': str(path_2),
                'format': 'epub',
                'source': 'Annotated.epub',
                'metadata': {
                    'title': 'Pride and Prejudice (Annotated Edition)',
                    'industryIdentifiers': [{'type': 'ISBN_13', 'identifier': '978-0-306-40615-7'}]
                }
            }
            imported, errors = importer.import_books(mock_gui, [row_isbn_dup])
            self.assertEqual(imported, [])
            self.assertEqual(row_isbn_dup.get('import_status'), 'duplicate skipped')

            # 3. Novel Non-Duplicate import
            row_novel = {
                'output': str(path_2),
                'format': 'epub',
                'source': 'NovelBook.epub',
                'metadata': {
                    'title': 'Novel Unique Book Title',
                    'isbn': '978-9-999-99999-9'
                }
            }
            imported, errors = importer.import_books(mock_gui, [row_novel])
            self.assertEqual(imported, [200])
            self.assertEqual(row_novel.get('import_status'), 'imported')

            # 4. Intra-batch duplicate import
            mock_db.create_book_entry.return_value = 201
            row_batch_1 = {
                'output': str(path_2),
                'format': 'epub',
                'source': 'BatchOne.epub',
                'metadata': {'title': 'Batch Unique Title', 'isbn': '978-8-888-88888-8'}
            }
            row_batch_2 = {
                'output': str(path_3),
                'format': 'epub',
                'source': 'BatchTwo.epub',
                'metadata': {'title': 'Batch Unique Title (Duplicate)', 'isbn': '978-8-888-88888-8'}
            }
            imported, errors = importer.import_books(mock_gui, [row_batch_1, row_batch_2])
            self.assertEqual(imported, [201])
            self.assertEqual(row_batch_1.get('import_status'), 'imported')
            self.assertEqual(row_batch_2.get('import_status'), 'duplicate skipped')

        finally:
            path_1.unlink(missing_ok=True)
            path_2.unlink(missing_ok=True)
            path_3.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()

