"""Asks the Google Gemini API to suggest fixes for book metadata, and checks the answers. Pure standard library so it can be
tested outside Calibre.

Nothing here changes a library. plan_fixes() returns suggestions; the caller shows them and applies only what the user accepts.
The model is told to leave anything it is unsure about alone, and every answer is validated again here before it is offered.
"""
import difflib
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from html.parser import HTMLParser

API_ROOT = 'https://generativelanguage.googleapis.com/v1beta'
DEFAULT_MODEL = 'gemini-2.5-flash'
CONFIDENCE = {'low': 0, 'medium': 1, 'high': 2}
FIELDS = ('title', 'authors', 'series', 'tags', 'language', 'isbn')
IMAGE_LIMIT = 6_000_000  # bytes; a bigger cover or back page is skipped rather than uploaded
RETRY_STATUS = {429, 500, 502, 503, 504}
# A new title this unlike the old one is flagged as a big change and left unticked in the preview.
BIG_CHANGE_RATIO = 0.5
IMAGE_TOKENS_PER_IMAGE = 258
MODEL_COST_PER_1M = {
    'gemini-2.5-flash': 0.075,
    'gemini-1.5-flash': 0.075,
    'gemini-2.5-flash-lite': 0.0375,
    'gemini-1.5-flash-lite': 0.0375,
    'gemini-2.5-pro': 1.25,
    'gemini-1.5-pro': 1.25,
}

INSTRUCTIONS = '''You clean up the metadata of ebooks in a personal Calibre library. You get a JSON list of books; for each you may suggest corrections.

Rules:
- Fix only what is clearly wrong: junk in titles (site names, "z-lib", "[epub]", "(retail)", file extensions, underscores or dots used as spaces, ALL CAPS or all lower case), authors written "Last, First" or joined into one string, authors that are really a website or "Unknown", series and volume number that are visible in the title or file name but missing from the series fields, and obviously mis-spelled or duplicate tags.
- Use proper title case for English titles, and keep the author's real spelling. Authors must be listed as "First Last".
- Titles that start with a volume marker, such as "book 1 - the force awakens", "Vol. 2: Title", "Part 3 - Title" or "#4. Title", must lose that marker: the title becomes just the real title in title case ("The Force Awakens") and the number goes in series_index. Books that carry a "numbering_in_title" hint have exactly this problem. Set series too when you know it from the author, from other books in the list, or from the title; if you do not know the series name, return series null and still fix the title and series_index.
- A series name must not contain the volume number, and the volume goes in series_index (for example title "Dune Messiah (Dune #2)" gives title "Dune Messiah", series "Dune", series_index 2).
- Tags: return the complete cleaned tag list for the book (keep good tags, merge duplicates such as "sci-fi" and "Science Fiction" into one form, fix case). You may add at most 5 well-known genre tags you are certain of. Do not invent plot details.
- language: only when the book has none; an ISO 639-2 three-letter code such as "eng".
- Cover pictures: some books come with images, each labelled with the book id and where it is from ("front cover", "back cover"). Read the text printed on them: the title, author, series and volume number on the front, and on the back the blurb, publisher, language and ISBN. Text actually printed on a cover is better evidence than a file name or a messy title, so use it to fix the metadata, and put what you read in cover_text. Do not identify a book from artwork alone, and do not report text you cannot read clearly. An ISBN is only valid if you can read every digit; otherwise return null.
- isbn: the ISBN printed on the back cover or copyright page, digits only (with X as the last character for an ISBN-10).
- Never invent facts. If you are not sure, return null for that field, which means "leave it as it is". Returning a field identical to the current value is the same as null.
- confidence says how sure you are about your suggestions for that book: high only when the correction is obvious from the data given.
- reason is one short sentence.
- Return one object for every book id you were given, in the same order.'''

