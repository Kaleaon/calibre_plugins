"""Site-specific scrub profiles and domain matching manager for EPUB Promo Scrubber."""

import json
import re
from urllib.parse import urlparse

DEFAULT_GLOBAL_PROFILE = {
    'id': 'global_default',
    'name': 'Global Default',
    'domain_patterns': ['*'],
    'extra_patterns': '',
    'heuristic': True,
    'drop_empty_pages': True,
    'is_default': True,
}

DOMAIN_REGEX = re.compile(
    r'(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)+)(?::\d+)?(?:/[^\s]*)?', re.IGNORECASE
)


def extract_domains_from_text(text):
    """Extract domain hostnames from a text string, stripping www."""
    domains = set()
    if not text or not isinstance(text, str):
        return domains

    text = text.strip()
    if not text:
        return domains

    if text.startswith(('http://', 'https://')):
        try:
            parsed = urlparse(text)
            netloc = parsed.netloc.split(':')[0].lower()
            if netloc.startswith('www.'):
                netloc = netloc[4:]
            if netloc and '.' in netloc:
                domains.add(netloc)
        except Exception:
            pass

    for match in DOMAIN_REGEX.finditer(text):
        dom = match.group(1).lower()
        if dom.startswith('www.'):
            dom = dom[4:]
        if dom and '.' in dom:
            domains.add(dom)

    return domains


def _local_name(el):
    tag = el.tag
    if isinstance(tag, str):
        if '}' in tag:
            return tag.split('}', 1)[1].lower()
        return tag.lower()
    return None


def extract_domains_from_opf(opf_root):
    """Extract source domains from OPF metadata elements (dc:identifier, dc:publisher, dc:source)."""
    domains = set()
    if opf_root is None:
        return domains

    try:
        metadata = None
        for el in opf_root.iter():
            if _local_name(el) == 'metadata':
                metadata = el
                break

        if metadata is None:
            return domains

        for el in metadata:
            name = _local_name(el)
            if name in ('identifier', 'publisher', 'source'):
                txt = el.text or ''
                cnt = el.get('content') or ''
                for val in (txt, cnt):
                    if val:
                        domains.update(extract_domains_from_text(val))
    except Exception:
        pass

    return domains


def extract_domains_from_metadata(mi):
    """Extract source domains from Calibre metadata object (identifiers, publisher, source)."""
    domains = set()
    if mi is None:
        return domains

    try:
        if getattr(mi, 'publisher', None):
            domains.update(extract_domains_from_text(mi.publisher))

        identifiers = getattr(mi, 'identifiers', None)
        if isinstance(identifiers, dict):
            for k, v in identifiers.items():
                if v:
                    domains.update(extract_domains_from_text(str(v)))

        if getattr(mi, 'source', None):
            domains.update(extract_domains_from_text(mi.source))
    except Exception:
        pass

    return domains


def match_domain_pattern(domain, pattern):
    """Match a domain against a pattern supporting exact matches, subdomains, wildcards, and re: regex."""
    if not domain or not pattern:
        return False

    domain = domain.lower().strip()
    pattern = pattern.lower().strip()

    if pattern == '*':
        return True

    if pattern.startswith('re:'):
        try:
            return bool(re.search(pattern[3:], domain, re.IGNORECASE))
        except re.error:
            return False

    if domain.startswith('www.'):
        domain = domain[4:]
    if pattern.startswith('www.'):
        pattern = pattern[4:]

    if pattern == domain:
        return True

    if '*' in pattern:
        regex_str = '^' + re.escape(pattern).replace(r'\*', '.*') + '$'
        try:
            return bool(re.match(regex_str, domain, re.IGNORECASE))
        except re.error:
            return False

    if domain.endswith('.' + pattern):
        return True

    return False


def resolve_profile(domains, profiles):
    """Hierarchically evaluate domain matches against profiles list.

    Falls back to global default profile if no specific domain matches.
    """
    if not profiles:
        return dict(DEFAULT_GLOBAL_PROFILE)

    cand_domains = set()
    if isinstance(domains, (set, list, tuple)):
        for d in domains:
            if d:
                cand_domains.add(d.lower().strip())
    elif isinstance(domains, str):
        cand_domains.update(extract_domains_from_text(domains))

    default_prof = None
    for prof in profiles:
        if prof.get('is_default'):
            default_prof = prof
            continue

        p_patterns = prof.get('domain_patterns', [])
        if isinstance(p_patterns, str):
            p_patterns = [p.strip() for p in p_patterns.splitlines() if p.strip()]

        for pat in p_patterns:
            for dom in cand_domains:
                if match_domain_pattern(dom, pat):
                    return prof

    if default_prof:
        return default_prof

    for prof in profiles:
        if prof.get('name') == 'Global Default' or '*' in prof.get('domain_patterns', []):
            return prof

    return profiles[0] if profiles else dict(DEFAULT_GLOBAL_PROFILE)


