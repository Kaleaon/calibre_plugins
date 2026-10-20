import unittest
import zipfile
from io import BytesIO
from unittest.mock import MagicMock

from common.libkit import merge_library_books
from common.storykit import build_epub, extract_epub_story, merge_epubs


class EpubMergeTests(unittest.TestCase):
    def setUp(self):
        self.story1 = {
            "id": "story-001",
            "title": "The First Adventure",
            "author": "Alice Smith",
            "summary": "A thrilling tale of adventure.",
            "tags": ["Fantasy", "Adventure"],
            "url": "https://example.com/story1",
            "sections": [
                ("Chapter 1", "<p>Once upon a time in a far away land...</p>"),
                ("Chapter 2", "<p>The journey continued into the deep forest.</p>"),
            ],
        }
        self.story2 = {
            "id": "story-002",
            "title": "The Second Mystery",
            "author": "Bob Jones",
            "summary": "A mysterious detective case.",
            "tags": ["Mystery"],
            "url": "https://example.com/story2",
            "sections": [
                ("Part I", "<p>It was a dark and stormy night.</p>"),
                ("Part II", "<p>The detective found the missing key.</p>"),
            ],
        }
        self.epub1_bytes = build_epub([self.story1])
        self.epub2_bytes = build_epub([self.story2])

    def test_extract_epub_story(self):
        story = extract_epub_story(self.epub1_bytes)
        self.assertEqual(story["title"], "The First Adventure")
        self.assertEqual(story["author"], "Alice Smith")
        self.assertEqual(story["summary"], "A thrilling tale of adventure.")
        self.assertIn("Fantasy", story["tags"])
        self.assertEqual(len(story["sections"]), 2)
        self.assertEqual(story["sections"][0][0], "Chapter 1")
        self.assertIn("far away land", story["sections"][0][1])

    def test_merge_epubs(self):
        merged_bytes = merge_epubs([self.epub1_bytes, self.epub2_bytes])
        self.assertTrue(len(merged_bytes) > 0)

        with zipfile.ZipFile(BytesIO(merged_bytes), "r") as z:
            names = z.namelist()
            self.assertIn("OEBPS/content.opf", names)
            self.assertIn("OEBPS/nav.xhtml", names)
            self.assertIn("OEBPS/cover.svg", names)
            self.assertIn("OEBPS/cover.xhtml", names)

            nav_text = z.read("OEBPS/nav.xhtml").decode("utf-8")
            self.assertIn("The First Adventure", nav_text)
            self.assertIn("The Second Mystery", nav_text)

            opf_text = z.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn("The First Adventure and 1 more", opf_text)
            self.assertIn("Alice Smith &amp; Bob Jones", opf_text)
            self.assertIn('<meta name="cover" content="cover-image"/>', opf_text)
            self.assertIn('properties="cover-image"', opf_text)
            self.assertIn('<guide><reference type="cover" title="Cover" href="cover.xhtml"/></guide>', opf_text)

        extracted_omnibus = extract_epub_story(merged_bytes)
        self.assertEqual(len(extracted_omnibus["sections"]), 4)

    def test_merge_library_books(self):
        # Stub GUI and DB
        class DummyDB:
            def __init__(db_self):
                db_self.books = {
                    1: {"title": "Book One", "authors": ["Alice"], "epub": self.epub1_bytes, "tags": ["Fiction"]},
                    2: {"title": "Book Two", "authors": ["Bob"], "epub": self.epub2_bytes, "tags": ["Drama"]},
                }
                db_self.added = []

            def format(db_self, book_id, fmt):
                if fmt == "EPUB" and book_id in db_self.books:
                    return db_self.books[book_id]["epub"]
                return None

            def get_metadata(db_self, book_id):
                b = db_self.books.get(book_id)
                if not b:
                    return None
                mi = MagicMock()
                mi.title = b["title"]
                mi.authors = b["authors"]
                return mi

            def add_books(db_self, books_list):
                added_ids = []
                for mi, fmt_map in books_list:
                    new_id = len(db_self.books) + 10
                    db_self.books[new_id] = {
                        "title": mi.title,
                        "authors": mi.authors,
                        "epub": fmt_map["EPUB"].getvalue(),
                    }
                    added_ids.append(new_id)
                return added_ids, []

            def set_cover(db_self, mapping):
                db_self.covers_set = getattr(db_self, "covers_set", {})
                db_self.covers_set.update(mapping)

        class DummyModel:
            def refresh(self):
                pass

        class DummyView:
            def model(self):
                return DummyModel()

        class DummyTagsView:
            def recount(self):
                pass

        class DummyGUI:
            def __init__(gui_self):
                gui_self.db = DummyDB()
                gui_self.current_db = MagicMock()
                gui_self.current_db.new_api = gui_self.db
                gui_self.library_view = DummyView()
                gui_self.tags_view = DummyTagsView()

        gui = DummyGUI()
        new_id, errors = merge_library_books(gui, [1, 2], title="Combined Anthology")
        self.assertEqual(errors, [])
        self.assertIsNotNone(new_id)
        self.assertEqual(len(errors), 0)
        self.assertIn(new_id, gui.db.books)
        self.assertEqual(gui.db.books[new_id]["title"], "Combined Anthology")
        self.assertIn(new_id, gui.db.covers_set)
        self.assertIn(b"<svg", gui.db.covers_set[new_id].lower())


if __name__ == "__main__":
    unittest.main()