RESPONSE_SCHEMA = {
    'type': 'ARRAY',
    'items': {
        'type': 'OBJECT',
        'properties': {
            'id': {'type': 'INTEGER'},
            'title': {'type': 'STRING', 'nullable': True},
            'authors': {'type': 'ARRAY', 'items': {'type': 'STRING'}, 'nullable': True},
            'series': {'type': 'STRING', 'nullable': True},
            'series_index': {'type': 'NUMBER', 'nullable': True},
            'tags': {'type': 'ARRAY', 'items': {'type': 'STRING'}, 'nullable': True},
            'language': {'type': 'STRING', 'nullable': True},
            'isbn': {'type': 'STRING', 'nullable': True},
            'cover_text': {'type': 'STRING', 'nullable': True},
            'confidence': {'type': 'STRING', 'enum': ['low', 'medium', 'high']},
            'reason': {'type': 'STRING'},
        },
        'required': ['id', 'confidence'],
    },
}


class GeminiError(Exception):
    """A problem talking to the API that the user should read. Never contains the API key."""


class GeminiPartialError(GeminiError):
    """Raised when batch processing stops early due to an error, carrying partial suggestions and skipped records."""

    def __init__(self, message, suggestions=None, skipped=None, original_error=None):
        super().__init__(message)
        self.suggestions = list(suggestions) if suggestions is not None else []
        self.skipped = list(skipped) if skipped is not None else []
        self.original_error = original_error


PartialGeminiError = GeminiPartialError


def sanitize_error_message(msg, api_key=None):
    """Sanitizes sensitive information like API keys from exception messages."""
    text = str(msg or '')
    if api_key and str(api_key).strip():
        key_str = str(api_key).strip()
        text = text.replace(key_str, '[REDACTED]')
    text = re.sub(r'([?&]key=)[^&\s]+', r'\1[REDACTED]', text)
    text = re.sub(r'([?&]api_key=)[^&\s]+', r'\1[REDACTED]', text)
    return text


class QuotaExhausted(GeminiError):
    """The free (or paid) quota for this model is used up for now; waiting a few seconds will not help."""


class Blocked(GeminiError):
    """Google's safety filter refused the request (common for adult fiction)."""


# ----------------------------------------------------------------------------------------------------- describing work

def describe_book(book):
    """One line for the activity log: the data about a book that is sent to Google."""
    parts = [f"“{book.get('title') or '(no title)'}”", 'by ' + (', '.join(book.get('authors') or []) or '(no author)')]
    if book.get('series'):
        index = book.get('series_index')
        parts.append(f"series {book['series']}" + (f' #{index:g}' if index is not None else ''))
    if book.get('tags'):
        parts.append('tags: ' + ', '.join(book['tags'][:6]) + (' …' if len(book['tags']) > 6 else ''))
    if book.get('languages'):
        parts.append('language: ' + ', '.join(book['languages']))
    if book.get('filenames'):
        parts.append('files: ' + ', '.join(book['filenames']))
    if book.get('excerpt'):
        parts.append(f"+ {len(book['excerpt'])} characters of opening text")
    if book.get('images'):
        parts.append('+ picture: ' + ', '.join(i['label'] for i in book['images']))
    return ' | '.join(parts)


def describe_change(field, old, new):
    if field == 'series':
        fmt = lambda v: (f'{v[0]} #{v[1]:g}' if v[0] and v[1] is not None else v[0]) or '(none)'
        return f'series {fmt(old)} → {fmt(new)}'
    if field == 'isbn':
        return f'ISBN {old or "(none)"} → {new}'
    if isinstance(new, list):
        return f"{field} {', '.join(old) or '(none)'} → {', '.join(new)}"
    return f'{field} “{old}” → “{new}”'


# ---------------------------------------------------------------------------------------------------------------- API

