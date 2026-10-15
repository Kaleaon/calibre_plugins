"""Clients for the free services this plugin matches against. Pure standard library so they can be tested outside Calibre.

- MusicBrainz (https://musicbrainz.org/doc/MusicBrainz_API): open music database. No key. It asks for at most one request a
  second and an identifying User-Agent, both done here; it answers 503 when busy, which is retried.
- Cover Art Archive (https://coverartarchive.org): front cover images for MusicBrainz releases. No key.
- AcoustID (https://acoustid.org/webservice) with Chromaprint's `fpcalc` (https://acoustid.org/chromaprint): audio
  fingerprints. Needs a free application key that the user registers; `fpcalc` is a separate free download.
- Open Library (https://openlibrary.org/developers/api): open book catalogue, used for audiobooks and ordinary books.

Nothing here touches a Calibre library. The caller passes a `fetch` function in tests; the default uses urllib.
"""
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

VERSION = '1.0.0'
PROJECT_URL = 'https://github.com/Kaleaon/calibre_plugins'
MB_ROOT = 'https://musicbrainz.org/ws/2'
CAA_ROOT = 'https://coverartarchive.org'
ACOUSTID_URL = 'https://api.acoustid.org/v2/lookup'
OL_ROOT = 'https://openlibrary.org'
OL_COVERS = 'https://covers.openlibrary.org'
RETRY_STATUS = {429, 502, 503, 504}
# Minimum seconds between requests to the same host. MusicBrainz's rule is one a second; the others ask only for politeness
# (AcoustID allows 3 a second).
MIN_INTERVAL = {'musicbrainz.org': 1.1, 'api.acoustid.org': 0.4, 'openlibrary.org': 0.5, 'covers.openlibrary.org': 0.3,
                'coverartarchive.org': 0.3}
MAX_IMAGE_BYTES = 12 * 1024 * 1024


class ServiceError(Exception):
    """A problem the user should read. Never contains an API key."""


class FingerprintError(ServiceError):
    """fpcalc is missing or could not read one file: that file just goes without a fingerprint."""


class Cancelled(Exception):
    pass


def user_agent(contact=''):
    """MusicBrainz and Open Library ask for a User-Agent that says who is calling and how to reach them."""
    contact = contact.strip()
    return f'CalibreMediaMatcher/{VERSION} ( {contact or PROJECT_URL} )'


def urllib_fetch(url, headers, data, timeout):
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_IMAGE_BYTES + 1)


class Http:
    """Polite HTTP: one request at a time per host spaced by MIN_INTERVAL, retry on busy/rate-limit answers, cancellable."""

    def __init__(self, fetch=None, contact='', sleep=time.sleep, clock=time.monotonic, cancelled=lambda: False,
                 on_wait=lambda seconds, why: None, retries=4, timeout=30):
        self.fetch, self.contact, self.sleep, self.clock = fetch or urllib_fetch, contact, sleep, clock
        self.cancelled, self.on_wait, self.retries, self.timeout = cancelled, on_wait, retries, timeout
        self.last = {}

    def _wait(self, seconds, why):
        if seconds <= 0:
            return
        self.on_wait(seconds, why)
        end = self.clock() + seconds
        while True:
            if self.cancelled():
                raise Cancelled()
            left = end - self.clock()
            if left <= 0:
                return
            self.sleep(min(0.2, left))

    def request(self, url, data=None, accept='application/json'):
        host = urllib.parse.urlsplit(url).hostname or ''
        headers = {'User-Agent': user_agent(self.contact), 'Accept': accept}
        if data is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        for attempt in range(self.retries + 1):
            if self.cancelled():
                raise Cancelled()
            gap = MIN_INTERVAL.get(host, 0)
            if host in self.last:
                self._wait(self.last[host] + gap - self.clock(), f'pacing requests to {host}')
            self.last[host] = self.clock()
            try:
                return self.fetch(url, headers, data, self.timeout)
            except urllib.error.HTTPError as exc:
                self.last[host] = self.clock()
                if exc.code in RETRY_STATUS and attempt < self.retries:
                    try:
                        delay = float(exc.headers.get('Retry-After', '')) if exc.headers else 0
                    except ValueError:
                        delay = 0
                    self._wait(max(delay, 2 ** (attempt + 1)), f'{host} is busy (HTTP {exc.code})')
                    continue
                raise HttpStatus(exc.code, host) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException) as exc:
                if attempt < self.retries:
                    self._wait(2 ** (attempt + 1), f'could not reach {host}')
                    continue
                reason = getattr(exc, 'reason', exc)
                raise ServiceError(f'Could not reach {host}: {reason}') from None
        raise ServiceError(f'{host} did not answer')

    def json(self, url, params=None, data=None):
        if params:
            url = url + ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        raw = self.request(url, data=urllib.parse.urlencode(data).encode() if data else None)
        try:
            return json.loads(raw.decode('utf-8'))
        except ValueError:
            raise ServiceError(f'{urllib.parse.urlsplit(url).hostname} sent an answer that is not JSON') from None


class HttpStatus(ServiceError):
    def __init__(self, code, host):
        super().__init__(f'{host} answered HTTP {code}')
        self.code, self.host = code, host


# -- MusicBrainz ---------------------------------------------------------------------------------------------------------
def lucene_phrase(text):
    """A quoted Lucene phrase safe to embed in a MusicBrainz search query."""
    return '"' + re.sub(r'(["\\])', r'\\\1', text.replace('\n', ' ')).strip() + '"'


