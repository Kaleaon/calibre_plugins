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


class ActionTests(unittest.TestCase):
    def setUp(self):
        import types
        from unittest.mock import MagicMock

        self.saved_modules = {k: v for k, v in sys.modules.items() if k.startswith(('calibre', 'qt', 'calibre_plugins'))}
        stubs = ['calibre', 'calibre.customize', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config',
                 'calibre.utils.date', 'calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.book',
                 'calibre.ebooks.metadata.book.base', 'qt', 'qt.core']
        for name in stubs:
            if name not in sys.modules:
                sys.modules[name] = MagicMock()

        class Meta:
            def __init__(self, title, authors):
                self.title, self.authors, self.ids = title, authors, {}
                self.publisher = None
                self.tags = []
                self.comments = None
                self.series = None
                self.series_index = None
                self.languages = []

            def set_identifier(self, key, value):
                self.ids[key] = value

        sys.modules['calibre.ebooks.metadata.book.base'].Metadata = Meta
        sys.modules['calibre.gui2.actions'].InterfaceAction = object

        class DummyAction:
            def __init__(self, text, parent=None):
                self.text = text
                self.parent = parent
                self.triggered_callback = None
                self.triggered = self
                self._menu = None

            def connect(self, slot):
                self.triggered_callback = slot

            def setMenu(self, menu):
                self._menu = menu

            def menu(self):
                return self._menu

        class DummyMenu:
            def __init__(self, parent=None):
                self.parent = parent
                self.actions_list = []

            def addAction(self, action):
                self.actions_list.append(action)

            def actions(self):
                return self.actions_list

        sys.modules['qt.core'].QAction = DummyAction
        sys.modules['qt.core'].QMenu = DummyMenu

        if 'calibre_plugins' not in sys.modules or not hasattr(sys.modules['calibre_plugins'], '__path__'):
            sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
            sys.modules['calibre_plugins'].__path__ = []

        pkg = types.ModuleType('calibre_plugins.fanfic_downloader')
        pkg.__path__ = []
        sys.modules['calibre_plugins.fanfic_downloader'] = pkg

        import common.libkit as libkit_mod
        import common.guikit as guikit_mod
        import fanfic.config as config_mod
        import fanfic.engine as engine_mod
        import fanfic.flaresolverr as flaresolverr_mod

        pkg.engine = engine_mod
        pkg.libkit = libkit_mod
        pkg.config = config_mod
        pkg.guikit = guikit_mod
        pkg.flaresolverr = flaresolverr_mod

        sys.modules['calibre_plugins.fanfic_downloader.engine'] = engine_mod
        sys.modules['calibre_plugins.fanfic_downloader.libkit'] = libkit_mod
        sys.modules['calibre_plugins.fanfic_downloader.config'] = config_mod
        sys.modules['calibre_plugins.fanfic_downloader.guikit'] = guikit_mod
        sys.modules['calibre_plugins.fanfic_downloader.flaresolverr'] = flaresolverr_mod

        import fanfic.ui as ui_mod
        pkg.ui = ui_mod
        sys.modules['calibre_plugins.fanfic_downloader.ui'] = ui_mod

        import fanfic.action as action_mod
        sys.modules['calibre_plugins.fanfic_downloader.action'] = action_mod

        self.action_mod = action_mod

    def tearDown(self):
        for k in [k for k in sys.modules if k.startswith(('calibre', 'qt'))]:
            del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def test_genesis_menu_setup(self):
        from unittest.mock import MagicMock
        from qt.core import QAction
        action = self.action_mod.FanficAction()
        action.gui = MagicMock()
        action.qaction = QAction('Main Action', action.gui)
        action.genesis()

        menu = action.qaction.menu()
        self.assertIsNotNone(menu)
        actions = menu.actions()
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[0].text, 'Download new stories from URLs...')
        self.assertEqual(actions[1].text, 'Update selected library books')

    def test_update_selected_no_selection(self):
        from unittest.mock import MagicMock, patch
        action = self.action_mod.FanficAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = []

        with patch.object(self.action_mod, 'info_dialog') as mock_info:
            action.update_selected()
            mock_info.assert_called_once()
            self.assertIn('No books selected', mock_info.call_args[0][2])

    @unittest.skipUnless(HAVE_FFF, 'FanFicFare is not installed')
    def test_update_selected_with_valid_and_missing_urls(self):
        from unittest.mock import MagicMock, patch
        action = self.action_mod.FanficAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [10, 20]

        mock_db = MagicMock()
        action.gui.current_db.new_api = mock_db

        mock_db.field_for.side_effect = lambda field, book_id, default=None: {
            (10, 'title'): 'Valid Story',
            (20, 'title'): 'Story Without URL',
        }.get((book_id, field), default)

        mock_db.all_field_for.return_value = {
            10: {'url': 'https://www.royalroad.com/fiction/21220/some-title'},
            20: {},
        }

        fake_story_result = {
            'epub': b'fake epub data',
            'url': 'https://www.royalroad.com/fiction/21220',
            'title': 'Valid Story',
            'authors': ['Author A'],
            'description': 'Desc',
            'tags': ['Tag1'],
            'series': None,
            'series_index': None,
            'site': 'www.royalroad.com',
            'published': None,
            'language': 'en',
            'chapters': 10,
            'status': 'Completed',
            'chapter_errors': 0,
        }

        with patch.object(self.action_mod.engine, 'download_story', return_value=fake_story_result) as mock_dl, \
             patch.object(self.action_mod.libkit, 'upsert_epub', return_value=(10, False)) as mock_upsert, \
             patch.object(self.action_mod, 'run_task', side_effect=lambda gui, title, func: type('Task', (), {'error': None, 'cancelled': lambda *a: False, 'result': func(type('T', (), {'cancelled': lambda *a: False, 'status': ''})())})()), \
             patch.object(self.action_mod, 'info_dialog') as mock_info:

            action.update_selected()

            mock_dl.assert_called_once()
            mock_upsert.assert_called_once()

            mock_info.assert_called_once()
            msg = mock_info.call_args[0][2]
            self.assertIn('1 book(s) updated', msg)
            self.assertIn("Skipped 'Story Without URL' (ID 20): No 'url' identifier", msg)

    def test_find_book_normalizes_colons_and_slashes(self):
        from unittest.mock import MagicMock
        from common.libkit import find_book
        gui = MagicMock()
        db = MagicMock()
        gui.current_db.new_api = db
        db.all_book_ids.return_value = [1, 2]
        db.all_field_for.return_value = {
            1: {'url': 'https|//www.royalroad.com/fiction/21220/'},
            2: {'url': 'https|//archiveofourown.org/works/12345'},
        }

        self.assertEqual(find_book(gui, 'url', 'https://www.royalroad.com/fiction/21220'), 1)
        self.assertEqual(find_book(gui, 'url', 'https://archiveofourown.org/works/12345/'), 2)


if __name__ == '__main__':
    unittest.main()
