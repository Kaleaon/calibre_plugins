"""Shared by every plugin here: throttled fetching, XHTML cleaning and EPUB building. Standard library only."""
import functools
import gzip
import html
import re
import threading
import time
import uuid
import zipfile
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path, PosixPath
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape, quoteattr

USER_AGENT = 'Mozilla/5.0 (compatible; CalibreStoryDownloader/1.0; personal use)'
MIN_INTERVAL = 1.0  # seconds between requests, to stay polite to small hobby sites
MAX_BYTES = 64 * 1024 * 1024


class Cancelled(Exception):
    pass


class RateLimited(IOError):
    """The site asked us to slow down (HTTP 429). retry_after is the number of seconds it asked for, or None."""

    def __init__(self, url, retry_after=None):
        super().__init__(f'The site is limiting requests (HTTP 429) for {url}' + (f'; it asked for {retry_after} seconds' if retry_after else ''))
        self.retry_after = retry_after


class Fetcher:
    """Throttled HTTP GET with retries, gzip support and cooperative cancellation."""

    def __init__(self, min_interval=MIN_INTERVAL, cancelled=lambda: False, opener=urlopen):
        self.min_interval = min_interval
        self.cancelled = cancelled
        self.opener = opener
        self._last = 0.0
        self._lock = threading.Lock()

    def get(self, url, retries=3, headers=None, limit=None, post=None):
        """Fetch a page as text. headers adds request headers; limit reads at most that many bytes (a partial read);
        post is a bytes body for a POST request (the content type, if needed, goes in headers)."""
        last_error = None
        for attempt in range(retries):
            if self.cancelled():
                raise Cancelled()
            with self._lock:
                wait = self._last + self.min_interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
            try:
                merged = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity' if limit else 'gzip'}
                merged.update(headers or {})
                req = Request(url, data=post, headers=merged)
                with self.opener(req, timeout=60) as resp:
                    cap = limit if limit else MAX_BYTES + 1
                    raw = resp.read(cap)
                    if not limit and len(raw) > MAX_BYTES:
                        raise ValueError('Response too large')
                    if resp.headers.get('Content-Encoding', '').lower() == 'gzip':
                        raw = gzip.decompress(raw)
                    return decode(raw, resp.headers.get_content_charset())
            except HTTPError as exc:
                last_error = exc
                hint = exc.headers.get('WWW-Authenticate') if exc.headers else None
                if hint:  # servers that refuse a login often say why here (e.g. Reddit: insufficient_scope)
                    last_error = f'{exc} ({hint})'
                if exc.code == 429:  # never retry into a rate limit; report it and let the caller back off
                    try:
                        retry_after = int(exc.headers.get('Retry-After', '')) if exc.headers else None
                    except (TypeError, ValueError):
                        retry_after = None
                    raise RateLimited(url, retry_after)
                if exc.code in (400, 401, 403, 404, 410, 416):
                    break  # retrying will not help
            except (URLError, OSError, EOFError) as exc:
                last_error = exc
            time.sleep(2 ** attempt)
        raise IOError(f'Could not fetch {url}: {last_error}')

    def get_range(self, url, length=3000, tail=False):
        """Just the first (or last) bytes of a file. Servers that ignore Range still only cost one read of `length`
        bytes from the start, so a tail request then returns the start; callers should treat both as samples."""
        spec = f'-{length}' if tail else f'0-{length - 1}'
        return self.get(url, headers={'Range': f'bytes={spec}'}, limit=length)


def decode(raw, charset=None):
    for enc in ([charset] if charset else []) + ['utf-8', 'cp1252']:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', 'replace')


def strip_tags(fragment):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', fragment))).strip()


# ---------------------------------------------------------------- XHTML cleaning

