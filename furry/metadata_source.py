"""Metadata-source side of the Inkbunny & SoFurry Downloader: answers
Calibre's identify requests using the same site clients as the downloader
(see core.py).

- Inkbunny: the official API search (the downloader's own ib_expand_search)
  by title, plus direct lookup by 'inkbunny' submission id. Uses the login
  saved in the downloader's settings, or the guest account when none is set.
- SoFurry: no site search endpoint exists in the downloader, so identify is
  by 'sofurry' submission id / story URL, plus an author-gallery pass when an
  author name is given (the gallery handle is tried as the author name with
  spaces removed). Reads carry the saved SoFurry token when one is set.

The companion ZIP (furry_source/) wraps this module in a Calibre Source.
"""
import html as html_module

try:
    from . import core
except ImportError:  # tests load this file with the folder and common/ on sys.path
    import core

try:
    from . import metakit
except ImportError:
    import metakit

SOURCE_NAME = 'Inkbunny & SoFurry'
CAPABILITIES = frozenset({'identify'})
TOUCHED_FIELDS = frozenset({'title', 'authors', 'tags', 'comments', 'pubdate', 'publisher',
                            'identifier:inkbunny', 'identifier:sofurry'})


def _ib_result(sub):
    keywords = [html_module.unescape(k.get('keyword_name') or '') for k in sub.get('keywords') or []]
    return {
        'title': html_module.unescape(sub.get('title') or ''),
        'authors': [sub.get('username')] if sub.get('username') else [],
        'publisher': core.PUBLISHERS[core.INKBUNNY],
        'tags': [k for k in keywords if k] + [c for c in (sub.get('type_name'), sub.get('rating_name')) if c],
        'summary': core.strip_tags(sub.get('description') or ''),
        'language': 'eng',
        'pubdate': core._date_only(sub.get('create_datetime')),
        'identifiers': {'inkbunny': str(sub.get('submission_id'))},
        'url': core.story_url(core.INKBUNNY, sub.get('submission_id')),
    }


def _sf_result(sub, key):
    author = sub.get('author') or {}
    return {
        'title': sub.get('title') or '',
        'authors': [author.get('username')] if author.get('username') else [],
        'publisher': core.PUBLISHERS[core.SOFURRY],
        'tags': core._sf_tags(sub),
        'summary': core.strip_tags(sub.get('description') or ''),
        'language': 'eng',
        'pubdate': core._date_only(sub.get('publishedAt')),
        'identifiers': {'sofurry': str(key)},
        'url': core.story_url(core.SOFURRY, key),
    }


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, fetcher=None, settings=None):
    identifiers = identifiers or {}
    settings = settings or {}
    if fetcher is None:
        fetcher = core.Fetcher(cancelled=abort.is_set if abort is not None else lambda: False)
    username = settings.get('inkbunny_username', '')
    ib_pass = settings.get('inkbunny_password', '')
    sf_tok = settings.get('sofurry_token', '')
    results = []

    ib_id = identifiers.get('inkbunny') or ''
    sf_id = identifiers.get('sofurry') or ''
    url = identifiers.get('url') or ''
    if url and not ib_id and not sf_id:
        found = core.classify(url)
        if found and found.get('kind') == 'story':
            if found['site'] == core.INKBUNNY:
                ib_id = found['key']
            elif found['site'] == core.SOFURRY:
                sf_id = found['key']

    if ib_id:
        sid = core.ib_session(fetcher, username, ib_pass)
        for sub in core.ib_details(fetcher, sid, [ib_id]):
            if core._ib_is_writing(sub):
                results.append(_ib_result(sub))
        return results
    if sf_id:
        read = core.authed_fetcher(fetcher, sf_tok)
        results.append(_sf_result(core._sf_submission(read, sf_id), sf_id))
        return results

    if title:
        sid = core.ib_session(fetcher, username, ib_pass)
        refs = core.ib_expand_search(fetcher, sid, title, lambda msg: None)
        refs = [r for r in refs if metakit.similarity(title, r.get('title') or '') >= 0.5]
        if authors:
            refs = [r for r in refs
                    if not r.get('author') or metakit.author_similarity(authors, [r['author']]) >= 0.4] or refs
        ids = [r['key'] for r in refs[:10]]
        if ids:
            for sub in core.ib_details(fetcher, sid, ids):
                if core._ib_is_writing(sub):
                    results.append(_ib_result(sub))
    if authors and not results:
        # SoFurry has no search; an author's gallery is the closest index.
        read = core.authed_fetcher(fetcher, sf_tok)
        for author in authors:
            handle = ''.join(author.split()).lower()
            if not handle:
                continue
            try:
                refs = core.sf_expand_author(read, handle, lambda msg: None)
            except Exception:
                continue
            for ref in refs:
                if title and metakit.similarity(title, ref.get('title') or '') < 0.5:
                    continue
                results.append({
                    'title': ref.get('title') or '',
                    'authors': [author],
                    'publisher': core.PUBLISHERS[core.SOFURRY],
                    'tags': list(ref.get('tags') or []),
                    'language': 'eng',
                    'pubdate': ref.get('date') or '',
                    'identifiers': {'sofurry': str(ref['key'])},
                    'url': core.story_url(core.SOFURRY, ref['key']),
                })
            if results:
                break
    return results


def book_url(identifiers):
    identifiers = identifiers or {}
    if identifiers.get('inkbunny'):
        return 'inkbunny', identifiers['inkbunny'], core.story_url(core.INKBUNNY, identifiers['inkbunny'])
    if identifiers.get('sofurry'):
        return 'sofurry', identifiers['sofurry'], core.story_url(core.SOFURRY, identifiers['sofurry'])
    return None


def id_from_url(url):
    found = core.classify(url or '')
    if found and found.get('kind') == 'story':
        return ('inkbunny' if found['site'] == core.INKBUNNY else 'sofurry'), found['key']
    return None
