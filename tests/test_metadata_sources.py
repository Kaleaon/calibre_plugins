"""Tests for the metadata-source companions: every plugin's metadata_source
module (pure search logic, driven here with fake fetchers/transports) and the
companion <plugin>_source/__init__.py Source classes that wrap them.

No test touches the network. Site answers are canned pages/JSON shaped like
the fixtures the plugins' own tests use.
"""
import importlib
import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'common'))

import metakit  # noqa: E402

PACKAGES = {}


def load_package(alias, folder):
    """Register a plugin folder as a package so its relative imports work, without running its __init__."""
    if alias in PACKAGES:
        return PACKAGES[alias]
    pkg = types.ModuleType(alias)
    pkg.__path__ = [str(ROOT / folder)]
    sys.modules[alias] = pkg
    PACKAGES[alias] = pkg
    return pkg


def load_source(alias, folder):
    load_package(alias, folder)
    return importlib.import_module(f'{alias}.metadata_source')


class FakeFetcher:
    """storykit.Fetcher-shaped stand-in: routes exact URLs to canned text."""

    def __init__(self, pages):
        self.pages, self.requested = pages, []

    def get(self, url, **kwargs):
        self.requested.append(url)
        try:
            return self.pages[url]
        except KeyError:
            raise OSError('missing ' + url) from None


# ---------------------------------------------------------------- metakit

class FakeSource:
    name = 'Fake'

    def __init__(self):
        self.covers = {}
        self.isbns = {}

    def cache_identifier_to_cover_url(self, identifiers, url):
        self.covers[tuple(sorted(identifiers.items()))] = url

    def cache_isbn_to_identifier(self, isbn, key, value):
        self.isbns[(isbn, key)] = value

    def clean_downloaded_metadata(self, mi):
        pass


class ListQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class Abort:
    def __init__(self):
        self._set = False

    def is_set(self):
        return self._set


class MetakitTests(unittest.TestCase):
    def test_similarity_and_scoring(self):
        self.assertEqual(metakit.similarity('The Big Story', 'big story'), 1.0)
        self.assertGreater(metakit.similarity('Big Story', 'Big Story Returns'), 0.4)
        self.assertEqual(metakit.similarity('', 'x'), 0.0)
        self.assertEqual(metakit.score_candidate('T', ['A'], {'title': 'T', 'authors': ['A'], 'score': 0.3}), 0.3)
        self.assertEqual(metakit.score_candidate(None, None, {'title': 'Anything'}), 1.0)

    def test_run_identify_orders_caches_and_stamps_relevance(self):
        source = FakeSource()
        queue = ListQueue()
        results = [
            {'title': 'Other Book', 'authors': ['Someone'], 'identifiers': {'isbn': '9780306406157', 'google': 'v1'},
             'cover_url': 'https://example.com/c.jpg'},
            {'title': 'Wanted Book', 'authors': ['Ann Author'], 'identifiers': {'isbn': '9780306406157'}},
        ]
        metakit.run_identify(source, lambda **kw: results, None, queue, Abort(),
                             title='Wanted Book', authors=['Ann Author'], identifiers={})
        self.assertEqual([mi.title for mi in queue.items], ['Wanted Book', 'Other Book'])
        self.assertEqual([mi.source_relevance for mi in queue.items], [0, 1])
        # The ambient Metadata class varies with the test environment (real Calibre, this
        # module's stub, or test_plugin_imports' stub, which stores identifiers as .ids).
        second = queue.items[1]
        self.assertEqual(getattr(second, 'identifiers', None) or getattr(second, 'ids', None),
                         {'isbn': '9780306406157', 'google': 'v1'})
        self.assertEqual(source.covers[(('google', 'v1'), ('isbn', '9780306406157'))], 'https://example.com/c.jpg')
        self.assertEqual(source.isbns[('9780306406157', 'google')], 'v1')

    def test_run_identify_swallows_search_errors(self):
        queue = ListQueue()

        def boom(**kw):
            raise OSError('site down')

        metakit.run_identify(FakeSource(), boom, None, queue, Abort(), title='X')
        self.assertEqual(queue.items, [])


