"""Unit tests for EPUB merger pre-flight extraction pipeline and MergePreviewDialog."""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PyQt5 import QtCore, QtGui, QtWidgets

    HAVE_QT = True
    qtcore = types.ModuleType('qt.core')
    for module in (QtWidgets, QtGui, QtCore):
        for name in dir(module):
            setattr(qtcore, name, getattr(module, name))
    sys.modules['qt.core'] = qtcore
    sys.modules['qt'] = types.ModuleType('qt')
    sys.modules['qt'].core = qtcore
except ImportError:
    HAVE_QT = False

from qt.core import QApplication, Qt

from common.storykit import build_epub, extract_epub_story, merge_epubs
from common.libkit import merge_library_books
from common.guikit import (
    MergePreviewDialog,
    scan_epub_files_for_merge,
    scan_library_books_for_merge,
)

app = QApplication.instance() if HAVE_QT else None
if app is None and HAVE_QT:
    app = QApplication([])


def make_dummy_story(story_id, title, author, pubdate='2025-01-01', chapters=None):
    if chapters is None:
        chapters = [('Chapter 1', '<p>First chapter text.</p>'), ('Chapter 2', '<p>Second chapter text.</p>')]
    return {
        'id': story_id,
        'title': title,
        'author': author,
        'summary': f'Summary for {title}',
        'tags': ['fiction', 'fantasy'],
        'pubdate': pubdate,
        'url': f'http://example.com/{story_id}',
        'sections': chapters,
    }


