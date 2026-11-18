"""Metadata-source side of the Fanfic Site Downloader: answers Calibre's
identify requests through FanFicFare's site adapters (see engine.py).

Most fanfic sites have no title search an adapter can use, so identify is
URL-driven: when the book carries a 'url' identifier (or a site identifier
whose value is a URL) that a FanFicFare adapter recognises, the adapter's
metadata-only pass fills in title, authors, description, tags, series,
language and dates — the same metadata the downloader writes into its
EPUBs, without downloading the story text. The downloader's settings apply:
adult sites stay off unless "Allow adult sites" is on there, and the
advanced (personal.ini) settings are honoured.

The companion ZIP (fanfic_source/) wraps this module in a Calibre Source.
"""
import configparser
from io import StringIO

try:
    from . import engine
except ImportError:  # tests load this file with the folder on sys.path
    import engine

SOURCE_NAME = 'FanFicFare Sites'
CAPABILITIES = frozenset({'identify', 'cover'})
TOUCHED_FIELDS = frozenset({'title', 'authors', 'tags', 'comments', 'series', 'language',
                            'pubdate', 'identifier:url'})


def fetch_story_metadata(info, personal_ini='', allow_adult=False):
    """FanFicFare metadata for one story URL, without downloading its text.

    Mirrors engine.download_story's setup, but stops after the adapter's
    metadata pass instead of writing an EPUB.
    """
    ns = engine.load()
    cfg = ns.Configuration(ns.adapters.getConfigSectionsFor(info['url']), 'epub')
    cfg.read_file(StringIO(ns.defaults_ini))
    if personal_ini.strip():
        cfg.read_file(StringIO(personal_ini))
    try:
        cfg.add_section('overrides')
    except configparser.DuplicateSectionError:
        pass
    cfg.set('overrides', 'is_adult', 'true' if allow_adult else 'false')
    adapter = ns.adapters.getAdapter(cfg, info['url'])
    adapter.setChaptersRange(info.get('begin'), info.get('end'))
    for _ in range(3):  # FanFicFare asks for the adult confirmation by raising, then expects a retry
        try:
            adapter.getStoryMetadataOnly()
            break
        except ns.exceptions.AdultCheckRequired:
            if not allow_adult:
                raise
            adapter.is_adult = True
    story = adapter.story
    series, index = engine.split_series(story.getMetadata('series'))
    result = {
        'title': story.getMetadata('title') or '',
        'authors': [a for a in story.getList('author') if a],
        'summary': story.getMetadata('description') or '',
        'tags': story.getSubjectTags(removeallentities=True),
        'series': series,
        'series_index': index,
        'language': story.getMetadata('language') or '',
        'pubdate': story.getMetadata('datePublished') or '',
        'identifiers': {'url': story.getMetadata('storyUrl') or info['url']},
        'url': story.getMetadata('storyUrl') or info['url'],
    }
    try:
        cover = story.getMetadata('cover_image')
    except Exception:
        cover = None
    if cover:
        result['cover_url'] = cover
    return result


def _candidate_urls(identifiers):
    urls = []
    for key, value in (identifiers or {}).items():
        value = str(value or '').strip()
        if value.startswith(('http://', 'https://')):
            urls.append(value)
    return urls


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, settings=None):
    settings = settings or {}
    allow_adult = bool(settings.get('allow_adult'))
    extra_adult = engine.parse_extra_adult(settings.get('extra_adult', ''))
    personal_ini = settings.get('advanced_ini', '')
    results = []
    for url in _candidate_urls(identifiers):
        try:
            info = engine.inspect_url(url, allow_adult=allow_adult, extra_adult=extra_adult)
        except engine.EngineError:
            continue
        found = fetch_story_metadata(info, personal_ini=personal_ini, allow_adult=allow_adult)
        if found.get('title'):
            results.append(found)
        if abort is not None and abort.is_set():
            break
    return results


def book_url(identifiers):
    urls = _candidate_urls(identifiers)
    if urls:
        return 'url', urls[0], urls[0]
    return None


def id_from_url(url):
    if url and str(url).startswith(('http://', 'https://')):
        try:
            engine.inspect_url(url, allow_adult=True)
        except Exception:
            return None
        return 'url', url
    return None
