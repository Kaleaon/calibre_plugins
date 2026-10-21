import importlib.util
import io
import sys
import tempfile
import unittest
from unittest.mock import MagicMock
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('scrub_core', Path(__file__).resolve().parents[1] / 'scrubber_plugin/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

XHTML = '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>%s</body></html>'
CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
OPF = '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Book Title - OceanofPDF.com</dc:title><dc:publisher>OceanofPDF.com</dc:publisher><dc:creator>An Author</dc:creator></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="promo" href="promo.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="promo"/><itemref idref="c1"/></spine></package>'''
NAV = XHTML % '<nav><ol><li><a href="promo.xhtml">Downloaded</a></li><li><a href="c1.xhtml">Chapter 1</a></li></ol></nav>'


def make(chapter, promo='<p>Downloaded from <a href="https://oceanofpdf.com">OceanofPDF.com</a></p>', opf=OPF):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', CONTAINER)
        z.writestr('OEBPS/content.opf', opf)
        z.writestr('OEBPS/nav.xhtml', NAV)
        z.writestr('OEBPS/promo.xhtml', XHTML % promo)
        z.writestr('OEBPS/c1.xhtml', XHTML % chapter)
    return b.getvalue()


def read(data, name):
    return zipfile.ZipFile(io.BytesIO(data)).read(name).decode()


class ScrubTests(unittest.TestCase):
    def test_removes_promo_page_links_and_metadata(self):
        data, report = core.scrub_epub(make('<p>Real story text.</p><p>' + 'The hero walked on. ' * 15 + 'Then she saw <a href="http://www.oceanofpdf.com/x">the old tower</a> ahead.</p>'))
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertNotIn('OEBPS/promo.xhtml', names)
        self.assertEqual(names[0], 'mimetype')
        self.assertEqual(report['dropped_pages'], ['OEBPS/promo.xhtml'])
        chapter = read(data, 'OEBPS/c1.xhtml')
        self.assertIn('Real story text.', chapter)
        self.assertNotIn('oceanofpdf', chapter.lower())
        self.assertIn('the old tower', chapter)
        opf = read(data, 'OEBPS/content.opf')
        self.assertNotIn('oceanofpdf', opf.lower())
        self.assertNotIn('promo', opf)
        self.assertIn('<dc:title>Book Title</dc:title>', opf)
        self.assertNotIn('promo.xhtml', read(data, 'OEBPS/nav.xhtml'))
        self.assertIn('Chapter 1', read(data, 'OEBPS/nav.xhtml'))

    def test_clean_book_is_unchanged(self):
        clean = OPF.replace(' - OceanofPDF.com', '').replace('OceanofPDF.com', 'Some Press')
        data, report = core.scrub_epub(make('<p>Nothing here.</p>', '<p>Cover page</p>', clean))
        self.assertIsNone(data)
        self.assertEqual(report['changes'], [])

    def test_long_paragraph_keeps_surrounding_text(self):
        text = 'A long paragraph. ' * 30 + 'Found at oceanofpdf.com okay. ' + 'More story. ' * 5
        data, _ = core.scrub_epub(make('<p>%s</p>' % text))
        chapter = read(data, 'OEBPS/c1.xhtml')
        self.assertIn('More story.', chapter)
        self.assertNotIn('oceanofpdf', chapter.lower())

    def test_generic_promo_block_and_toggle(self):
        chapter = '<p>Story.</p><p>Downloaded from www.freebooks-example.net</p>'
        data, _ = core.scrub_epub(make(chapter))
        self.assertNotIn('freebooks-example', read(data, 'OEBPS/c1.xhtml'))
        data, _ = core.scrub_epub(make(chapter), heuristic=False)
        self.assertIn('freebooks-example', read(data, 'OEBPS/c1.xhtml'))

    def test_custom_pattern_and_author_url_kept(self):
        chapter = '<p>See <a href="https://author.example/">the author site</a>.</p><p>Brought by BookBlaster!</p>'
        data, _ = core.scrub_epub(make(chapter), extra_patterns=['BookBlaster'])
        chapter = read(data, 'OEBPS/c1.xhtml')
        self.assertIn('author.example', chapter)
        self.assertNotIn('BookBlaster', chapter)

    def test_not_epub_and_files_api(self):
        with self.assertRaises(ValueError):
            core.scrub_epub(b'not a zip')
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'in'; src.mkdir()
            (src / 'a.epub').write_bytes(make('<p>Hi</p>'))
            rows, report = core.scrub_files(src, Path(d) / 'out')
            self.assertEqual(rows[0]['status'], 'scrubbed')
            self.assertTrue(report.exists())
            self.assertIn(b'OceanofPDF', (src / 'a.epub').read_bytes())

    def test_in_place_replaces_original_only_when_changed(self):
        with tempfile.TemporaryDirectory() as d:
            clean = OPF.replace(' - OceanofPDF.com', '').replace('OceanofPDF.com', 'Some Press')
            (Path(d) / 'dirty.epub').write_bytes(make('<p>Hi</p>'))
            untouched = make('<p>Hi</p>', '<p>Cover</p>', clean)
            (Path(d) / 'clean.epub').write_bytes(untouched)
            rows, _ = core.scrub_files(d, in_place=True)
            self.assertEqual({Path(r['input']).name: r['status'] for r in rows}, {'clean.epub': 'unchanged', 'dirty.epub': 'replaced'})
            self.assertNotIn('oceanofpdf', read((Path(d) / 'dirty.epub').read_bytes(), 'OEBPS/content.opf').lower())
            self.assertEqual((Path(d) / 'clean.epub').read_bytes(), untouched)
            self.assertFalse(list(Path(d).glob('*.scrub-tmp')))
            with self.assertRaises(ValueError):
                core.scrub_files(d)


class ScrubActionTests(unittest.TestCase):
    def setUp(self):
        import types
        self.saved_modules = {k: v for k, v in sys.modules.items() if k.startswith(('calibre', 'qt', 'calibre_plugins'))}
        for name in ['calibre', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config', 'qt', 'qt.core']:
            if name not in sys.modules:
                sys.modules[name] = MagicMock()

        class FakePrefs(dict):
            def __init__(self):
                self.defaults = {}
            def __getitem__(self, key):
                return self.get(key, self.defaults.get(key))

        sys.modules['calibre.utils.config'].JSONConfig = lambda name: FakePrefs()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object

        root_dir = Path(__file__).resolve().parents[1]
        calibre_plugins = types.ModuleType('calibre_plugins')
        calibre_plugins.__path__ = []
        sys.modules['calibre_plugins'] = calibre_plugins

        scrubber_pkg = types.ModuleType('calibre_plugins.epub_promo_scrubber')
        scrubber_pkg.__path__ = [str(root_dir / 'scrubber_plugin'), str(root_dir / 'common')]
        sys.modules['calibre_plugins.epub_promo_scrubber'] = scrubber_pkg

    def tearDown(self):
        for k in list(sys.modules):
            if k.startswith(('calibre', 'qt')) and k not in self.saved_modules:
                del sys.modules[k]

    def test_scrub_selected_runs_in_background_and_reports_progress(self):
        act_spec = importlib.util.spec_from_file_location('scrub_action', Path(__file__).resolve().parents[1] / 'scrubber_plugin/action.py')
        action_mod = importlib.util.module_from_spec(act_spec)
        act_spec.loader.exec_module(action_mod)

        gui = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1, 2, 3]

        db = MagicMock()
        gui.current_db.new_api = db

        clean_opf = OPF.replace(' - OceanofPDF.com', '').replace('OceanofPDF.com', 'Some Press')
        dirty_epub = make('<p>Dirty content</p>')
        clean_epub = make('<p>Clean content</p>', '<p>Cover</p>', clean_opf)

        def fake_format(book_id, fmt):
            if book_id in (1, 2):
                return dirty_epub
            return clean_epub

        db.format.side_effect = fake_format
        db.formats.return_value = ['EPUB']
        db.add_format.return_value = True
        db.get_metadata.return_value = MagicMock(title='Title', publisher='Pub', comments='Comm')

        info_dialog_mock = MagicMock()
        action_mod.info_dialog = info_dialog_mock

        action = action_mod.ScrubAction()
        action.gui = gui

        statuses = []

        def mock_run_task(parent, title, work):
            class FakeTask:
                def __init__(self):
                    self._status = ''
                    self.error = None
                @property
                def status(self):
                    return self._status
                @status.setter
                def status(self, val):
                    self._status = val
                    statuses.append(val)
                def cancelled(self):
                    return False

            task = FakeTask()
            work(task)
            return task

        action_mod.run_task = mock_run_task
        action_mod.prefs['show_preview'] = False

        action.scrub_selected()

        gui.library_view.model().refresh.assert_called_once()
        gui.tags_view.recount.assert_called_once()

        self.assertIn('Scrubbing book 1 of 3…', statuses)
        self.assertIn('Scrubbing book 2 of 3…', statuses)
        self.assertIn('Scrubbing book 3 of 3…', statuses)

        info_dialog_mock.assert_called_once()
        msg = info_dialog_mock.call_args[0][2]
        self.assertIn('2 EPUB(s) scrubbed; 1 had nothing to remove; 0 error(s).', msg)

    def test_scrub_selected_cancellation(self):
        act_spec = importlib.util.spec_from_file_location('scrub_action', Path(__file__).resolve().parents[1] / 'scrubber_plugin/action.py')
        action_mod = importlib.util.module_from_spec(act_spec)
        act_spec.loader.exec_module(action_mod)

        gui = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1, 2, 3, 4, 5]

        db = MagicMock()
        gui.current_db.new_api = db
        db.format.return_value = make('<p>Dirty content</p>')
        db.formats.return_value = ['EPUB']
        db.add_format.return_value = True
        db.get_metadata.return_value = MagicMock(title='Title', publisher='Pub', comments='Comm')

        info_dialog_mock = MagicMock()
        action_mod.info_dialog = info_dialog_mock

        action = action_mod.ScrubAction()
        action.gui = gui

        processed_count = 0

        def mock_run_task_cancel_after_2(parent, title, work):
            class FakeTask:
                def __init__(self):
                    self._status = ''
                    self.processed = 0
                    self.error = None
                @property
                def status(self):
                    return self._status
                @status.setter
                def status(self, val):
                    self._status = val
                    self.processed += 1
                def cancelled(self):
                    return self.processed >= 2

            task = FakeTask()
            work(task)
            nonlocal processed_count
            processed_count = task.processed
            return task

        action_mod.run_task = mock_run_task_cancel_after_2
        action_mod.prefs['show_preview'] = False

        action.scrub_selected()

        self.assertEqual(processed_count, 2)
        gui.library_view.model().refresh.assert_called_once()
        gui.tags_view.recount.assert_called_once()

        info_dialog_mock.assert_called_once()
        msg = info_dialog_mock.call_args[0][2]
        self.assertIn('Operation cancelled.', msg)





