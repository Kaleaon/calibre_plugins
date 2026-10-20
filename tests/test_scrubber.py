import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

spec = importlib.util.spec_from_file_location(
    "scrub_core", Path(__file__).resolve().parents[1] / "scrubber_plugin/core.py"
)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

XHTML = '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>%s</body></html>'
CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
OPF = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Book Title - OceanofPDF.com</dc:title><dc:publisher>OceanofPDF.com</dc:publisher><dc:creator>An Author</dc:creator></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="promo" href="promo.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="promo"/><itemref idref="c1"/></spine></package>"""
NAV = (
    XHTML
    % '<nav><ol><li><a href="promo.xhtml">Downloaded</a></li><li><a href="c1.xhtml">Chapter 1</a></li></ol></nav>'
)


def make(
    chapter,
    promo='<p>Downloaded from <a href="https://oceanofpdf.com">OceanofPDF.com</a></p>',
    opf=OPF,
):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/nav.xhtml", NAV)
        z.writestr("OEBPS/promo.xhtml", XHTML % promo)
        z.writestr("OEBPS/c1.xhtml", XHTML % chapter)
    return b.getvalue()


def read(data, name):
    return zipfile.ZipFile(io.BytesIO(data)).read(name).decode()


class ScrubTests(unittest.TestCase):
    def test_removes_promo_page_links_and_metadata(self):
        data, report = core.scrub_epub(
            make(
                "<p>Real story text.</p><p>"
                + "The hero walked on. " * 15
                + 'Then she saw <a href="http://www.oceanofpdf.com/x">the old tower</a> ahead.</p>'
            )
        )
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertNotIn("OEBPS/promo.xhtml", names)
        self.assertEqual(names[0], "mimetype")
        self.assertEqual(report["dropped_pages"], ["OEBPS/promo.xhtml"])
        chapter = read(data, "OEBPS/c1.xhtml")
        self.assertIn("Real story text.", chapter)
        self.assertNotIn("oceanofpdf", chapter.lower())
        self.assertIn("the old tower", chapter)
        opf = read(data, "OEBPS/content.opf")
        self.assertNotIn("oceanofpdf", opf.lower())
        self.assertNotIn("promo", opf)
        self.assertIn("<dc:title>Book Title</dc:title>", opf)
        self.assertNotIn("promo.xhtml", read(data, "OEBPS/nav.xhtml"))
        self.assertIn("Chapter 1", read(data, "OEBPS/nav.xhtml"))

    def test_clean_book_is_unchanged(self):
        clean = OPF.replace(" - OceanofPDF.com", "").replace(
            "OceanofPDF.com", "Some Press"
        )
        data, report = core.scrub_epub(
            make("<p>Nothing here.</p>", "<p>Cover page</p>", clean)
        )
        self.assertIsNone(data)
        self.assertEqual(report["changes"], [])

    def test_long_paragraph_keeps_surrounding_text(self):
        text = (
            "A long paragraph. " * 30
            + "Found at oceanofpdf.com okay. "
            + "More story. " * 5
        )
        data, _ = core.scrub_epub(make(f"<p>{text}</p>"))
        chapter = read(data, "OEBPS/c1.xhtml")
        self.assertIn("More story.", chapter)
        self.assertNotIn("oceanofpdf", chapter.lower())

    def test_generic_promo_block_and_toggle(self):
        chapter = "<p>Story.</p><p>Downloaded from www.freebooks-example.net</p>"
        data, _ = core.scrub_epub(make(chapter))
        self.assertNotIn("freebooks-example", read(data, "OEBPS/c1.xhtml"))
        data, _ = core.scrub_epub(make(chapter), heuristic=False)
        self.assertIn("freebooks-example", read(data, "OEBPS/c1.xhtml"))

    def test_custom_pattern_and_author_url_kept(self):
        chapter = '<p>See <a href="https://author.example/">the author site</a>.</p><p>Brought by BookBlaster!</p>'
        data, _ = core.scrub_epub(make(chapter), extra_patterns=["BookBlaster"])
        chapter = read(data, "OEBPS/c1.xhtml")
        self.assertIn("author.example", chapter)
        self.assertNotIn("BookBlaster", chapter)

    def test_not_epub_and_files_api(self):
        with self.assertRaises(ValueError):
            core.scrub_epub(b"not a zip")
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "in"
            src.mkdir()
            (src / "a.epub").write_bytes(make("<p>Hi</p>"))
            rows, report = core.scrub_files(src, Path(d) / "out")
            self.assertEqual(rows[0]["status"], "scrubbed")
            self.assertTrue(report.exists())
            self.assertIn(b"OceanofPDF", (src / "a.epub").read_bytes())

    def test_in_place_replaces_original_only_when_changed(self):
        with tempfile.TemporaryDirectory() as d:
            clean = OPF.replace(" - OceanofPDF.com", "").replace(
                "OceanofPDF.com", "Some Press"
            )
            (Path(d) / "dirty.epub").write_bytes(make("<p>Hi</p>"))
            untouched = make("<p>Hi</p>", "<p>Cover</p>", clean)
            (Path(d) / "clean.epub").write_bytes(untouched)
            rows, _ = core.scrub_files(d, in_place=True)
            self.assertEqual(
                {Path(r["input"]).name: r["status"] for r in rows},
                {"clean.epub": "unchanged", "dirty.epub": "replaced"},
            )
            self.assertNotIn(
                "oceanofpdf",
                read(
                    (Path(d) / "dirty.epub").read_bytes(), "OEBPS/content.opf"
                ).lower(),
            )
            self.assertEqual((Path(d) / "clean.epub").read_bytes(), untouched)
            self.assertFalse(list(Path(d).glob("*.scrub-tmp")))
            with self.assertRaises(ValueError):
                core.scrub_files(d)


