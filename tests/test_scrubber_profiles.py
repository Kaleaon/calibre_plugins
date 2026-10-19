"""Unit tests for site-specific scrub profiles and domain matching manager."""

import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock
import zipfile

from PyQt6 import QtCore, QtGui, QtWidgets

# Register qt.core shim for tests
qtcore = types.ModuleType('qt.core')
for module in (QtWidgets, QtGui, QtCore):
    for name in dir(module):
        setattr(qtcore, name, getattr(module, name))
sys.modules['qt.core'] = qtcore
sys.modules['qt'] = types.ModuleType('qt')

ROOT = Path(__file__).resolve().parents[1]

# Dynamically import profiles and core modules
spec_prof = importlib.util.spec_from_file_location('profiles', ROOT / 'scrubber_plugin/profiles.py')
profiles_mod = importlib.util.module_from_spec(spec_prof)
spec_prof.loader.exec_module(profiles_mod)

spec_core = importlib.util.spec_from_file_location('scrub_core', ROOT / 'scrubber_plugin/core.py')
core_mod = importlib.util.module_from_spec(spec_core)
spec_core.loader.exec_module(core_mod)

spec_ui = importlib.util.spec_from_file_location('scrub_ui', ROOT / 'scrubber_plugin/ui.py')
ui_mod = importlib.util.module_from_spec(spec_ui)
spec_ui.loader.exec_module(ui_mod)


XHTML = '<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body>%s</body></html>'
CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'


def make_test_epub(body_html, opf_metadata_xml=""):
    opf = f'''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title>{opf_metadata_xml}</metadata>
    <manifest>
      <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
    </manifest>
    <spine><itemref idref="c1"/></spine>
    </package>'''

    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', CONTAINER)
        z.writestr('OEBPS/content.opf', opf)
        z.writestr('OEBPS/c1.xhtml', XHTML % body_html)
    return b.getvalue()


class DomainExtractionTests(unittest.TestCase):
    """Test extracting domains from text, OPF, and Calibre metadata."""

    def test_extract_domains_from_text(self):
        text = "Visit https://www.royalroad.com/fiction/123 or http://archiveofourown.org/works/99"
        domains = profiles_mod.extract_domains_from_text(text)
        self.assertIn('royalroad.com', domains)
        self.assertIn('archiveofourown.org', domains)

    def test_extract_domains_from_opf(self):
        opf_xml = core_mod._parse('''<?xml version="1.0"?>
        <package xmlns="http://www.idpf.org/2007/opf">
          <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
            <dc:identifier>https://www.royalroad.com/fiction/55555</dc:identifier>
            <dc:publisher>royalroad.com</dc:publisher>
            <dc:source>https://scribblehub.com/series/1000</dc:source>
          </metadata>
        </package>'''.encode('utf-8'))

        domains = profiles_mod.extract_domains_from_opf(opf_xml)
        self.assertIn('royalroad.com', domains)
        self.assertIn('scribblehub.com', domains)

    def test_extract_domains_from_metadata(self):
        mi = MagicMock()
        mi.publisher = 'Royal Road'
        mi.identifiers = {'url': 'https://www.royalroad.com/fiction/12345', 'isbn': '123456789'}
        mi.source = 'https://fanfiction.net/s/999'

        domains = profiles_mod.extract_domains_from_metadata(mi)
        self.assertIn('royalroad.com', domains)
        self.assertIn('fanfiction.net', domains)

    def test_malformed_metadata_extraction_returns_empty_safely(self):
        self.assertEqual(profiles_mod.extract_domains_from_text(None), set())
        self.assertEqual(profiles_mod.extract_domains_from_text(""), set())
        self.assertEqual(profiles_mod.extract_domains_from_opf(None), set())
        self.assertEqual(profiles_mod.extract_domains_from_metadata(None), set())


