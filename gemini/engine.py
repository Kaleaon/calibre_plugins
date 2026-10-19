"""Asks the Google Gemini API to suggest fixes for book metadata, and checks the answers. Pure standard library so it can be
tested outside Calibre.

Nothing here changes a library. plan_fixes() returns suggestions; the caller shows them and applies only what the user accepts.
The model is told to leave anything it is unsure about alone, and every answer is validated again here before it is offered.
"""
import difflib
import json
import os
import re
import string
import time
import urllib.error
import urllib.request
import zipfile
try:
    from .storykit import strip_tags
except ImportError:
    try:
        from storykit import strip_tags
    except ImportError:
        from common.storykit import strip_tags

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

BUILTIN_PRESETS = {
    'Default Ebook Cleanup': {
        'name': 'Default Ebook Cleanup',
        'description': 'Standard metadata cleanup instructions for Calibre ebook collections.',
        'text': INSTRUCTIONS,
        'read_only': True,
    },
    'Comics & Manga Metadata': {
        'name': 'Comics & Manga Metadata',
        'description': 'Tailored rules for comics and manga: volume/chapter numbers, publisher logos, and genre demographics.',
        'text': '''You clean up the metadata of comic books and manga in a personal Calibre library. You get a JSON list of books; for each you may suggest corrections for these fields: {fields}.

Rules:
- Identify series name, volume number, and chapter numbers from issue titles or file names (e.g. "Spider-Man v2 #10" or "One Piece Vol. 101"). Extract volume and issue numbers into series_index.
- Normalize publisher and imprint names (e.g. Marvel Comics, DC Comics, Image Comics, Shueisha, Kodansha).
- Fix creator names to "First Last" format and list primary creators clearly.
- Tags: keep relevant genre tags ({tag_policy}), including comic tropes, manga demographics (Shonen, Seinen, Shojo), or publisher tags.
- Cover pictures: inspect covers for volume, issue numbers, subtitle, and publisher logos.
- Never invent facts. Set confidence to at least {min_confidence} when sure.
- Return one object for every book id you were given, in the same order.''',
        'read_only': True,
    },
    'Academic & Technical Papers': {
        'name': 'Academic & Technical Papers',
        'description': 'Formal title, author list, and subject tag normalization for technical papers and textbooks.',
        'text': '''You clean up metadata for academic papers, technical ebooks, and textbooks in a personal Calibre library. Fields to fix: {fields}.

Rules:
- Normalize academic authors as "First Last" and list all co-authors accurately.
- Retain full formal titles without truncation or informal abbreviations.
- Extract publication year or journal/conference series information into series if appropriate.
- Tags: clean domain-specific tags and subject keywords ({tag_policy}).
- Confidence threshold: set confidence to at least {min_confidence} when sure.
- Return one object for every book id in order.''',
        'read_only': True,
    },
}


class TemplateError(Exception):
    """Raised when template variable interpolation fails or syntax is malformed."""


class _StrictTemplateFormatter(string.Formatter):
    """Custom Formatter that ensures unknown keys or malformed syntax raise KeyError/ValueError."""
    def get_value(self, key, args, kwargs):
        if isinstance(key, str):
            if key in kwargs:
                return kwargs[key]
            raise KeyError(f"Unknown template variable {{{key}}}")
        return super().get_value(key, args, kwargs)


def get_all_templates(custom_templates=None):
    """Returns a dict combining BUILTIN_PRESETS and custom_templates."""
    templates = dict(BUILTIN_PRESETS)
    if custom_templates and isinstance(custom_templates, dict):
        for name, data in custom_templates.items():
            if isinstance(data, dict):
                entry = dict(data)
                entry['read_only'] = False
                templates[name] = entry
    return templates


def get_default_template_vars(fields=None, min_confidence='medium', tags_remove=False,
                              batch_size=15, model=DEFAULT_MODEL, covers=False,
                              excerpt=False, **extra):
    f_list = fields if fields is not None else FIELDS
    if isinstance(f_list, (list, tuple)):
        fields_str = ', '.join(f_list)
    else:
        fields_str = str(f_list)

    if tags_remove:
        tag_policy_str = "allow tag removal and renaming"
    else:
        tag_policy_str = "keep existing tags, merge duplicates, add at most 5 genre tags"

    vars_dict = {
        'fields': fields_str,
        'min_confidence': str(min_confidence or 'medium'),
        'tag_policy': tag_policy_str,
        'batch_size': str(batch_size),
        'model': str(model or DEFAULT_MODEL),
        'covers': 'enabled' if covers else 'disabled',
        'excerpt': 'enabled' if excerpt else 'disabled',
    }
    vars_dict.update({k: str(v) for k, v in extra.items()})
    return vars_dict