def _post(url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, headers=headers, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _get(url, headers, timeout):
    request = urllib.request.Request(url, headers=headers, method='GET')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _error_info(exc):
    """(message, seconds Google says to wait or None, True when it is a per-day quota) from an HTTP error answer."""
    try:
        data = json.loads(exc.read().decode('utf-8', 'replace'))['error']
    except Exception:
        return (exc.reason if isinstance(exc.reason, str) else f'HTTP {exc.code}'), None, False
    message = str(data.get('message') or f'HTTP {exc.code}')
    retry, daily = None, False
    for detail in data.get('details') or []:
        kind = str(detail.get('@type', ''))
        if kind.endswith('RetryInfo'):
            m = re.match(r'([\d.]+)s', str(detail.get('retryDelay', '')))
            retry = float(m.group(1)) if m else retry
        elif kind.endswith('QuotaFailure'):
            daily = daily or any('perday' in str(v.get('quotaId', '')).lower() for v in detail.get('violations') or [])
    if retry is None:
        m = re.search(r'retry in ([\d.]+)s', message)
        retry = float(m.group(1)) if m else None
    return message, retry, daily


def _request(method, url, api_key, body=None, retries=4, timeout=120, transport=None, sleep=time.sleep, cancelled=lambda: False,
             notify=lambda text: None):
    """One API call with the retry rules: rate limits and server errors are retried with a growing pause."""
    headers = {'x-goog-api-key': api_key, 'Content-Type': 'application/json', 'User-Agent': 'calibre-gemini-library-fixer'}
    send = transport or ((lambda u, h, b, t: _post(u, h, b, t)) if method == 'POST' else (lambda u, h, b, t: _get(u, h, t)))
    delay = 3.0

    def nap(seconds, why):
        """Pause before a retry in one-second slices so Cancel is noticed, and say so, so the window is not silent."""
        notify(f'{why}; waiting {seconds:.0f}s before trying again')
        left = seconds
        while left > 0 and not cancelled():
            sleep(min(1.0, left))
            left -= 1.0

    for attempt in range(retries + 1):
        if cancelled():
            raise GeminiError('Cancelled')
        try:
            return json.loads(send(url, headers, body, timeout).decode('utf-8'))
        except urllib.error.HTTPError as exc:
            message, advised, daily = _error_info(exc)
            if exc.code == 429 and (daily or (advised is not None and advised > 120)):
                raise QuotaExhausted(f'The request limit for this model is used up for now (Google: {message}). Free keys have a small daily '
                                     f'allowance that resets once a day; you can wait, pick a lighter model (a "flash-lite" one), or '
                                     f'turn on billing for your key. Results found so far are kept.') from None
            if exc.code in RETRY_STATUS and attempt < retries:
                try:
                    wait = min(float(exc.headers.get('Retry-After')), 120)
                except (TypeError, ValueError, AttributeError):
                    wait = min(advised + 1, 120) if advised is not None else delay
                nap(wait, 'Google asked for a pause' if exc.code == 429 else f'Google had a temporary problem ({exc.code})')
                delay *= 2
                continue
            if exc.code in (400, 403) and 'API key' in message:
                raise GeminiError('Google rejected the API key. Check that it is copied completely and that the Gemini API is enabled for it.') from None
            if exc.code == 429:
                raise GeminiError(f'Google says the request limit is used up: {message}') from None
            raise GeminiError(f'Google returned an error ({exc.code}): {message}') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            if attempt < retries:
                nap(delay, 'Could not reach Google')
                delay *= 2
                continue
            reason = getattr(exc, 'reason', exc)
            raise GeminiError(f'Could not reach Google: {reason}') from None
        except ValueError:
            raise GeminiError('Google sent an answer that is not JSON.') from None
    raise GeminiError('Google did not answer.')


def generate(api_key, model, prompt, **kw):
    """Asks the model for JSON that matches RESPONSE_SCHEMA and returns the parsed list. prompt is text, or a list of API parts."""
    parts = [{'text': prompt}] if isinstance(prompt, str) else prompt
    body = json.dumps({
        'systemInstruction': {'parts': [{'text': INSTRUCTIONS}]},
        'contents': [{'role': 'user', 'parts': parts}],
        'generationConfig': {'temperature': 0.1, 'responseMimeType': 'application/json', 'responseSchema': RESPONSE_SCHEMA},
    }).encode('utf-8')
    data = _request('POST', f'{API_ROOT}/models/{model}:generateContent', api_key, body, **kw)
    feedback = data.get('promptFeedback') or {}
    if feedback.get('blockReason'):
        raise Blocked(f"Google's safety filter blocked the request ({feedback['blockReason']}).")
    candidates = data.get('candidates') or []
    if not candidates:
        raise Blocked('Google returned no answer for this request.')
    candidate = candidates[0]
    text = ''.join(p.get('text', '') for p in (candidate.get('content') or {}).get('parts', []))
    if not text.strip():
        if candidate.get('finishReason') in ('SAFETY', 'PROHIBITED_CONTENT', 'BLOCKLIST', 'RECITATION'):
            raise Blocked(f"Google's safety filter blocked the answer ({candidate['finishReason']}).")
        raise GeminiError(f"Google returned an empty answer (finish reason: {candidate.get('finishReason', 'unknown')}).")
    try:
        result = json.loads(text)
    except ValueError:
        raise GeminiError('The model\'s answer was not valid JSON (it may have been cut off). Try a smaller batch size.') from None
    if not isinstance(result, list):
        raise GeminiError('The model\'s answer was not a list.')
    return result


def list_models(api_key, **kw):
    """Names of models that can generate content, such as 'gemini-2.5-flash'."""
    names = []
    token = ''
    while True:
        data = _request('GET', f'{API_ROOT}/models?pageSize=100' + (f'&pageToken={token}' if token else ''), api_key, **kw)
        for m in data.get('models', []):
            if 'generateContent' in m.get('supportedGenerationMethods', []):
                names.append(m['name'].split('/', 1)[-1])
        token = data.get('nextPageToken')
        if not token:
            return sorted(names)


# ------------------------------------------------------------------------------------------------------------- input

class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ('script', 'style', 'head')

    def handle_endtag(self, tag):
        self.skip -= tag in ('script', 'style', 'head') and self.skip > 0

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def epub_excerpt(path, limit=1500):
    """The first readable text of an EPUB (skipping very short front-matter pages), or ''. Never raises."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(('.xhtml', '.html', '.htm'))]
            names.sort()
            for name in names:
                parser = _Text()
                parser.feed(z.read(name).decode('utf-8', 'replace'))
                text = re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()
                if len(text) > 400:
                    return text[:limit]
    except Exception:
        pass
    return ''


NUMBERED = re.compile(r'^\s*(?:book|bk\.?|vol(?:ume)?\.?|part|#)\s*(\d+(?:\.\d+)?)\s*[-:\u2013\u2014.]\s*(\S.*)$', re.I)


def numbering_in_title(title):
    """('1', 'the force awakens') for "book 1 - the force awakens"; None when the title has no leading volume marker."""
    m = NUMBERED.match(title or '')
    return (m.group(1), m.group(2).strip()) if m else None


def build_prompt(books):
    """books: dicts with id, title, authors, series, series_index, tags, languages, publisher, filenames and optional excerpt."""
    rows = []
    for b in books:
        row = {'id': b['id'], 'title': b.get('title') or '', 'authors': list(b.get('authors') or []),
               'series': b.get('series') or '', 'series_index': b.get('series_index'), 'tags': list(b.get('tags') or []),
               'language': ', '.join(b.get('languages') or []), 'publisher': b.get('publisher') or '',
               'file_names': list(b.get('filenames') or [])}
        found = numbering_in_title(b.get('title'))
        if found:
            row['numbering_in_title'] = {'volume': float(found[0]), 'title_without_it': found[1]}
        if b.get('excerpt'):
            row['opening_text'] = b['excerpt']
        rows.append(row)
    return 'Books to check:\n' + json.dumps(rows, ensure_ascii=False, indent=1)


def _load_zip_entry(path, entry_name):
    try:
        with zipfile.ZipFile(path) as z:
            return z.read(entry_name)
    except Exception:
        return None


def _get_default_image_hydrator():
    try:
        from calibre_plugins.gemini_library_fixer.action import prepare_image
        return prepare_image
    except Exception:
        try:
            from gemini.action import prepare_image
            return prepare_image
        except Exception:
            return None


def hydrate_image(image, image_hydrator=None, db=None):
    """Converts a lightweight image descriptor dict into a hydrated image dict with base64 'data', or returns None if hydration fails.
    If 'data' is already in image, returns image as-is (backward compatibility)."""
    if not isinstance(image, dict):
        return None
    if 'data' in image and image['data']:
        return image

    raw_data = None
    if callable(image.get('loader')):
        try:
            raw_data = image['loader']()
        except Exception:
            raw_data = None
    elif image.get('type') == 'front' and db is not None and image.get('book_id'):
        try:
            raw_data = db.cover(image['book_id'])
        except Exception:
            raw_data = None
    elif image.get('type') == 'back' and image.get('path') and image.get('entry_name'):
        try:
            raw_data = _load_zip_entry(image['path'], image['entry_name'])
        except Exception:
            raw_data = None
    elif image.get('raw_bytes'):
        raw_data = image['raw_bytes']

    if not raw_data:
        return None

    hydrator = image_hydrator or _get_default_image_hydrator()
    b64 = None
    if hydrator is not None:
        try:
            b64 = hydrator(raw_data)
        except Exception:
            b64 = None
    else:
        try:
            if isinstance(raw_data, str):
                b64 = raw_data
            elif isinstance(raw_data, bytes):
                import base64
                b64 = base64.b64encode(raw_data).decode('ascii')
        except Exception:
            b64 = None

    if not b64:
        return None

    return {
        'label': image.get('label', 'cover'),
        'mime': image.get('mime', 'image/jpeg'),
        'data': b64
    }


def build_parts(books, image_hydrator=None, db=None, hydrate=True):
    """The request parts: the text prompt first, then each book's pictures, every one labelled with its book id."""
    parts = [{'text': build_prompt(books)}]
    for b in books:
        for image in b.get('images') or []:
            if hydrate:
                h_img = hydrate_image(image, image_hydrator=image_hydrator, db=db)
                if h_img and h_img.get('data'):
                    parts.append({'text': f"Image for book id {b['id']} ({h_img['label']}):"})
                    parts.append({'inlineData': {'mimeType': h_img.get('mime', 'image/jpeg'), 'data': h_img['data']}})
            else:
                parts.append({'text': f"Image for book id {b['id']} ({image['label']}):"})
    return parts


def estimate_batch_usage(books, batch_size=15, covers=False, min_interval=0, model=DEFAULT_MODEL):
    """Calculates text characters, text tokens, image tokens, batch counts, estimated duration, and costs
    before dispatching API requests. Returns a dict with estimation details."""
    if not books:
        return {
            'books_count': 0,
            'text_chars': 0,
            'text_tokens': 0,
            'image_count': 0,
            'image_tokens': 0,
            'prompt_tokens': 0,
            'batch_count': 0,
            'per_request': 0,
            'estimated_duration': 0,
            'estimated_cost': 0.0,
        }

    bsize = max(1, int(batch_size))
    per_request = min(bsize, 8) if covers else bsize

    sorted_books = sorted(books, key=lambda b: ((b.get('authors') or [''])[0].lower(), (b.get('series') or '').lower(), (b.get('title') or '').lower()))

    total_text_chars = 0
    total_images = 0
    batch_count = 0

    for start in range(0, len(sorted_books), per_request):
        batch = sorted_books[start:start + per_request]
        batch_count += 1

        batch_text_chars = len(INSTRUCTIONS)
        parts = build_parts(batch, hydrate=False)
        for part in parts:
            if 'text' in part and part['text']:
                batch_text_chars += len(part['text'])
        total_text_chars += batch_text_chars

        for b in batch:
            total_images += len(b.get('images') or [])

    text_tokens = (total_text_chars + 3) // 4
    image_tokens = total_images * IMAGE_TOKENS_PER_IMAGE
    prompt_tokens = text_tokens + image_tokens

    interval = max(0, int(min_interval or 0))
    estimated_duration = max(0, batch_count - 1) * interval + batch_count * 2

    rate = MODEL_COST_PER_1M.get(str(model or '').strip().lower(), 0.075)
    estimated_cost = (prompt_tokens / 1_000_000.0) * rate

    return {
        'books_count': len(books),
        'text_chars': total_text_chars,
        'text_tokens': text_tokens,
        'image_count': total_images,
        'image_tokens': image_tokens,
        'prompt_tokens': prompt_tokens,
        'batch_count': batch_count,
        'per_request': per_request,
        'estimated_duration': estimated_duration,
        'estimated_cost': round(estimated_cost, 6),
    }


# ------------------------------------------------------------------------------------------------ covers and ISBNs

BACK_NAME = re.compile(r'(^|[^a-z])(back|rear)([^a-z]|$)', re.I)
IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}


