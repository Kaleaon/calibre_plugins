import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if 'calibre' not in sys.modules:
    for mod_name in ('calibre', 'calibre.customize', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config',
                     'calibre.utils.date', 'calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.meta',
                     'calibre.ebooks.metadata.book', 'calibre.ebooks.metadata.book.base'):
        sys.modules[mod_name] = MagicMock()
    class StubPrefs(dict):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.defaults = {}
        def __getitem__(self, key):
            try:
                return super().__getitem__(key)
            except KeyError:
                return self.defaults[key]
        def get(self, key, default=None):
            if key in self:
                return self[key]
            if key in self.defaults:
                return self.defaults[key]
            return default
    class Metadata:
        def __init__(self, title, authors):
            self.title = title
            self.authors = authors
            self.ids = {}
        def set_identifier(self, k, v):
            self.ids[k] = v
    sys.modules['calibre.utils.config'].JSONConfig = StubPrefs
    sys.modules['calibre.ebooks.metadata.book.base'].Metadata = Metadata
    sys.modules['calibre.ebooks.metadata.meta'].get_metadata = lambda stream, stream_type: Metadata('Test', ['Author'])
    sys.modules['calibre.utils.date'].parse_date = lambda d, assume_utc=True: None

if 'calibre_plugins' not in sys.modules:
    cp = types.ModuleType('calibre_plugins')
    cp.__path__ = [str(ROOT)]
    sys.modules['calibre_plugins'] = cp
elif not getattr(sys.modules['calibre_plugins'], '__path__', None):
    sys.modules['calibre_plugins'].__path__ = [str(ROOT)]

if 'calibre_plugins.takeout_book_fixer' not in sys.modules:
    pkg = types.ModuleType('calibre_plugins.takeout_book_fixer')
    pkg.__path__ = [str(ROOT / 'plugin')]
    sys.modules['calibre_plugins.takeout_book_fixer'] = pkg

from calibre_plugins.takeout_book_fixer.config import prefs
from calibre_plugins.takeout_book_fixer.watcher import WatchFolderManager
from calibre_plugins.takeout_book_fixer import ui


class FakeDb:
    def __init__(self):
        self.books = {}
        self.next_id = 1

    def all_book_ids(self):
        return set(self.books)

    def all_field_for(self, field, ids, default=None):
        return {}

    def create_book_entry(self, mi):
        book_id = self.next_id
        self.next_id += 1
        self.books[book_id] = {'mi': mi, 'formats': {}}
        return book_id

    def add_format(self, book_id, fmt, stream, replace=True):
        self.books[book_id]['formats'][fmt] = stream.read()
        return True

    def set_cover(self, cover_dict):
        pass


class FakeGui:
    def __init__(self):
        self.db = FakeDb()
        self.current_db = MagicMock()
        self.current_db.new_api = self.db
        self.library_view = MagicMock()
        self.tags_view = MagicMock()


class WatchFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.watch_dir = self.root / 'watch'
        self.output_dir = self.root / 'output'
        self.processed_dir = self.root / 'processed'

        self.watch_dir.mkdir()
        self.output_dir.mkdir()
        self.processed_dir.mkdir()

        # Save old prefs
        self.old_prefs = dict(prefs)
        prefs['watch_dir'] = str(self.watch_dir)
        prefs['output_dir'] = str(self.output_dir)
        prefs['processed_dir'] = str(self.processed_dir)
        prefs['auto_watch'] = True
        prefs['convert_comics'] = False
        prefs['auto_import'] = True
        prefs['quiet_period'] = 3

        self.gui = FakeGui()

    def tearDown(self):
        for k in list(prefs.keys()):
            del prefs[k]
        prefs.update(self.old_prefs)
        self.tmp.cleanup()

    def test_candidate_filtering(self):
        manager = WatchFolderManager(self.gui)

        # Create valid book ZIP file
        zip_file = self.watch_dir / 'takeout1.zip'
        zip_file.write_bytes(b'PK\x03\x04test')

        # Create temporary download files that should be ignored
        (self.watch_dir / 'downloading.tmp').write_bytes(b'tmp')
        (self.watch_dir / 'large.crdownload').write_bytes(b'part')
        (self.watch_dir / '.DS_Store').write_bytes(b'hidden')

        # Create output and processed dirs inside watch_dir to test exclusion
        sub_processed = self.watch_dir / 'processed'
        sub_processed.mkdir(exist_ok=True)
        (sub_processed / 'old.zip').write_bytes(b'old')

        candidates, out, proc = manager.get_candidate_items()
        candidate_names = [c.name for c in candidates]

        self.assertIn('takeout1.zip', candidate_names)
        self.assertNotIn('downloading.tmp', candidate_names)
        self.assertNotIn('large.crdownload', candidate_names)
        self.assertNotIn('.DS_Store', candidate_names)
        self.assertNotIn('processed', candidate_names)

    def test_transient_lock_and_file_stability(self):
        manager = WatchFolderManager(self.gui)
        test_file = self.watch_dir / 'writing.zip'
        test_file.write_bytes(b'initial')

        # First snapshot
        self.assertTrue(manager._is_file_stable(test_file))

        # Modify file size
        test_file.write_bytes(b'initial + new data appended')
        self.assertFalse(manager._is_file_stable(test_file))

        # Second check without change -> now stable
        self.assertTrue(manager._is_file_stable(test_file))

    def test_process_watch_dir_and_relocation(self):
        manager = WatchFolderManager(self.gui)

        # Create a valid EPUB book in watch folder
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip')
            z.writestr('META-INF/container.xml', '<container/>')
        epub_data = buf.getvalue()

        book_path = self.watch_dir / 'mybook.epub'
        book_path.write_bytes(epub_data)

        self.assertTrue(book_path.exists())
        self.assertEqual(len(list(self.watch_dir.glob('*.epub'))), 1)

        # Process watch directory
        manager.process_watch_dir()

        # File in watch_dir should have been moved to processed_dir
        self.assertFalse(book_path.exists())
        self.assertTrue((self.processed_dir / 'mybook.epub').exists())

        # Recovered book should be in output_dir
        recovered = list(self.output_dir.glob('book-*.epub'))
        self.assertEqual(len(recovered), 1)

        # Book should be imported into library
        self.assertEqual(len(self.gui.db.books), 1)

        # Re-running process_watch_dir should be a no-op (no duplicate loop)
        manager.process_watch_dir()
        self.assertEqual(len(self.gui.db.books), 1)

    def test_relocation_collision_resolution(self):
        manager = WatchFolderManager(self.gui)

        # Put an existing file with same name in processed_dir
        (self.processed_dir / 'drop.zip').write_bytes(b'first')

        # Drop new file into watch_dir
        new_drop = self.watch_dir / 'drop.zip'
        new_drop.write_bytes(b'PK\x03\x04second')

        manager._relocate_processed(new_drop, self.processed_dir)

        # Original drop.zip in processed_dir remains untouched
        self.assertEqual((self.processed_dir / 'drop.zip').read_bytes(), b'first')

        # Relocated drop file should have a new unique name in processed_dir
        relocated = [f for f in self.processed_dir.glob('drop_*.zip')]
        self.assertEqual(len(relocated), 1)
        self.assertEqual(relocated[0].read_bytes(), b'PK\x03\x04second')


if __name__ == '__main__':
    unittest.main()
