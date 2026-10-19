"""Bridge to the bundled FanFicFare library: URL checks, the adult-sites setting, and downloading one story as EPUB.

Inside Calibre the library ships in the plugin zip as a sub-package; in tests it is imported from site-packages.
"""
import configparser
import logging
import re
from io import BytesIO, StringIO

# Sites whose content is primarily adult. Curated by hand and deliberately conservative; sites that merely allow
# adult works (Archive of Our Own, FanFiction.Net, ...) are handled by FanFicFare's own adult-confirmation step.
ADULT_DOMAINS = (
    'adult-fanfiction.org', 'aneroticstory.com', 'asexstories.com', 'bdsmlibrary.com', 'erosnsappho.sycophanthex.com',
    'fictionmania.tv', 'giantessworld.net', 'gluttonyfiction.com', 'hentai-foundry.com', 'inkbunny.net', 'literotica.com',
    'mcstories.com', 'sinful-dreams.com', 'sofurry.com', 'storiesonline.net', 'sunnydaleafterdark.com',
    'tgstorytime.com', 'thehookupzone.net', 'utopiastories.com', 'voracity2.e-fic.com',
)

_ns = None


class EngineError(Exception):
    """A problem to show the user as-is."""


def load():
    """Import the FanFicFare pieces once. Returns a namespace with adapters, exceptions, writers, Configuration."""
    global _ns
    if _ns is not None:
        return _ns
    try:
        try:
            from .fanficfare import adapters, exceptions, writers
            from .fanficfare.configurable import Configuration
            package = __package__ + '.fanficfare'
        except ImportError:
            from fanficfare import adapters, exceptions, writers
            from fanficfare.configurable import Configuration
            package = 'fanficfare'
    except ImportError as exc:
        raise EngineError(f'The bundled FanFicFare library could not be loaded: {exc}') from exc
    logging.getLogger(package).setLevel(logging.WARNING)  # FanFicFare logs every request at DEBUG
    ns = type('FFF', (), {})()
    ns.adapters, ns.exceptions, ns.writers, ns.Configuration, ns.package = adapters, exceptions, writers, Configuration, package
    ns.defaults_ini = _read_defaults(package)
    _ns = ns
    return ns


def _read_defaults(package):
    data = None
    try:
        data = get_resources('fanficfare/defaults.ini')  # noqa: F821  injected by Calibre's plugin loader
    except NameError:
        pass
    if not data:
        import importlib
        import os
        module = importlib.import_module(package)
        with open(os.path.join(os.path.dirname(module.__file__), 'defaults.ini'), 'rb') as stream:
            data = stream.read()
    return data.decode('utf-8') if isinstance(data, bytes) else data


def is_adult_site(domain, extra=()):
    domain = (domain or '').lower()
    return any(domain == d or domain.endswith('.' + d) for d in tuple(ADULT_DOMAINS) + tuple(extra))


def parse_extra_adult(text):
    """User-added adult domains, one per line or comma separated; '#' starts a comment."""
    out = []
    for line in (text or '').splitlines():
        for part in line.split('#')[0].split(','):
            part = re.sub(r'^https?://', '', part.strip().lower()).split('/')[0]
            if part.startswith('www.'):
                part = part[4:]
            if part:
                out.append(part)
    return out


def inspect_url(url, allow_adult=False, extra_adult=()):
    """Describe a story URL. Returns {'url', 'domain', 'adult', 'begin', 'end'}; raises EngineError if unusable."""
    url = (url or '').strip()
    ns = load()
    try:
        stripped, begin, end = ns.adapters.get_url_chapter_range(url)
        found = ns.adapters.getNormalStoryURLSite(stripped)
    except Exception as exc:  # FanFicFare raises assorted errors for malformed input
        raise EngineError(f'Not a recognised story address: {url}') from exc
    if not found:
        raise EngineError(f'No FanFicFare adapter handles this address: {url}')
    normal, domain = found
    adult = is_adult_site(domain, extra_adult)
    if adult and not allow_adult:
        raise EngineError(f'{domain} is an adult site and adult sites are turned off: {url}')
    return {'url': normal, 'domain': domain, 'adult': adult, 'begin': begin, 'end': end}


