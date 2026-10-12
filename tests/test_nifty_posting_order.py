import importlib.util
import io
from pathlib import Path
import sys
import unittest
import zipfile
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))

spec_core = importlib.util.spec_from_file_location('ncore', Path(__file__).resolve().parents[1] / 'nifty/core.py')
core = importlib.util.module_from_spec(spec_core)
spec_core.loader.exec_module(core)

from common.storykit import build_epub, extract_epub_story, sort_by_posting_order
from common.libkit import consolidate_nifty_omnibuses, find_nifty_books, group_related_nifty_stories


class PostingOrderSortingTests(unittest.TestCase):

    def test_sort_by_posting_date_chronological(self):
        s1 = {'title': 'Chapter 1', 'pubdate': '1999-10-02', 'id': 'gay/college/saga-1'}
        s2 = {'title': 'Chapter 2', 'pubdate': '1999-10-03', 'id': 'gay/college/saga-2'}
        s3 = {'title': 'Chapter 3', 'pubdate': '2026-10-01', 'id': 'gay/college/saga-3'}
        
        unordered = [s3, s1, s2]
        sorted_stories = sort_by_posting_order(unordered)
        self.assertEqual([s['title'] for s in sorted_stories], ['Chapter 1', 'Chapter 2', 'Chapter 3'])

    def test_sort_by_header_date_fallback(self):
        body1 = 'Date: Thu, 2 Oct 1999 10:00:00 +0000\nFrom: x\n\nBody 1'
        body2 = 'Date: Fri, 3 Oct 1999 10:00:00 +0000\nFrom: x\n\nBody 2'
        s1 = {'title': 'Story One', 'sections': [('Part 1', body1)], 'id': 'gay/college/s1'}
        s2 = {'title': 'Story Two', 'sections': [('Part 1', body2)], 'id': 'gay/college/s2'}

        unordered = [s2, s1]
        sorted_stories = sort_by_posting_order(unordered)
        self.assertEqual([s['title'] for s in sorted_stories], ['Story One', 'Story Two'])

    def test_sort_equal_dates_fallback_to_chapter_index(self):
        s1 = {'title': 'Saga - Part 10', 'pubdate': '2000-01-01', 'series_index': 10.0}
        s2 = {'title': 'Saga - Part 2', 'pubdate': '2000-01-01', 'series_index': 2.0}
        s3 = {'title': 'Saga - Part 1', 'pubdate': '2000-01-01', 'series_index': 1.0}

        sorted_stories = sort_by_posting_order([s1, s2, s3])
        self.assertEqual([s['title'] for s in sorted_stories], ['Saga - Part 1', 'Saga - Part 2', 'Saga - Part 10'])

    def test_sort_missing_dates_fallback_to_title(self):
        s1 = {'title': 'Alpha Story'}
        s2 = {'title': 'Beta Story'}
        s3 = {'title': 'Gamma Story'}

        sorted_stories = sort_by_posting_order([s3, s1, s2])
        self.assertEqual([s['title'] for s in sorted_stories], ['Alpha Story', 'Beta Story', 'Gamma Story'])


class OmnibusCreationTests(unittest.TestCase):

    def test_epub_omnibus_posting_order(self):
        s1 = {
            'id': 'gay/college/saga-1',
            'title': 'Saga Part 1',
            'author': 'Writer A',
            'pubdate': '1999-10-02',
            'sections': [('Chapter 1', '<p>First chapter content</p>')]
        }
        s2 = {
            'id': 'gay/college/saga-2',
            'title': 'Saga Part 2',
            'author': 'Writer A',
            'pubdate': '1999-10-03',
            'sections': [('Chapter 1', '<p>Second chapter content</p>')]
        }
        epub_data = core.build_epub([s2, s1], title='Saga Omnibus')
        self.assertTrue(epub_data.startswith(b'PK'))

        extracted = extract_epub_story(epub_data)
        self.assertEqual(extracted['title'], 'Saga Omnibus')
        self.assertEqual(len(extracted['sections']), 2)


