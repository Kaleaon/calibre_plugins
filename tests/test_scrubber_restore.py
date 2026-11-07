"""Unit and integration tests for EPUB Promo Scrubber Restore Manager,
pre-scrubbed OPF metadata recovery, and standalone folder sidecar backups.
"""

import io
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scrubber_plugin"))
sys.path.insert(0, str(ROOT / "common"))

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

# Setup qt.core shim
qtcore = types.ModuleType("qt.core")
for module in (QtWidgets, QtGui, QtCore):
    for name in dir(module):
        setattr(qtcore, name, getattr(module, name))
sys.modules["qt.core"] = qtcore
sys.modules["qt"] = types.ModuleType("qt")
sys.modules["qt"].core = qtcore

from scrubber_plugin.core import parse_opf_metadata, restore_files, scrub_files  # noqa: E402
from scrubber_plugin.ui import (  # noqa: E402
    RestoreApplyWorker,
    RestoreScanWorker,
    ScrubRestoreManagerDialog,
)

XHTML = '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>%s</body></html>'
CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
OPF_DIRTY = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Great Novel - OceanofPDF.com</dc:title>
<dc:publisher>OceanofPDF.com</dc:publisher>
<dc:creator>Jane Doe</dc:creator>
<dc:description>Downloaded from OceanofPDF.com. Best free books.</dc:description>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine><itemref idref="c1"/></spine>
</package>"""

OPF_ORIGINAL = """<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>The Original Great Novel</dc:title>
<dc:publisher>Acme Publishing House</dc:publisher>
<dc:creator>Jane Doe</dc:creator>
<dc:description>A gripping tale of exploration and mystery across distant lands.</dc:description>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine><itemref idref="c1"/></spine>
</package>"""

NAV = XHTML % '<nav><ol><li><a href="c1.xhtml">Chapter 1</a></li></ol></nav>'


def make_test_epub(chapter_text="<p>Story text.</p>", opf_xml=OPF_ORIGINAL):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", opf_xml)
        z.writestr("OEBPS/nav.xhtml", NAV)
        z.writestr("OEBPS/c1.xhtml", XHTML % chapter_text)
    return b.getvalue()


class SidecarAndFolderRestoreTests(unittest.TestCase):
    def test_in_place_creates_sidecar_before_overwriting_and_ignores_on_rescrub(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            orig_bytes = make_test_epub(
                "<p>Original unscrubbed content. Downloaded from OceanofPDF.com</p>",
                OPF_DIRTY,
            )
            epub_file = dir_path / "my_book.epub"
            epub_file.write_bytes(orig_bytes)

            clean_bytes = make_test_epub("<p>Clean content</p>", OPF_ORIGINAL)
            clean_file = dir_path / "clean_book.epub"
            clean_file.write_bytes(clean_bytes)

            # Scrub in place
            rows, report_path = scrub_files(dir_path, in_place=True)
            self.assertEqual(len(rows), 2)
            self.assertTrue(report_path.exists())

            # Check that dirty book was replaced and sidecar created
            sidecar_file = dir_path / "my_book.original.epub"
            self.assertTrue(sidecar_file.exists(), "Sidecar .original.epub must be created")
            self.assertEqual(
                sidecar_file.read_bytes(),
                orig_bytes,
                "Sidecar must preserve pre-scrubbed original bytes",
            )

            # Check that clean book was unchanged and has NO sidecar
            clean_sidecar = dir_path / "clean_book.original.epub"
            self.assertFalse(clean_sidecar.exists(), "Unchanged files should not produce sidecars")

            # Re-scrubbing folder should ignore sidecars and not scrub them
            rows2, _ = scrub_files(dir_path, in_place=True)
            inputs = [Path(r["input"]).name for r in rows2]
            self.assertNotIn(
                "my_book.original.epub",
                inputs,
                "Sidecars must be excluded from scrub input list",
            )

    def test_restore_files_replaces_primary_epub_and_cleans_sidecars_when_confirmed(
        self,
    ):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            orig_bytes = make_test_epub("<p>Original True Content</p>", OPF_ORIGINAL)
            scrubbed_bytes = make_test_epub("<p>Scrubbed modified content</p>", OPF_ORIGINAL)

            epub_file = dir_path / "story.epub"
            sidecar_file = dir_path / "story.original.epub"

            epub_file.write_bytes(scrubbed_bytes)
            sidecar_file.write_bytes(orig_bytes)

            # Restore with remove_sidecars=True
            rows, report_path = restore_files(dir_path, remove_sidecars=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "restored")
            self.assertTrue(report_path.exists())

            # Target epub is restored to original
            self.assertEqual(epub_file.read_bytes(), orig_bytes)
            # Sidecar is removed
            self.assertFalse(sidecar_file.exists())

    def test_restore_files_preserves_sidecars_when_remove_sidecars_is_false(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            orig_bytes = make_test_epub("<p>Original Story</p>", OPF_ORIGINAL)
            epub_file = dir_path / "novel.epub"
            sidecar_file = dir_path / "novel.original.epub"

            epub_file.write_bytes(b"corrupted or changed")
            sidecar_file.write_bytes(orig_bytes)

            rows, _ = restore_files(dir_path, remove_sidecars=False)
            self.assertEqual(rows[0]["status"], "restored")
            self.assertEqual(epub_file.read_bytes(), orig_bytes)
            self.assertTrue(
                sidecar_file.exists(),
                "Sidecar should remain when remove_sidecars is False",
            )

    def test_restore_files_verifies_zip_integrity_and_handles_corrupt_sidecar(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            epub_file = dir_path / "bad.epub"
            epub_file.write_bytes(b"safe current content")
            corrupt_sidecar = dir_path / "bad.original.epub"
            corrupt_sidecar.write_bytes(b"NOT A VALID ZIP FILE")

            rows, report_path = restore_files(dir_path, remove_sidecars=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "error")
            self.assertIn("not a valid ZIP", rows[0]["error"])
            # Primary file must NOT be destroyed by corrupt sidecar
            self.assertEqual(epub_file.read_bytes(), b"safe current content")
            self.assertFalse(list(dir_path.glob("*.restore-tmp")))


class OPFMetadataParserTests(unittest.TestCase):
    def test_parse_opf_metadata_extracts_title_publisher_comments(self):
        epub_data = make_test_epub("<p>Chapter content</p>", OPF_ORIGINAL)
        meta = parse_opf_metadata(epub_data)
        self.assertEqual(meta["title"], "The Original Great Novel")
        self.assertEqual(meta["publisher"], "Acme Publishing House")
        self.assertEqual(
            meta["comments"],
            "A gripping tale of exploration and mystery across distant lands.",
        )

    def test_parse_opf_metadata_handles_corrupted_data_gracefully(self):
        meta = parse_opf_metadata(b"garbage data")
        self.assertEqual(meta, {"title": None, "publisher": None, "comments": None})


class ScrubRestoreManagerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.orig_epub_data = make_test_epub("<p>Original Book 1</p>", OPF_ORIGINAL)
        self.curr_epub_data = make_test_epub("<p>Scrubbed Book 1</p>", OPF_DIRTY)

        # Mock Calibre db
        self.db = MagicMock()
        self.db.all_book_ids.return_value = [1, 2, 3]

        # Book 1 has ORIGINAL_EPUB and differs in title & publisher
        # Book 2 has ORIGINAL_EPUB and identical metadata
        # Book 3 has NO ORIGINAL_EPUB
        self.book_formats = {
            1: ["EPUB", "ORIGINAL_EPUB"],
            2: ["EPUB", "ORIGINAL_EPUB"],
            3: ["EPUB"],
        }

        self.db.formats.side_effect = lambda bid: self.book_formats.get(bid, [])

        def mock_format(bid, fmt):
            if fmt == "ORIGINAL_EPUB" and bid in (1, 2):
                return self.orig_epub_data
            if fmt == "EPUB":
                return self.curr_epub_data
            return None

        self.db.format.side_effect = mock_format

        self.metadata_store = {
            1: MagicMock(
                title="Great Novel - OceanofPDF.com",
                publisher="OceanofPDF.com",
                comments="Visit OceanofPDF for free books.",
                authors=["Jane Doe"],
            ),
            2: MagicMock(
                title="The Original Great Novel",
                publisher="Acme Publishing House",
                comments="A gripping tale of exploration and mystery across distant lands.",
                authors=["Jane Doe"],
            ),
            3: MagicMock(
                title="Book 3",
                publisher="Some Publisher",
                comments="",
                authors=["Author Three"],
            ),
        }
        self.db.get_metadata.side_effect = lambda bid: self.metadata_store.get(bid)

        self.db.add_format.return_value = True
        self.db.remove_formats.side_effect = lambda mapping: [
            self.book_formats[bid].remove("ORIGINAL_EPUB")
            for bid in mapping
            if "ORIGINAL_EPUB" in self.book_formats.get(bid, [])
        ]
        self.db.set_field.side_effect = lambda field, mapping: None

    def test_restore_scan_worker_detects_backups_and_metadata_differences(self):
        worker = RestoreScanWorker(self.db)
        found_items = []
        worker.book_found.connect(found_items.append)

        finished_summary = {}
        worker.scan_finished.connect(finished_summary.update)

        worker.run()

        self.assertEqual(len(found_items), 2, "Only books with ORIGINAL_EPUB should be scanned")
        self.assertEqual(finished_summary["total_backups"], 2)
        self.assertEqual(finished_summary["diff_count"], 1)

        b1 = next(item for item in found_items if item["book_id"] == 1)
        self.assertTrue(b1["has_diff"])
        self.assertTrue(b1["diff_title"])
        self.assertTrue(b1["diff_publisher"])
        self.assertTrue(b1["diff_comments"])
        self.assertEqual(b1["orig_title"], "The Original Great Novel")
        self.assertEqual(b1["orig_publisher"], "Acme Publishing House")

        b2 = next(item for item in found_items if item["book_id"] == 2)
        self.assertFalse(b2["has_diff"])

    def test_restore_apply_worker_replaces_format_and_reverts_metadata(self):
        items = [
            {
                "book_id": 1,
                "curr_title": "Great Novel - OceanofPDF.com",
                "orig_title": "The Original Great Novel",
                "raw_orig": self.orig_epub_data,
            }
        ]

        worker = RestoreApplyWorker(
            self.db,
            items,
            restore_epub=True,
            restore_metadata=True,
            fields_to_restore={"title", "publisher", "comments"},
            remove_backup=False,
        )

        results = {}
        worker.restore_finished.connect(results.update)
        worker.run()

        self.assertEqual(results["restored"], [1])
        self.assertEqual(results["errors"], [])

        # Format restored via db.add_format
        self.db.add_format.assert_called_with(1, "EPUB", unittest.mock.ANY, replace=True)

        # Metadata fields reverted via db.set_field
        self.db.set_field.assert_any_call("title", {1: "The Original Great Novel"})
        self.db.set_field.assert_any_call("publisher", {1: "Acme Publishing House"})
        self.db.set_field.assert_any_call(
            "comments",
            {1: "A gripping tale of exploration and mystery across distant lands."},
        )

        # Non-destructive default: ORIGINAL_EPUB preserved
        self.assertIn("ORIGINAL_EPUB", self.book_formats[1])

    def test_restore_apply_worker_removes_backup_when_remove_backup_is_true(self):
        items = [{"book_id": 1, "curr_title": "Great Novel", "raw_orig": self.orig_epub_data}]

        worker = RestoreApplyWorker(
            self.db,
            items,
            restore_epub=True,
            restore_metadata=False,
            remove_backup=True,
        )

        results = {}
        worker.restore_finished.connect(results.update)
        worker.run()

        self.assertEqual(results["restored"], [1])
        self.db.remove_formats.assert_called_with({1: {"ORIGINAL_EPUB"}})

    def test_dialog_ui_initialization_table_population_and_actions(self):
        parent = MagicMock()
        dialog = ScrubRestoreManagerDialog(parent, self.db)
        if dialog.scan_worker and dialog.scan_worker.isRunning():
            dialog.scan_worker.wait()
        QApplication.processEvents()

        self.assertEqual(dialog.table.rowCount(), 2)

        # Test selection helpers
        dialog.select_all()
        self.assertEqual(len(dialog.get_checked_items()), 2)
        dialog.deselect_all()
        self.assertEqual(len(dialog.get_checked_items()), 0)
        dialog.select_diff_only()
        self.assertEqual(len(dialog.get_checked_items()), 1)
        self.assertEqual(dialog.get_checked_items()[0]["book_id"], 1)

        # Test filtering
        dialog.filter_edit.setText("Jane Doe")
        dialog.apply_filter()
        self.assertFalse(dialog.table.isRowHidden(0))

        dialog.filter_edit.setText("Nonexistent Title")
        dialog.apply_filter()
        self.assertTrue(dialog.table.isRowHidden(0))
        self.assertTrue(dialog.table.isRowHidden(1))

        dialog.filter_edit.setText("")
        dialog.apply_filter()

        # Test detail preview rendering
        dialog.table.selectRow(0)
        dialog.on_table_selection_changed()
        html_content = dialog.detail_text.toHtml()
        self.assertIn("The Original Great Novel", html_content)
        self.assertIn("Acme Publishing House", html_content)

        # Test batch backup deletion
        with patch.object(
            QtWidgets.QMessageBox,
            "warning",
            return_value=QtWidgets.QMessageBox.StandardButton.Yes,
        ):
            with patch.object(QtWidgets.QMessageBox, "information"):
                dialog.select_all()
                dialog.delete_backups()
                if dialog.scan_worker and dialog.scan_worker.isRunning():
                    dialog.scan_worker.wait()
                QApplication.processEvents()
                self.db.remove_formats.assert_called()

    def test_action_menu_has_restore_entries(self):
        from scrubber_plugin.action import ScrubAction

        gui = QtWidgets.QWidget()
        qaction = MagicMock()
        action = ScrubAction()
        action.gui = gui
        action.qaction = qaction

        # Call genesis to build the dropdown menu
        action.genesis()

        # Verify setMenu was called
        qaction.setMenu.assert_called_once()
        menu = qaction.setMenu.call_args[0][0]
        action_titles = [
            act.text() if callable(getattr(act, "text", None)) else str(getattr(act, "text", ""))
            for act in menu.actions()
        ]

        self.assertIn("Restore original EPUBs & metadata…", action_titles)
        self.assertIn("Restore EPUB files from folder…", action_titles)


if __name__ == "__main__":
    unittest.main()
