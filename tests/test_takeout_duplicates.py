import tempfile
import sys
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

try:
    import PyQt6.QtWidgets as _QtWidgets
    import PyQt6.QtCore as _QtCore
    import PyQt6.QtGui as _QtGui
    qt_core = type(sys)('qt.core')
    for mod in (_QtWidgets, _QtCore, _QtGui):
        for attr in dir(mod):
            if not attr.startswith('_'):
                setattr(qt_core, attr, getattr(mod, attr))
    sys.modules['qt'] = type(sys)('qt')
    sys.modules['qt.core'] = qt_core
except ImportError:
    pass

STUBS = ['calibre', 'calibre.customize', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config',
         'calibre.utils.date', 'calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.meta',
         'calibre.ebooks.metadata.book', 'calibre.ebooks.metadata.book.base']

for name in STUBS:
    if name not in sys.modules:
        sys.modules[name] = MagicMock()

class Meta:
    def __init__(self, title, authors):
        self.title, self.authors, self.ids = title, authors, {}

    def set_identifier(self, key, value):
        self.ids[key] = value

sys.modules['calibre.ebooks.metadata.book.base'].Metadata = Meta
sys.modules['calibre.gui2.actions'].InterfaceAction = object

from plugin import importer, action


class TakeoutDuplicatePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

        # Create dummy file
        self.book_file = self.tmp_path / 'test_book.epub'
        self.book_file.write_bytes(b'EPUB_TEST_CONTENT_' * 1000)

    def tearDown(self):
        self.tmp.cleanup()

    def _create_mock_gui(self, existing_title='Existing Book', existing_isbn='978-0-306-40615-7', existing_id=100):
        mock_gui = MagicMock()
        mock_db = MagicMock()
        mock_gui.current_db.new_api = mock_db

        mock_db.all_book_ids.return_value = [existing_id]

        def mock_all_field_for(field, book_ids, default=None):
            if field == 'title':
                return {existing_id: existing_title}
            if field == 'identifiers':
                return {existing_id: {'isbn': existing_isbn}}
            if field == 'formats':
                return {existing_id: ('EPUB',)}
            return default or {}

        mock_db.all_field_for.side_effect = mock_all_field_for
        mock_db.field_for.side_effect = lambda f, bid, default=None: existing_title if f == 'title' else (('Author A',) if f == 'authors' else default)
        mock_db.formats.return_value = ('EPUB',)
        mock_db.format_metadata.return_value = {'size': len(b'EPUB_TEST_CONTENT_' * 1000)}
        mock_db.format_abspath.return_value = None
        mock_db.format.return_value = b'EPUB_TEST_CONTENT_' * 1000
        mock_db.create_book_entry.return_value = 200
        mock_db.add_format.return_value = True

        return mock_gui, mock_db

    def test_auto_skip_policy(self):
        mock_gui, mock_db = self._create_mock_gui()
        row = {
            'output': str(self.book_file),
            'format': 'epub',
            'source': 'test_book.epub',
            'metadata': {'title': 'Existing Book', 'isbn': '978-0-306-40615-7'}
        }
        imported, errors = importer.import_books(mock_gui, [row], duplicate_policy='skip')
        self.assertEqual(imported, [])
        self.assertEqual(row.get('import_status'), 'duplicate skipped')

    def test_auto_merge_policy(self):
        mock_gui, mock_db = self._create_mock_gui()
        row = {
            'output': str(self.book_file),
            'format': 'epub',
            'source': 'test_book.epub',
            'metadata': {'title': 'Existing Book', 'isbn': '978-0-306-40615-7'}
        }
        imported, errors = importer.import_books(mock_gui, [row], duplicate_policy='merge')
        self.assertEqual(imported, [100])
        self.assertEqual(row.get('import_status'), 'merged')
        self.assertEqual(row.get('calibre_id'), 100)
        mock_db.add_format.assert_called()

    def test_auto_overwrite_policy(self):
        mock_gui, mock_db = self._create_mock_gui()
        row = {
            'output': str(self.book_file),
            'format': 'epub',
            'source': 'test_book.epub',
            'metadata': {'title': 'Existing Book Updated', 'isbn': '978-0-306-40615-7'}
        }
        imported, errors = importer.import_books(mock_gui, [row], duplicate_policy='overwrite')
        self.assertEqual(imported, [100])
        self.assertEqual(row.get('import_status'), 'overwritten')
        self.assertEqual(row.get('calibre_id'), 100)
        mock_db.set_metadata.assert_called()

    def test_keep_both_policy(self):
        mock_gui, mock_db = self._create_mock_gui()
        row = {
            'output': str(self.book_file),
            'format': 'epub',
            'source': 'test_book.epub',
            'metadata': {'title': 'Existing Book', 'isbn': '978-0-306-40615-7'}
        }
        imported, errors = importer.import_books(mock_gui, [row], duplicate_policy='keep_both')
        self.assertEqual(imported, [200])
        self.assertEqual(row.get('import_status'), 'imported')
        self.assertEqual(row.get('calibre_id'), 200)
        mock_db.create_book_entry.assert_called()

    def test_prompt_policy_with_dialog_resolution(self):
        mock_gui, mock_db = self._create_mock_gui()
        row = {
            'output': str(self.book_file),
            'format': 'epub',
            'source': 'test_book.epub',
            'metadata': {'title': 'Existing Book', 'isbn': '978-0-306-40615-7'}
        }

        with patch('plugin.importer._show_conflict_dialog') as mock_dialog:
            mock_dialog.return_value = {0: 'merge'}
            imported, errors = importer.import_books(mock_gui, [row], duplicate_policy='prompt')
            self.assertEqual(imported, [100])
            self.assertEqual(row.get('import_status'), 'merged')
            mock_dialog.assert_called_once()

    def test_conflict_dialog_qt_widget(self):
        try:
            from PyQt6.QtWidgets import QApplication
        except ImportError:
            self.skipTest('PyQt6 not available for Qt widget test')

        app = QApplication.instance() or QApplication([])

        conflicts = [
            {
                'parsed_idx': 0,
                'title': 'Incoming Book 1',
                'authors': 'Author One',
                'existing_title': 'Existing Book 1',
                'existing_authors': 'Author One',
                'existing_formats': 'EPUB',
                'incoming_format': 'PDF',
                'matched_id': 101
            },
            {
                'parsed_idx': 1,
                'title': 'Incoming Book 2',
                'authors': 'Author Two',
                'existing_title': 'Existing Book 2',
                'existing_authors': 'Author Two',
                'existing_formats': 'MOBI',
                'incoming_format': 'EPUB',
                'matched_id': 102
            }
        ]

        dlg = importer.DuplicateConflictDialog(None, conflicts)
        self.assertEqual(dlg.table.rowCount(), 2)
        resolutions = dlg.get_resolutions()
        self.assertEqual(len(resolutions), 2)

        # Test batch apply action
        idx = dlg.batch_combo.findData('overwrite')
        if idx >= 0:
            dlg.batch_combo.setCurrentIndex(idx)
        dlg._apply_batch_action()

        updated_resolutions = dlg.get_resolutions()
        self.assertEqual(updated_resolutions[0], 'overwrite')
        self.assertEqual(updated_resolutions[1], 'overwrite')


if __name__ == '__main__':
    unittest.main()