class LibraryScanningAndConsolidationTests(unittest.TestCase):

    def setUp(self):
        s1 = {
            'id': 'gay/college/saga-1',
            'title': 'Saga: Part 1',
            'author': 'Writer A',
            'pubdate': '1999-10-02',
            'sections': [('Ch 1', '<p>Start</p>')]
        }
        s2 = {
            'id': 'gay/college/saga-2',
            'title': 'Saga: Part 2',
            'author': 'Writer A',
            'pubdate': '1999-10-03',
            'sections': [('Ch 2', '<p>Middle</p>')]
        }
        self.epub1_bytes = build_epub([s1])
        self.epub2_bytes = build_epub([s2])

    def test_find_and_group_nifty_books(self):
        class DummyDB:
            def __init__(db_self, epub1, epub2):
                db_self.books = {
                    1: {'title': 'Saga: Part 1', 'authors': ['Writer A'], 'idents': {'nifty': 'gay/college/saga-1'}, 'epub': epub1},
                    2: {'title': 'Saga: Part 2', 'authors': ['Writer A'], 'idents': {'nifty': 'gay/college/saga-2'}, 'epub': epub2},
                }

            def all_book_ids(db_self):
                return list(db_self.books.keys())

            def all_field_for(db_self, field, book_ids, default):
                if field == 'identifiers':
                    return {b_id: db_self.books[b_id]['idents'] for b_id in book_ids if b_id in db_self.books}
                return default

            def format(db_self, book_id, fmt):
                if fmt == 'EPUB' and book_id in db_self.books:
                    return db_self.books[book_id]['epub']
                return None

            def get_metadata(db_self, book_id):
                b = db_self.books.get(book_id)
                if not b:
                    return None
                mi = MagicMock()
                mi.title = b['title']
                mi.authors = b['authors']
                mi.series = None
                mi.series_index = None
                mi.pubdate = None
                return mi

        class DummyGUI:
            def __init__(gui_self, db):
                gui_self.current_db = MagicMock()
                gui_self.current_db.new_api = db

        gui = DummyGUI(DummyDB(self.epub1_bytes, self.epub2_bytes))
        found = find_nifty_books(gui)
        self.assertEqual(len(found), 2)

        groups = group_related_nifty_stories(found)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['title'], 'Saga')
        self.assertEqual(len(groups[0]['stories']), 2)

    def test_consolidate_nifty_omnibuses(self):
        class DummyDB:
            def __init__(db_self, epub1, epub2):
                db_self.books = {
                    1: {'title': 'Saga: Part 1', 'authors': ['Writer A'], 'idents': {'nifty': 'gay/college/saga-1'}, 'epub': epub1},
                    2: {'title': 'Saga: Part 2', 'authors': ['Writer A'], 'idents': {'nifty': 'gay/college/saga-2'}, 'epub': epub2},
                }
                db_self.deleted = []

            def all_book_ids(db_self):
                return [b_id for b_id in db_self.books if b_id not in db_self.deleted]

            def all_field_for(db_self, field, book_ids, default):
                if field == 'identifiers':
                    return {b_id: db_self.books[b_id]['idents'] for b_id in book_ids if b_id in db_self.books and b_id not in db_self.deleted}
                return default

            def format(db_self, book_id, fmt):
                if fmt == 'EPUB' and book_id in db_self.books and book_id not in db_self.deleted:
                    return db_self.books[book_id]['epub']
                return None

            def get_metadata(db_self, book_id):
                b = db_self.books.get(book_id)
                if not b or book_id in db_self.deleted:
                    return None
                mi = MagicMock()
                mi.title = b['title']
                mi.authors = b['authors']
                mi.series = None
                mi.series_index = None
                mi.pubdate = None
                return mi

            def add_books(db_self, books_list):
                added_ids = []
                for mi, fmt_map in books_list:
                    new_id = 99
                    db_self.books[new_id] = {'title': mi.title, 'authors': mi.authors, 'idents': {'nifty': 'gay/college/saga'}, 'epub': fmt_map['EPUB'].getvalue()}
                    added_ids.append(new_id)
                return added_ids, []

            def add_format(db_self, book_id, fmt, stream, replace=False):
                if book_id in db_self.books:
                    db_self.books[book_id]['epub'] = stream.getvalue()
                    return True
                return False

            def set_field(db_self, name, val_dict):
                pass

            def delete_books(db_self, ids):
                db_self.deleted.extend(ids)

        class DummyModel:
            def refresh(self):
                pass

            def refresh_ids(self, ids=None):
                pass

        class DummyView:
            def model(self):
                return DummyModel()

        class DummyTagsView:
            def recount(self):
                pass

        class DummyGUI:
            def __init__(gui_self, db):
                gui_self.db = db
                gui_self.current_db = MagicMock()
                gui_self.current_db.new_api = db
                gui_self.library_view = DummyView()
                gui_self.tags_view = DummyTagsView()

        gui = DummyGUI(DummyDB(self.epub1_bytes, self.epub2_bytes))
        added_ids, errors = consolidate_nifty_omnibuses(gui)
        self.assertEqual(errors, [])
        self.assertEqual(len(added_ids), 1)
        self.assertIn(1, gui.db.deleted)
        self.assertIn(2, gui.db.deleted)


if __name__ == '__main__':
    unittest.main()