class DomainMatchingTests(unittest.TestCase):
    """Test domain pattern matching including wildcards and regexes."""

    def test_match_exact_and_subdomain(self):
        self.assertTrue(profiles_mod.match_domain_pattern('royalroad.com', 'royalroad.com'))
        self.assertTrue(profiles_mod.match_domain_pattern('www.royalroad.com', 'royalroad.com'))
        self.assertTrue(profiles_mod.match_domain_pattern('sub.royalroad.com', 'royalroad.com'))

    def test_match_wildcards(self):
        self.assertTrue(profiles_mod.match_domain_pattern('m.royalroad.com', '*.royalroad.com'))
        self.assertTrue(profiles_mod.match_domain_pattern('royalroad.org', 'royalroad.*'))
        self.assertTrue(profiles_mod.match_domain_pattern('anything.com', '*'))

    def test_match_regex(self):
        self.assertTrue(profiles_mod.match_domain_pattern('scribblehub.com', 're:.*scribble.*'))
        self.assertFalse(profiles_mod.match_domain_pattern('other.com', 're:.*scribble.*'))

    def test_non_matching_domains(self):
        self.assertFalse(profiles_mod.match_domain_pattern('notroyalroad.com', 'royalroad.com'))
        self.assertFalse(profiles_mod.match_domain_pattern('royalroad.com.attacker.com', 'royalroad.com'))


class ProfileResolutionTests(unittest.TestCase):
    """Test hierarchical profile resolution and global default fallback."""

    def setUp(self):
        self.profiles = [
            {
                'id': 'p1',
                'name': 'Royal Road Profile',
                'domain_patterns': ['royalroad.com', '*.royalroad.com'],
                'extra_patterns': 're:chapter\\s+\\d+\\s+sponsored',
                'heuristic': True,
                'drop_empty_pages': True,
                'is_default': False,
            },
            {
                'id': 'p2',
                'name': 'AO3 Profile',
                'domain_patterns': ['archiveofourown.org'],
                'extra_patterns': 'ao3mark',
                'heuristic': False,
                'drop_empty_pages': False,
                'is_default': False,
            },
            {
                'id': 'global_default',
                'name': 'Global Default',
                'domain_patterns': ['*'],
                'extra_patterns': 'oceanofpdf',
                'heuristic': True,
                'drop_empty_pages': True,
                'is_default': True,
            },
        ]

    def test_resolve_specific_profile(self):
        prof = profiles_mod.resolve_profile({'royalroad.com'}, self.profiles)
        self.assertEqual(prof['name'], 'Royal Road Profile')

        prof_ao3 = profiles_mod.resolve_profile({'archiveofourown.org'}, self.profiles)
        self.assertEqual(prof_ao3['name'], 'AO3 Profile')

    def test_fallback_to_global_default_when_unmatched(self):
        prof = profiles_mod.resolve_profile({'unknownsite.com'}, self.profiles)
        self.assertEqual(prof['name'], 'Global Default')

        prof_no_domains = profiles_mod.resolve_profile(set(), self.profiles)
        self.assertEqual(prof_no_domains['name'], 'Global Default')


class ProfileSchemaAndJsonTests(unittest.TestCase):
    """Test profile JSON schema validation and import/export."""

    def test_validate_valid_json_schema(self):
        valid_json = json.dumps({
            'profiles': [
                {
                    'name': 'Custom Site',
                    'domain_patterns': ['custom.com'],
                    'extra_patterns': 'custommark',
                    'heuristic': True,
                    'drop_empty_pages': True,
                }
            ]
        })
        imported = profiles_mod.import_profiles_from_json(valid_json)
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]['name'], 'Custom Site')

    def test_validate_invalid_json_schema_raises_error(self):
        invalid_json_missing_domains = json.dumps([
            {'name': 'Incomplete Profile'}
        ])
        with self.assertRaises(ValueError):
            profiles_mod.import_profiles_from_json(invalid_json_missing_domains)

        invalid_types_json = json.dumps([
            {'name': 'Bad Profile', 'domain_patterns': ['test.com'], 'heuristic': 'not-a-bool'}
        ])
        with self.assertRaises(ValueError):
            profiles_mod.import_profiles_from_json(invalid_types_json)

    def test_export_profiles_to_json(self):
        profs = [dict(profiles_mod.DEFAULT_GLOBAL_PROFILE)]
        exported_str = profiles_mod.export_profiles_to_json(profs)
        parsed = json.loads(exported_str)
        self.assertIn('profiles', parsed)
        self.assertEqual(parsed['profiles'][0]['name'], 'Global Default')