BLOCKS = {'p', 'div', 'blockquote', 'ul', 'ol', 'li', 'hr', 'pre', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
INLINE = {'em', 'strong', 'i', 'b', 'u', 's', 'sub', 'sup', 'span', 'a', 'small', 'big'}
ALLOWED_CLASSES = {'author-note', 'author-note-label'}  # set by our own plugins for a boxed aside; any other class is dropped
RENAME = {'strike': 's', 'del': 's', 'center': 'div', 'cite': 'em'}
VOID = {'br', 'hr'}
DROP_CONTENT = {'script', 'style', 'head', 'title'}
STYLE_OK = {
    'text-align': re.compile(r'^(left|right|center|justify)$'),
    'margin-left': re.compile(r'^\d+(\.\d+)?(px|em|%)?$'),
    'margin-right': re.compile(r'^\d+(\.\d+)?(px|em|%)?$'),
    'text-indent': re.compile(r'^-?\d+(\.\d+)?(px|em|%)?$'),
    'font-style': re.compile(r'^(italic|normal)$'),
    'font-weight': re.compile(r'^(bold|normal)$'),
    'text-decoration': re.compile(r'^(underline|line-through)$'),
}


def safe_style(value):
    out = []
    for part in (value or '').split(';'):
        if ':' in part:
            key, val = (x.strip().lower() for x in part.split(':', 1))
            if key in STYLE_OK and STYLE_OK[key].match(val):
                out.append(f'{key}:{val}')
    return ';'.join(out)


class Cleaner(HTMLParser):
    """Turns messy site HTML (e.g. nested <p><p>) into well-formed XHTML with a small whitelist."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.stack = []
        self.skip = 0

    def _close_to(self, tag):
        while self.stack:
            top = self.stack.pop()
            self.out.append(f'</{top}>')
            if top == tag:
                break

    def _ensure_paragraph(self):
        if not self.stack:
            self.out.append('<p>')
            self.stack.append('p')

    def handle_starttag(self, tag, attrs):
        tag = RENAME.get(tag, tag)
        attrs = dict(attrs)
        if tag in DROP_CONTENT:
            self.skip += 1
            return
        if tag == 'center':
            attrs['style'] = 'text-align:center'
        if tag in VOID:
            if tag == 'hr':
                self._close_to('p') if 'p' in self.stack else None
            else:
                self._ensure_paragraph()
            self.out.append(f'<{tag}/>')
            return
        if tag in BLOCKS:
            if tag == 'p' or tag in ('div', 'blockquote', 'ul', 'ol', 'pre') or tag.startswith('h'):
                if 'p' in self.stack:
                    self._close_to('p')
            if tag == 'li' and 'li' in self.stack:
                self._close_to('li')
            style = safe_style(attrs.get('style'))
            css_class = attrs.get('class') if attrs.get('class') in ALLOWED_CLASSES and tag in ('div', 'p') else None
            self.out.append(f'<{tag}' + (f' class={quoteattr(css_class)}' if css_class else '') + (f' style={quoteattr(style)}' if style else '') + '>')
            self.stack.append(tag)
        elif tag in INLINE:
            self._ensure_paragraph()
            extra = ''
            if tag == 'a':
                href = attrs.get('href') or ''
                if re.match(r'^https?://', href, re.I):
                    extra = f' href={quoteattr(href)}'
            elif tag == 'span':
                style = safe_style(attrs.get('style'))
                if not style:
                    return
                extra = f' style={quoteattr(style)}'
            self.out.append(f'<{tag}{extra}>')
            self.stack.append(tag)
        # anything else (font, table cells, img...) is unwrapped; its text still flows through

    def handle_endtag(self, tag):
        tag = RENAME.get(tag, tag)
        if tag in DROP_CONTENT:
            self.skip = max(0, self.skip - 1)
        elif tag in self.stack and (tag in BLOCKS or tag in INLINE):
            self._close_to(tag)

    def handle_data(self, data):
        if self.skip:
            return
        if not data.strip() and not self.stack:
            return
        if data.strip():
            self._ensure_paragraph()
        self.out.append(escape(data))

    def result(self):
        self._close_to(None)
        text = ''.join(self.out)
        previous = None
        while previous != text:  # drop paragraphs left empty by the site's nested markup
            previous = text
            text = re.sub(r'<p(?: [^>]*)?>\s*</p>', '', text)
        return text


def clean_fragment(fragment):
    cleaner = Cleaner()
    cleaner.feed(fragment)
    cleaner.close()
    return cleaner.result()


# ---------------------------------------------------------------- EPUB building

CSS = '''body { line-height: 1.4; }
h1, h2, h3 { text-align: center; page-break-after: avoid; }
p { margin: 0 0 0.8em 0; }
.byline, .meta { text-align: center; }
.summary { font-style: italic; text-align: center; }
hr { margin: 1.5em 0; }
.author-note { margin: 1em 1.5em; padding: 0.4em 0.9em; border-left: 3px solid #888; font-size: 0.9em; }
.author-note p { margin: 0.3em 0; }
.author-note-label { font-weight: bold; font-size: 0.8em; letter-spacing: 0.05em; text-transform: uppercase; }
'''

XHTML = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
         '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en">'
         '<head><meta charset="utf-8"/><title>{title}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>'
         '<body>{body}</body></html>')


def _page(title, body):
    return XHTML.format(title=escape(title), body=body)


def chapter_title(heading, story_title, only_one):
    if not heading or only_one and re.match(r'^part\s*\d+\w*$', heading, re.I):
        return story_title
    return heading


def sort_by_posting_order(stories):
    """Sort stories chronologically by posting date (pubdate / header date), falling back to chapter/series index or title."""
    def get_date(s):
        d = s.get('pubdate') or s.get('date') or ''
        if not d and s.get('sections') and len(s['sections']) > 0:
            m = re.search(r'(?mi)^Date:[ \t]*(.+)$', str(s['sections'][0][1])[:1500])
            if m:
                try:
                    from email.utils import parsedate_to_datetime
                    d = parsedate_to_datetime(m.group(1).strip()).date().isoformat()
                except (TypeError, ValueError, IndexError):
                    pass
        if hasattr(d, 'isoformat'):
            d = d.isoformat()
        d_str = str(d)[:10] if d else ''
        return d_str if re.match(r'^\d{4}-\d{2}-\d{2}', d_str) else ''

    def get_index(s):
        idx = s.get('series_index')
        if idx is not None:
            try:
                return float(idx)
            except (ValueError, TypeError):
                pass
        title = s.get('title', '')
        m = re.search(r'^(.*?)[-_]?(\d+)$', title.strip())
        if m and m.group(2):
            return float(m.group(2))
        m2 = re.search(r'\b(?:chapter|chap|ch|part|pt|book|episode|ep)\.?\s*(\d+)', title, re.I)
        if m2:
            return float(m2.group(1))
        sid = s.get('id', '') or s.get('path', '')
        if sid and not sid.startswith('urn:'):
            m_id = re.search(r'^(.*?)[-_]?(\d+)$', sid.rsplit('/', 1)[-1])
            if m_id and m_id.group(2):
                return float(m_id.group(2))
        return float('inf')

    def get_title(s):
        return (s.get('title') or '').casefold()

    def key(s):
        d = get_date(s)
        return (d if d else '9999-99-99', get_index(s), get_title(s))

    return sorted(stories, key=key)


def build_epub(stories, title=None, author=None, publisher=None, tags=None, summary=None, preserve_story_order=False):
    """Return EPUB bytes. One story keeps its parts as chapters; several stories become one omnibus."""
    if not stories:
        raise ValueError('No stories to build')
    if not preserve_story_order:
        stories = sort_by_posting_order(stories)
    combined = len(stories) > 1
    title = title or (stories[0]['title'] if not combined else f"{stories[0]['title']} and {len(stories) - 1} more")
    authors = list(dict.fromkeys(s['author'] for s in stories))
    author = author or ' & '.join(authors)
    if tags is None:
        tags = list(dict.fromkeys(t for s in stories for t in s.get('tags', []) + s.get('categories', [])))
    elif isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]
    if summary is None:
        summary = stories[0].get('summary', '') if not combined else ''
    book_id = 'urn:uuid:' + str(uuid.uuid5(uuid.NAMESPACE_URL, '|'.join(s.get('id', s['title']) for s in stories)))

    files = []  # (filename, xhtml)
    toc = []    # (label, href, [children])
    title_body = f'<h1>{escape(title)}</h1><p class="byline">by {escape(author)}</p>'
    if summary:
        title_body += f'<p class="summary">{escape(summary)}</p>'
    if combined:
        title_body += '<ol>' + ''.join(f'<li>{escape(s["title"])} — {escape(s["author"])}</li>' for s in stories) + '</ol>'
    elif stories[0].get('url'):
        title_body += f'<p class="meta">Source: {escape(stories[0]["url"])}</p>'
    title_body += '<p class="meta">Story copyright remains with the author. Personal copy; not for reposting.</p>'
    files.append(('title.xhtml', _page(title, title_body)))
    toc.append(('Title page', 'title.xhtml', []))

    for si, story in enumerate(stories, 1):
        only_one = len([h for h, _ in story['sections'] if not re.match(r"^author'?s? note", h, re.I)]) <= 1  # a lone part needs no 'Part 1' label
        children = []
        story_href = None
        for pi, (heading, body) in enumerate(story['sections'], 1):
            name = f's{si:03d}_{pi:03d}.xhtml'
            label = chapter_title(heading, story['title'], only_one)
            header = ''
            if combined and pi == 1:
                header = f'<h1>{escape(story["title"])}</h1><p class="byline">by {escape(story["author"])}</p>'
            fragment = clean_fragment(body)
            files.append((name, _page(label, f'{header}<h2>{escape(label)}</h2>{fragment}')))
            story_href = story_href or name
            children.append((label, name, []))
        if combined:
            toc.append((story['title'], story_href, children))
        else:
            toc.extend(children)

    manifest = ''.join(f'<item id="c{i}" href="{n}" media-type="application/xhtml+xml"/>' for i, (n, _) in enumerate(files))
    spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(len(files)))
    subjects = ''.join(f'<dc:subject>{escape(t)}</dc:subject>' for t in tags)
    description = f'<dc:description>{escape(summary)}</dc:description>' if summary else ''
    publisher_xml = f'<dc:publisher>{escape(publisher)}</dc:publisher>' if publisher else ''
    source = f'<dc:source>{escape(stories[0]["url"])}</dc:source>' if (not combined and stories[0].get('url')) else ''
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="bookid">{book_id}</dc:identifier><dc:title>{escape(title)}</dc:title>'
           f'<dc:creator>{escape(author)}</dc:creator><dc:language>en</dc:language>{publisher_xml}'
           f'{description}{subjects}{source}'
           f'<meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta></metadata>'
           '<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
           '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
           f'<item id="css" href="style.css" media-type="text/css"/>{manifest}</manifest>'
           f'<spine toc="ncx">{spine}</spine></package>')

    def nav_list(entries):
        return '<ol>' + ''.join(
            f'<li><a href="{h}">{escape(l)}</a>{nav_list(c) if c else ""}</li>' for l, h, c in entries) + '</ol>'

    nav = _page('Contents', f'<nav epub:type="toc" id="toc"><h1>Contents</h1>{nav_list(toc)}</nav>')
    counter = [0]

    def ncx_points(entries):
        out = []
        for label, href, children in entries:
            counter[0] += 1
            n = counter[0]
            out.append(f'<navPoint id="n{n}" playOrder="{n}"><navLabel><text>{escape(label)}</text></navLabel>'
                       f'<content src="{href}"/>{ncx_points(children)}</navPoint>')
        return ''.join(out)

    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
           f'<head><meta name="dtb:uid" content="{book_id}"/></head><docTitle><text>{escape(title)}</text></docTitle>'
           f'<navMap>{ncx_points(toc)}</navMap></ncx>')
    container = ('<?xml version="1.0"?>\n<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

    out = BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/content.opf', opf, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/nav.xhtml', nav, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/toc.ncx', ncx, zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/style.css', CSS, zipfile.ZIP_DEFLATED)
        for name, content in files:
            z.writestr('OEBPS/' + name, content, zipfile.ZIP_DEFLATED)
    return out.getvalue()


def epub_builder(publisher):
    """Return a build_epub partial function pre-bound with publisher."""
    return functools.partial(build_epub, publisher=publisher)


make_epub_builder = epub_builder


def extract_epub_story(epub_source):
    """Extract story structure (metadata, chapters) from an EPUB file (bytes, path, or file-like object).
    Returns a story dictionary: {'id': ..., 'title': ..., 'author': ..., 'summary': ..., 'tags': ..., 'sections': [(heading, body)]}
    """
    if isinstance(epub_source, (str, bytes)):
        f = BytesIO(epub_source) if isinstance(epub_source, bytes) else open(epub_source, 'rb')
    elif hasattr(epub_source, 'read'):
        f = epub_source
    else:
        f = BytesIO(bytes(epub_source))

    with zipfile.ZipFile(f, 'r') as z:
        names = z.namelist()
        opf_path = None
        if 'META-INF/container.xml' in names:
            try:
                c_xml = z.read('META-INF/container.xml').decode('utf-8', 'ignore')
                m = re.search(r'full-path=["\']([^"\']+)["\']', c_xml, re.I)
                if m:
                    opf_path = m.group(1)
            except Exception:
                pass
        if not opf_path:
            for n in names:
                if n.endswith('.opf'):
                    opf_path = n
                    break
        if not opf_path or opf_path not in names:
            raise ValueError('Not a valid EPUB: OPF manifest not found')

        opf_dir = PosixPath(opf_path).parent.as_posix()
        if opf_dir == '.':
            opf_dir = ''
        elif opf_dir and not opf_dir.endswith('/'):
            opf_dir += '/'

        opf_text = z.read(opf_path).decode('utf-8', 'ignore')

        # Extract Metadata
        title_m = re.search(r'<dc:title[^>]*>(.*?)</dc:title>', opf_text, re.S | re.I)
        title = strip_tags(title_m.group(1)) if title_m else 'Untitled'

        author_m = re.search(r'<dc:creator[^>]*>(.*?)</dc:creator>', opf_text, re.S | re.I)
        author = strip_tags(author_m.group(1)) if author_m else 'Unknown'

        summary_m = re.search(r'<dc:description[^>]*>(.*?)</dc:description>', opf_text, re.S | re.I)
        summary = strip_tags(summary_m.group(1)) if summary_m else ''

        tags = [strip_tags(m.group(1)) for m in re.finditer(r'<dc:subject[^>]*>(.*?)</dc:subject>', opf_text, re.S | re.I)]

        date_m = re.search(r'<dc:date[^>]*>(.*?)</dc:date>', opf_text, re.S | re.I)
        pubdate = strip_tags(date_m.group(1))[:10] if date_m else ''

        id_m = re.search(r'<dc:identifier[^>]*>(.*?)</dc:identifier>', opf_text, re.S | re.I)
        story_id = strip_tags(id_m.group(1)) if id_m else title

        # Extract Manifest
        manifest = {}
        for item_m in re.finditer(r'<item\b([^>]+)>', opf_text, re.I):
            attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', item_m.group(1)))
            if 'id' in attrs and 'href' in attrs:
                manifest[attrs['id']] = attrs

        # Extract Spine
        spine = []
        for ref_m in re.finditer(r'<itemref\b([^>]+)>', opf_text, re.I):
            attrs = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', ref_m.group(1)))
            if 'idref' in attrs and attrs['idref'] in manifest:
                spine.append(manifest[attrs['idref']])

        # Extract TOC labels if NCX or NAV present
        toc_labels = {}
        ncx_item = next((item for item in manifest.values() if item.get('media-type') == 'application/x-dtbncx+xml'), None)
        if ncx_item:
            ncx_href = opf_dir + ncx_item['href']
            if ncx_href in names:
                ncx_text = z.read(ncx_href).decode('utf-8', 'ignore')
                for np in re.finditer(r'<navPoint[^>]*>.*?<text>(.*?)</text>.*?<content\s+src=["\']([^"\']+)["\']', ncx_text, re.S | re.I):
                    lbl = strip_tags(np.group(1))
                    src = np.group(2).split('#')[0]
                    if src and lbl:
                        toc_labels[src] = lbl

        sections = []
        for idx, item in enumerate(spine, 1):
            href = item['href']
            # Ignore title page and nav pages if they exist
            if href in ('title.xhtml', 'nav.xhtml', 'toc.xhtml'):
                continue
            full_path = opf_dir + href
            if full_path not in names:
                continue
            content = z.read(full_path).decode('utf-8', 'ignore')

            # Determine chapter label
            label = toc_labels.get(href)
            if not label:
                h_match = re.search(r'<h[1-3][^>]*>(.*?)</h[1-3]>', content, re.S | re.I)
                if h_match:
                    label = strip_tags(h_match.group(1))
            if not label:
                t_match = re.search(r'<title[^>]*>(.*?)</title>', content, re.S | re.I)
                if t_match:
                    label = strip_tags(t_match.group(1))
            if not label or label.lower() in ('title', 'title page', 'contents'):
                label = f'Chapter {idx}'

            # Extract body
            body_match = re.search(r'<body[^>]*>(.*?)</body>', content, re.S | re.I)
            body_text = body_match.group(1) if body_match else content

            sections.append((label, body_text))

        return {
            'id': story_id,
            'title': title,
            'author': author,
            'summary': summary,
            'tags': tags,
            'pubdate': pubdate,
            'url': '',
            'sections': sections,
        }


def merge_epubs(epub_list, title=None, author=None, publisher=None, tags=None, summary=None, stories_override=None, preserve_story_order=False):
    """Extract stories from a list of EPUB bytes or paths and build a merged EPUB byte string."""
    if stories_override is not None:
        stories = stories_override
    else:
        stories = []
        for item in epub_list:
            try:
                story = extract_epub_story(item)
                if story and story['sections']:
                    stories.append(story)
            except Exception:
                continue
    if not stories:
        raise ValueError('No valid EPUB stories found to merge')
    return build_epub(stories, title=title, author=author, publisher=publisher, tags=tags, summary=summary, preserve_story_order=preserve_story_order)