def find_back_cover_descriptor(path, fmt):
    """Returns a lightweight descriptor dict for the back cover or last page, or None. Never raises."""
    try:
        with zipfile.ZipFile(path) as z:
            images = [i for i in z.infolist() if i.filename.lower().endswith(IMAGE_EXT) and 0 < i.file_size <= IMAGE_LIMIT]
            if fmt.upper() == 'CBZ' and images:
                last = sorted(images, key=lambda i: i.filename.lower())[-1]
                name = last.filename.rsplit('/', 1)[-1]
                return {
                    'type': 'back',
                    'label': f'last page ({name})',
                    'mime': 'image/jpeg',
                    'path': path,
                    'entry_name': last.filename,
                    'loader': lambda p=path, e=last.filename: _load_zip_entry(p, e)
                }
            if fmt.upper() == 'EPUB':
                for info in images:
                    name = info.filename.rsplit('/', 1)[-1]
                    if BACK_NAME.search(name):
                        return {
                            'type': 'back',
                            'label': f'back cover ({name})',
                            'mime': 'image/jpeg',
                            'path': path,
                            'entry_name': info.filename,
                            'loader': lambda p=path, e=info.filename: _load_zip_entry(p, e)
                        }
    except Exception:
        pass
    return None


def find_back_cover(path, fmt):
    """(bytes, description) for the back cover of a book file, or None. Calibre stores only a front cover, so this looks inside
    the file: an EPUB image whose name says back/rear (back-cover.jpg), or the last page of a CBZ comic. Never raises."""
    desc = find_back_cover_descriptor(path, fmt)
    if not desc:
        return None
    data = desc['loader']() if callable(desc.get('loader')) else _load_zip_entry(desc['path'], desc['entry_name'])
    if data:
        return data, desc['label']
    return None


