"""Source detection and keyword rules for tagging books. Pure standard library so it can be tested outside Calibre.

Everything here is additive: the plan only ever adds tags, never removes or renames existing ones.
"""
import functools
import re

try:
    from .fff_sites import FFF_SITES
except ImportError:  # tests load this file directly
    from fff_sites import FFF_SITES

# (display name, hosts, identifier keys that mean "this came from there")
CORE_SITES = [
    ('Metabods', ['metabods.com'], ['metabods']),
    ('Nifty', ['nifty.org', 'niftyarchives.org'], ['nifty']),
    ('Royal Road', ['royalroad.com', 'royalroadl.com'], ['royalroad', 'royalroadl']),
    ('Archive of Our Own', ['archiveofourown.org', 'ao3.org'], ['ao3']),
    ('FanFiction.Net', ['fanfiction.net'], ['ffnet', 'fanfictionnet']),
    ('Wattpad', ['wattpad.com'], []),
    ('Scribble Hub', ['scribblehub.com'], []),
    ('Literotica', ['literotica.com'], []),
    ('Sufficient Velocity', ['sufficientvelocity.com'], []),
    ('SpaceBattles', ['spacebattles.com'], []),
    ('Webnovel', ['webnovel.com'], []),
    ('Wuxiaworld', ['wuxiaworld.com'], []),
    ('SoFurry', ['sofurry.com'], []),
    ('Fur Affinity', ['furaffinity.net'], []),
    ('Tapas', ['tapas.io'], []),
]



def _host_covered(host, claimed_hosts, claimed_keys):
    if not host or not str(host).strip():
        return False
    h = str(host).strip().lower()
    if h in claimed_keys:
        return True
    for c in claimed_hosts:
        if h == c or h.endswith('.' + c) or c.endswith('.' + h):
            return True
    return False


def _key_covered(key, claimed_hosts, claimed_keys):
    if not key or not str(key).strip():
        return False
    k = str(key).strip().lower()
    if k in claimed_keys or k in claimed_hosts:
        return True
    for c in claimed_hosts:
        if k == c or k.endswith('.' + c) or c.endswith('.' + k):
            return True
    return False


def merge_sites(core, extra):
    """Curated entries win; extra/generated entries only add hosts/keys not already covered (subdomains count)."""
    claimed_hosts = {str(h).strip().lower() for _, hosts, _ in core for h in hosts if h and str(h).strip()}
    claimed_keys = {str(k).strip().lower() for _, _, keys in core for k in keys if k and str(k).strip()}
    merged = list(core)

    for name, hosts, keys in extra:
        primary_covered = False
        if hosts:
            if _host_covered(hosts[0], claimed_hosts, claimed_keys):
                primary_covered = True
        elif keys:
            if _key_covered(keys[0], claimed_hosts, claimed_keys):
                primary_covered = True

        if primary_covered:
            continue

        fresh_hosts = [h for h in hosts if not _host_covered(h, claimed_hosts, claimed_keys)]
        fresh_keys = [k for k in keys if not _key_covered(k, claimed_hosts, claimed_keys)]

        if not fresh_hosts and not fresh_keys:
            continue

        claimed_hosts.update(str(h).strip().lower() for h in fresh_hosts if h and str(h).strip())
        claimed_keys.update(str(k).strip().lower() for k in fresh_keys if k and str(k).strip())
        merged.append((name, fresh_hosts, fresh_keys))

    return merged


_merge_sites = merge_sites


DEFAULT_SITES = merge_sites(CORE_SITES, FFF_SITES)

FIELDS = {'tag': 'tags', 'tags': 'tags', 'title': 'title', 'author': 'authors', 'authors': 'authors', 'series': 'series',
          'comments': 'comments', 'publisher': 'publisher', 'source': 'source', 'any': 'any'}
DEFAULT_FIELDS = ('title', 'tags', 'series')