class ProfileExecutionTests(unittest.TestCase):
    """Test executing site-specific scrub profiles during EPUB scrubbing."""

    def setUp(self):
        self.profiles = [
            {
                'id': 'p_rr',
                'name': 'RoyalRoad Specific',
                'domain_patterns': ['royalroad.com'],
                'extra_patterns': 're:chapter\\s+\\d+\\s+sponsored',
                'heuristic': True,
                'drop_empty_pages': True,
                'is_default': False,
            },
            {
                'id': 'global_default',
                'name': 'Global Default',
                'domain_patterns': ['*'],
                'extra_patterns': 'oceanofpdf',
                'heuristic': True,
                'drop_empty_pages': True,
                'is_default': True,
            },
        ]

    def test_domain_matched_profile_executes_site_specific_rules(self):
        # EPUB metadata has royalroad domain, body has 'Chapter 1 Sponsored' mark
        opf_meta = '<dc:identifier>https://www.royalroad.com/fiction/100</dc:identifier>'
        body = '<p>Chapter 1 Sponsored</p><p>Actual story content.</p>'
        epub_data = make_test_epub(body, opf_meta)

        cleaned, report = core_mod.scrub_epub(epub_data, profiles=self.profiles)
        self.assertEqual(report['active_profile'], 'RoyalRoad Specific')
        self.assertIn('royalroad.com', report['detected_domains'])
        self.assertIsNotNone(cleaned)

    def test_fallback_to_global_default_profile(self):
        # EPUB has no matching domain, body has oceanofpdf mark
        opf_meta = '<dc:identifier>urn:isbn:9780000000000</dc:identifier>'
        body = '<p>Downloaded from OceanofPDF.com</p><p>Actual story content.</p>'
        epub_data = make_test_epub(body, opf_meta)

        cleaned, report = core_mod.scrub_epub(epub_data, profiles=self.profiles)
        self.assertEqual(report['active_profile'], 'Global Default')
        self.assertIsNotNone(cleaned)


class PreviewAndUiTests(unittest.TestCase):
    """Test preview dialog active profile display and ScrubSettingsDialog tabs."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_preview_dialog_displays_active_profile_context(self):
        scan_results = [
            {
                'book_id': 101,
                'title': 'RoyalRoad Fiction Book',
                'active_profile': 'RoyalRoad Specific',
                'changes': [
                    {'file': 'OEBPS/c1.xhtml', 'action': 'block removed', 'text': 'Sponsored', 'id': 'book_101:c1:0', 'detail': 'Detail', 'category': 'Content Blocks'}
                ]
            }
        ]

        dlg = ui_mod.ScrubPreviewDialog(None, scan_results)
        book_item = dlg.tree.topLevelItem(0)
        self.assertEqual(book_item.text(0), 'RoyalRoad Fiction Book')
        self.assertIn('RoyalRoad Specific', book_item.text(1))

        # Test detail view when book item is selected
        dlg.tree.setCurrentItem(book_item)
        self.assertIn('RoyalRoad Specific', dlg.detail_edit.toPlainText())

    def test_settings_dialog_tabbed_profile_manager(self):
        prefs = {}
        dlg = ui_mod.ScrubSettingsDialog(None, prefs)
        self.assertEqual(dlg.tabs.count(), 2)
        self.assertEqual(dlg.tabs.tabText(0), 'General Settings')
        self.assertEqual(dlg.tabs.tabText(1), 'Profile Manager')
        self.assertGreaterEqual(len(dlg.profiles), 1)


if __name__ == '__main__':
    unittest.main()