def clean_isbn(text):
    """The ISBN-10 or ISBN-13 in text as digits (final X kept), or '' unless the check digit is right."""
    text = re.sub(r'(?i)^\s*isbn(?:[- ]?1[03]\s*:|\s*:)?', '', str(text or ''))  # "ISBN-13:" / "ISBN:" / "ISBN "
    digits = re.sub(r'[\s-]', '', text.upper())
    if re.fullmatch(r'\d{13}', digits):
        if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits)) % 10 == 0:
            return digits
    elif re.fullmatch(r'\d{9}[\dX]', digits):
        if sum((10 - i) * (10 if c == 'X' else int(c)) for i, c in enumerate(digits)) % 11 == 0:
            return digits
    return ''


# ------------------------------------------------------------------------------------------------------- validation

def _clean(text, limit):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text if 0 < len(text) <= limit else ''


def _same_ci(a, b):
    return re.sub(r'\W+', '', a).lower() == re.sub(r'\W+', '', b).lower()


def sanitize(book, raw, fields, tags_may_be_removed=False):
    """Turns one raw model answer into {field: (old, new)} for a real, safe change. Anything doubtful is dropped."""
    changes = {}
    if 'title' in fields:
        new = _clean(raw.get('title'), 300)
        if new and new != book.get('title'):
            changes['title'] = (book.get('title') or '', new)
    if 'authors' in fields and isinstance(raw.get('authors'), list):
        new = list(dict.fromkeys(a for a in (_clean(x, 100) for x in raw['authors']) if a))[:10]
        old = list(book.get('authors') or [])
        # "Unknown" is Calibre's placeholder; never write it back as an author name.
        new = [a for a in new if a.lower() not in ('unknown', 'n/a', 'none')]
        if new and new != old:
            changes['authors'] = (old, new)
    if 'series' in fields:
        series = _clean(raw.get('series'), 200)
        index = raw.get('series_index')
        try:
            index = float(index) if index is not None and not isinstance(index, bool) else None
        except (TypeError, ValueError):
            index = None
        if index is not None and not 0 <= index <= 10000:
            index = None
        if not series and book.get('series') and index is not None and index != book.get('series_index'):
            series = book['series']  # the book already has a series; only the volume number is being fixed
        if series and (series != book.get('series') or (index is not None and index != book.get('series_index'))):
            old_index = book.get('series_index') if book.get('series') else None
            changes['series'] = ((book.get('series') or '', old_index),
                                 (series, index if index is not None else book.get('series_index') or 1.0))
    if 'tags' in fields and isinstance(raw.get('tags'), list):
        old = list(book.get('tags') or [])
        proposed = list(dict.fromkeys(t for t in (_clean(x, 60) for x in raw['tags']) if t))[:40]
        have = {t.lower() for t in old}
        if tags_may_be_removed:
            new = proposed
        else:
            new = old + [t for t in proposed if t.lower() not in have][:5]
        if new and sorted(new) != sorted(old):
            changes['tags'] = (old, new)
    if 'language' in fields and not book.get('languages'):
        code = str(raw.get('language') or '').strip().lower()
        if re.fullmatch(r'[a-z]{3}', code):
            changes['language'] = ([], [code])
    if 'isbn' in fields:
        isbn = clean_isbn(raw.get('isbn'))
        old = clean_isbn((book.get('identifiers') or {}).get('isbn'))
        if isbn and isbn != old:
            changes['isbn'] = ((book.get('identifiers') or {}).get('isbn', ''), isbn)
    return changes


