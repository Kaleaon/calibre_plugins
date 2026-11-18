"""Metadata-source side of the Reddit Story Follower: answers Calibre's
identify requests using the follower's own feed/API readers (see core.py).

Reddit is searched the way the follower discovers stories: a site-wide
search feed for the title (or an author's submitted-posts feed when only an
author is known), grouped into series and single stories by
core.group_series. Each group becomes one result — a series group carries
the series name and first part number, a single story its post title. Reads
use the mode and credentials saved in the follower's settings (public Atom
feeds by default, the official API when configured).

The companion ZIP (reddit_source/) wraps this module in a Calibre Source.
"""
from datetime import datetime, timezone

try:
    from . import core
except ImportError:  # tests load this file with the folder and common/ on sys.path
    import core

try:
    from . import metakit
except ImportError:
    import metakit

SOURCE_NAME = 'Reddit Stories'
CAPABILITIES = frozenset({'identify'})
TOUCHED_FIELDS = frozenset({'title', 'authors', 'tags', 'series', 'pubdate', 'publisher', 'identifier:url'})


def _group_result(group):
    posts = group.get('posts') or []
    first = posts[0] if posts else {}
    tags = ([f"r/{first['subreddit']}"] if first.get('subreddit') else []) + sorted(group.get('flairs') or [])
    result = {
        'title': group.get('name') or first.get('title') or '',
        'authors': list(group.get('authors') or []),
        'publisher': 'Reddit',
        'tags': [t for t in tags if t],
        'language': 'eng',
        'identifiers': {'url': first['link']} if first.get('link') else {},
        'url': first.get('link') or '',
    }
    if first.get('created'):
        result['pubdate'] = datetime.fromtimestamp(first['created'], tz=timezone.utc).date().isoformat()
    if group.get('is_series'):
        result['series'] = group['name']
        numbers = sorted(n for n in (group.get('numbers') or []) if n is not None)
        result['series_index'] = float(numbers[0]) if numbers else 1.0
        result['summary'] = 'Series found on Reddit: ' + ', '.join(
            p.get('title') or '' for p in posts[:20])
    else:
        result['title'] = first.get('title') or result['title']
    return result


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, source=None, settings=None):
    settings = settings or {}
    if source is None:
        # Positional on purpose: make_source(mode, client_id, client_secret, username, cancelled, refresh_token)
        source = core.make_source(settings.get('mode', 'rss'),
                                  settings.get('client_id', ''),
                                  settings.get('client_secret', ''),
                                  settings.get('username', ''),
                                  abort.is_set if abort is not None else lambda: False,
                                  settings.get('refresh_token', ''))
    groups = []
    if title:
        stem, _number = core.series_parts(title)
        query = stem or title
        if authors and all(' ' not in a for a in authors):
            query = f'{query} author:{authors[0]}'
        src = {'kind': 'search', 'subreddit': '', 'user': '', 'query': query, 'sort': 'relevance'}
        groups = core.discover(source, src, pages=2)
    elif authors:
        for author in core.parse_authors(authors):
            src = {'kind': 'user', 'subreddit': '', 'user': author, 'query': ''}
            groups += core.discover(source, src, pages=1)
    results = []
    for group in groups:
        result = _group_result(group)
        if not result['title']:
            continue
        if title and not group.get('is_series') and metakit.similarity(title, result['title']) < 0.5:
            continue
        if authors and result['authors'] and metakit.author_similarity(authors, result['authors']) < 0.4 \
                and not (title and group.get('is_series')):
            continue
        results.append(result)
    return results[:10]


def book_url(identifiers):
    url = (identifiers or {}).get('url') or ''
    if 'reddit.com' in url:
        return 'url', url, url
    return None


def id_from_url(url):
    if url and 'reddit.com' in url:
        return 'url', url
    return None
