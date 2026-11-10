import hashlib
import importlib
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock

# Setup stub modules for calibre if calibre is not installed in the environment
if 'calibre' not in sys.modules:
    for name in (
        'calibre',
        'calibre.ebooks',
        'calibre.ebooks.metadata',
        'calibre.ebooks.metadata.meta',
        'calibre.ebooks.metadata.book',
        'calibre.ebooks.metadata.book.base',
        'calibre.utils',
        'calibre.utils.date',
    ):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)

    class StubMetadata:
        def __init__(self, title='Unknown', authors=None):
            self.title = title
            self.authors = authors or ['Unknown']
            self.cover_data = None
        def set_identifier(self, key, val):
            pass

    sys.modules['calibre.ebooks.metadata.book.base'].Metadata = StubMetadata
    sys.modules['calibre.ebooks.metadata.meta'].get_metadata = lambda stream, stream_type=None: StubMetadata('Mock Title')
    sys.modules['calibre.utils.date'].parse_date = lambda d, assume_utc=True: None

spec = importlib.util.spec_from_file_location('importer', Path(__file__).resolve().parents[1] / 'plugin/importer.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class MockNewAPI:

    """Mock Calibre new_api database supporting books, formats, format_abspath, and format_metadata."""

    def __init__(self):
        self.books = {}  # book_id -> {'title': str, 'formats': {fmt: {'path': Path or bytes, 'data': bytes}}}
        self.next_id = 1

    def all_book_ids(self):
        return list(self.books.keys())

    def field_for(self, name, book_id, default=None):
        if name == 'title' and book_id in self.books:
            return self.books[book_id].get('title', default)
        return default

    def format_abspath(self, book_id, fmt):
        if book_id in self.books and fmt.upper() in self.books[book_id]['formats']:
            info = self.books[book_id]['formats'][fmt.upper()]
            if 'path' in info and info['path'] is not None:
                return str(info['path'])
        return None

    def format_metadata(self, book_id, fmt):
        if book_id in self.books and fmt.upper() in self.books[book_id]['formats']:
            info = self.books[book_id]['formats'][fmt.upper()]
            if 'path' in info and info['path'] is not None and Path(info['path']).is_file():
                return {'size': Path(info['path']).stat().st_size}
            elif 'data' in info and info['data'] is not None:
                return {'size': len(info['data'])}
        return None

    def format(self, book_id, fmt, as_file=False):
        if book_id in self.books and fmt.upper() in self.books[book_id]['formats']:
            info = self.books[book_id]['formats'][fmt.upper()]
            if 'path' in info and info['path'] is not None and Path(info['path']).is_file():
                f = open(info['path'], 'rb')
                return f if as_file else f.read()
            elif 'data' in info and info['data'] is not None:
                b = io.BytesIO(info['data'])
                return b if as_file else info['data']
        return None

    def create_book_entry(self, mi):
        bid = self.next_id
        self.next_id += 1
        self.books[bid] = {'title': mi.title, 'formats': {}}
        return bid

    def add_format(self, book_id, fmt, stream, replace=True):
        if book_id not in self.books:
            return False
        data = stream.read() if hasattr(stream, 'read') else stream
        self.books[book_id]['formats'][fmt.upper()] = {'data': data}
        return True

    def set_cover(self, cover_dict):
        pass


class TestImporterDuplicateDetection(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_gui(self, db):
        gui = MagicMock()
        gui.current_db.new_api = db
        gui.library_view.model().refresh = MagicMock()
        gui.tags_view.recount = MagicMock()
        return gui

    def test_duplicate_same_size_and_content_skipped(self):
        db = MockNewAPI()
        # Add an existing book on disk
        existing_file = self.root / 'existing.epub'
        content = b'EPUB content sample ' * 1000
        existing_file.write_bytes(content)

        bid = db.create_book_entry(MagicMock(title='Sample Book'))
        db.books[bid]['formats']['EPUB'] = {'path': existing_file}

        # Candidate file to import with same content
        candidate_file = self.root / 'candidate.epub'
        candidate_file.write_bytes(content)

        gui = self._make_gui(db)
        rows = [{
            'output': str(candidate_file),
            'format': 'epub',
            'source': 'sample.pdf',
            'metadata': {'title': 'Sample Book'}
        }]

        imported, errors = importer.import_books(gui, rows)
        self.assertEqual(imported, [])
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['import_status'], 'duplicate skipped')

    def test_different_size_not_duplicate_and_imported(self):
        db = MockNewAPI()
        # Existing book on disk with size X
        existing_file = self.root / 'existing.epub'
        existing_file.write_bytes(b'EPUB content sample size 1')

        bid = db.create_book_entry(MagicMock(title='Sample Book'))
        db.books[bid]['formats']['EPUB'] = {'path': existing_file}

        # Candidate file to import with DIFFERENT size Y (same title and format)
        candidate_file = self.root / 'candidate.epub'
        candidate_file.write_bytes(b'EPUB content sample size 1 - extra content extended')

        gui = self._make_gui(db)
        rows = [{
            'output': str(candidate_file),
            'format': 'epub',
            'source': 'sample.pdf',
            'metadata': {'title': 'Sample Book'}
        }]

        imported, errors = importer.import_books(gui, rows)
        self.assertEqual(len(imported), 1)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['import_status'], 'imported')

    def test_same_size_different_content_not_duplicate(self):
        db = MockNewAPI()
        # Existing book on disk
        existing_file = self.root / 'existing.epub'
        existing_file.write_bytes(b'1234567890AAAAA')

        bid = db.create_book_entry(MagicMock(title='Sample Book'))
        db.books[bid]['formats']['EPUB'] = {'path': existing_file}

        # Candidate file with SAME size but DIFFERENT SHA-256 hash
        candidate_file = self.root / 'candidate.epub'
        candidate_file.write_bytes(b'1234567890BBBBB')

        gui = self._make_gui(db)
        rows = [{
            'output': str(candidate_file),
            'format': 'epub',
            'source': 'sample.pdf',
            'metadata': {'title': 'Sample Book'}
        }]

        imported, errors = importer.import_books(gui, rows)
        self.assertEqual(len(imported), 1)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]['import_status'], 'imported')

    def test_virtual_in_memory_db_duplicate(self):
        db = MockNewAPI()
        content = b'Virtual DB binary stream ' * 500

        # Existing book stored in memory (path is None)
        bid = db.create_book_entry(MagicMock(title='Virtual Book'))
        db.books[bid]['formats']['EPUB'] = {'path': None, 'data': content}

        candidate_file = self.root / 'candidate.epub'
        candidate_file.write_bytes(content)

        gui = self._make_gui(db)
        rows = [{
            'output': str(candidate_file),
            'format': 'epub',
            'source': 'sample.pdf',
            'metadata': {'title': 'Virtual Book'}
        }]

        imported, errors = importer.import_books(gui, rows)
        self.assertEqual(imported, [])
        self.assertEqual(rows[0]['import_status'], 'duplicate skipped')

    def test_large_file_streaming(self):
        """Test streaming hashing with larger file (> 128 KiB, multi-chunk)."""
        db = MockNewAPI()
        # 256 KiB file
        content = b'X' * (256 * 1024)
        existing_file = self.root / 'large_existing.epub'
        existing_file.write_bytes(content)

        bid = db.create_book_entry(MagicMock(title='Large Book'))
        db.books[bid]['formats']['EPUB'] = {'path': existing_file}

        candidate_file = self.root / 'large_candidate.epub'
        candidate_file.write_bytes(content)

        gui = self._make_gui(db)
        rows = [{
            'output': str(candidate_file),
            'format': 'epub',
            'source': 'large.pdf',
            'metadata': {'title': 'Large Book'}
        }]

        imported, errors = importer.import_books(gui, rows)
        self.assertEqual(imported, [])
        self.assertEqual(rows[0]['import_status'], 'duplicate skipped')

    def test_different_title_imported_immediately(self):
        db = MockNewAPI()
        existing_file = self.root / 'existing.epub'
        existing_file.write_bytes(b'Content AAA')

        bid = db.create_book_entry(MagicMock(title='Title A'))
        db.books[bid]['formats']['EPUB'] = {'path': existing_file}

        candidate_file = self.root / 'candidate.epub'
        candidate_file.write_bytes(b'Content AAA')

        gui = self._make_gui(db)
        rows = [{
            'output': str(candidate_file),
            'format': 'epub',
            'source': 'sample.pdf',
            'metadata': {'title': 'Title B'}
        }]

        imported, errors = importer.import_books(gui, rows)
        self.assertEqual(len(imported), 1)
        self.assertEqual(rows[0]['import_status'], 'imported')

    def test_early_exit_on_different_file_size(self):
        """Verify candidate hash is not computed if file sizes differ."""
        db = MockNewAPI()
        existing_file = self.root / 'existing.epub'
        existing_file.write_bytes(b'Short content')

        bid = db.create_book_entry(MagicMock(title='Size Precheck Book'))
        db.books[bid]['formats']['EPUB'] = {'path': existing_file}

        candidate_file = self.root / 'candidate.epub'
        candidate_file.write_bytes(b'Much longer content that differs in size significantly')

        gui = self._make_gui(db)
        rows = [{
            'output': str(candidate_file),
            'format': 'epub',
            'source': 'sample.pdf',
            'metadata': {'title': 'Size Precheck Book'}
        }]

        from unittest.mock import patch
        with patch.object(importer, '_sha256_path') as mock_sha:
            imported, errors = importer.import_books(gui, rows)
            self.assertEqual(len(imported), 1)
            self.assertEqual(rows[0]['import_status'], 'imported')
            # Mock SHA-256 should NOT be called at all because size check failed early!
            mock_sha.assert_not_called()

    def test_sha256_chunk_size(self):
        """Verify SHA-256 stream reads in 64 KiB chunks."""
        data = b'Y' * (128 * 1024 + 10)
        stream = io.BytesIO(data)
        read_sizes = []

        orig_read = stream.read
        def tracking_read(n=-1):
            read_sizes.append(n)
            return orig_read(n)

        stream.read = tracking_read
        digest = importer._sha256_stream(stream, chunk_size=64 * 1024)
        self.assertTrue(digest)
        self.assertEqual(read_sizes[0], 64 * 1024)
        self.assertEqual(read_sizes[1], 64 * 1024)


if __name__ == '__main__':
    unittest.main()