# -------------------------------------------------------------------------------------------------
# Image Classification & Library Scanner Tests
# -------------------------------------------------------------------------------------------------

import struct
from scrubber_plugin.image_classifier import (
    classify_image_context,
    get_image_dimensions,
    get_image_info,
)
from scrubber_plugin.scanner import LibraryScanner


class ImageClassifierTests(unittest.TestCase):
    def test_binary_header_parsing(self):
        # 1. PNG 400x200
        png_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 400, 200) + b'\x08\x02\x00\x00\x00'
        w, h, fmt = get_image_dimensions(png_data, 'test.png')
        self.assertEqual((w, h, fmt), (400, 200, 'png'))

        # 2. GIF 320x50
        gif_data = b'GIF89a' + struct.pack('<HH', 320, 50) + b'\x80\x00\x00'
        w, h, fmt = get_image_dimensions(gif_data, 'banner.gif')
        self.assertEqual((w, h, fmt), (320, 50, 'gif'))

        # 3. SVG with width and height
        svg_data = b'<svg xmlns="http://www.w3.org/2000/svg" width="640px" height="480px"><rect/></svg>'
        w, h, fmt = get_image_dimensions(svg_data, 'vector.svg')
        self.assertEqual((w, h, fmt), (640, 480, 'svg'))

    def test_classify_ad_watermark_and_banners(self):
        # OceanofPDF banner
        res = classify_image_context(None, 'oceanofpdf_banner.png', {})
        self.assertTrue(res['is_promo'])
        self.assertEqual(res['category'], 'ad')
        self.assertIn('oceanofpdf', res['reason'].lower())

        # Ad banner with 728x90 aspect ratio
        gif_banner = b'GIF89a' + struct.pack('<HH', 728, 90) + b'\x80\x00\x00'
        res = classify_image_context(gif_banner, 'web_banner.gif', {'frequency': 1})
        self.assertTrue(res['is_promo'])
        self.assertEqual(res['category'], 'ad')

        # Image linking to external Patreon / Discord
        res = classify_image_context(None, 'badge.png', {
            'is_linked': True,
            'link_href': 'https://patreon.com/mybooks'
        })
        self.assertTrue(res['is_promo'])
        self.assertEqual(res['category'], 'ad')
        self.assertTrue('patreon' in res['reason'].lower() or 'crowdfunding' in res['reason'].lower())

    def test_classify_publisher_logo_safe(self):
        # Publisher logo on title page with publisher keyword
        logo_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 150, 80) + b'\x08\x02\x00\x00\x00'
        res = classify_image_context(logo_png, 'publisher_colophon.png', {
            'page_file': 'titlepage.xhtml',
            'page_role': 'titlepage',
            'surrounding_text': 'Published by Penguin Random House LLC, New York. All rights reserved.',
            'spine_index': 1,
            'total_spine': 10
        })
        self.assertFalse(res['is_promo'])
        self.assertEqual(res['category'], 'logo')
        self.assertEqual(res['suggested_action'], 'keep')

    def test_classify_ornament_and_illustration_safe(self):
        # Repeated chapter divider
        res = classify_image_context(None, 'divider.png', {'frequency': 8})
        self.assertFalse(res['is_promo'])
        self.assertEqual(res['category'], 'ornament')

        # Narrative illustration inside chapter story
        story_text = "The traveler looked across the misty valley toward the ancient stone towers. " * 10
        res = classify_image_context(None, 'illustration_castle.jpg', {
            'page_file': 'chapter02.xhtml',
            'page_role': 'chapter',
            'surrounding_text': story_text
        })
        self.assertFalse(res['is_promo'])
        self.assertEqual(res['category'], 'illustration')

    def test_classify_cover_safe(self):
        # Cover image marked in manifest
        res = classify_image_context(None, 'book_cover.jpg', {
            'is_cover_manifest': True,
            'page_file': 'cover.xhtml',
            'page_role': 'cover'
        })
        self.assertFalse(res['is_promo'])
        self.assertEqual(res['category'], 'cover')