def render_template(template_text, template_vars=None):
    """
    Renders template_text using template_vars dict.
    Raises TemplateError if syntax is malformed or an unknown variable is referenced.
    """
    if not template_text:
        return INSTRUCTIONS
    if template_vars is None:
        template_vars = get_default_template_vars()
    formatter = _StrictTemplateFormatter()
    try:
        return formatter.format(template_text, **template_vars)
    except (KeyError, ValueError, IndexError) as exc:
        raise TemplateError(f"Malformed template syntax or unknown variable: {exc}") from exc
    except Exception as exc:
        raise TemplateError(f"Error rendering template: {exc}") from exc


def compile_instructions(template_text=None, template_vars=None, notify=None):
    """
    Compiles system instructions from template_text and template_vars.
    If template_text is None or empty, or if rendering fails due to syntax errors,
    falls back to default INSTRUCTIONS and invokes notify if provided.
    """
    if not template_text:
        template_text = INSTRUCTIONS
    try:
        return render_template(template_text, template_vars)
    except TemplateError as exc:
        if notify:
            notify(f"⚠ Template syntax error ({exc}); falling back to default instructions.")
        return INSTRUCTIONS


def get_system_instructions(mode='append', custom_text='', base_instructions=None):
    """Returns compiled system instructions based on mode ('append' or 'override') and custom_text."""
    base = base_instructions if base_instructions is not None else INSTRUCTIONS
    text = str(custom_text or '').strip()
    if not text:
        return base
    if mode == 'override':
        return text
    return f"{base}\n\n{text}"


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
        if len(key_str) >= 4:
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


