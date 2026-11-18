"""Metadata-source side of the Takeout Book Fixer: answers Calibre's
identify requests from the Google Books catalogue.

This is the same metadata the fixer recovers from Google Takeout sidecars —
a Takeout sidecar JSON *is* a Google Books volumeInfo record — so the field
mapping here deliberately mirrors importer.py's sidecar mapping: title,
authors, publisher, description, language, categories as tags, published
date, and ISBNs from industryIdentifiers. Covers come from the volume's
image links and are cached for Calibre's cover download.

The companion ZIP (plugin_source/) wraps this module in a Calibre Source.
"""
import json
from urllib.parse import urlencode

try:  # inside Calibre the shared module ships in the plugin package
    from .storykit import Fetcher
except ImportError:  # tests load this file with common/ on sys.path
    from storykit import Fetcher

SOURCE_NAME = 'Takeout Google Books'
API_ROOT = 'https://www.googleapis.com/books/v1/volumes'
CAPABILITIES = frozenset({'identify', 'cover'})
TOUCHED_FIELDS = frozenset({'title', 'authors', 'tags', 'comments', 'publisher', 'pubdate',
                            'language', 'identifier:isbn', 'identifier:google'})


def _clean_isbn(value):
    return ''.join(c for c in str(value or '') if c.isdigit() or c in 'Xx').upper()


def from_volume(volume):
    """One Google Books volume record -> a metakit result dict."""
    info = volume.get('volumeInfo') or {}
    identifiers = {}
    for entry in info.get('industryIdentifiers') or []:
        if str(entry.get('type', '')).startswith('ISBN') and entry.get('identifier'):
            identifiers['isbn'] = _clean_isbn(entry['identifier'])
    if volume.get('id'):
        identifiers['google'] = volume['id']
    result = {
        'title': info.get('title') or '',
        'authors': list(info.get('authors') or []),
        'publisher': info.get('publisher') or '',
        'tags': list(info.get('categories') or []),
        'summary': info.get('description') or '',
        'language': info.get('language') or '',
        'pubdate': info.get('publishedDate') or '',
        'identifiers': identifiers,
        'url': info.get('infoLink') or info.get('canonicalVolumeLink') or '',
    }
    images = info.get('imageLinks') or {}
    cover = images.get('thumbnail') or images.get('smallThumbnail') or ''
    if cover:
        result['cover_url'] = cover.replace('http://', 'https://')
    return result


def _get_json(fetcher, url):
    return json.loads(fetcher.get(url))


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, fetcher=None):
    identifiers = identifiers or {}
    if fetcher is None:
        fetcher = Fetcher(cancelled=abort.is_set if abort is not None else lambda: False)
    if identifiers.get('google'):
        data = _get_json(fetcher, f"{API_ROOT}/{identifiers['google']}")
        found = from_volume(data)
        return [found] if found.get('title') else []
    terms = []
    if identifiers.get('isbn'):
        terms.append(f"isbn:{_clean_isbn(identifiers['isbn'])}")
    if title:
        terms.append(f'intitle:"{title}"')
    for author in authors or []:
        terms.append(f'inauthor:"{author}"')
    if not terms:
        return []
    url = f"{API_ROOT}?{urlencode({'q': ' '.join(terms), 'maxResults': 10})}"
    data = _get_json(fetcher, url)
    return [found for v in data.get('items') or [] if (found := from_volume(v)).get('title')]


def book_url(identifiers):
    volume_id = (identifiers or {}).get('google')
    if volume_id:
        return 'google', volume_id, f'https://books.google.com/books?id={volume_id}'
    return None


def id_from_url(url):
    from urllib.parse import parse_qs, urlparse
    query = parse_qs(urlparse(str(url or '')).query)
    if query.get('id'):
        return 'google', query['id'][0]
    return None
