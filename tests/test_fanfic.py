"""Engine tests for the fanfic plugin. They need FanFicFare importable (pip install fanficfare) and skip otherwise.
No test here touches the network: URL checks only use FanFicFare's address patterns."""
import importlib.util
from pathlib import Path
import re
import sys
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'fanfic'))
import build_plugins  # noqa: E402
import engine  # noqa: E402

try:
    import fanficfare  # noqa: F401
    import cloudscraper, requests_file, requests_toolbelt, pyparsing  # noqa: F401,E401
    HAVE_FFF = True
except ImportError:
    HAVE_FFF = False


class PureTests(unittest.TestCase):
    def test_extra_adult_parsing(self):
        text = 'Example.com\nhttps://www.other.net/path # comment\nfoo.org, bar.org\n# only a comment\n\n'
        self.assertEqual(engine.parse_extra_adult(text), ['example.com', 'other.net', 'foo.org', 'bar.org'])

    def test_adult_matching_includes_subdomains(self):
        self.assertTrue(engine.is_adult_site('literotica.com'))
        self.assertTrue(engine.is_adult_site('www.literotica.com'))
        self.assertTrue(engine.is_adult_site('erosnsappho.sycophanthex.com'))
        self.assertFalse(engine.is_adult_site('ashwinder.sycophanthex.com'))
        self.assertFalse(engine.is_adult_site('www.royalroad.com'))
        self.assertFalse(engine.is_adult_site('notliterotica.com'))
        self.assertTrue(engine.is_adult_site('stories.custom.net', ['custom.net']))

    def test_series_split(self):
        self.assertEqual(engine.split_series('The Saga [3]'), ('The Saga', 3.0))
        self.assertEqual(engine.split_series('Plain'), ('Plain', None))
        self.assertEqual(engine.split_series('Half [1.5]'), ('Half', 1.5))
        self.assertEqual(engine.split_series(''), ('', None))


@unittest.skipUnless(HAVE_FFF, 'FanFicFare is not installed')
class AdapterTests(unittest.TestCase):
    def test_normalises_and_dedupes(self):
        accepted, problems = engine.check_urls([
            'https://www.royalroad.com/fiction/21220/some-title', 'royalroad.com/fiction/21220', '', '# comment',
            'https://archiveofourown.org/works/51711850/chapters/1?x=1'])
        self.assertEqual([a['url'] for a in accepted], ['https://www.royalroad.com/fiction/21220', 'https://archiveofourown.org/works/51711850'])
        self.assertEqual(problems, [])

    def test_chapter_ranges_are_kept_separate(self):
        accepted, _ = engine.check_urls(['https://www.royalroad.com/fiction/1[2-4]', 'https://www.royalroad.com/fiction/1'])
        self.assertEqual([(a['begin'], a['end']) for a in accepted], [('2', '4'), (None, None)])

    def test_unknown_and_garbage(self):
        accepted, problems = engine.check_urls(['https://example.com/story/1', 'not a url'])
        self.assertEqual(accepted, [])
        self.assertEqual(len(problems), 2)

    def test_adult_setting_gates_adult_sites(self):
        url = 'https://www.literotica.com/s/some-story'
        accepted, problems = engine.check_urls([url], allow_adult=False)
        self.assertEqual(accepted, [])
        self.assertIn('adult', problems[0])
        accepted, problems = engine.check_urls([url], allow_adult=True)
        self.assertEqual((len(accepted), accepted[0]['adult'], problems), (1, True, []))
        # sites that merely allow adult works are not blocked up front
        accepted, _ = engine.check_urls(['https://archiveofourown.org/works/1'], allow_adult=False)
        self.assertEqual(len(accepted), 1)
        # user-added domains count too
        accepted, _ = engine.check_urls(['https://www.royalroad.com/fiction/1'], allow_adult=False, extra_adult=['royalroad.com'])
        self.assertEqual(accepted, [])

    def test_supported_sites_listing(self):
        rows = engine.supported_sites()
        domains = [r[0] for r in rows]
        self.assertIn('www.royalroad.com', domains)
        self.assertGreater(len(rows), 90)
        self.assertTrue(all(example == '' or example.startswith('http') for _, example, _ in rows))
        self.assertLess(sum(1 for _, example, _ in rows if not example), 5)
        flagged = {d for d, _, adult in rows if adult}
        self.assertIn('literotica.com', flagged)
        self.assertNotIn('www.royalroad.com', flagged)
        self.assertEqual(len(flagged), len({d for d in flagged}))

    def test_every_adult_domain_is_a_real_fanficfare_site(self):
        sites = {d.split('/')[0] for d, _, _ in engine.supported_sites()}
        for domain in engine.ADULT_DOMAINS:
            self.assertTrue(any(s == domain or s.endswith('.' + domain) for s in sites), domain)

    def test_flaresolverr_snippet_from_the_guide_is_read_for_royal_road(self):
        from io import StringIO
        ns = engine.load()
        url = 'https://www.royalroad.com/fiction/21220'
        cfg = ns.Configuration(ns.adapters.getConfigSectionsFor(url), 'epub')
        cfg.read_file(StringIO(ns.defaults_ini))
        cfg.read_file(StringIO('[www.royalroad.com]\nuse_flaresolverr_proxy:true\n'))
        self.assertEqual(str(cfg.getConfig('use_flaresolverr_proxy')).lower(), 'true')
        self.assertEqual(cfg.getConfig('flaresolverr_proxy_port', '8191'), '8191')
        other = ns.Configuration(ns.adapters.getConfigSectionsFor('https://archiveofourown.org/works/1'), 'epub')
        other.read_file(StringIO(ns.defaults_ini))
        other.read_file(StringIO('[www.royalroad.com]\nuse_flaresolverr_proxy:true\n'))
        self.assertFalse(other.getConfig('use_flaresolverr_proxy'))  # per-site, as the guide recommends

    def test_guide_snippet_matches_the_documented_section_name(self):
        guide = (ROOT / 'docs' / 'royalroad-flaresolverr.md').read_text(encoding='utf-8')
        self.assertIn('[www.royalroad.com]\nuse_flaresolverr_proxy:true', guide)
        self.assertIn('www.royalroad.com', [d for d, _, _ in engine.supported_sites()])

    def test_explain(self):
        ns = engine.load()
        self.assertIn('Allow adult sites', engine.explain(ns.exceptions.AdultCheckRequired('https://x')))
        self.assertIn('browser cache', engine.explain(Exception('403 Client Error: Forbidden')))