def generate(api_key, model, prompt, system_instruction_mode='append', custom_instructions='', system_instruction=None, **kw):
    """Asks the model for JSON that matches RESPONSE_SCHEMA and returns the parsed list. prompt is text, or a list of API parts."""
    if system_instruction is not None:
        sys_inst = system_instruction
    else:
        instructions = kw.pop('instructions', None)
        template_vars = kw.pop('template_vars', None)
        compiled = compile_instructions(instructions, template_vars, notify=kw.get('notify'))
        sys_inst = get_system_instructions(system_instruction_mode, custom_instructions, base_instructions=compiled)
    parts = [{'text': prompt}] if isinstance(prompt, str) else prompt
    body = json.dumps({
        'systemInstruction': {'parts': [{'text': sys_inst}]},
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

def epub_excerpt(path, limit=1500):
    """The first readable text of an EPUB (skipping very short front-matter pages), or ''. Never raises."""
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(('.xhtml', '.html', '.htm'))]
            names.sort()
            for name in names:
                html_content = z.read(name).decode('utf-8', 'replace')
                text = strip_tags(html_content)
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


def build_parts(books):
    """The request parts: the text prompt first, then each book's pictures, every one labelled with its book id."""
    parts = [{'text': build_prompt(books)}]
    for b in books:
        for image in b.get('images') or []:
            parts.append({'text': f"Image for book id {b['id']} ({image['label']}):"})
            parts.append({'inlineData': {'mimeType': image.get('mime', 'image/jpeg'), 'data': image['data']}})
    return parts


def estimate_batch_usage(books, batch_size=15, covers=False, min_interval=0, model=DEFAULT_MODEL,
                         system_instruction_mode='append', custom_instructions='', system_instruction=None,
                         instructions=None, template_vars=None):
    """Calculates text characters, text tokens, image tokens, batch counts, estimated duration, and costs
    before dispatching API requests. Returns a dict with estimation details."""
    if system_instruction is not None:
        sys_inst = system_instruction
    else:
        if template_vars is None:
            template_vars = get_default_template_vars(covers=covers, model=model, batch_size=batch_size)
        compiled = compile_instructions(instructions, template_vars)
        sys_inst = get_system_instructions(system_instruction_mode, custom_instructions, base_instructions=compiled)

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

    sys_inst_len = len(sys_inst)

    bsize = max(1, int(batch_size))
    per_request = min(bsize, 8) if covers else bsize

    sorted_books = sorted(books, key=lambda b: ((b.get('authors') or [''])[0].lower(), (b.get('series') or '').lower(), (b.get('title') or '').lower()))

    total_text_chars = 0
    total_images = 0
    batch_count = 0

    for start in range(0, len(sorted_books), per_request):
        batch = sorted_books[start:start + per_request]
        batch_count += 1

        batch_text_chars = sys_inst_len
        parts = build_parts(batch)
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


def _natural_sort_key(s):
    name = s.filename if hasattr(s, 'filename') else str(s)
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', name)]


def find_back_cover(path, fmt):
    """(bytes, description) for the back cover of a book file, or None. Calibre stores only a front cover, so this looks inside
    the file: an EPUB image whose name says back/rear (back-cover.jpg), or the last page of a CBZ comic. Never raises."""
    try:
        with zipfile.ZipFile(path) as z:
            images = [i for i in z.infolist() if i.filename.lower().endswith(IMAGE_EXT) and 0 < i.file_size <= IMAGE_LIMIT]
            if fmt.upper() == 'CBZ' and images:
                last = sorted(images, key=_natural_sort_key)[-1]
                return z.read(last), f'last page ({last.filename.rsplit("/", 1)[-1]})'
            if fmt.upper() == 'EPUB':
                for info in images:
                    name = info.filename.rsplit('/', 1)[-1]
                    if BACK_NAME.search(name):
                        return z.read(info), f'back cover ({name})'
    except Exception:
        pass
    return None


def clean_isbn(text):
    """The ISBN-10 or ISBN-13 in text as digits (final X kept), or '' unless the check digit is right."""
    if text is None:
        return ''
    raw = str(text)
    text_clean = re.sub(r'(?i)^\s*isbn(?:[\s-]*1[03])?\s*:?\s*', '', raw)
    candidates = re.finditer(r'\b(?:(?:\d[ -]*){12}\d|(?:\d[ -]*){9}[\dX])\b', text_clean, re.IGNORECASE)
    for match in candidates:
        digits = re.sub(r'[\s-]', '', match.group(0).upper())
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


def get_fallback_model(model):
    """Returns the lightweight flash-lite variant for the given model if available."""
    m = str(model or '').strip().lower()
    if '1.5' in m:
        if 'flash-lite' in m:
            return model
        return 'gemini-1.5-flash-lite'
    else:
        if 'flash-lite' in m:
            return model
        return 'gemini-2.5-flash-lite'


def is_rate_limit_error(exc):
    """Returns True if exc represents a transient HTTP 429 rate limit error (not a daily QuotaExhausted error)."""
    if isinstance(exc, QuotaExhausted):
        return False
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
        return True
    msg = str(exc).lower()
    if 'cancelled' in msg:
        return False
    return any(k in msg for k in ('429', 'request limit', 'rate limit', 'resource exhausted'))


# ------------------------------------------------------------------------------------------------------------- driver

def plan_fixes(books, api_key, model=DEFAULT_MODEL, fields=FIELDS, min_confidence='medium', batch_size=15,
               tags_may_be_removed=False, progress=lambda text: None, cancelled=lambda: False, on_event=lambda kind, **info: None,
               min_interval=0, sleep=time.sleep, clock=time.monotonic,
               system_instruction_mode='append', custom_instructions='', system_instruction=None, **kw):
    """Returns (suggestions, skipped). A suggestion is {'id', 'title', 'changes', 'confidence', 'reason', 'big'}; skipped is a
    list of 'title: why' strings for books that could not be checked. On cancel, returns what was finished so far.

    on_event(kind, **info) reports the work as it happens: 'sending' (books=batch), 'received' (count), 'answer' (book, changes,
    confidence, reason, kept, dropped_why), 'notice' (text), 'skipped' (text), 'progress' (done, total)."""
    suggestions, skipped = [], []
    state = {'done': 0, 'last': None}
    threshold = CONFIDENCE.get(min_confidence, 1)
    by_id = {b['id']: b for b in books}
    total = len(books)
    current_model = [model]

    instructions = kw.pop('instructions', None)
    template_vars = kw.pop('template_vars', None)
    if template_vars is None:
        template_vars = get_default_template_vars(
            fields=fields,
            min_confidence=min_confidence,
            tags_remove=tags_may_be_removed,
            batch_size=batch_size,
            model=model,
        )

    compiled_inst = compile_instructions(instructions, template_vars, notify=lambda msg: on_event('notice', text=msg))
    if system_instruction is not None:
        sys_inst = system_instruction
    else:
        sys_inst = get_system_instructions(system_instruction_mode, custom_instructions, base_instructions=compiled_inst)

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

    def ask_direct(batch):
        pace()
        if cancelled():
            return
        on_event('sending', books=batch)
        answers = generate(api_key, current_model[0], build_parts(batch), system_instruction=sys_inst, cancelled=cancelled, sleep=sleep,
                           notify=lambda t: on_event('notice', text=t), **kw)
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

    def ask(batch):
        if cancelled():
            return
        try:
            ask_direct(batch)
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
        except Exception as exc:
            if str(exc) == 'Cancelled' or cancelled():
                raise
            if is_rate_limit_error(exc):
                if len(batch) > 1:
                    on_event('notice', text=f'Rate limit hit (HTTP 429) on batch of {len(batch)} books. Splitting into single-item requests with pacing delays.')
                    for item in batch:
                        if cancelled():
                            break
                        ask([item])
                    return
                else:
                    item = batch[0]
                    fallback = get_fallback_model(current_model[0])
                    can_downgrade = (fallback != current_model[0])
                    if can_downgrade:
                        on_event('notice', text=f'Rate limit hit on model {current_model[0]}. Downgrading model to {fallback}.')
                        current_model[0] = fallback
                        try:
                            ask_direct(batch)
                            return
                        except Exception as retry_exc:
                            if str(retry_exc) == 'Cancelled' or cancelled():
                                raise
                            if not is_rate_limit_error(retry_exc):
                                raise
                            exc = retry_exc

                    backoff = 2.0
                    for attempt in range(2):
                        if cancelled():
                            return
                        on_event('notice', text=f'Rate limit hit for "{item.get("title") or item["id"]}". Waiting {backoff:.0f}s before retrying single item.')
                        left = backoff
                        while left > 0 and not cancelled():
                            sleep(min(1.0, left))
                            left -= 1.0
                        if cancelled():
                            return
                        try:
                            ask_direct(batch)
                            return
                        except Exception as retry_exc:
                            if str(retry_exc) == 'Cancelled' or cancelled():
                                raise
                            if not is_rate_limit_error(retry_exc):
                                raise
                            exc = retry_exc
                        backoff *= 2.0

                    msg = sanitize_error_message(exc, api_key)
                    skipped.append(f"{item.get('title') or item['id']}: request limit used up ({msg})")
                    on_event('notice', text=f'Request limit used up for {item.get("title") or item["id"]}: {msg}')
                    on_event('skipped', text=skipped[-1])
                    finish(1)
                    return
            else:
                raise

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
            on_event('notice', text=sanitize_error_message(exc, api_key))
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


# ------------------------------------------------------------------------------------------------ batches and compilation

class CompiledBatchResult:
    """Compiled results from separate batch output files, maintaining backwards compatibility
    with code that expects a (suggestions, skipped) 2-tuple."""

    def __init__(self, suggestions, skipped, metadata=None, batch_files=None):
        self.suggestions = list(suggestions) if suggestions is not None else []
        self.skipped = list(skipped) if skipped is not None else []
        self.metadata = dict(metadata or {})
        self.batch_files = list(batch_files or [])

    def __iter__(self):
        return iter((self.suggestions, self.skipped))

    def __getitem__(self, idx):
        return (self.suggestions, self.skipped)[idx]

    def __len__(self):
        return 2

    @property
    def total_batches(self):
        return self.metadata.get('total_batches', len(self.batch_files))

    @property
    def total_suggestions(self):
        return len(self.suggestions)

    @property
    def total_skipped(self):
        return len(self.skipped)


def save_batch_output(batch_dir, batch_index, suggestions, skipped=None, book_ids=None, total_batches=None, metadata=None):
    """Saves a single batch's suggestions and skipped items to a separate JSON file in batch_dir.
    Uses atomic write (.tmp -> rename) so corrupt partial files are avoided.
    Returns the absolute path to the saved file."""
    os.makedirs(batch_dir, exist_ok=True)
    filename = f'batch_{int(batch_index):04d}.json'
    filepath = os.path.join(batch_dir, filename)
    tmppath = filepath + '.tmp'

    serialized_suggestions = []
    for s in (suggestions or []):
        entry = dict(s)
        if 'changes' in entry and isinstance(entry['changes'], dict):
            raw_changes = {}
            for field, vals in entry['changes'].items():
                if isinstance(vals, (list, tuple)) and len(vals) == 2:
                    raw_changes[field] = [vals[0], vals[1]]
                else:
                    raw_changes[field] = vals
            entry['changes'] = raw_changes
        serialized_suggestions.append(entry)

    payload = {
        'version': 1,
        'batch_index': int(batch_index),
        'total_batches': int(total_batches) if total_batches is not None else None,
        'book_ids': list(book_ids or []),
        'timestamp': time.time(),
        'suggestions': serialized_suggestions,
        'skipped': list(skipped or []),
        'metadata': dict(metadata or {}),
    }

    with open(tmppath, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    if os.path.exists(filepath):
        os.replace(tmppath, filepath)
    else:
        os.rename(tmppath, filepath)
    return filepath


def load_batch_output(file_path):
    """Loads a single batch JSON file and returns its payload dictionary,
    restoring tuple structures for series field changes."""
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    for s in data.get('suggestions', []):
        if 'changes' in s and isinstance(s['changes'], dict):
            fixed_changes = {}
            for field, pair in s['changes'].items():
                if isinstance(pair, list) and len(pair) == 2:
                    old_val, new_val = pair[0], pair[1]
                    if field == 'series':
                        if isinstance(old_val, list):
                            old_val = tuple(old_val)
                        if isinstance(new_val, list):
                            new_val = tuple(new_val)
                    fixed_changes[field] = (old_val, new_val)
                else:
                    fixed_changes[field] = pair
            s['changes'] = fixed_changes

    return data


def list_batch_files(batch_dir):
    """Returns a sorted list of batch file paths in batch_dir matching batch_*.json."""
    if not batch_dir or not os.path.isdir(batch_dir):
        return []
    files = []
    for name in os.listdir(batch_dir):
        if name.startswith('batch_') and name.endswith('.json') and not name.endswith('.tmp'):
            files.append(os.path.join(batch_dir, name))

    def sort_key(p):
        base = os.path.basename(p)
        m = re.search(r'batch_(\d+)\.json$', base)
        return (0, int(m.group(1))) if m else (1, base)

    return sorted(files, key=sort_key)


def compile_batch_outputs(batch_dir_or_files):
    """Compiles separate batch JSON files into a unified CompiledBatchResult (suggestions, skipped).
    Deduplicates suggestions by book id (keeping the latest occurrence) while preserving order.
    Accepts either a directory path or an iterable of file paths."""
    if isinstance(batch_dir_or_files, (list, tuple, set)):
        files = list(batch_dir_or_files)
    else:
        files = list_batch_files(batch_dir_or_files)

    all_suggestions = []
    seen_suggestion_ids = set()
    all_skipped = []
    seen_skipped = set()
    all_book_ids = set()

    for fpath in files:
        try:
            data = load_batch_output(fpath)
        except Exception:
            continue
        for s in data.get('suggestions', []):
            bid = s.get('id')
            if bid is not None:
                all_book_ids.add(bid)
                if bid in seen_suggestion_ids:
                    all_suggestions = [item for item in all_suggestions if item.get('id') != bid]
                seen_suggestion_ids.add(bid)
            all_suggestions.append(s)
        for sk in data.get('skipped', []):
            if sk not in seen_skipped:
                seen_skipped.add(sk)
                all_skipped.append(sk)
        for bid in data.get('book_ids', []):
            all_book_ids.add(bid)

    meta = {
        'total_batches': len(files),
        'total_books': len(all_book_ids),
        'total_suggestions': len(all_suggestions),
        'total_skipped': len(all_skipped),
    }
    return CompiledBatchResult(all_suggestions, all_skipped, metadata=meta, batch_files=files)


def clear_batch_outputs(batch_dir):
    """Removes all batch_*.json and batch_*.json.tmp files in batch_dir.
    Returns the count of deleted files."""
    if not batch_dir or not os.path.isdir(batch_dir):
        return 0
    count = 0
    for name in os.listdir(batch_dir):
        if name.startswith('batch_') and (name.endswith('.json') or name.endswith('.tmp')):
            try:
                os.remove(os.path.join(batch_dir, name))
                count += 1
            except OSError:
                pass
    return count


def get_completed_batch_book_ids(batch_dir):
    """Returns a set of all book IDs found in any existing batch files in batch_dir."""
    files = list_batch_files(batch_dir)
    ids = set()
    for fpath in files:
        try:
            data = load_batch_output(fpath)
            for bid in data.get('book_ids', []):
                ids.add(bid)
            for s in data.get('suggestions', []):
                if s.get('id') is not None:
                    ids.add(s['id'])
        except Exception:
            pass
    return ids


def estimate_library_summary(total_books, batch_size=10, covers=False, excerpt=False, min_interval=0, model=DEFAULT_MODEL, avg_chars_per_book=200, **kw):
    """Calculates estimated token counts, durations, and costs for a given number of books
    without requiring books to be loaded into memory."""
    bsize = max(1, int(batch_size or 10))
    per_request = min(bsize, 8) if covers else bsize
    batch_count = (total_books + per_request - 1) // per_request if total_books > 0 else 0
    text_chars_per_book = avg_chars_per_book + (1500 if excerpt else 0)
    total_text_chars = total_books * text_chars_per_book + batch_count * 1200
    text_tokens = (total_text_chars + 3) // 4
    image_tokens = total_books * (IMAGE_TOKENS_PER_IMAGE * (2 if covers else 0))
    prompt_tokens = text_tokens + image_tokens
    interval = max(0, int(min_interval or 0))
    estimated_duration = max(0, batch_count - 1) * interval + batch_count * 2
    rate = MODEL_COST_PER_1M.get(str(model or '').strip().lower(), 0.075)
    estimated_cost = (prompt_tokens / 1_000_000.0) * rate
    return {
        'books_count': total_books,
        'text_chars': total_text_chars,
        'text_tokens': text_tokens,
        'image_tokens': image_tokens,
        'prompt_tokens': prompt_tokens,
        'batch_count': batch_count,
        'per_request': per_request,
        'estimated_duration': estimated_duration,
        'estimated_cost': round(estimated_cost, 6),
    }


def plan_fixes_batched(fetch_books_fn, ids, api_key, batch_dir, batch_size=10, model=DEFAULT_MODEL,
                       fields=FIELDS, min_confidence='medium', tags_may_be_removed=False,
                       progress=lambda text: None, cancelled=lambda: False,
                       on_event=lambda kind, **info: None, min_interval=0,
                       resume=False, sleep=time.sleep, clock=time.monotonic, **kw):
    """Executes library book fixing in batches of batch_size (default 10), saving each batch's output
    separately to disk in batch_dir as it completes. Then compiles all separate batch outputs together.
    If an error or cancellation interrupts the process, already saved batches are preserved on disk.
    If resume=True, already completed book IDs are skipped."""
    os.makedirs(batch_dir, exist_ok=True)
    if resume:
        completed_ids = get_completed_batch_book_ids(batch_dir)
        target_ids = [bid for bid in ids if bid not in completed_ids]
        if completed_ids:
            on_event('notice', text=f'Resuming: {len(completed_ids)} book(s) previously saved in batches, {len(target_ids)} remaining.')
    else:
        clear_batch_outputs(batch_dir)
        target_ids = list(ids)

    size = max(1, int(batch_size or 10))
    chunks = [target_ids[i:i + size] for i in range(0, len(target_ids), size)]
    total_batches = len(chunks)

    existing_files = list_batch_files(batch_dir) if resume else []
    start_batch_num = len(existing_files) + 1

    for idx_offset, chunk_ids in enumerate(chunks):
        if cancelled():
            break
        current_batch_num = start_batch_num + idx_offset
        batch_label = f'Batch {current_batch_num}' + (f' of {total_batches + len(existing_files)}' if total_batches else '')
        progress(f'Processing {batch_label} ({len(chunk_ids)} books)…')

        batch_books = fetch_books_fn(chunk_ids)
        if not batch_books:
            continue

        try:
            batch_suggestions, batch_skipped = plan_fixes(
                batch_books, api_key, model=model, fields=fields,
                min_confidence=min_confidence, batch_size=len(batch_books),
                tags_may_be_removed=tags_may_be_removed,
                cancelled=cancelled, on_event=on_event,
                min_interval=min_interval, sleep=sleep, clock=clock, **kw
            )
        except (GeminiError, Exception) as exc:
            compiled = compile_batch_outputs(batch_dir)
            msg = sanitize_error_message(exc, api_key)
            raise GeminiPartialError(msg, suggestions=compiled.suggestions, skipped=compiled.skipped, original_error=exc) from exc

        saved_path = save_batch_output(
            batch_dir, current_batch_num, batch_suggestions,
            skipped=batch_skipped, book_ids=chunk_ids,
            total_batches=total_batches + len(existing_files)
        )
        on_event(
            'batch_saved',
            batch_index=current_batch_num,
            total_batches=total_batches + len(existing_files),
            file=saved_path,
            count=len(batch_suggestions),
            skipped_count=len(batch_skipped),
            book_ids=chunk_ids
        )

    return compile_batch_outputs(batch_dir)

