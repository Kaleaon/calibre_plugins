import importlib.util
import io
import os
import sys
import types
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location('scrub_core', ROOT / 'scrubber_plugin/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
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

XHTML = '<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>%s</body></html>'
CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'
OPF = '''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Book Title - OceanofPDF.com</dc:title><dc:publisher>OceanofPDF.com</dc:publisher></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="promo" href="promo.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>
<spine><itemref idref="promo"/><itemref idref="c1"/></spine></package>'''
NAV = XHTML % '<nav><ol><li><a href="promo.xhtml">Downloaded</a></li><li><a href="c1.xhtml">Chapter 1</a></li></ol></nav>'


def make_epub(chapter_html, promo_html='<p>Downloaded from <a href="https://oceanofpdf.com">OceanofPDF.com</a></p>'):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', CONTAINER)
        z.writestr('OEBPS/content.opf', OPF)
        z.writestr('OEBPS/nav.xhtml', NAV)
        z.writestr('OEBPS/promo.xhtml', XHTML % promo_html)
        z.writestr('OEBPS/c1.xhtml', XHTML % chapter_html)
    return b.getvalue()


class ScrubberPreviewCoreTests(unittest.TestCase):
    def test_dry_run_scan_returns_candidates_without_modifying_bytes(self):
        data = make_epub('<p>Chapter 1. Visit <a href="https://oceanofpdf.com">OceanofPDF.com</a> for more.</p>')
        out, report = core.scrub_epub(data, dry_run=True)
        self.assertIsNone(out)  # Dry run returns None for new bytes
        self.assertGreater(len(report['changes']), 0)
        first_change = report['changes'][0]
        self.assertIn('id', first_change)
        self.assertIn('file', first_change)
        self.assertIn('action', first_change)
        self.assertIn('detail', first_change)
        self.assertIn('category', first_change)

    def test_selective_item_filtering(self):
        data = make_epub('<p>Story text. <a href="https://oceanofpdf.com">Get free ebooks</a> at site.</p>')
        _, dry_report = core.scrub_epub(data, dry_run=True)
        all_changes = dry_report['changes']
        self.assertGreater(len(all_changes), 1)

        # Select ONLY the title metadata change item, excluding publisher metadata / page drop / link items
        title_item = next(c for c in all_changes if c['file'] == 'OEBPS/content.opf' and 'metadata trimmed' in c['action'])
        allowed = {title_item['id']}

        out, apply_report = core.scrub_epub(data, allowed_change_ids=allowed)
        self.assertIsNotNone(out)

        # Verify title metadata was trimmed
        opf_str = zipfile.ZipFile(io.BytesIO(out)).read('OEBPS/content.opf').decode()
        self.assertIn('<dc:title>Book Title</dc:title>', opf_str)
        # Verify publisher metadata was NOT removed because it was excluded (unchecked)
        self.assertIn('<dc:publisher>OceanofPDF.com</dc:publisher>', opf_str)

        # Verify promo.xhtml page was NOT dropped because it was excluded
        names = zipfile.ZipFile(io.BytesIO(out)).namelist()
        self.assertIn('OEBPS/promo.xhtml', names)


@unittest.skipUnless(HAVE_QT, 'PyQt6 is required for UI tests')
class ScrubPreviewDialogUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_preview_dialog_hierarchy_and_checkbox_propagation(self):
        spec_ui = importlib.util.spec_from_file_location('scrub_ui', ROOT / 'scrubber_plugin/ui.py')
        scrub_ui = importlib.util.module_from_spec(spec_ui)
        spec_ui.loader.exec_module(scrub_ui)

        scan_results = [
            {
                'book_id': 1,
                'title': 'Test Book 1',
                'changes': [
                    {'file': 'OEBPS/c1.xhtml', 'action': 'link removed', 'text': 'Link text 1', 'id': 'book_1:c1:0', 'detail': 'Detail 1', 'category': 'Hyperlinks'},
                    {'file': 'OEBPS/c1.xhtml', 'action': 'link removed', 'text': 'Link text 2', 'id': 'book_1:c1:1', 'detail': 'Detail 2', 'category': 'Hyperlinks'},
                ]
            }
        ]

        dialog = scrub_ui.ScrubPreviewDialog(None, scan_results)
        self.assertEqual(len(dialog.get_selected_change_ids()), 2)

        # Find leaf items and uncheck one item
        tree = dialog.tree
        book_item = tree.topLevelItem(0)
        self.assertEqual(book_item.text(0), 'Test Book 1')

        # Uncheck first child item under Hyperlinks category
        cat_item = book_item.child(0)
        self.assertEqual(cat_item.text(0), 'Hyperlinks')
        child_item = cat_item.child(0)
        child_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)

        selected = dialog.get_selected_change_ids()
        self.assertEqual(len(selected), 1)
        self.assertNotIn('book_1:c1:0', selected)
        self.assertIn('book_1:c1:1', selected)

        # Check parent node checkState is PartiallyChecked because 1 of 2 children is checked
        self.assertEqual(cat_item.checkState(0), QtCore.Qt.CheckState.PartiallyChecked)
        self.assertEqual(book_item.checkState(0), QtCore.Qt.CheckState.PartiallyChecked)

        # Uncheck book parent node -> all children become unchecked
        book_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(len(dialog.get_selected_change_ids()), 0)

        # Test select all / deselect all
        dialog.select_all()
        self.assertEqual(len(dialog.get_selected_change_ids()), 2)

        dialog.deselect_all()
        self.assertEqual(len(dialog.get_selected_change_ids()), 0)


if __name__ == '__main__':
    unittest.main()
