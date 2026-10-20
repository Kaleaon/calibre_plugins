import importlib.util
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "core", Path(__file__).resolve().parents[1] / "plugin/core.py"
)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def epub():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", "<container/>")
    return b.getvalue()


class RecoveryTests(unittest.TestCase):
    def test_misnamed_epub_and_repeatability(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "input"
            source.mkdir()
            output = Path(d) / "output"
            original = source / "wrong.pdf"
            original.write_bytes(epub())
            files, rows, _ = core.recover(source, output)
            self.assertEqual(rows[0]["format"], "epub")
            self.assertEqual(Path(files[0]).read_bytes(), epub())
            self.assertEqual(original.read_bytes(), epub())
            again, rows, _ = core.recover(source, output)
            self.assertEqual(files, again)
            self.assertEqual(rows[0]["status"], "already recovered")

    def test_individual_file_and_sidecar_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "wrong.pdf"
            source.write_bytes(epub())
            files, rows, _ = core.recover(source, Path(d) / "out")
            self.assertEqual(rows[0]["format"], "epub")
            folder = Path(d) / "input"
            folder.mkdir()
            (folder / "book.pdf").write_bytes(epub())
            (folder / "book.json").write_text(
                '{"volumeInfo": {"title": "Recovered title", "authors": ["Author"]}}'
            )
            _, rows, _ = core.recover(folder, Path(d) / "out2")
            book = next(r for r in rows if "output" in r)
            self.assertEqual(book["metadata"]["title"], "Recovered title")

    def test_archive_paths_and_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "takeout.zip"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("../../evil.pdf", epub())
                z.writestr("metadata.json", "{}")
            files, rows, _ = core.recover(source, Path(d) / "output")
            self.assertEqual(len(files), 1)
            self.assertEqual(Path(files[0]).parent, Path(d) / "output")
            self.assertEqual(rows[1]["status"], "skipped")
            self.assertFalse((Path(d) / "evil.pdf").exists())

    def test_text_pdf_preserved_and_image_pdf_converted(self):
        try:
            import pymupdf as fitz
        except ImportError:
            try:
                import fitz
            except ImportError:
                self.skipTest("PyMuPDF is not installed (pip install pymupdf)")
        with fitz.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "Readable book text")
            self.assertIsNone(core.image_pdf_to_cbz(doc.tobytes()))
        with fitz.open() as doc:
            page = doc.new_page(width=200, height=200)
            page.draw_rect(fitz.Rect(20, 20, 180, 180), color=(1, 0, 0), fill=(0, 1, 0))
            page = doc.new_page(width=200, height=200)
            page.draw_circle(fitz.Point(100, 100), 50)
            data = core.image_pdf_to_cbz(doc.tobytes())
            self.assertEqual(core.detect(data), "cbz")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                self.assertEqual(z.namelist(), ["00001.png", "00002.png"])
                self.assertTrue(z.read("00001.png").startswith(b"\x89PNG"))

    def test_archive_size_can_exceed_individual_book_limit(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "takeout.zip"
            with zipfile.ZipFile(source, "w") as z:
                for i in range(10):
                    z.writestr(f"book{i}.pdf", epub())
            old = core.MAX_MEMBER
            try:
                core.MAX_MEMBER = 1000
                self.assertGreater(source.stat().st_size, core.MAX_MEMBER)
                files, rows, _ = core.recover(source, Path(d) / "out")
                self.assertEqual(len(files), 10)
                self.assertTrue(all(r["format"] == "epub" for r in rows))
            finally:
                core.MAX_MEMBER = old

    def test_unknown_and_archive_limits(self):
        self.assertIsNone(core.detect(b"not a book"))
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "input.zip"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("large.pdf", b"x" * 100)
            old = core.MAX_MEMBER
            try:
                core.MAX_MEMBER = 50
                files, rows, report = core.recover(source, Path(d) / "out")
                self.assertFalse(files)
                self.assertEqual(rows[0]["status"], "error")
                self.assertTrue(Path(report).exists())
            finally:
                core.MAX_MEMBER = old


if __name__ == "__main__":
    unittest.main()