# ---------------------------------------------------------------- metabods

MB_PRINT = ('<div class="xyp_section"><table><tr><td>logo</td></tr></table></div>'
            '<div class="xyp_section"><h1>Big &amp; Bold</h1><p class="xyp_noindent"><strong>by Some Author</strong></p></div>'
            '<div class="xyp_section"><h3>Part 1</h3><div class=" xyp_section_cols"><p>Text.</p></div></div>')
MB_STORY = ('<h5>by <a href="/mbxy/site/archive.php?list=author&id=5">Some Author</a></h5>'
            '<p>A summary &amp; more</p>'
            '<span class="fa-tags me-2"></span>'
            '<a href="/mbxy/site/archive.php?list=tag&id=9">Muscle</a>'
            '<a href="/mbxy/site/archive.php?list=category&id=2">M/M</a>')


class MetabodsSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_metabods', 'metabods')
        cls.core = importlib.import_module('ms_metabods.core')

    def fetcher(self):
        core = self.core
        return FakeFetcher({
            core.print_url('big-bold-1'): MB_PRINT,
            core.story_url('big-bold-1'): MB_STORY,
            core.SITE + 'archive.php?list=author': '<a href="/mbxy/site/archive.php?list=author&id=5">Some Author</a>',
            core.SITE + 'archive.php?list=author&id=5':
                '<td><a href="/mbxy/site/story.php?id=big-bold-1" >Big &amp; Bold</a> by '
                '<a href="?list=author&id=5">Some Author</a></td>',
        })

    def test_by_identifier(self):
        out = self.ms.search(identifiers={'metabods': 'big-bold-1'}, fetcher=self.fetcher())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['title'], 'Big & Bold')
        self.assertEqual(out[0]['authors'], ['Some Author'])
        self.assertEqual(out[0]['summary'], 'A summary & more')
        self.assertEqual(out[0]['identifiers'], {'metabods': 'big-bold-1'})
        self.assertIn('Muscle', out[0]['tags'])

    def test_by_url_identifier(self):
        out = self.ms.search(identifiers={'url': 'https://metabods.com/mbxy/site/story.php?id=big-bold-1'},
                             fetcher=self.fetcher())
        self.assertEqual([r['title'] for r in out], ['Big & Bold'])

    def test_by_author_and_title(self):
        out = self.ms.search(title='Big & Bold', authors=['Some Author'], fetcher=self.fetcher())
        self.assertEqual([r['identifiers'] for r in out], [{'metabods': 'big-bold-1'}])

    def test_nothing_to_go_on(self):
        self.assertEqual(self.ms.search(fetcher=self.fetcher()), [])


# ---------------------------------------------------------------- nifty

def nifty_panel(anchor, name, *entries):
    lis = '\n'.join(f'<li><a href="{href}">{title}</a>' for href, title in entries)
    return (f'<div id="{anchor}" class="panel panel-default"><div class="panel-heading">'
            f'<h4 class="panel-title">{name}</h4></div><div class="panel-body"><ul>\n{lis}\n</ul></div></div>')


NIFTY_AUTHORS = nifty_panel('writer', 'The Writer', ('/nifty/gay/college/lone-story', 'Lone Story'))
NIFTY_STORY = ('Date: Mon, 5 Jan 2026 10:00:00 +0000\nFrom: The Writer <writer@example.com>\n'
               'Subject: Lone Story\n\nOnly text.\n')


class NiftySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_nifty', 'nifty')
        cls.core = importlib.import_module('ms_nifty.core')

    def fetcher(self):
        core = self.core
        pages = {core.SITE + name: '' for name in ('prolific.html', 'eprolific.html')}
        pages[core.SITE + 'authors.html'] = NIFTY_AUTHORS
        pages[core.url_for('gay/college/lone-story')] = NIFTY_STORY
        return FakeFetcher(pages)

    def test_by_author_and_title(self):
        out = self.ms.search(title='Lone Story', authors=['The Writer'], fetcher=self.fetcher())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['title'], 'Lone Story')
        self.assertEqual(out[0]['authors'], ['The Writer'])
        self.assertEqual(out[0]['pubdate'], '2026-01-05')
        self.assertEqual(out[0]['identifiers'], {'nifty': 'gay/college/lone-story'})

    def test_by_identifier(self):
        out = self.ms.search(identifiers={'nifty': 'gay/college/lone-story'}, fetcher=self.fetcher())
        self.assertEqual([r['title'] for r in out], ['Lone Story'])


# ---------------------------------------------------------------- furry

IB_LOGIN = json.dumps({'sid': 'SID1'})
IB_SEARCH = json.dumps({'pages_count': 1, 'submissions': [
    {'submission_id': '42', 'title': 'A Tale', 'username': 'writer', 'hidden': 'f',
     'submission_type_id': '12', 'type_name': 'Writing - Document', 'create_datetime': '2026-01-02 10:00:00'}]})
IB_DETAILS = json.dumps({'submissions': [
    {'submission_id': '42', 'title': 'A Tale', 'username': 'writer', 'type_name': 'Writing - Document',
     'rating_name': 'General', 'create_datetime': '2026-01-02 10:00:00', 'description': 'A summary',
     'keywords': [{'keyword_name': 'tag1'}], 'files': []}]})


class FurryFetcher:
    def __init__(self):
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        if 'api_login.php' in url:
            return IB_LOGIN
        if 'api_search.php' in url:
            return IB_SEARCH
        if 'api_submissions.php' in url:
            return IB_DETAILS
        raise OSError('unexpected ' + url)


class FurrySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_furry', 'furry')
        cls.core = importlib.import_module('ms_furry.core')

    def test_inkbunny_search(self):
        out = self.ms.search(title='A Tale', fetcher=FurryFetcher(), settings={})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['authors'], ['writer'])
        self.assertEqual(out[0]['tags'], ['tag1', 'Writing - Document', 'General'])
        self.assertEqual(out[0]['identifiers'], {'inkbunny': '42'})
        self.assertEqual(out[0]['pubdate'], '2026-01-02')

    def test_inkbunny_by_identifier(self):
        out = self.ms.search(identifiers={'inkbunny': '42'}, fetcher=FurryFetcher(), settings={})
        self.assertEqual([r['title'] for r in out], ['A Tale'])

    def test_sofurry_by_identifier(self):
        original = self.core._sf_submission
        self.core._sf_submission = lambda fetcher, key: {
            'title': 'SF Tale', 'author': {'username': 'sfwriter'}, 'description': 'Desc',
            'tags': [{'name': 'sf-tag'}], 'rating': 0, 'publishedAt': '2026-02-03T10:00:00'}
        try:
            out = self.ms.search(identifiers={'sofurry': 'abc123'}, fetcher=FurryFetcher(), settings={})
        finally:
            self.core._sf_submission = original
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['authors'], ['sfwriter'])
        self.assertEqual(out[0]['identifiers'], {'sofurry': 'abc123'})
        self.assertEqual(out[0]['pubdate'], '2026-02-03')


# ---------------------------------------------------------------- reddit

sys.path.insert(0, str(ROOT / 'tests'))
from reddit_samples import entry, feed  # noqa: E402


class RedditSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_reddit', 'reddit')
        cls.core = importlib.import_module('ms_reddit.core')

    def source(self):
        page = feed(entry('a1', 'Cool Series 1', 'First part text', author='/u/storyteller'),
                    entry('a2', 'Cool Series 2', 'Second part text', author='/u/storyteller'),
                    entry('b1', 'Unrelated Post', 'Something else', author='/u/other'))

        class AnyFetcher:
            def get(self, url, **kwargs):
                return page

        return self.core.FeedSource(AnyFetcher())

    def test_series_search(self):
        out = self.ms.search(title='Cool Series', source=self.source())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['title'], 'Cool Series')
        self.assertEqual(out[0]['authors'], ['storyteller'])
        self.assertEqual(out[0]['series'], 'Cool Series')
        self.assertEqual(out[0]['series_index'], 1.0)
        self.assertIn('r/HFY', out[0]['tags'])


