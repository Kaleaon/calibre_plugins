"""Metadata-source side of the Metabods Downloader: answers Calibre's identify
requests using the same parsers as the downloader (see core.py).

Metabods has no title search, so identify works two ways:

- by identifier: a 'metabods' story id (or a story URL in the 'url'
  identifier) is fetched in full — title, author, tags, categories, summary;
- by author: the site's author index is read to find the author's id, their
  story list is read, and titles are matched against the query.

The companion ZIP (metabods_source/) wraps this module in a Calibre Source.
"""
import re

try:
    from . import core
except ImportError:  # tests load this file with the folder and common/ on sys.path
    import core

try:
    from . import metakit
except ImportError:
    import metakit

SOURCE_NAME = 'Metabods'
IDENTIFIER = 'metabods'
CAPABILITIES = frozenset({'identify'})
TOUCHED_FIELDS = frozenset({'title', 'authors', 'tags', 'comments', 'publisher', 'identifier:metabods'})

_AUTHOR_LINK = re.compile(r'href="[^"]*archive\.php\?list=author&(?:amp;)?id=(\d+)"[^>]*>(.*?)</a>', re.S)


def _from_story(story):
    return {
        'title': story.get('title') or story['id'],
        'authors': [story['author']] if story.get('author') else [],
        'publisher': core.PUBLISHER,
        'tags': list(story.get('tags') or []) + list(story.get('categories') or []),
        'summary': story.get('summary') or '',
        'language': 'eng',
        'identifiers': {IDENTIFIER: story['id']},
        'url': story.get('url') or core.story_url(story['id']),
    }


def _author_id(fetcher, author_name):
    """The site's numeric author id for a name, from the author index page, or None."""
    try:
        page = fetcher.get(core.SITE + 'archive.php?list=author')
    except Exception:
        return None
    wanted = metakit.normalize(author_name)
    for author_id, raw in _AUTHOR_LINK.findall(page):
        name = core.strip_tags(raw).strip()
        if name and metakit.normalize(name) == wanted:
            return author_id
    return None


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, fetcher=None):
    identifiers = identifiers or {}
    if fetcher is None:
        fetcher = core.Fetcher(cancelled=abort.is_set if abort is not None else lambda: False)
    story_id = identifiers.get(IDENTIFIER) or ''
    if not story_id and identifiers.get('url'):
        found = core.classify(identifiers['url'])
        if found and found[0] == 'story':
            story_id = found[1]
    if story_id:
        return [_from_story(core.fetch_story(fetcher, story_id))]
    if authors:
        results = []
        for author in authors:
            author_id = _author_id(fetcher, author)
            if author_id is None:
                continue
            rows = core.parse_story_rows(fetcher.get(f'{core.SITE}archive.php?list=author&id={author_id}'))
            for row in rows:
                if title and metakit.similarity(title, row.get('title') or '') < 0.5:
                    continue
                results.append({
                    'title': row.get('title') or row['id'],
                    'authors': [row.get('author') or author],
                    'publisher': core.PUBLISHER,
                    'language': 'eng',
                    'identifiers': {IDENTIFIER: row['id']},
                    'url': core.story_url(row['id']),
                })
        return results
    return []


def book_url(identifiers):
    story_id = (identifiers or {}).get(IDENTIFIER)
    if story_id:
        return IDENTIFIER, story_id, core.story_url(story_id)
    return None


def id_from_url(url):
    found = core.classify(url or '')
    if found and found[0] == 'story':
        return IDENTIFIER, found[1]
    return None