class EpubMergeDialogTests(unittest.TestCase):

    def setUp(self):
        self.story1 = make_dummy_story('story1', 'Alpha Chronicles', 'Alice Smith', pubdate='2025-01-01')
        self.story2 = make_dummy_story('story2', 'Beta Saga', 'Bob Jones', pubdate='2025-02-01')
        self.story3 = make_dummy_story('story3', 'Gamma Realm', 'Charlie Brown', pubdate='2025-03-01')

        self.epub1_bytes = build_epub([self.story1])
        self.epub2_bytes = build_epub([self.story2])
        self.epub3_bytes = build_epub([self.story3])
        self.corrupted_bytes = b'PK\x03\x04 corrupted invalid zip data'

    def test_scan_epub_files_for_merge_valid_and_corrupted(self):
        scan_results = scan_epub_files_for_merge([
            self.epub1_bytes,
            self.corrupted_bytes,
            self.epub2_bytes
        ])

        self.assertEqual(len(scan_results), 3)
        self.assertEqual(scan_results[0]['status'], 'ok')
        self.assertEqual(scan_results[0]['story']['title'], 'Alpha Chronicles')

        self.assertEqual(scan_results[1]['status'], 'error')
        self.assertIn('Failed to extract', scan_results[1]['error'])

        self.assertEqual(scan_results[2]['status'], 'ok')
        self.assertEqual(scan_results[2]['story']['title'], 'Beta Saga')

    def test_scan_library_books_for_merge(self):
        gui = MagicMock()
        db = MagicMock()
        gui.current_db.new_api = db

        def mock_get_metadata(book_id):
            mi = MagicMock()
            if book_id == 1:
                mi.title = 'Alpha Chronicles'
                mi.authors = ['Alice Smith']
            elif book_id == 2:
                mi.title = 'Corrupted Book'
                mi.authors = ['Bad Author']
            return mi

        def mock_format(book_id, fmt):
            if book_id == 1 and fmt == 'EPUB':
                return self.epub1_bytes
            if book_id == 2 and fmt == 'EPUB':
                return self.corrupted_bytes
            return None

        db.get_metadata.side_effect = mock_get_metadata
        db.format.side_effect = mock_format

        results = scan_library_books_for_merge(gui, [1, 2, 3])
        self.assertEqual(len(results), 3)

        self.assertEqual(results[0]['status'], 'ok')
        self.assertEqual(results[0]['title'], 'Alpha Chronicles')

        self.assertEqual(results[1]['status'], 'error')
        self.assertIn('Failed to extract', results[1]['error'])

        self.assertEqual(results[2]['status'], 'error')
        self.assertIn('has no EPUB format', results[2]['error'])

    def test_dialog_tree_population_and_metadata_prepopulation(self):
        scan_results = [
            {'status': 'ok', 'title': 'Alpha Chronicles', 'story': self.story1},
            {'status': 'error', 'title': 'Corrupted Book', 'error': 'Corrupted zip structure'},
            {'status': 'ok', 'title': 'Beta Saga', 'story': self.story2},
        ]

        dialog = MergePreviewDialog(None, scan_results)

        self.assertIn('Alpha Chronicles', dialog.title_edit.text())
        self.assertIn('Alice Smith', dialog.author_edit.text())
        self.assertIn('Bob Jones', dialog.author_edit.text())
        self.assertEqual(dialog.publisher_edit.text(), 'Merged Omnibus')
        self.assertIn('fiction', dialog.tags_edit.text())

        tree = dialog.tree
        self.assertEqual(tree.topLevelItemCount(), 3)

        item0 = tree.topLevelItem(0)
        self.assertIn('Alpha Chronicles', item0.text(0))
        self.assertEqual(item0.checkState(0), Qt.CheckState.Checked)

        item1 = tree.topLevelItem(1)
        self.assertIn('CORRUPTED', item1.text(0))
        self.assertEqual(item1.checkState(0), Qt.CheckState.Unchecked)

        item2 = tree.topLevelItem(2)
        self.assertIn('Beta Saga', item2.text(0))

        active = dialog.get_active_stories()
        self.assertEqual(len(active), 2)
        self.assertEqual(active[0]['title'], 'Alpha Chronicles')
        self.assertEqual(active[1]['title'], 'Beta Saga')

    def test_dialog_reordering_move_up_move_down_auto_sort(self):
        scan_results = [
            {'status': 'ok', 'title': 'Beta Saga', 'story': self.story2},  # pubdate 2025-02-01
            {'status': 'ok', 'title': 'Alpha Chronicles', 'story': self.story1},  # pubdate 2025-01-01
        ]

        dialog = MergePreviewDialog(None, scan_results)
        active_initial = dialog.get_active_stories()
        self.assertEqual(active_initial[0]['title'], 'Beta Saga')
        self.assertEqual(active_initial[1]['title'], 'Alpha Chronicles')

        # Select item 0 and move down
        item0 = dialog.tree.topLevelItem(0)
        dialog.tree.setCurrentItem(item0)
        dialog.move_down()

        active_after_move = dialog.get_active_stories()
        self.assertEqual(active_after_move[0]['title'], 'Alpha Chronicles')
        self.assertEqual(active_after_move[1]['title'], 'Beta Saga')

        # Move item back up
        dialog.move_up()
        active_after_up = dialog.get_active_stories()
        self.assertEqual(active_after_up[0]['title'], 'Beta Saga')

        # Test Auto-Sort (chronological order)
        dialog.auto_sort()
        active_after_sort = dialog.get_active_stories()
        self.assertEqual(active_after_sort[0]['title'], 'Alpha Chronicles')
        self.assertEqual(active_after_sort[1]['title'], 'Beta Saga')

    def test_dialog_chapter_deselection(self):
        scan_results = [
            {'status': 'ok', 'title': 'Alpha Chronicles', 'story': self.story1},
        ]

        dialog = MergePreviewDialog(None, scan_results)
        book_item = dialog.tree.topLevelItem(0)
        chapter2_item = book_item.child(1)

        # Uncheck chapter 2
        chapter2_item.setCheckState(0, Qt.CheckState.Unchecked)

        active = dialog.get_active_stories()
        self.assertEqual(len(active), 1)
        self.assertEqual(len(active[0]['sections']), 1)
        self.assertEqual(active[0]['sections'][0][0], 'Chapter 1')

    def test_merge_epubs_with_reordered_stories_and_custom_metadata(self):
        active_stories = [self.story2, self.story1]  # Beta then Alpha
        merged_bytes = merge_epubs(
            [self.epub2_bytes, self.epub1_bytes],
            title='Chronicles Anthology',
            author='Custom Author Pair',
            publisher='Custom Publishing Co',
            tags=['anthology', 'omnibus'],
            summary='An epic combined collection.',
            stories_override=active_stories,
            preserve_story_order=True
        )

        extracted = extract_epub_story(merged_bytes)
        self.assertEqual(extracted['title'], 'Chronicles Anthology')
        self.assertEqual(extracted['author'], 'Custom Author Pair')

        # Check section count
        self.assertEqual(len(extracted['sections']), 4)
        # Beta story chapters come first
        self.assertIn('Beta Saga', extracted['sections'][0][1])

    def test_merge_library_books_with_custom_metadata_and_reordering(self):
        gui = MagicMock()
        db = MagicMock()
        gui.current_db.new_api = db
        db.add_books.return_value = ([99], {})

        active_stories = [self.story2, self.story1]
        book_id, errors = merge_library_books(
            gui,
            [1, 2],
            title='Library Omnibus',
            author='Alice & Bob',
            publisher='Library Press',
            tags=['lib-tag'],
            description='Library description',
            stories_override=active_stories,
            preserve_story_order=True
        )

        self.assertEqual(book_id, 99)
        self.assertEqual(errors, [])
        db.add_books.assert_called_once()
        mi, formats = db.add_books.call_args[0][0][0]
        self.assertEqual(mi.title, 'Library Omnibus')
        self.assertEqual(mi.publisher, 'Library Press')
        self.assertEqual(mi.tags, ['lib-tag'])
        self.assertEqual(mi.comments, 'Library description')


if __name__ == '__main__':
    unittest.main()