class ScrubActionTests(unittest.TestCase):
    def setUp(self):
        import types

        self.saved_modules = {
            k: v
            for k, v in sys.modules.items()
            if k.startswith(("calibre", "qt", "calibre_plugins"))
        }
        for name in [
            "calibre",
            "calibre.gui2",
            "calibre.gui2.actions",
            "calibre.utils",
            "calibre.utils.config",
            "qt",
            "qt.core",
        ]:
            if name not in sys.modules:
                sys.modules[name] = MagicMock()

        class FakePrefs(dict):
            def __init__(self):
                self.defaults = {}

            def __getitem__(self, key):
                return self.get(key, self.defaults.get(key))

        sys.modules["calibre.utils.config"].JSONConfig = lambda name: FakePrefs()
        sys.modules["calibre.gui2.actions"].InterfaceAction = object

        root_dir = Path(__file__).resolve().parents[1]
        calibre_plugins = types.ModuleType("calibre_plugins")
        calibre_plugins.__path__ = []
        sys.modules["calibre_plugins"] = calibre_plugins

        scrubber_pkg = types.ModuleType("calibre_plugins.epub_promo_scrubber")
        scrubber_pkg.__path__ = [
            str(root_dir / "scrubber_plugin"),
            str(root_dir / "common"),
        ]
        sys.modules["calibre_plugins.epub_promo_scrubber"] = scrubber_pkg

    def tearDown(self):
        for k in list(sys.modules):
            if k.startswith(("calibre", "qt")) and k not in self.saved_modules:
                del sys.modules[k]

    def test_scrub_selected_runs_in_background_and_reports_progress(self):
        act_spec = importlib.util.spec_from_file_location(
            "scrub_action",
            Path(__file__).resolve().parents[1] / "scrubber_plugin/action.py",
        )
        action_mod = importlib.util.module_from_spec(act_spec)
        act_spec.loader.exec_module(action_mod)

        gui = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1, 2, 3]

        db = MagicMock()
        gui.current_db.new_api = db

        clean_opf = OPF.replace(" - OceanofPDF.com", "").replace(
            "OceanofPDF.com", "Some Press"
        )
        dirty_epub = make("<p>Dirty content</p>")
        clean_epub = make("<p>Clean content</p>", "<p>Cover</p>", clean_opf)

        def fake_format(book_id, fmt):
            if book_id in (1, 2):
                return dirty_epub
            return clean_epub

        db.format.side_effect = fake_format
        db.formats.return_value = ["EPUB"]
        db.add_format.return_value = True
        db.get_metadata.return_value = MagicMock(
            title="Title", publisher="Pub", comments="Comm"
        )

        info_dialog_mock = MagicMock()
        action_mod.info_dialog = info_dialog_mock

        action = action_mod.ScrubAction()
        action.gui = gui

        statuses = []

        def mock_run_task(parent, title, work):
            class FakeTask:
                def __init__(self):
                    self._status = ""
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
        action_mod.prefs["show_preview"] = False

        action.scrub_selected()

        gui.library_view.model().refresh.assert_called_once()
        gui.tags_view.recount.assert_called_once()

        self.assertIn("Scrubbing book 1 of 3…", statuses)
        self.assertIn("Scrubbing book 2 of 3…", statuses)
        self.assertIn("Scrubbing book 3 of 3…", statuses)

        info_dialog_mock.assert_called_once()
        msg = info_dialog_mock.call_args[0][2]
        self.assertIn("2 EPUB(s) scrubbed; 1 had nothing to remove; 0 error(s).", msg)

    def test_scrub_selected_cancellation(self):
        act_spec = importlib.util.spec_from_file_location(
            "scrub_action",
            Path(__file__).resolve().parents[1] / "scrubber_plugin/action.py",
        )
        action_mod = importlib.util.module_from_spec(act_spec)
        act_spec.loader.exec_module(action_mod)

        gui = MagicMock()
        gui.library_view.get_selected_ids.return_value = [1, 2, 3, 4, 5]

        db = MagicMock()
        gui.current_db.new_api = db
        db.format.return_value = make("<p>Dirty content</p>")
        db.formats.return_value = ["EPUB"]
        db.add_format.return_value = True
        db.get_metadata.return_value = MagicMock(
            title="Title", publisher="Pub", comments="Comm"
        )

        info_dialog_mock = MagicMock()
        action_mod.info_dialog = info_dialog_mock

        action = action_mod.ScrubAction()
        action.gui = gui

        processed_count = 0

        def mock_run_task_cancel_after_2(parent, title, work):
            class FakeTask:
                def __init__(self):
                    self._status = ""
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
        action_mod.prefs["show_preview"] = False

        action.scrub_selected()

        self.assertEqual(processed_count, 2)
        gui.library_view.model().refresh.assert_called_once()
        gui.tags_view.recount.assert_called_once()

        info_dialog_mock.assert_called_once()
        msg = info_dialog_mock.call_args[0][2]
        self.assertIn("Operation cancelled.", msg)


if __name__ == "__main__":
    unittest.main()