def mb_search_recordings(http, title='', artist='', album='', recording_id='', limit=25, recording_ids=None):
    """Recording search results (each with its releases and the track's place on them). Searches the more specific
    title+artist+album first; the caller decides whether to broaden."""
    raw_ids = recording_ids if recording_ids is not None else recording_id
    if raw_ids:
        if isinstance(raw_ids, (list, tuple, set)):
            ids = [r.strip() for r in raw_ids if isinstance(r, str) and r.strip()]
        elif isinstance(raw_ids, str) and raw_ids.strip():
            ids = [raw_ids.strip()]
        else:
            ids = []
        ids = ids[:4]
        if ids:
            query = ' OR '.join(f'rid:{r}' for r in ids)
        else:
            query = ''
    else:
        query = ''

    if not query:
        parts = []
        if title:
            parts.append(f'recording:{lucene_phrase(title)}')
        if artist:
            parts.append(f'artist:{lucene_phrase(artist)}')
        if album:
            parts.append(f'release:{lucene_phrase(album)}')
        if not parts:
            return []
        query = ' AND '.join(parts)
    try:
        data = http.json(MB_ROOT + '/recording/', {'query': query, 'fmt': 'json', 'limit': limit})
    except HttpStatus as exc:
        if exc.code == 400:  # a query MusicBrainz cannot parse is a "no result", not a failure of the whole run
            return []
        raise
    return data.get('recordings', [])


def cover_art_url(release_id, group_id=''):
    return [f'{CAA_ROOT}/release/{release_id}/front-500'] + ([f'{CAA_ROOT}/release-group/{group_id}/front-500'] if group_id else [])


def is_image(data):
    return data[:3] == b'\xff\xd8\xff' or data[:8] == b'\x89PNG\r\n\x1a\n' or data[:4] == b'GIF8' or (data[:4] == b'RIFF' and data[8:12] == b'WEBP')


def fetch_image(http, urls):
    """First of the URLs that gives an image, or None if none does (a 404 just means "no cover")."""
    for url in urls:
        try:
            data = http.request(url, accept='image/*')
        except HttpStatus as exc:
            if exc.code in (400, 403, 404):
                continue
            raise
        if is_image(data) and len(data) <= MAX_IMAGE_BYTES:
            return data
    return None


# -- AcoustID / Chromaprint ----------------------------------------------------------------------------------------------
def find_fpcalc(configured=''):
    """Path of Chromaprint's fpcalc: the configured path (a file, or a folder holding it), else the system PATH, else
    next to the Calibre or plugin folder. None if not found."""
    names = ['fpcalc.exe', 'fpcalc'] if sys.platform == 'win32' else ['fpcalc']
    configured = (configured or '').strip().strip('"')
    if configured:
        if os.path.isfile(configured):
            return configured
        for name in names:
            candidate = os.path.join(configured, name)
            if os.path.isfile(candidate):
                return candidate
        return None
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def fingerprint(path, fpcalc, runner=subprocess.run):
    """-> (duration seconds int, fingerprint str) using `fpcalc -json`. Raises ServiceError if it fails."""
    kwargs = {'capture_output': True, 'timeout': 120}
    if sys.platform == 'win32':
        kwargs['creationflags'] = 0x08000000  # CREATE_NO_WINDOW: no console flash from the GUI
    try:
        done = runner([fpcalc, '-json', path], **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise FingerprintError(f'fpcalc could not be run: {exc}') from None
    if done.returncode != 0:
        message = (done.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        raise FingerprintError('fpcalc could not read this file' + (f': {message[-1]}' if message else ''))
    try:
        data = json.loads(done.stdout.decode('utf-8'))
        return int(float(data['duration'])), data['fingerprint']
    except (ValueError, KeyError, TypeError):
        raise FingerprintError('fpcalc gave an answer that could not be understood (is it Chromaprint 1.4 or newer?)') from None


def acoustid_lookup(http, api_key, duration, fp):
    """-> [(acoustid score 0..1, [recording ids])], best first."""
    try:
        data = http.json(ACOUSTID_URL, data={'client': api_key, 'meta': 'recordings', 'duration': duration, 'fingerprint': fp,
                                             'format': 'json'})
    except HttpStatus as exc:
        if exc.code in (400, 401, 403):
            raise ServiceError('AcoustID refused the request; check the application API key in the settings') from None
        raise
    if data.get('status') != 'ok':
        message = (data.get('error') or {}).get('message', 'unknown error')
        raise ServiceError(f'AcoustID: {message}')
    out = []
    for result in data.get('results', []):
        ids = [r['id'] for r in result.get('recordings', []) if r.get('id')]
        if ids:
            out.append((float(result.get('score', 0)), ids))
    return sorted(out, key=lambda x: -x[0])


# -- Open Library ----------------------------------------------------------------------------------------------------------
OL_FIELDS = 'key,title,author_name,first_publish_year,isbn,publisher,cover_i,cover_edition_key,subject,language'


def ol_search(http, title, author='', limit=6):
    params = {'title': title, 'limit': limit, 'fields': OL_FIELDS}
    if author:
        params['author'] = author
    docs = http.json(OL_ROOT + '/search.json', params).get('docs', [])
    if not docs and author:  # an author spelled differently from Open Library's would otherwise hide the book
        docs = http.json(OL_ROOT + '/search.json', {'title': title, 'limit': limit, 'fields': OL_FIELDS}).get('docs', [])
    return docs


def ol_cover_urls(cover_id):
    return [f'{OL_COVERS}/b/id/{cover_id}-L.jpg?default=false'] if cover_id else []