class ScrubImageTests(unittest.TestCase):
    def test_scrub_epub_removes_ad_image_and_purges_orphaned_file(self):
        png_banner = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 728, 90) + b'\x08\x02\x00\x00\x00'
        png_logo = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 120, 60) + b'\x08\x02\x00\x00\x00'
        png_divider = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 100, 10) + b'\x08\x02\x00\x00\x00'
        jpg_cover = b'\xff\xd8\xff\xe0\x00\x10JFIF' + b'\x00' * 20

        opf_with_images = '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Test Book</dc:title><meta name="cover" content="cov"/></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
<item id="cov" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>
<item id="logo" href="images/publisher_logo.png" media-type="image/png"/>
<item id="div" href="images/divider.png" media-type="image/png"/>
<item id="ad" href="images/oceanofpdf_banner.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="titlepage"/><itemref idref="c1"/></spine></package>'''

        titlepage_html = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<h1>My Masterpiece</h1>'
            '<p><img src="images/publisher_logo.png" alt="Publisher Logo"/></p>'
            '<p>Published by Great Press. All rights reserved.</p>'
            '</body></html>'
        )

        chapter_html = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<h2>Chapter 1</h2>'
            '<p><img src="images/divider.png" alt="Section divider"/></p>'
            '<p>' + 'The adventure began in the early morning fog. ' * 10 + '</p>'
            '<p><a href="https://oceanofpdf.com"><img src="images/oceanofpdf_banner.png" alt="Download more free books at OceanofPDF"/></a></p>'
            '</body></html>'
        )

        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
            z.writestr('META-INF/container.xml', CONTAINER)
            z.writestr('OEBPS/content.opf', opf_with_images)
            z.writestr('OEBPS/nav.xhtml', NAV)
            z.writestr('OEBPS/titlepage.xhtml', titlepage_html)
            z.writestr('OEBPS/c1.xhtml', chapter_html)
            z.writestr('OEBPS/images/cover.jpg', jpg_cover)
            z.writestr('OEBPS/images/publisher_logo.png', png_logo)
            z.writestr('OEBPS/images/divider.png', png_divider)
            z.writestr('OEBPS/images/oceanofpdf_banner.png', png_banner)

        epub_bytes = b.getvalue()
        cleaned_bytes, report = core.scrub_epub(epub_bytes)

        self.assertIsNotNone(cleaned_bytes)
        with zipfile.ZipFile(io.BytesIO(cleaned_bytes)) as z_out:
            out_names = z_out.namelist()
            # 1. Orphaned ad image file is purged
            self.assertNotIn('OEBPS/images/oceanofpdf_banner.png', out_names)
            # 2. Cover, logo, and divider files are preserved!
            self.assertIn('OEBPS/images/cover.jpg', out_names)
            self.assertIn('OEBPS/images/publisher_logo.png', out_names)
            self.assertIn('OEBPS/images/divider.png', out_names)

            # 3. Chapter 1 no longer has the ad banner
            c1_cleaned = z_out.read('OEBPS/c1.xhtml').decode('utf-8')
            self.assertNotIn('oceanofpdf_banner.png', c1_cleaned)
            self.assertIn('divider.png', c1_cleaned)

            # 4. Titlepage still has publisher logo
            tp_cleaned = z_out.read('OEBPS/titlepage.xhtml').decode('utf-8')
            self.assertIn('publisher_logo.png', tp_cleaned)

            # 5. Manifest in content.opf dropped item id="ad"
            opf_cleaned = z_out.read('OEBPS/content.opf').decode('utf-8')
            self.assertNotIn('oceanofpdf_banner.png', opf_cleaned)
            self.assertIn('publisher_logo.png', opf_cleaned)

        # Image stats report
        stats = report.get('image_stats', {})
        self.assertGreaterEqual(stats.get('ads', 0), 1)
        self.assertGreaterEqual(stats.get('logos', 0), 1)
        self.assertGreaterEqual(stats.get('ornaments', 0), 1)

    def test_scrub_epub_drops_page_when_ad_image_removed(self):
        png_ad = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 400, 300) + b'\x08\x02\x00\x00\x00'

        opf = '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Test Book</dc:title></metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="adpage" href="adpage.xhtml" media-type="application/xhtml+xml"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
