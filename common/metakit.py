"""Shared helpers for the metadata-source companions of the plugins in this repo.

Calibre registers exactly one plugin per ZIP (the first Plugin subclass in its
``__init__.py``), so every downloader/fixer plugin that also wants to answer
Calibre's "Download metadata" (identify) requests ships a second, small ZIP:
a ``Source`` subclass whose ``__init__.py`` lives in a ``<plugin>_source``
folder and which delegates all real work to a ``metadata_source`` module in
the original plugin folder. This module is the shared plumbing between the
two: result normalisation, relevance scoring and the identify/cover loops.

A metadata search returns a list of plain dicts, so the search logic can be
tested outside Calibre with fake fetchers:

    {'title': str, 'authors': [str, ...], 'publisher': str,
     'tags': [str, ...], 'summary': str,            # plain text or HTML
     'series': str, 'series_index': float,
     'language': 'eng',                              # ISO 639-2/T code
     'pubdate': 'YYYY-MM-DD' or a datetime,
     'identifiers': {'isbn': '...', '<site>': '...'},
     'cover_url': str, 'url': str,
     'score': float}                                 # optional, 0..1

``run_identify`` turns those dicts into Calibre ``Metadata`` objects, in
descending score order, and caches cover/ISBN mappings on the source the way
Calibre's own sources do.
"""
import difflib
import re
import unicodedata
from datetime import datetime, timezone

GENERIC_WORDS = {'the', 'a', 'an', 'of', 'and', 'in', 'to', 'on', 'for', 'with', 'at', 'by'}


def normalize(text):
    """Lowercase, accent-folded, punctuation-free form used for comparisons."""
    text = unicodedata.normalize('NFKD', str(text or ''))
    text = ''.join(c for c in text if not unicodedata.combining(c)).lower().replace('&', ' and ')
    text = re.sub(r'^(.+?),\s*(the|a|an)$', r'\2 \1', text.strip())
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', text)).strip()


def tokens(text):
    return [t for t in normalize(text).split() if t not in GENERIC_WORDS]


def _drop_article(text):
    return re.sub(r'^(the|a|an)\s+', '', text)


def similarity(a, b):
    """0..1 closeness of two titles or names: sequence ratio blended with token overlap."""
    na, nb = _drop_article(normalize(a)), _drop_article(normalize(b))
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    overlap = len(ta & tb) / max(len(ta | tb), 1)
    return ratio * 0.6 + overlap * 0.4


def author_similarity(query_authors, cand_authors):
    """Best pairwise author similarity; 0 when either side names nobody."""
    if not query_authors or not cand_authors:
        return 0.0
    return max(similarity(q, c) for q in query_authors for c in cand_authors)


def score_candidate(query_title, query_authors, cand):
    """How well one result dict answers an identify query (0..1).

    An explicit 'score' from the search itself wins; otherwise the title
    carries most of the weight and the authors the rest. With no query title
    (an identifier-driven lookup) the result is trusted as-is.
    """
    if cand.get('score') is not None:
        try:
            return max(0.0, min(1.0, float(cand['score'])))
        except (TypeError, ValueError):
            pass
    if not query_title:
        return 1.0
    title_score = similarity(query_title, cand.get('title') or '')
    if query_authors and cand.get('authors'):
        return title_score * 0.7 + author_similarity(query_authors, cand['authors']) * 0.3
    return title_score


def _parse_pubdate(value):
    if value is None or isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        from calibre.utils.date import parse_date
        return parse_date(text, assume_utc=True)
    except Exception:
        pass
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def make_metadata(result):
    """A Calibre Metadata object (or the test stub) filled from a result dict."""
    try:
        from calibre.ebooks.metadata.book.base import Metadata
    except ImportError:
        class Metadata:  # minimal stand-in for tests outside Calibre
            def __init__(self, title, authors):
                self.title, self.authors = title, authors
                self.publisher = None
                self.languages = []
                self.tags = []
                self.comments = None
                self.series = None
                self.series_index = None
                self.pubdate = None
                self.identifiers = {}

            def set_identifier(self, key, value):
                self.identifiers[key] = value

    mi = Metadata(result.get('title') or 'Unknown', list(result.get('authors') or ['Unknown']))
    if result.get('publisher'):
        mi.publisher = result['publisher']
    if result.get('tags'):
        mi.tags = list(dict.fromkeys(str(t) for t in result['tags'] if t))
    summary = result.get('summary') or result.get('comments') or ''
    if summary:
        mi.comments = summary if '<' in summary else f'<p>{summary}</p>'
    if result.get('series'):
        mi.series = result['series']
        try:
            mi.series_index = float(result.get('series_index') or 1.0)
        except (TypeError, ValueError):
            mi.series_index = 1.0
    if result.get('language'):
        mi.languages = [result['language']]
    pubdate = _parse_pubdate(result.get('pubdate'))
    if pubdate is not None:
        mi.pubdate = pubdate
    for key, value in (result.get('identifiers') or {}).items():
        if value:
            mi.set_identifier(str(key), str(value))
    return mi


def run_identify(source, search, log, result_queue, abort, title=None, authors=None, identifiers=None, timeout=30):
    """The identify() body every companion source shares.

    ``search`` is the plugin's metadata_source.search callable. Results are
    scored, sorted best-first, given Calibre's source_relevance numbering and
    queued. Cover and ISBN mappings are cached on the source first, as
    Calibre's download_cover relies on them.
    """
    identifiers = dict(identifiers or {})
    try:
        results = search(title=title, authors=authors, identifiers=identifiers, timeout=timeout, abort=abort) or []
    except Exception as exc:
        if log is not None:
            log.exception(f'{source.name}: metadata search failed')
            log.error(f'{source.name}: {exc}')
        return
    scored = [(score_candidate(title, authors, r), r) for r in results if r and r.get('title')]
    scored.sort(key=lambda pair: -pair[0])
    for relevance, (_score, result) in enumerate(scored):
        if abort.is_set():
            return
        mi = make_metadata(result)
        mi.source_relevance = relevance
        idents = dict(result.get('identifiers') or {})
        cover_url = result.get('cover_url') or ''
        if cover_url:
            try:
                source.cache_identifier_to_cover_url(idents, cover_url)
            except Exception:
                pass
        isbn = idents.get('isbn')
        if isbn:
            for key, value in idents.items():
                if key != 'isbn':
                    try:
                        source.cache_isbn_to_identifier(isbn, key, value)
                    except Exception:
                        pass
        try:
            source.clean_downloaded_metadata(mi)
        except Exception:
            pass
        result_queue.put(mi)


def cached_cover_url(source, identifiers):
    """Cover URL cached during identify for these identifiers, or None."""
    try:
        return source.get_cached_cover_url(dict(identifiers or {}))
    except Exception:
        return None
