"""Metadata-source side of Media Matcher: answers Calibre's identify
requests using the plugin's own service clients and scoring (sources.py,
matching.py) — MusicBrainz + Cover Art Archive for music-shaped queries and
Open Library for books and audiobooks.

The plugin's manual_search already turns a title/artist/album or a direct
identifier (MusicBrainz recording/release id, Open Library id, ISBN) into
scored candidates; this module maps those candidates onto Calibre results:
album becomes series with the track number as series index for music, and
the first listed cover URL is cached for Calibre's cover download.

The companion ZIP (media_source/) wraps this module in a Calibre Source.
"""
try:
    from . import matching, sources
except ImportError:  # tests load this file with the folder on sys.path
    import matching
    import sources

SOURCE_NAME = 'Media Matcher Lookup'
CAPABILITIES = frozenset({'identify', 'cover'})
TOUCHED_FIELDS = frozenset({'title', 'authors', 'tags', 'series', 'pubdate',
                            'identifier:isbn', 'identifier:musicbrainz', 'identifier:openlibrary'})
COVER_FIELDS = frozenset()  # covers arrive through the cached cover URL, not a field


def _from_candidate(cand):
    result = {
        'title': cand.get('title') or '',
        'authors': list(cand.get('authors') or []),
        'tags': list(cand.get('subjects') or []),
        'pubdate': cand.get('date') or '',
        'score': cand.get('score'),
        'identifiers': {},
    }
    if cand.get('album'):
        result['series'] = cand['album']
        if cand.get('track'):
            result['series_index'] = float(cand['track'])
    if cand.get('recording_id'):
        result['identifiers']['musicbrainz'] = cand['recording_id']
        result['url'] = f"https://musicbrainz.org/recording/{cand['recording_id']}"
    if cand.get('work'):
        result['identifiers']['openlibrary'] = cand['work']
        result['url'] = f"https://openlibrary.org/works/{cand['work']}"
    covers = cand.get('cover_urls') or []
    if covers:
        result['cover_url'] = covers[0]
    return result


def _direct_id(identifiers):
    identifiers = identifiers or {}
    for key in ('musicbrainz', 'musicbrainz_recording', 'mbid'):
        if identifiers.get(key):
            return identifiers[key]
    for key in ('openlibrary', 'olid'):
        if identifiers.get(key):
            return f"olid:{identifiers[key]}"
    if identifiers.get('isbn'):
        return f"isbn:{identifiers['isbn']}"
    return ''


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, http=None, settings=None):
    settings = settings or {}
    if http is None:
        http = sources.Http(contact=settings.get('contact', ''),
                            cancelled=abort.is_set if abort is not None else lambda: False)
    artist = ' & '.join(authors or [])
    direct = _direct_id(identifiers)
    candidates = matching.manual_search(http, options=settings, title=title or '', artist=artist,
                                        direct_id=direct, media_kind=settings.get('kind', 'auto'))
    results = [_from_candidate(c) for c in candidates]
    if direct and (identifiers or {}).get('isbn'):
        for result in results:
            result['identifiers'].setdefault('isbn', identifiers['isbn'])
    return results


def book_url(identifiers):
    identifiers = identifiers or {}
    if identifiers.get('musicbrainz'):
        return 'musicbrainz', identifiers['musicbrainz'], \
            f"https://musicbrainz.org/recording/{identifiers['musicbrainz']}"
    if identifiers.get('openlibrary'):
        return 'openlibrary', identifiers['openlibrary'], \
            f"https://openlibrary.org/works/{identifiers['openlibrary']}"
    return None


def id_from_url(url):
    url = str(url or '')
    for host, key in (('musicbrainz.org/recording/', 'musicbrainz'), ('openlibrary.org/works/', 'openlibrary')):
        if host in url:
            return key, url.split(host, 1)[1].split('/')[0].split('?')[0]
    return None
