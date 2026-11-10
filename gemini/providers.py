"""Metadata provider resolver and enrichment for Gemini Library Fixer.

Extracts ground-truth metadata from specialized book providers such as:
- FanFicFare (Fanfiction, Royal Road, AO3, etc.)
- Nifty (nifty.org story archives)
- Metabods (metabods.com adult fiction archives)
- Open Library / ISBN metadata sources

When standard search turns up sparse or missing information, these providers
furnish verified title, author, series, summary, and tag details to feed into
Gemini prompt synthesis.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger('gemini.providers')

DEFAULT_TIMEOUT = 10
USER_AGENT = 'Calibre-GeminiFixer/1.0 (Mozilla/5.0 compatible)'


def _default_fetcher(url: str, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Fetch URL contents as a string using standard library urllib."""
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or 'utf-8'
        raw = resp.read()
        return raw.decode(charset, errors='replace')


_default_fetcher_orig = _default_fetcher
_http_get = _default_fetcher


def _strip_tags(text: str) -> str:
    """Strip basic HTML tags and normalize whitespace."""
    if not text:
        return ''
    cleaned = re.sub(r'<[^>]+>', ' ', text)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


def detect_provider(
    identifiers: Any = None,
    comments: str = '',
    filenames: Optional[List[str]] = None,
    publisher: str = '',
    title: str = ''
) -> Optional[Tuple[str, str]]:
    """Detect whether a book corresponds to a supported metadata provider.

    Returns:
        (provider_key, identifier_or_url) or None
        where provider_key in ('metabods', 'nifty', 'fanficfare', 'openlibrary')
    """
    if isinstance(identifiers, str):
        url_val = identifiers
        identifiers = {'url': url_val} if ('://' in url_val or '.' in url_val) else {}
        comments = comments or url_val
    elif isinstance(identifiers, dict) and any(k in identifiers for k in ('id', 'title', 'comments', 'identifiers', 'tags', 'filenames', 'publisher')):
        book = identifiers
        identifiers = book.get('identifiers')
        comments = book.get('comments', '')
        filenames = book.get('filenames')
        publisher = book.get('publisher', '')
        title = book.get('title', '')

    if isinstance(identifiers, dict):
        idents = {str(k).lower(): str(v) for k, v in identifiers.items()}
    elif isinstance(identifiers, str):
        idents = {'url': identifiers}
    else:
        idents = {}
    comments_str = comments or ''
    filenames_str = ' '.join(filenames or [])
    all_text = f"{comments_str} {filenames_str} {publisher} {title}"

    # 1. Metabods
    if 'metabods' in idents:
        return 'metabods', idents['metabods']
    if 'metabods_story_id' in idents:
        return 'metabods', idents['metabods_story_id']
    if 'url' in idents and 'metabods.com' in idents['url'].lower():
        m = re.search(r'id=(\d+)', idents['url'])
        if m:
            return 'metabods', m.group(1)
    m_mb = re.search(r'metabods\.com/(?:mbxy/site/)?(?:story|story_print)\.php\?id=(\d+)', all_text, re.I)
    if m_mb:
        return 'metabods', m_mb.group(1)
    if publisher.lower() == 'metabods' and re.search(r'\b(?:story|id)\s*[:#]?\s*(\d+)', all_text, re.I):
        m_id = re.search(r'\b(?:story|id)\s*[:#]?\s*(\d+)', all_text, re.I)
        if m_id:
            return 'metabods', m_id.group(1)

    # 2. Nifty
    if 'nifty' in idents:
        return 'nifty', idents['nifty']
    if 'url' in idents and 'nifty.org' in idents['url'].lower():
        return 'nifty', idents['url']
    m_nf = re.search(r'(?:https?://)?(?:www\.)?nifty\.org/(?:nifty/)?([^"\'\s<>]+)', all_text, re.I)
    if m_nf:
        return 'nifty', m_nf.group(0)
    if 'nifty.org' in all_text.lower():
        m_path = re.search(r'([a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-]+(?:\.html|\.txt)?)', filenames_str)
        if m_path:
            return 'nifty', m_path.group(1)

    # 3. FanFicFare supported sites / URLs
    fff_keys = ('fanficfare', 'royalroad', 'archiveofourown', 'ao3', 'fictionpress', 'fanfiction', 'scribblehub', 'wattpad')
    for k in fff_keys:
        if k in idents and idents[k]:
            val = idents[k]
            if val.startswith('http://') or val.startswith('https://'):
                return 'fanficfare', val
            elif k in ('royalroad', 'ao3', 'archiveofourown', 'fanfiction', 'fictionpress', 'scribblehub'):
                # Expand known ID to canonical URL
                if k == 'royalroad':
                    return 'fanficfare', f'https://www.royalroad.com/fiction/{val}'
                elif k in ('ao3', 'archiveofourown'):
                    return 'fanficfare', f'https://archiveofourown.org/works/{val}'
                elif k == 'scribblehub':
                    return 'fanficfare', f'https://www.scribblehub.com/series/{val}/'

    if 'url' in idents:
        url_val = idents['url']
        if re.search(r'(?:royalroad\.com|archiveofourown\.org|fanfiction\.net|fictionpress\.com|scribblehub\.com|wattpad\.com)', url_val, re.I):
            return 'fanficfare', url_val

    m_url = re.search(r'https?://(?:www\.)?(?:royalroad\.com/fiction/\d+|archiveofourown\.org/works/\d+|fanfiction\.net/s/\d+|fictionpress\.com/s/\d+|scribblehub\.com/series/\d+)', all_text, re.I)
    if m_url:
        return 'fanficfare', m_url.group(0)

    # 4. Open Library / ISBN
    if 'isbn' in idents and idents['isbn']:
        return 'openlibrary', idents['isbn']
    if 'openlibrary' in idents and idents['openlibrary']:
        return 'openlibrary', idents['openlibrary']

    return None