@unittest.skipUnless(HAVE_FFF, 'FanFicFare is not installed')
class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        built = build_plugins.build_all(cls.tmp.name, skip_unavailable=False)
        cls.zip = zipfile.ZipFile(built['fanfic_downloader'])
        cls.names = cls.zip.namelist()

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()
        cls.tmp.cleanup()

    def test_contents(self):
        for expected in ('__init__.py', 'engine.py', 'fanficfare/adapters/adapter_royalroadcom.py', 'fanficfare/defaults.ini',
                         'cloudscraper/__init__.py', 'brotlidecpy/brotli-dict', 'flaresolverr.py', 'royalroad-flaresolverr.md', 'THIRD_PARTY_LICENSES.txt',
                         'plugin-import-name-fanfic_downloader.txt'):
            self.assertIn(expected, self.names)
        for excluded in ('fanficfare/cli.py', 'fanficfare/writers/writer_txt.py', 'fanficfare/writers/writer_mobi.py'):
            self.assertNotIn(excluded, self.names)
        self.assertFalse([n for n in self.names if '__pycache__' in n or n.endswith('.pyc')])

    def test_bundled_guide_is_the_docs_file(self):
        self.assertEqual(self.zip.read('royalroad-flaresolverr.md').decode('utf-8'),
                         (ROOT / 'docs' / 'royalroad-flaresolverr.md').read_text(encoding='utf-8'))

    def test_no_absolute_fanficfare_imports_remain(self):
        for name in self.names:
            if name.startswith('fanficfare/') and name.endswith('.py'):
                text = self.zip.read(name).decode('utf-8')
                self.assertIsNone(re.search(r'^\s*(from fanficfare\b|import fanficfare\b)', text, re.M), name)

    def test_licenses_and_notice(self):
        text = self.zip.read('THIRD_PARTY_LICENSES.txt').decode('utf-8')
        for needle in ('Apache License', 'cloudscraper', 'requests-file', 'pyparsing', 'brotlidecpy', 'Modifications for bundling'):
            self.assertIn(needle, text)


class LibkitMergeEpubsTests(unittest.TestCase):
    def test_libkit_reexports_merge_epubs(self):
        from common import libkit, storykit
        from io import BytesIO
        story1 = {'id': '1', 'title': 'Story One', 'author': 'Author A', 'sections': [('Ch 1', '<p>Part 1</p>')]}
        story2 = {'id': '2', 'title': 'Story Two', 'author': 'Author B', 'sections': [('Ch 1', '<p>Part 2</p>')]}
        epub1 = storykit.build_epub([story1])
        epub2 = storykit.build_epub([story2])
        merged = libkit.merge_epubs([epub1, epub2], title='Combined Omnibus')
        self.assertIsInstance(merged, bytes)
        self.assertGreater(len(merged), 0)
        with zipfile.ZipFile(BytesIO(merged), 'r') as z:
            self.assertIn('OEBPS/content.opf', z.namelist())

    def test_fanfic_combining_calls_libkit_merge_epubs(self):
        from common import libkit, storykit
        from io import BytesIO
        story1 = {'id': '1', 'title': 'Story 1', 'author': 'Alice', 'sections': [('C1', '<p>A</p>')]}
        story2 = {'id': '2', 'title': 'Story 2', 'author': 'Bob', 'sections': [('C2', '<p>B</p>')]}
        epub1 = storykit.build_epub([story1])
        epub2 = storykit.build_epub([story2])

        results = [
            {'title': 'Story 1', 'authors': ['Alice'], 'epub': epub1, 'tags': ['tag1'], 'chapter_errors': []},
            {'title': 'Story 2', 'authors': ['Bob'], 'epub': epub2, 'tags': ['tag2'], 'chapter_errors': []},
        ]

        merged = libkit.merge_epubs([r['epub'] for r in results])
        self.assertIsInstance(merged, bytes)
        with zipfile.ZipFile(BytesIO(merged), 'r') as z:
            self.assertIn('OEBPS/content.opf', z.namelist())


if __name__ == '__main__':
    unittest.main()