# ---------------------------------------------------------------- fanfic

try:
    import fanficfare  # noqa: F401
    HAVE_FFF = True
except ImportError:
    HAVE_FFF = False


@unittest.skipUnless(HAVE_FFF, 'FanFicFare not installed')
class FanficSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_fanfic', 'fanfic')

    def test_no_url_no_results(self):
        self.assertEqual(self.ms.search(title='Anything', authors=['Anyone']), [])

    def test_url_identifier_uses_metadata_pass(self):
        original = self.ms.fetch_story_metadata
        self.ms.fetch_story_metadata = lambda info, personal_ini='', allow_adult=False: {
            'title': 'A Fic', 'authors': ['Fic Author'], 'summary': 'Desc', 'tags': ['Drama'],
            'series': '', 'series_index': None, 'language': 'en', 'pubdate': '2024-05-06',
            'identifiers': {'url': info['url']}, 'url': info['url']}
        try:
            out = self.ms.search(identifiers={'url': 'https://archiveofourown.org/works/12345'},
                                 settings={'allow_adult': True})
        finally:
            self.ms.fetch_story_metadata = original
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['title'], 'A Fic')
        self.assertEqual(out[0]['identifiers'], {'url': 'https://archiveofourown.org/works/12345'})


# ---------------------------------------------------------------- media

MB = json.loads((ROOT / 'tests' / 'media_fixtures' / 'mb_yesterday_help.json').read_text(encoding='utf-8'))['recordings']
OL = json.loads((ROOT / 'tests' / 'media_fixtures' / 'ol_dune.json').read_text(encoding='utf-8'))['docs']


class ScriptedFetch:
    def __init__(self, routes):
        self.routes = routes

    def __call__(self, url, headers, data, timeout):
        for fragment, payload in self.routes.items():
            if fragment in url:
                return json.dumps(payload).encode()
        raise AssertionError('unrouted ' + url)


class MediaSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_media', 'media')
        cls.sources = importlib.import_module('ms_media.sources')

    def http(self):
        ticks = iter(range(0, 10 ** 6, 10))
        return self.sources.Http(fetch=ScriptedFetch({
            'musicbrainz.org/ws/2/recording': {'recordings': MB},
            'openlibrary.org/search.json': {'docs': OL}}),
            sleep=lambda seconds: None, clock=lambda: next(ticks))

    def test_music_search(self):
        out = self.ms.search(title='Yesterday', authors=['The Beatles'], http=self.http(), settings={})
        self.assertTrue(out)
        self.assertEqual(out[0]['title'], 'Yesterday')
        self.assertEqual(out[0]['series'], 'Help!')
        self.assertIn('musicbrainz', out[0]['identifiers'])
        self.assertIn('coverartarchive.org', out[0]['cover_url'])

    def test_book_search(self):
        out = self.ms.search(title='Dune', authors=['Frank Herbert'], http=self.http(),
                             settings={'kind': 'book'})
        self.assertTrue(out)
        self.assertEqual(out[0]['title'], 'Dune')
        self.assertEqual(out[0]['identifiers'].get('openlibrary'), 'OL893414W')
        self.assertIn('covers.openlibrary.org', out[0]['cover_url'])

    def test_isbn_identifier(self):
        out = self.ms.search(identifiers={'isbn': '9780140328721'}, http=self.http(), settings={'kind': 'book'})
        self.assertTrue(out)
        self.assertEqual(out[0]['identifiers'].get('isbn'), '9780140328721')


# ---------------------------------------------------------------- gemini

OL_API = json.dumps({'ISBN:9780306406157': {
    'title': 'A Book', 'authors': [{'name': 'Ann Author'}], 'publishers': [{'name': 'Press'}],
    'subjects': [{'name': 'Fiction'}], 'url': 'https://openlibrary.org/books/OL1M', 'description': 'Desc'}})


class GeminiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_gemini', 'gemini')
        cls.providers = importlib.import_module('ms_gemini.providers')
        cls.engine = importlib.import_module('ms_gemini.engine')

    def test_provider_enrichment_by_isbn(self):
        for attr in ('_default_fetcher', '_http_get'):
            if hasattr(self.providers, attr):
                setattr(self.providers, attr, lambda url, timeout=10: OL_API)
        out = self.ms.search(title='A Book', identifiers={'isbn': '9780306406157'},
                             settings={'provider_metadata': True})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['authors'], ['Ann Author'])
        self.assertEqual(out[0]['identifiers']['isbn'], '9780306406157')

    def test_gemini_changes_applied(self):
        original = self.engine.plan_fixes
        self.engine.plan_fixes = lambda books, key, **kw: (
            [{'id': 0, 'title': books[0]['title'],
              'changes': {'title': ('messy title', 'Clean Title'), 'tags': ([], ['Fiction']),
                          'isbn': ('', '9780306406157')}, 'confidence': 'high', 'reason': '', 'big': False}], [])
        try:
            out = self.ms.search(title='messy title', authors=['Ann'],
                                 settings={'provider_metadata': False, 'api_key': 'test-key'})
        finally:
            self.engine.plan_fixes = original
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['title'], 'Clean Title')
        self.assertEqual(out[0]['tags'], ['Fiction'])
        self.assertEqual(out[0]['identifiers']['isbn'], '9780306406157')

    def test_nothing_configured_nothing_found(self):
        out = self.ms.search(title='Whatever', settings={'provider_metadata': False})
        self.assertEqual(out, [])
        self.assertTrue(self.ms.is_configured({'provider_metadata': True}))
        self.assertFalse(self.ms.is_configured({'provider_metadata': False}))


# ---------------------------------------------------------------- takeout (Google Books)

GB_SEARCH = json.dumps({'items': [{'id': 'v1', 'volumeInfo': {
    'title': 'Sidecar Sample Title', 'authors': ['Sidecar Author'], 'publisher': 'Sample Press',
    'description': 'Recovered description', 'categories': ['Sample'], 'publishedDate': '2024-01-01',
    'language': 'en', 'industryIdentifiers': [{'type': 'ISBN_13', 'identifier': '978-0-306-40615-7'}],
    'imageLinks': {'thumbnail': 'http://books.google.com/thumb.jpg'},
    'infoLink': 'https://books.google.com/books?id=v1'}}]})


class TakeoutSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_takeout', 'plugin')

    def test_search_maps_volume_info_like_the_importer(self):
        class AnyPages(dict):
            def __missing__(self, key):
                return GB_SEARCH

        out = self.ms.search(title='Sidecar Sample Title', authors=['Sidecar Author'],
                             fetcher=FakeFetcher(AnyPages()))
        self.assertEqual(len(out), 1)
        found = out[0]
        self.assertEqual(found['authors'], ['Sidecar Author'])
        self.assertEqual(found['publisher'], 'Sample Press')
        self.assertEqual(found['tags'], ['Sample'])
        self.assertEqual(found['identifiers'], {'isbn': '9780306406157', 'google': 'v1'})
        self.assertEqual(found['cover_url'], 'https://books.google.com/thumb.jpg')

    def test_isbn_search(self):
        class AnyPages(dict):
            def __missing__(self, key):
                return GB_SEARCH

        fetcher = FakeFetcher(AnyPages())
        out = self.ms.search(identifiers={'isbn': '9780306406157'}, fetcher=fetcher)
        self.assertEqual(len(out), 1)
        from urllib.parse import unquote
        self.assertIn('isbn:9780306406157', unquote(fetcher.requested[0]))


# ---------------------------------------------------------------- tagger

class TaggerSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_tagger', 'tagger')

    def test_source_tag_from_identifier(self):
        out = self.ms.search(title='Some Story', identifiers={'metabods': 'abc'}, settings={})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['tags'], ['Source.Metabods'])
        self.assertEqual(out[0]['title'], 'Some Story')

    def test_keyword_rule(self):
        out = self.ms.search(title='A Dragon Tale', settings={'rules': 'title:dragon => Fantasy'})
        self.assertEqual(out[0]['tags'], ['Fantasy'])

    def test_no_title_no_tags(self):
        self.assertEqual(self.ms.search(identifiers={'metabods': 'abc'}, settings={}), [])


# ---------------------------------------------------------------- scrubber

class ScrubberSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ms = load_source('ms_scrubber', 'scrubber_plugin')

    def test_cleaned_title_returned(self):
        out = self.ms.search(title='Some Book - OceanofPDF.com', authors=['Ann'], settings={})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['title'], 'Some Book')
        self.assertEqual(out[0]['authors'], ['Ann'])

    def test_clean_title_returns_nothing(self):
        self.assertEqual(self.ms.search(title='A Perfectly Clean Title', settings={}), [])


# ---------------------------------------------------------------- companion classes

SOURCE_FOLDERS = {
    'plugin_source': ('plugin', 'ms_takeout'),
    'scrubber_source': ('scrubber_plugin', 'ms_scrubber'),
    'metabods_source': ('metabods', 'ms_metabods'),
    'nifty_source': ('nifty', 'ms_nifty'),
    'tagger_source': ('tagger', 'ms_tagger'),
    'fanfic_source': ('fanfic', 'ms_fanfic'),
    'reddit_source': ('reddit', 'ms_reddit'),
    'gemini_source': ('gemini', 'ms_gemini'),
    'media_source': ('media', 'ms_media'),
    'furry_source': ('furry', 'ms_furry'),
}


def plugin_version(folder):
    text = (ROOT / folder / '__init__.py').read_text(encoding='utf-8')
    return tuple(int(x) for x in re.search(r'(?m)^\s*version = \((\d+), (\d+), (\d+)\)', text).groups())


class CompanionClassTests(unittest.TestCase):
    """Each <plugin>_source/__init__.py defines exactly one Source subclass whose
    name, capabilities, touched fields and version agree with the plugin."""

    def setUp(self):
        self.saved = {k: v for k, v in sys.modules.items() if k.startswith(('calibre', 'qt'))}
        for name in ('calibre', 'calibre.customize', 'calibre.ebooks', 'calibre.ebooks.metadata',
                     'calibre.ebooks.metadata.sources', 'calibre.ebooks.metadata.sources.base',
                     'calibre.utils', 'calibre.utils.config'):
            sys.modules[name] = types.ModuleType(name)
        sys.modules['calibre.ebooks.metadata.sources.base'].Source = type('Source', (), {})
        sys.modules['calibre.utils.config'].JSONConfig = \
            lambda name: type('Prefs', (dict,), {'defaults': {}})()

    def tearDown(self):
        for key in [k for k in sys.modules if k.startswith(('calibre', 'qt'))]:
            del sys.modules[key]
        sys.modules.update(self.saved)

    def test_companions_agree_with_plugins(self):
        import build_plugins
        self.assertEqual({v[0] for v in build_plugins.SOURCES.values()}, set(SOURCE_FOLDERS))
        for source_folder, (folder, alias) in SOURCE_FOLDERS.items():
            with self.subTest(source=source_folder):
                ms = load_source(alias, folder)
                spec = importlib.util.spec_from_file_location(f'companion_{source_folder}',
                                                              ROOT / source_folder / '__init__.py')
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                classes = [v for v in vars(module).values()
                           if isinstance(v, type) and v.__module__ == module.__name__]
                self.assertEqual(len(classes), 1)
                cls = classes[0]
                self.assertEqual(cls.name, ms.SOURCE_NAME)
                self.assertEqual(cls.capabilities, ms.CAPABILITIES)
                self.assertEqual(cls.touched_fields, ms.TOUCHED_FIELDS)
                self.assertEqual(cls.version, plugin_version(folder))
                self.assertFalse(hasattr(cls, 'actual_plugin'))


if __name__ == '__main__':
    unittest.main()