def fetch_metabods_metadata(story_id_or_url: str, fetcher: Optional[Callable[[str], str]] = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Fetch and parse metadata from Metabods for a given story ID or URL."""
    fetch = fetcher or _http_get
    m = re.search(r'\d+', str(story_id_or_url))
    if not m:
        return {}
    story_id = m.group(0)

    url = f"https://metabods.com/mbxy/site/story.php?id={story_id}"
    try:
        html_page = fetch(url)
    except Exception as exc:
        logger.warning("Metabods metadata fetch failed for story %s: %s", story_id, exc)
        return {'provider': 'metabods', 'id': story_id, 'url': url}

    meta: Dict[str, Any] = {
        'provider': 'metabods',
        'id': story_id,
        'url': url,
        'tags': [],
        'categories': []
    }

    m_title = re.search(r'<h1[^>]*>(.*?)</h1>', html_page, re.S)
    if m_title:
        meta['title'] = _strip_tags(m_title.group(1))
    else:
        m_t = re.search(r'<title>Metabods\s*-\s*(.*?)\s+by\s+(.*?)</title>', html_page, re.I)
        if m_t:
            meta['title'] = _strip_tags(m_t.group(1))
            if 'author' not in meta:
                meta['author'] = _strip_tags(m_t.group(2))

    m_author = re.search(r'<h5>by\s*<a[^>]*>(.*?)</a>', html_page, re.S)
    if m_author:
        meta['author'] = _strip_tags(m_author.group(1))
    elif not meta.get('author'):
        m_a2 = re.search(r'<h2[^>]*>By:\s*(.*?)</h2>', html_page, re.I | re.S)
        if m_a2:
            meta['author'] = _strip_tags(m_a2.group(1))

    m_summary = re.search(r'<h5>.*?</h5>\s*<p[^>]*>(.*?)</p>', html_page, re.S)
    if m_summary:
        meta['summary'] = _strip_tags(m_summary.group(1))
    else:
        m_s2 = re.search(r'<div[^>]*class=["\']summary["\'][^>]*>(.*?)</div>', html_page, re.I | re.S)
        if m_s2:
            meta['summary'] = _strip_tags(m_s2.group(1))

    # Parse tags
    start = html_page.find('fa-tags me-2')
    if start >= 0:
        end = html_page.find('fa-tags me-2', start + 12)
        block = html_page[start:end if end > 0 else start + 60000]
        seen = set()
        for kind, key in (('tag', 'tags'), ('category', 'categories')):
            for tm in re.finditer(r'<a\s[^>]*href="/mbxy/site/archive\.php\?list=%s&(?:amp;)?id=\d+"[^>]*>(.*?)</a>' % kind, block, re.S):
                tag_name = _strip_tags(tm.group(1)).lstrip('\u2022').strip()
                if tag_name and tag_name not in seen:
                    seen.add(tag_name)
                    meta[key].append(tag_name)

    m_div_tags = re.search(r'<div[^>]*class=["\']tags["\'][^>]*>(.*?)</div>', html_page, re.I | re.S)
    if m_div_tags:
        for t in re.split(r'[,;]', _strip_tags(m_div_tags.group(1))):
            t_clean = t.strip()
            if t_clean and t_clean not in meta['tags']:
                meta['tags'].append(t_clean)

    meta['authors'] = [meta['author']] if meta.get('author') else []
    meta['description'] = meta.get('summary', '')

    return meta


def fetch_nifty_metadata(path_or_url: str, fetcher: Optional[Callable[[str], str]] = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Fetch and parse story headers from Nifty for a given path or URL."""
    fetch = fetcher or _http_get
    clean_path = str(path_or_url).strip()
    if clean_path.startswith('http://') or clean_path.startswith('https://'):
        m = re.search(r'nifty\.org/(.*)', clean_path, re.I)
        if m:
            clean_path = m.group(1)

    url = f"https://www.nifty.org/{clean_path.lstrip('/')}"
    try:
        content = fetch(url)
    except Exception as exc:
        logger.warning("Nifty metadata fetch failed for %s: %s", clean_path, exc)
        return {'provider': 'nifty', 'path': clean_path, 'url': url}

    meta: Dict[str, Any] = {
        'provider': 'nifty',
        'path': clean_path,
        'url': url,
        'tags': []
    }

    # Extract headers commonly formatted in Nifty text/HTML files
    header_block = content[:4000]
    for line in header_block.splitlines():
        line_clean = _strip_tags(line).strip()
        m_t = re.match(r'^(?:Title|Story):\s*(.+)$', line_clean, re.I)
        if m_t and 'title' not in meta:
            meta['title'] = m_t.group(1).strip()
        m_a = re.match(r'^(?:Author|By):\s*(.+)$', line_clean, re.I)
        if m_a and 'author' not in meta:
            meta['author'] = m_a.group(1).strip()
        m_c = re.match(r'^(?:Category|Genre|Categories):\s*(.+)$', line_clean, re.I)
        if m_c:
            cats = [c.strip() for c in re.split(r'[,;/]', m_c.group(1)) if c.strip()]
            meta['tags'].extend(cats)
        m_s = re.match(r'^(?:Summary|Description):\s*(.+)$', line_clean, re.I)
        if m_s and 'summary' not in meta:
            meta['summary'] = m_s.group(1).strip()

    # Fallback to HTML title and meta author
    if 'title' not in meta:
        m_title = re.search(r'<title[^>]*>(.*?)</title>', content, re.I | re.S)
        if m_title:
            t = _strip_tags(m_title.group(1))
            meta['title'] = re.sub(r'\s*-\s*Nifty.*$', '', t, flags=re.I).strip()

    if 'author' not in meta:
        m_meta_author = re.search(r'<meta\s+name=["\']author["\']\s+content=["\'](.*?)["\']', content, re.I)
        if m_meta_author:
            meta['author'] = _strip_tags(m_meta_author.group(1)).strip()

    meta['authors'] = [meta['author']] if meta.get('author') else []
    meta['description'] = meta.get('summary', '')

    return meta


def fetch_fanfic_metadata(url: str, fetcher: Optional[Callable[[str], str]] = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Fetch story metadata from FanFicFare site adapters or webpage meta fallback."""
    meta: Dict[str, Any] = {
        'provider': 'fanficfare',
        'url': url,
        'tags': []
    }

    # 1. Attempt native FanFicFare adapter resolution if available
    try:
        import fanficfare.adapters
        adapter = fanficfare.adapters.get_adapter(url)
        if adapter:
            meta['provider'] = f"fanficfare ({adapter.getSiteDomain()})"
            meta['site'] = adapter.getSiteDomain()
            # If adapter has metadata methods
            if hasattr(adapter, 'extractChapterUrlsAndMetadata'):
                try:
                    adapter.extractChapterUrlsAndMetadata()
                    meta['title'] = adapter.getMetadata('title')
                    meta['author'] = adapter.getMetadata('author')
                    meta['summary'] = _strip_tags(adapter.getMetadata('description'))
                    meta['series'] = adapter.getMetadata('series')
                    meta['status'] = adapter.getMetadata('status')
                    raw_tags = adapter.getMetadataList('genre') + adapter.getMetadataList('characters')
                    meta['tags'] = [t for t in raw_tags if t]
                    meta['authors'] = [meta['author']] if meta.get('author') else []
                    meta['description'] = meta.get('summary', '')
                    return meta
                except Exception:
                    pass
    except (ImportError, Exception):
        pass

    # 2. HTTP page fetch fallback with OpenGraph / HTML metadata extraction
    fetch = fetcher or _http_get
    try:
        page = fetch(url)
    except Exception as exc:
        logger.warning("Fanfic metadata fetch failed for %s: %s", url, exc)
        return meta

    # Extract OpenGraph tags
    m_og_title = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']', page, re.I)
    if m_og_title:
        meta['title'] = _strip_tags(m_og_title.group(1))

    m_og_desc = re.search(r'<meta\s+(?:property=["\']og:description["\']|name=["\']description["\'])\s+content=["\'](.*?)["\']', page, re.I)
    if m_og_desc:
        meta['summary'] = _strip_tags(m_og_desc.group(1))

    # Royal Road specific
    if 'royalroad.com' in url.lower():
        meta['provider'] = 'royalroad'
        m_rr_title = re.search(r'<h1[^>]*class=["\'][^"\']*font-white[^"\']*["\'][^>]*>(.*?)</h1>', page, re.I | re.S)
        if m_rr_title:
            meta['title'] = _strip_tags(m_rr_title.group(1))
        m_rr_author = re.search(r'<h4[^>]*class=["\'][^"\']*font-white[^"\']*["\'][^>]*>\s*By\s*<a[^>]*>(.*?)</a>', page, re.I | re.S)
        if m_rr_author:
            meta['author'] = _strip_tags(m_rr_author.group(1))
        for m_tag in re.finditer(r'<span[^>]*class=["\'][^"\']*badge[^"\']*["\'][^>]*>(.*?)</span>', page, re.I | re.S):
            t = _strip_tags(m_tag.group(1))
            if t and t not in meta['tags']:
                meta['tags'].append(t)

    # AO3 specific
    elif 'archiveofourown.org' in url.lower():
        meta['provider'] = 'archiveofourown'
        m_ao3_title = re.search(r'<h2[^>]*class=["\']title heading["\'][^>]*>(.*?)</h2>', page, re.I | re.S)
        if m_ao3_title:
            meta['title'] = _strip_tags(m_ao3_title.group(1))
        m_ao3_author = re.search(r'<a[^>]*rel=["\']author["\'][^>]*>(.*?)</a>', page, re.I | re.S)
        if m_ao3_author:
            meta['author'] = _strip_tags(m_ao3_author.group(1))
        m_summary = re.search(r'<div[^>]*class=["\']userstuff summary["\'][^>]*>(.*?)</div>', page, re.I | re.S)
        if m_summary:
            meta['summary'] = _strip_tags(m_summary.group(1))

    meta['authors'] = [meta['author']] if meta.get('author') else []
    meta['description'] = meta.get('summary', '')

    return meta


def fetch_openlibrary_metadata(isbn_or_id: str, fetcher: Optional[Callable[[str], str]] = None, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Fetch book metadata from Open Library using an ISBN or Open Library key."""
    fetch = fetcher or _http_get
    clean_id = re.sub(r'[^0-9X]', '', str(isbn_or_id).upper())
    if not clean_id:
        return {}

    url = f"https://openlibrary.org/api/books?bibkeys=ISBN:{clean_id}&format=json&jscmd=data"
    try:
        content = fetch(url)
        data = json.loads(content)
        key = f"ISBN:{clean_id}"
        if key in data:
            b = data[key]
            authors = [a.get('name', '') for a in b.get('authors', []) if a.get('name')]
            subjects = [s.get('name', '') for s in b.get('subjects', []) if s.get('name')]
            return {
                'provider': 'openlibrary',
                'title': b.get('title'),
                'author': ', '.join(authors) if authors else None,
                'authors': authors,
                'publisher': b.get('publishers', [{}])[0].get('name') if b.get('publishers') else None,
                'tags': subjects[:10],
                'url': b.get('url'),
                'isbn': clean_id,
                'description': b.get('description', '')
            }
    except Exception as exc:
        logger.warning("Open Library lookup failed for %s: %s", clean_id, exc)

    return {'provider': 'openlibrary', 'isbn': clean_id}


def get_provider_metadata(book: Dict[str, Any], fetcher: Optional[Callable[[str], str]] = None, timeout: float = DEFAULT_TIMEOUT) -> Optional[Dict[str, Any]]:
    """Determine provider and fetch metadata for a book dictionary."""
    detection = detect_provider(book)
    if not detection:
        return None

    provider_type, ident_val = detection

    if provider_type == 'metabods':
        return fetch_metabods_metadata(ident_val, fetcher=fetcher, timeout=timeout)
    elif provider_type == 'nifty':
        return fetch_nifty_metadata(ident_val, fetcher=fetcher, timeout=timeout)
    elif provider_type == 'fanficfare':
        return fetch_fanfic_metadata(ident_val, fetcher=fetcher, timeout=timeout)
    elif provider_type == 'openlibrary':
        return fetch_openlibrary_metadata(ident_val, fetcher=fetcher, timeout=timeout)

    return None


def is_metadata_sparse(book: Dict[str, Any]) -> bool:
    """Check if book metadata is sparse, generic, or likely uninformative."""
    title = (book.get('title') or '').strip()
    authors = [a for a in (book.get('authors') or []) if a and a.lower() != 'unknown']

    # Title checks: empty, generic, or just filename
    if not title or title.lower() in ('unknown', 'untitled', 'book', 'novel'):
        return True
    if re.match(r'^(?:chapter|part|ch|section)\s*\d+$', title, re.I):
        return True
    if re.search(r'\.(?:epub|pdf|mobi|azw3|cbz|txt)$', title, re.I):
        return True

    # Authors check
    if not authors:
        return True

    # Description and tags both empty
    has_tags = bool(book.get('tags'))
    has_comments = bool(book.get('comments'))
    if not has_tags and not has_comments:
        return True

    return False


def enrich_books_with_providers(
    books: List[Dict[str, Any]],
    fetcher: Optional[Callable[[str], str]] = None,
    force: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    logger_fn: Optional[Callable[[str], None]] = None
) -> int:
    """Enrich books with metadata provider details when metadata is sparse or force=True.

    Modifies the dictionaries in-place by adding 'provider_info' if found.
    Returns the count of books enriched.
    """
    enriched_count = 0
    for b in books:
        if not force and not is_metadata_sparse(b) and not b.get('identifiers'):
            continue

        try:
            meta = get_provider_metadata(b, fetcher=fetcher, timeout=timeout)
            if meta and (meta.get('title') or meta.get('author') or meta.get('summary') or meta.get('tags')):
                b['provider_info'] = meta
                b['provider_metadata'] = meta
                if not b.get('authors') and meta.get('authors'):
                    b['authors'] = list(meta['authors'])
                elif not b.get('authors') and meta.get('author'):
                    b['authors'] = [meta['author']]
                if meta.get('tags'):
                    seen = set(b.get('tags') or [])
                    for t in meta['tags']:
                        if t not in seen:
                            b.setdefault('tags', []).append(t)
                            seen.add(t)
                enriched_count += 1
                if logger_fn:
                    prov_name = meta.get('provider', 'provider')
                    story_t = meta.get('title') or b.get('title') or 'unknown'
                    logger_fn(f"Discovered {prov_name} metadata for: {story_t}")
        except Exception as exc:
            logger.debug("Failed provider enrichment for book %s: %s", b.get('id'), exc)

    return enriched_count