def check_urls(lines, allow_adult=False, extra_adult=()):
    """Split pasted lines into usable stories and problems. Returns (accepted, problems)."""
    accepted, problems, seen = [], [], set()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            info = inspect_url(line, allow_adult, extra_adult)
        except EngineError as exc:
            problems.append(str(exc))
            continue
        key = (info['url'], info['begin'], info['end'])
        if key not in seen:
            seen.add(key)
            info['input'] = line
            accepted.append(info)
    return accepted, problems


def supported_sites(extra_adult=()):
    """[(domain, example url, adult?)] for the sites the bundled FanFicFare can read."""
    ns = load()
    rows = []
    for section, examples in ns.adapters.getSiteExamples():
        if section.startswith('test'):
            continue
        example = next((e for e in examples if e.startswith('http')), '')  # one adapter publishes the text 'no'
        rows.append((section, example, is_adult_site(section.split('/')[0], extra_adult)))
    return sorted(rows)


def split_series(value):
    """FanFicFare writes series as 'Name [3]'. Returns (name, index or None)."""
    m = re.match(r'^(.*?)\s*\[(\d+(?:\.\d+)?)\]\s*$', value or '')
    return (m.group(1), float(m.group(2))) if m else ((value or '').strip(), None)


def explain(exc, url=None):
    """Turn FanFicFare exceptions into a message that says what to do."""
    ns = load()
    name = type(exc).__name__
    text = str(exc)
    if isinstance(exc, ns.exceptions.AdultCheckRequired):
        return 'This story needs an adult confirmation. Turn on "Allow adult sites" to confirm it.'
    if isinstance(exc, ns.exceptions.FailedToLogin):
        return ('The site wants a login. Add username and password for it under Advanced settings '
                '(see the example there), then try again.')
    if '403' in text or 'Forbidden' in text or 'Cloudflare' in text:
        return ('The site refused the request (HTTP 403, often a bot challenge). FanFicFare has options for this, such as '
                'reading pages from your browser cache or using a FlareSolverr proxy; put them under Advanced settings.')
    return f'{name}: {text}' if text else name


def download_story(info, personal_ini='', allow_adult=False):
    """Download one story with FanFicFare and return {'epub': bytes, plus metadata fields}. Blocks until finished."""
    ns = load()
    cfg = ns.Configuration(ns.adapters.getConfigSectionsFor(info['url']), 'epub')
    cfg.read_file(StringIO(ns.defaults_ini))
    if personal_ini.strip():
        cfg.read_file(StringIO(personal_ini))
    try:
        cfg.add_section('overrides')
    except configparser.DuplicateSectionError:
        pass
    cfg.set('overrides', 'is_adult', 'true' if allow_adult else 'false')  # the dialog setting always wins
    adapter = ns.adapters.getAdapter(cfg, info['url'])
    adapter.setChaptersRange(info.get('begin'), info.get('end'))
    for _ in range(3):  # FanFicFare asks for the adult confirmation by raising, then expects a retry
        try:
            adapter.getStoryMetadataOnly()
            break
        except ns.exceptions.AdultCheckRequired:
            if not allow_adult:
                raise
            adapter.is_adult = True
    story = adapter.story
    out = BytesIO()
    writer = ns.writers.getWriter('epub', cfg, adapter)
    writer.writeStory(outstream=out, metaonly=False)
    series, index = split_series(story.getMetadata('series'))
    return {
        'epub': out.getvalue(),
        'url': story.getMetadata('storyUrl') or info['url'],
        'title': story.getMetadata('title'),
        'authors': [a for a in story.getList('author') if a] or ['Unknown'],
        'description': story.getMetadata('description') or '',
        'tags': story.getSubjectTags(removeallentities=True),
        'series': series,
        'series_index': index,
        'site': story.getMetadata('site') or info['domain'],
        'published': story.getMetadata('datePublished'),
        'language': story.getMetadata('language') or '',
        'chapters': story.getMetadata('numChapters'),
        'status': story.getMetadata('status'),
        'chapter_errors': getattr(story, 'chapter_error_count', 0),
    }