<item id="adimg" href="images/oceanofpdf.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="adpage"/><itemref idref="c1"/></spine></package>'''

        ad_html = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<p><img src="images/oceanofpdf.png" alt="oceanofpdf ad"/></p>'
            '</body></html>'
        )

        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
            z.writestr('META-INF/container.xml', CONTAINER)
            z.writestr('OEBPS/content.opf', opf)
            z.writestr('OEBPS/nav.xhtml', NAV)
            z.writestr('OEBPS/adpage.xhtml', ad_html)
            z.writestr('OEBPS/c1.xhtml', XHTML % '<p>Story content</p>')
            z.writestr('OEBPS/images/oceanofpdf.png', png_ad)

        cleaned_bytes, report = core.scrub_epub(b.getvalue(), drop_empty_pages=True)
        self.assertIsNotNone(cleaned_bytes)
        with zipfile.ZipFile(io.BytesIO(cleaned_bytes)) as z_out:
            # Standalone ad page is dropped!
            self.assertNotIn('OEBPS/adpage.xhtml', z_out.namelist())
            self.assertNotIn('OEBPS/images/oceanofpdf.png', z_out.namelist())
            self.assertIn('OEBPS/adpage.xhtml', report.get('dropped_pages', []))


class LibraryScannerTests(unittest.TestCase):
    def test_scanner_discovers_and_cleans_promos(self):
        png_banner = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' + struct.pack('>II', 728, 90) + b'\x08\x02\x00\x00\x00'
        dirty_epub = make(
            chapter='<p>Story chapter 1</p><p><img src="images/oceanofpdf_banner.png" alt="oceanofpdf"/></p>',
            promo='<p>Downloaded from OceanofPDF.com</p>'
        )

        # Append image to dirty epub
        b = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(dirty_epub)) as z_in:
            with zipfile.ZipFile(b, 'w') as z_out:
                for item in z_in.infolist():
                    z_out.writestr(item, z_in.read(item.filename))
                z_out.writestr('OEBPS/images/oceanofpdf_banner.png', png_banner)
        dirty_epub_with_img = b.getvalue()

        clean_epub = make(
            chapter='<p>' + 'A completely clean story with nothing promotional. ' * 10 + '</p>',
            promo='<p>Preface: Welcome to the book.</p>',
            opf='''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Clean Book</dc:title><dc:publisher>Good Publisher</dc:publisher></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="c1"/></spine></package>'''
        )

        # Mock Calibre db
        db = MagicMock()
        db.all_book_ids.return_value = [101, 102]

        def mock_format(book_id, fmt):
            if fmt == 'EPUB':
                return clean_epub if book_id == 101 else dirty_epub_with_img
            return None

        def mock_metadata(book_id):
            if book_id == 101:
                return MagicMock(title='Clean Book', publisher='Good Publisher', comments=None, authors=['Author One'])
            return MagicMock(title='Dirty Book - OceanofPDF.com', publisher='OceanofPDF.com', comments='Visit OceanofPDF', authors=['Author Two'])

        db.format.side_effect = mock_format
        db.get_metadata.side_effect = mock_metadata
        db.formats.return_value = ['EPUB']
        db.add_format.return_value = True

        scanner = LibraryScanner(db)

        # Test scan_library
        scan_rep = scanner.scan_library()
        self.assertEqual(scan_rep['total_scanned'], 2)
        self.assertEqual(scan_rep['clean_count'], 1)
        self.assertEqual(scan_rep['flagged_count'], 1)

        flagged = scan_rep['books'][0]
        self.assertEqual(flagged['book_id'], 102)
        self.assertTrue(flagged['has_ads'])
        self.assertGreater(flagged['categories']['ads_and_banners'], 0)

        # Test get_image_data
        img_bytes = scanner.get_image_data(102, 'images/oceanofpdf_banner.png')
        self.assertIsNotNone(img_bytes)
        self.assertEqual(img_bytes, png_banner)

        # Test apply_clean
        clean_rep = scanner.apply_clean(scan_rep['books'])
        self.assertEqual(clean_rep['cleaned_count'], 1)
        self.assertIn(102, clean_rep['cleaned_book_ids'])
        db.add_format.assert_called()


class LibraryScannerDialogTests(unittest.TestCase):
    def test_dialog_instantiation_and_filtering(self):
        try:
            from scrubber_plugin.ui import LibraryScannerDialog
        except ImportError:
            return

        db = MagicMock()
        db.all_book_ids.return_value = [1, 2]
        db.get_metadata.return_value = MagicMock(title='Title', publisher='Pub', comments='', authors=['Author'])
        db.format.return_value = None

        dialog = LibraryScannerDialog(None, db, {}, selected_book_ids=[1])
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.rb_selected.isChecked())

        # Test select all / deselect all
        dialog.select_all()
        dialog.deselect_all()
        dialog.select_only_ads()
        dialog.select_only_pages()

if __name__ == '__main__':
    unittest.main()
