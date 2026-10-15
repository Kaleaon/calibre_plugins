import xml.dom.minidom
import zipfile
import unittest
from io import BytesIO
from unittest.mock import MagicMock

from common.storykit import build_epub, generate_cover_svg, extract_epub_story, wrap_text
from common.libkit import extract_epub_cover, import_stories, add_prebuilt, upsert_epub, metadata_for


class StorykitCoverTests(unittest.TestCase):

    def test_generate_cover_svg_basic(self):
        title = "A Short Story"
        author = "Jane Doe"
        publisher = "Metabods"
        site_meta = "https://metabods.com/mbxy/site/story.php?id=1"

        svg_str = generate_cover_svg(title, author, publisher=publisher, site_meta=site_meta)
        self.assertIn("A Short Story", svg_str)
        self.assertIn("Jane Doe", svg_str)
        self.assertIn("METABODS", svg_str)
        self.assertIn("metabods.com", svg_str)

        # XML minidom strict parsing validation
        dom = xml.dom.minidom.parseString(svg_str)
        self.assertEqual(dom.documentElement.tagName, "svg")

    def test_generate_cover_svg_xml_escaping(self):
        title = "Tom & Jerry: <The> \"Great\" 'Cat & Mouse' Chase"
        author = "Rock & Roll <Author>"
        publisher = "Nifty & Co"
        site_meta = "https://nifty.org/story?id=a&b=c"

        svg_str = generate_cover_svg(title, author, publisher=publisher, site_meta=site_meta)
        self.assertIn("Tom &amp; Jerry", svg_str)
        self.assertIn("&lt;The&gt;", svg_str)
        self.assertIn("&quot;Great&quot;", svg_str)
        self.assertIn("Rock &amp; Roll", svg_str)

        # Must be strict valid XML
        dom = xml.dom.minidom.parseString(svg_str)
        self.assertEqual(dom.documentElement.tagName, "svg")

    def test_dynamic_font_sizing_and_wrapping_for_long_titles(self):
        long_title = (
            "This is an Extremely Long Story Title Designed Specifically to Test the Dynamic "
            "Text Sizing and Line Wrapping System in the Standard Library SVG Cover Generator "
            "Without Clipping Any Text"
        )
        svg_str = generate_cover_svg(long_title, "Long Author Name", publisher="Test Publisher")
        dom = xml.dom.minidom.parseString(svg_str)
        self.assertEqual(dom.documentElement.tagName, "svg")

        # Check font size scaling down
        self.assertIn('font-size="', svg_str)
        # Check multiple tspans for wrapped lines
        self.assertGreater(svg_str.count("<tspan"), 3)

    def test_epub_packaging_cover_items(self):
        story = {
            'id': 'test-story-01',
            'title': 'The Epic Journey of Alex',
            'author': 'Alex Rivers',
            'summary': 'An epic journey across realms.',
            'url': 'https://metabods.com/story/1',
            'tags': ['Fantasy', 'Adventure'],
            'sections': [
                ('Chapter 1', '<p>The portal opened brightly.</p>'),
                ('Chapter 2', '<p>They stepped through the gateway.</p>')
            ]
        }

        epub_bytes = build_epub([story], publisher='Metabods')
        with zipfile.ZipFile(BytesIO(epub_bytes), 'r') as z:
            names = z.namelist()
            self.assertIn('OEBPS/cover.svg', names)
            self.assertIn('OEBPS/cover.xhtml', names)
            self.assertIn('OEBPS/content.opf', names)

            # Strict XML parsing of all XML/XHTML files in EPUB container
            for name in names:
                if name.endswith(('.xhtml', '.opf', '.ncx', '.xml', '.svg')):
                    content = z.read(name)
                    xml.dom.minidom.parseString(content)

            opf_text = z.read('OEBPS/content.opf').decode('utf-8')
            self.assertIn('<meta name="cover" content="cover-image"/>', opf_text)
            self.assertIn('<item id="cover-xhtml" href="cover.xhtml" media-type="application/xhtml+xml"/>', opf_text)
            self.assertIn('<item id="cover-image" href="cover.svg" media-type="image/svg+xml" properties="cover-image"/>', opf_text)
            self.assertIn('<itemref idref="cover-xhtml"/>', opf_text)
            self.assertIn('<guide><reference type="cover" title="Cover" href="cover.xhtml"/></guide>', opf_text)

            svg_text = z.read('OEBPS/cover.svg').decode('utf-8')
            self.assertIn('The Epic Journey of Alex', svg_text)
            self.assertIn('Alex Rivers', svg_text)
            self.assertIn('METABODS', svg_text)

    def test_extract_epub_story_ignores_cover_xhtml(self):
        story = {
            'id': 'story-cover-test',
            'title': 'Cover Test Story',
            'author': 'Author One',
            'sections': [('Part 1', '<p>First chapter text.</p>')]
        }
        epub_bytes = build_epub([story])
        extracted = extract_epub_story(epub_bytes)
        self.assertEqual(len(extracted['sections']), 1)
        self.assertEqual(extracted['sections'][0][0], 'Cover Test Story')
        self.assertIn('First chapter text.', extracted['sections'][0][1])

    def test_extract_epub_cover(self):
        story = {
            'id': 'story-cover-extract',
            'title': 'Extract Cover Story',
            'author': 'Author Two',
            'sections': [('Chapter 1', '<p>Content</p>')]
        }
        epub_bytes = build_epub([story], publisher='Metabods')
        cover_bytes = extract_epub_cover(epub_bytes)
        self.assertIsNotNone(cover_bytes)
        self.assertIn(b'<svg', cover_bytes.lower())

    def test_libkit_import_stories_syncs_cover_to_db(self):
        class DummyDB:
            def __init__(self):
                self.books = {}
                self.covers = {}

            def add_books(self, books_list):
                ids = []
                for mi, fmt_map in books_list:
                    b_id = len(self.books) + 1
                    self.books[b_id] = {'mi': mi, 'fmt': fmt_map}
                    ids.append(b_id)
                return ids, []

            def set_cover(self, mapping):
                self.covers.update(mapping)

        class DummyGUI:
            def __init__(self):
                self.db = DummyDB()
                self.current_db = MagicMock()
                self.current_db.new_api = self.db
                self.library_view = MagicMock()
                self.tags_view = MagicMock()

        gui = DummyGUI()
        stories = [{
            'id': 'm1',
            'title': 'Metabods Story',
            'author': 'Author M',
            'sections': [('Ch 1', '<p>Text</p>')]
        }]

        added, errors = import_stories(gui, stories, combine=False, title=None, build_epub=build_epub, identifier='metabods', publisher='Metabods')
        self.assertEqual(added, 1)
        self.assertEqual(errors, [])
        self.assertIn(1, gui.db.covers)
        self.assertIn(b'<svg', gui.db.covers[1].lower())

    def test_libkit_upsert_epub_syncs_cover_to_db(self):
        class DummyDB:
            def __init__(self):
                self.books = {}
                self.covers = {}

            def all_field_for(self, field, book_ids, default):
                return {}

            def all_book_ids(self):
                return list(self.books.keys())

            def add_books(self, books_list):
                ids = []
                for mi, fmt_map in books_list:
                    b_id = len(self.books) + 1
                    self.books[b_id] = {'mi': mi, 'fmt': fmt_map}
                    ids.append(b_id)
                return ids, []

            def set_cover(self, mapping):
                self.covers.update(mapping)

        class DummyGUI:
            def __init__(self):
                self.db = DummyDB()
                self.current_db = MagicMock()
                self.current_db.new_api = self.db
                self.library_view = MagicMock()
                self.tags_view = MagicMock()

        gui = DummyGUI()
        story = {
            'id': 'r1',
            'title': 'Reddit Story Title',
            'author': 'RedditAuthor',
            'sections': [('Post 1', '<p>Content</p>')]
        }
        epub_data = build_epub([story], publisher='Reddit')
        mi = metadata_for([story], 'Reddit Story Title', 'reddit', 'Reddit')

        b_id, created = upsert_epub(gui, mi, epub_data, 'reddit', 'r1')
        self.assertTrue(created)
        self.assertEqual(b_id, 1)
        self.assertIn(1, gui.db.covers)
        self.assertIn(b'<svg', gui.db.covers[1].lower())


if __name__ == '__main__':
    unittest.main()