class Rule:
    def __init__(self, field, pattern, tags):
        self.field, self.pattern, self.tags = field, pattern, tags
        if pattern.startswith('re:'):
            self.match = re.compile(pattern[3:], re.I).search
        elif pattern.startswith('='):
            exact = pattern[1:].strip().casefold()
            self.match = lambda text: text.casefold() == exact
        else:
            needle = pattern.strip().casefold()
            self.match = lambda text: needle in text.casefold()


def parse_sites(text):
    """Extra sites, one per line: 'host[, host2] => Name'. Returns (sites, errors)."""
    sites, errors = [], []
    for n, line in enumerate((text or '').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=>' not in line:
            errors.append(f'Site line {n}: expected "host => Name"')
            continue
        hosts, name = (x.strip() for x in line.split('=>', 1))
        hosts = [h.strip().lower() for h in hosts.split(',') if h.strip()]
        if not hosts or not name:
            errors.append(f'Site line {n}: expected "host => Name"')
            continue
        sites.append((name, hosts, []))
    return sites, errors


def parse_rules(text):
    """Rules, one per line: '[field:]pattern => Tag A, Tag B'. Returns (rules, errors)."""
    rules, errors = [], []
    for n, line in enumerate((text or '').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=>' not in line:
            errors.append(f'Rule line {n}: expected "pattern => tag"')
            continue
        left, right = (x.strip() for x in line.split('=>', 1))
        field = None
        m = re.match(r'^([A-Za-z]+):(.*)$', left)
        if m and m.group(1).lower() in FIELDS:
            field, left = FIELDS[m.group(1).lower()], m.group(2).strip()
        tags = [t.strip() for t in right.split(',') if t.strip()]
        if not left or not tags:
            errors.append(f'Rule line {n}: expected "pattern => tag"')
            continue
        try:
            rules.append(Rule(field, left, tags))
        except re.error as exc:
            errors.append(f'Rule line {n}: bad regular expression ({exc})')
    return rules, errors


@functools.lru_cache(maxsize=None)
def _host_pattern(host):
    return re.compile(r'(?<![\w-])' + re.escape(host) + r'(?![\w-])', re.I)


def detect_sites(book, sites, scan_comments=False):
    """Names of the sites a book appears to come from, in table order."""
    idents = {str(k).lower(): str(v) for k, v in (book.get('identifiers') or {}).items()}
    haystacks = list(idents.values()) + [book.get('publisher') or '']
    if scan_comments:
        haystacks.append(book.get('comments') or '')
    found = []
    for name, hosts, keys in sites:
        if name not in found and (any(k.lower() in idents for k in keys) or any(
                _host_pattern(h).search(text) for h in hosts for text in haystacks if text)):
            found.append(name)
    return found


def _values(book, field):
    value = book.get(field)
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, (list, tuple)) else [str(value)]


def rule_hits(book, rule, sources):
    field = rule.field
    if field == 'source':
        texts = list(sources)
    elif field in (None, 'any'):
        texts = [t for f in DEFAULT_FIELDS for t in _values(book, f)]
        if field == 'any':
            texts += _values(book, 'authors') + _values(book, 'comments') + _values(book, 'publisher')
    else:
        texts = _values(book, field)
    return any(rule.match(t) for t in texts)


def plan_changes(books, source_prefix='Source.', add_source=True, series_prefix='', rules=(), sites=DEFAULT_SITES,
                 scan_comments=False):
    """Tags to add per book. Returns [{'id', 'title', 'add': [...], 'tags': [...final tags...]}] for books that change."""
    plan = []
    for book in books:
        have = list(book.get('tags') or [])
        known = {t.casefold() for t in have}
        sources = detect_sites(book, sites, scan_comments)
        wanted = []
        if add_source:
            wanted += [f'{source_prefix}{s}' for s in sources]
        if series_prefix and book.get('series'):
            wanted.append(f"{series_prefix}{book['series']}")
        for rule in rules:
            if rule_hits(book, rule, sources):
                wanted += rule.tags
        add = []
        for tag in wanted:
            if tag.casefold() not in known:
                known.add(tag.casefold())
                add.append(tag)
        if add:
            plan.append({'id': book['id'], 'title': book.get('title', ''), 'add': add, 'tags': have + add})
    return plan


class TagSelectionState:
    """Manages active selection states for dual-pane preview with global tag toggles and per-book overrides."""

    def __init__(self, plan):
        self.plan = plan
        self.tag_counts = {}
        for p in plan:
            for tag in p.get('add', []):
                self.tag_counts[tag] = self.tag_counts.get(tag, 0) + 1

        self.global_tags = {tag: True for tag in self.tag_counts}
        self.book_overrides = {}

    def get_distinct_tags(self):
        return list(self.tag_counts.items())

    def set_global_tag(self, tag, checked):
        checked = bool(checked)
        self.global_tags[tag] = checked
        for p in self.plan:
            book_id = p['id']
            if tag in p.get('add', []):
                self.book_overrides[(book_id, tag)] = checked

    def set_book_tag(self, book_id, tag, checked):
        self.book_overrides[(book_id, tag)] = bool(checked)

    def is_tag_active(self, book_id, tag):
        if (book_id, tag) in self.book_overrides:
            return self.book_overrides[(book_id, tag)]
        return self.global_tags.get(tag, True)

    def get_active_plan(self):
        active_plan = []
        for p in self.plan:
            book_id = p['id']
            add_tags = p.get('add', [])
            existing_count = len(p.get('tags', [])) - len(add_tags)
            existing_tags = p.get('tags', [])[:existing_count] if existing_count >= 0 else []

            active_add = [t for t in add_tags if self.is_tag_active(book_id, t)]
            if active_add:
                known = {t.casefold() for t in existing_tags}
                final_tags = list(existing_tags)
                final_add = []
                for t in active_add:
                    if t.casefold() not in known:
                        known.add(t.casefold())
                        final_add.append(t)
                        final_tags.append(t)
                if final_add:
                    active_plan.append({
                        'id': book_id,
                        'title': p.get('title', ''),
                        'add': final_add,
                        'tags': final_tags
                    })
        return active_plan

    def get_summary_counts(self):
        active_assignments = 0
        affected_books = 0
        for p in self.plan:
            book_id = p['id']
            book_active = [t for t in p.get('add', []) if self.is_tag_active(book_id, t)]
            if book_active:
                affected_books += 1
                active_assignments += len(book_active)
        return active_assignments, affected_books


RULE_SET_SCHEMA_VERSION = 1


def export_rule_set_data(rules_text, sites_text, title="Exported Rule Set", description=""):
    """Serializes rules and extra sites multiline text into a versioned JSON-compatible dictionary."""
    from datetime import datetime, timezone
    parsed_rules, _ = parse_rules(rules_text)
    parsed_sites, _ = parse_sites(sites_text)

    rules_list = []
    for r in parsed_rules:
        field_prefix = f"{r.field}:" if r.field else ""
        raw = f"{field_prefix}{r.pattern} => {', '.join(r.tags)}"
        rules_list.append({
            "field": r.field,
            "pattern": r.pattern,
            "tags": r.tags,
            "raw": raw
        })

    sites_list = []
    for name, hosts, _ in parsed_sites:
        raw = f"{', '.join(hosts)} => {name}"
        sites_list.append({
            "name": name,
            "hosts": hosts,
            "raw": raw
        })

    return {
        "version": RULE_SET_SCHEMA_VERSION,
        "metadata": {
            "title": title,
            "description": description,
            "exported_at": datetime.now(timezone.utc).isoformat()
        },
        "rules": rules_list,
        "sites": sites_list,
        "raw_rules": rules_text or "",
        "raw_sites": sites_text or ""
    }


def import_rule_set_data(data):
    """Parses a versioned rule set JSON dictionary and validates syntax.
    Returns (rules, sites, errors).
    """
    if not isinstance(data, dict):
        return [], [], ["Invalid rule set data: expected a JSON object"]

    version = data.get("version")
    if version != RULE_SET_SCHEMA_VERSION:
        return [], [], [f"Unsupported schema version '{version}'. Expected version {RULE_SET_SCHEMA_VERSION}."]

    rule_lines = []
    if "rules" in data and isinstance(data["rules"], list):
        for idx, item in enumerate(data["rules"], 1):
            if isinstance(item, str):
                rule_lines.append(item)
            elif isinstance(item, dict):
                if item.get("raw"):
                    rule_lines.append(str(item["raw"]))
                elif "pattern" in item and "tags" in item:
                    fld = item.get("field")
                    field_prefix = f"{fld}:" if fld else ""
                    tags_val = item["tags"]
                    tags_str = ", ".join(tags_val) if isinstance(tags_val, list) else str(tags_val)
                    rule_lines.append(f"{field_prefix}{item['pattern']} => {tags_str}")
                else:
                    return [], [], [f"Rule item {idx} in JSON is missing 'pattern' or 'tags'"]
            else:
                return [], [], [f"Invalid rule item at index {idx}"]
    elif "raw_rules" in data and isinstance(data["raw_rules"], str):
        rule_lines = [line for line in data["raw_rules"].splitlines() if line.strip()]

    site_lines = []
    if "sites" in data and isinstance(data["sites"], list):
        for idx, item in enumerate(data["sites"], 1):
            if isinstance(item, str):
                site_lines.append(item)
            elif isinstance(item, dict):
                if item.get("raw"):
                    site_lines.append(str(item["raw"]))
                elif "name" in item and "hosts" in item:
                    hosts_val = item["hosts"]
                    hosts_str = ", ".join(hosts_val) if isinstance(hosts_val, list) else str(hosts_val)
                    site_lines.append(f"{hosts_str} => {item['name']}")
                else:
                    return [], [], [f"Site item {idx} in JSON is missing 'name' or 'hosts'"]
            else:
                return [], [], [f"Invalid site item at index {idx}"]
    elif "raw_sites" in data and isinstance(data["raw_sites"], str):
        site_lines = [line for line in data["raw_sites"].splitlines() if line.strip()]

    rules_text = "\n".join(rule_lines)
    sites_text = "\n".join(site_lines)

    parsed_rules, rule_errors = parse_rules(rules_text)
    parsed_sites, site_errors = parse_sites(sites_text)

    all_errors = rule_errors + site_errors
    if all_errors:
        return [], [], all_errors

    return parsed_rules, parsed_sites, []


def classify_rule(incoming_rule, local_rules):
    """Returns 'Duplicate', 'Conflict', or 'New' for incoming_rule compared against local_rules."""
    inc_fld = (incoming_rule.field or 'default').casefold()
    inc_pat = incoming_rule.pattern.casefold()
    inc_tags = sorted([t.casefold() for t in incoming_rule.tags])

    for loc in local_rules:
        loc_fld = (loc.field or 'default').casefold()
        loc_pat = loc.pattern.casefold()
        if inc_fld == loc_fld and inc_pat == loc_pat:
            loc_tags = sorted([t.casefold() for t in loc.tags])
            if inc_tags == loc_tags:
                return 'Duplicate'
            else:
                return 'Conflict'
    return 'New'


def classify_site(incoming_site, local_sites):
    """incoming_site: (name, hosts, keys)
    local_sites: list of (name, hosts, keys)
    Returns 'Duplicate', 'Conflict', or 'New'.
    """
    inc_name, inc_hosts, _ = incoming_site
    inc_hosts_set = {h.casefold() for h in inc_hosts if h}

    for loc_name, loc_hosts, _ in local_sites:
        loc_hosts_set = {h.casefold() for h in loc_hosts if h}
        if inc_hosts_set & loc_hosts_set:
            if inc_name.casefold() == loc_name.casefold():
                return 'Duplicate'
            else:
                return 'Conflict'
    return 'New'

