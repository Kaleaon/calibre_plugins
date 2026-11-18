"""Metadata-source side of the Nifty Downloader: answers Calibre's identify
requests using the same archive readers as the downloader (see core.py).

Nifty has no site-wide search; its author directory is the searchable index.
Identify works:

- by identifier: a 'nifty' archive path (or a story URL in the 'url'
  identifier) is resolved and its first chapter read for the real title,
  author and date;
- by author/title: the author directory is read, the author found by name,
  and their stories matched against the query title with core.match_title
  (plus a similarity pass for near matches). Only the first chapter of a
  matched story is read, which carries the title/author/date headers.

The companion ZIP (nifty_source/) wraps this module in a Calibre Source.
"""
try:
    from . import core
except ImportError:  # tests load this file with the folder and common/ on sys.path
    import core

try:
    from . import metakit
except ImportError:
    import metakit

SOURCE_NAME = 'Nifty'
IDENTIFIER = 'nifty'
CAPABILITIES = frozenset({'identify'})
TOUCHED_FIELDS = frozenset({'title', 'authors', 'tags', 'pubdate', 'publisher', 'identifier:nifty'})


def _from_story(story):
    return {
        'title': story.get('title') or story['id'],
        'authors': [story['author']] if story.get('author') else [],
        'publisher': core.PUBLISHER,
        'tags': list(story.get('tags') or []),
        'summary': story.get('summary') or '',
        'language': 'eng',
        'pubdate': story.get('pubdate') or '',
        'identifiers': {IDENTIFIER: story['id']},
        'url': story.get('url') or core.url_for(story['id']),
    }


def _fetch_metadata_for_ref(fetcher, ref):
    """Resolve a story reference and read only its first chapter for metadata."""
    resolved = core.resolve_ref(fetcher, ref)
    if not resolved:
        return None
    first = dict(resolved[0])
    if first.get('files'):
        first['files'] = first['files'][:1]
    return _from_story(core.fetch_book(fetcher, first))


def _ref_for_story(story):
    return {'id': story['path'], 'title': story.get('title') or '', 'author': '',
            'date': '', 'dir': story['path'] if story.get('dir') else None,
            'files': None if story.get('dir') else [story['path']], 'detail': ''}


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, fetcher=None):
    identifiers = identifiers or {}
    if fetcher is None:
        fetcher = core.Fetcher(cancelled=abort.is_set if abort is not None else lambda: False)
    path = ''
    raw = identifiers.get(IDENTIFIER) or identifiers.get('url') or ''
    if raw:
        # The 'nifty' identifier stores the bare archive path; a URL classifies down to it.
        path = core.classify(raw) or ('' if '://' in raw else raw.strip('/'))
    if path:
        refs, _errors = core.expand_targets(fetcher, [core.url_for(path)])
        results = []
        for ref in refs[:5]:
            found = _fetch_metadata_for_ref(fetcher, ref)
            if found:
                results.append(found)
        return results
    if not (title or authors):
        return []
    results = []
    for author in core.load_authors(fetcher):
        if authors and metakit.author_similarity(authors, [author['name']]) < 0.6:
            continue
        stories = author.get('stories') or []
        matched = []
        if title:
            hit = core.match_title(title, stories)
            if hit:
                matched.append(hit)
            matched += [s for s in stories
                        if s not in matched and metakit.similarity(title, s.get('title') or '') >= 0.75]
        else:
            matched = stories[:10]
        for story in matched[:5]:
            found = _fetch_metadata_for_ref(fetcher, _ref_for_story(story))
            if found:
                if not found['authors']:
                    found['authors'] = [author['name']]
                results.append(found)
        if results and authors:
            break  # the named author's matches are the answer; other authors only add noise
    return results


def book_url(identifiers):
    path = (identifiers or {}).get(IDENTIFIER)
    if path:
        return IDENTIFIER, path, core.url_for(path)
    return None


def id_from_url(url):
    path = core.classify(url or '')
    if path:
        return IDENTIFIER, path
    return None