REQUIRED_SCHEMA_KEYS = {'name', 'domain_patterns'}


def validate_profile_dict(prof):
    """Validate that a profile dictionary has required structure and field types."""
    if not isinstance(prof, dict):
        raise ValueError('Profile must be a JSON object (dictionary)')

    missing = REQUIRED_SCHEMA_KEYS - set(prof.keys())
    if missing:
        raise ValueError(f"Profile missing required key(s): {', '.join(sorted(missing))}")

    if not isinstance(prof['name'], str) or not prof['name'].strip():
        raise ValueError("Profile field 'name' must be a non-empty string")

    pats = prof['domain_patterns']
    if not isinstance(pats, (list, str)):
        raise ValueError("Profile field 'domain_patterns' must be a list or multi-line string")

    if 'heuristic' in prof and not isinstance(prof['heuristic'], bool):
        raise ValueError("Profile field 'heuristic' must be a boolean")

    if 'drop_empty_pages' in prof and not isinstance(prof['drop_empty_pages'], bool):
        raise ValueError("Profile field 'drop_empty_pages' must be a boolean")


def validate_profile_schema(data):
    """Validate imported JSON data structure for site profiles.

    Raises ValueError if JSON structure is invalid. Returns normalized profile list.
    """
    if isinstance(data, dict):
        if 'profiles' in data and isinstance(data['profiles'], list):
            items = data['profiles']
        else:
            items = [data]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError('Imported profile JSON must be a list of profiles or an object containing a "profiles" key.')

    if not items:
        raise ValueError('No profiles found in imported JSON data.')

    normalized = []
    for idx, item in enumerate(items):
        try:
            validate_profile_dict(item)
        except ValueError as exc:
            raise ValueError(f'Profile #{idx + 1} validation error: {exc}') from exc

        d_pats = item['domain_patterns']
        if isinstance(d_pats, str):
            d_pats = [p.strip() for p in d_pats.splitlines() if p.strip()]

        normalized.append({
            'id': item.get('id') or f"profile_{item['name'].lower().replace(' ', '_')}",
            'name': item['name'].strip(),
            'domain_patterns': d_pats,
            'extra_patterns': str(item.get('extra_patterns', '')),
            'heuristic': bool(item.get('heuristic', True)),
            'drop_empty_pages': bool(item.get('drop_empty_pages', True)),
            'is_default': bool(item.get('is_default', False)),
        })

    return normalized


def import_profiles_from_json(json_str):
    """Parse and validate profiles from JSON string."""
    try:
        data = json.loads(json_str)
    except Exception as exc:
        raise ValueError(f'Invalid JSON content: {exc}') from exc

    return validate_profile_schema(data)


def export_profiles_to_json(profiles):
    """Serialize profile list to formatted JSON string."""
    export_data = {'profiles': profiles}
    return json.dumps(export_data, indent=2, ensure_ascii=False)


def get_profiles_from_prefs(prefs):
    """Load profiles from Calibre JSONConfig prefs object, initializing defaults if missing."""
    stored = prefs.get('profiles')
    if not stored or not isinstance(stored, list):
        extra = prefs.get('extra_patterns', '')
        heuristic = prefs.get('heuristic', True)
        drop = prefs.get('drop_empty_pages', True)

        global_def = dict(DEFAULT_GLOBAL_PROFILE)
        global_def['extra_patterns'] = extra
        global_def['heuristic'] = heuristic
        global_def['drop_empty_pages'] = drop

        stored = [global_def]
        prefs['profiles'] = stored

    has_default = any(p.get('is_default') or p.get('name') == 'Global Default' for p in stored)
    if not has_default:
        stored.insert(0, dict(DEFAULT_GLOBAL_PROFILE))
        prefs['profiles'] = stored

    return stored


def save_profiles_to_prefs(prefs, profiles):
    """Save profiles list to Calibre JSONConfig prefs object and sync legacy keys."""
    prefs['profiles'] = profiles

    default_prof = resolve_profile([], profiles)
    prefs['extra_patterns'] = default_prof.get('extra_patterns', '')
    prefs['heuristic'] = default_prof.get('heuristic', True)
    prefs['drop_empty_pages'] = default_prof.get('drop_empty_pages', True)