def is_big_change(changes):
    """True when a title or author change looks like a different book rather than a tidy-up."""
    if 'title' in changes:
        old, new = changes['title']
        if difflib.SequenceMatcher(None, re.sub(r'\W+', ' ', old).lower().strip(), re.sub(r'\W+', ' ', new).lower().strip()).ratio() < BIG_CHANGE_RATIO:
            return True
    if 'authors' in changes:
        old, new = changes['authors']
        if old and not any(_same_ci(o, n) or set(re.findall(r'\w+', o.lower())) & set(re.findall(r'\w+', n.lower())) for o in old for n in new):
            return True
    return False


# ------------------------------------------------------------------------------------------------------------- driver

def plan_fixes(books, api_key, model=DEFAULT_MODEL, fields=FIELDS, min_confidence='medium', batch_size=15,
               tags_may_be_removed=False, progress=lambda text: None, cancelled=lambda: False, on_event=lambda kind, **info: None,
               min_interval=0, sleep=time.sleep, clock=time.monotonic, image_hydrator=None, db=None, **kw):
    """Returns (suggestions, skipped). A suggestion is {'id', 'title', 'changes', 'confidence', 'reason', 'big'}; skipped is a
    list of 'title: why' strings for books that could not be checked. On cancel, returns what was finished so far.

    on_event(kind, **info) reports the work as it happens: 'sending' (books=batch), 'received' (count), 'answer' (book, changes,
    confidence, reason, kept, dropped_why), 'notice' (text), 'skipped' (text), 'progress' (done, total)."""
    suggestions, skipped = [], []
    state = {'done': 0, 'last': None}
    threshold = CONFIDENCE.get(min_confidence, 1)
    by_id = {b['id']: b for b in books}
    total = len(books)

    def finish(count):
        state['done'] += count
        on_event('progress', done=state['done'], total=total)

    def pace():
        """Free keys allow only a few requests a minute, so leave a gap between requests instead of running into the limit."""
        if min_interval and state['last'] is not None:
            wait = state['last'] + min_interval - clock()
            if wait > 2:
                on_event('notice', text=f'Pausing {wait:.0f}s between requests to stay inside the free-tier limit')
            while wait > 0 and not cancelled():
                sleep(min(1.0, wait))
                wait -= 1.0
        state['last'] = clock()

    def ask(batch):
        pace()
        if cancelled():
            return
        on_event('sending', books=batch)
        try:
            parts = build_parts(batch, image_hydrator=image_hydrator, db=db, hydrate=True)
            answers = generate(api_key, model, parts, cancelled=cancelled, sleep=sleep,
                               notify=lambda t: on_event('notice', text=t), **kw)
        except Blocked as exc:
            if len(batch) == 1:
                skipped.append(f"{batch[0].get('title') or batch[0]['id']}: {exc}")
                on_event('skipped', text=skipped[-1])
                finish(1)
                return
            on_event('notice', text=f'{exc} Splitting these {len(batch)} books to find the one responsible.')
            half = len(batch) // 2  # find the book that trips the filter instead of losing the whole batch
            ask(batch[:half])
            ask(batch[half:])
            return
        wanted = {b['id'] for b in batch}
        seen = set()
        on_event('received', count=len(answers))
        for raw in answers:
            if not isinstance(raw, dict) or raw.get('id') not in wanted or raw['id'] in seen:
                continue  # an id we did not ask about, or a repeat, is ignored
            seen.add(raw['id'])
            book = by_id[raw['id']]
            confidence = str(raw.get('confidence') or 'low').lower()
            changes = sanitize(book, raw, fields, tags_may_be_removed)
            enough = CONFIDENCE.get(confidence, 0) >= threshold
            on_event('answer', book=book, changes=changes, confidence=confidence, reason=_clean(raw.get('reason'), 300),
                     cover_text=_clean(raw.get('cover_text'), 400),
                     kept=bool(changes) and enough, dropped_why=None if (enough or not changes) else f'{confidence} confidence is below your minimum')
            if changes and enough:
                suggestions.append({'id': book['id'], 'title': book.get('title') or '', 'changes': changes, 'confidence': confidence,
                                    'reason': _clean(raw.get('reason'), 300), 'big': is_big_change(changes)})
        for missing in wanted - seen:
            skipped.append(f"{by_id[missing].get('title') or missing}: no answer from the model")
            on_event('skipped', text=skipped[-1])
        finish(len(batch))

    size = max(1, int(batch_size))
    # Books by the same author go in the same request, so volumes of one series can be recognised together.
    books = sorted(books, key=lambda b: ((b.get('authors') or [''])[0].lower(), (b.get('series') or '').lower(), (b.get('title') or '').lower()))
    for start in range(0, len(books), size):
        if cancelled():
            break
        progress(f'Asking Gemini… {min(start + size, len(books))} of {len(books)} books')
        try:
            ask(books[start:start + size])
        except QuotaExhausted as exc:
            left = total - state['done']
            skipped.append(f'{left} book(s) not checked: request limit used up')
            on_event('notice', text=str(exc))
            on_event('skipped', text=skipped[-1])
            break
        except GeminiError as exc:
            if str(exc) == 'Cancelled':
                break
            msg = sanitize_error_message(exc, api_key)
            raise GeminiPartialError(msg, suggestions=suggestions, skipped=skipped, original_error=exc) from exc
        except Exception as exc:
            msg = sanitize_error_message(exc, api_key)
            raise GeminiPartialError(msg, suggestions=suggestions, skipped=skipped, original_error=exc) from exc
    return suggestions, skipped
