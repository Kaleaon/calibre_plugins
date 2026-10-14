"""Bundled preset libraries and structured rule management for Story Collection Tagger."""

import json
import os
import pkgutil
import re
from pathlib import Path

try:
    from .rules import FIELDS, merge_sites, parse_rules, parse_sites
except ImportError:
    from rules import FIELDS, merge_sites, parse_rules, parse_sites


def rule_dict_to_string(rule_dict):
    field = (rule_dict.get('field') or 'any').lower()
    match_mode = (rule_dict.get('match_mode') or 'substring').lower()
    pattern = rule_dict.get('pattern', '').strip()
    tags = rule_dict.get('tags') or []

    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]

    if match_mode == 'regex':
        pat_str = f"re:{pattern}"
    elif match_mode == 'exact':
        pat_str = f"={pattern}"
    else:
        pat_str = pattern

    field_prefix = f"{field}:" if field and field != 'any' else ""
    tags_str = ", ".join(tags)
    return f"{field_prefix}{pat_str} => {tags_str}"


def rule_string_to_dict(line):
    line = line.strip()
    if not line or line.startswith('#') or '=>' not in line:
        return None

    left, right = (x.strip() for x in line.split('=>', 1))
    field = 'any'
    m = re.match(r'^([A-Za-z]+):(.*)$', left)
    if m and m.group(1).lower() in FIELDS:
        field = FIELDS[m.group(1).lower()]
        left = m.group(2).strip()

    tags = [t.strip() for t in right.split(',') if t.strip()]

    if left.startswith('re:'):
        match_mode = 'regex'
        pattern = left[3:]
    elif left.startswith('='):
        match_mode = 'exact'
        pattern = left[1:].strip()
    else:
        match_mode = 'substring'
        pattern = left.strip()

    return {
        'field': field,
        'match_mode': match_mode,
        'pattern': pattern,
        'tags': tags
    }


def text_to_rule_dicts(text):
    rule_dicts = []
    for line in (text or '').splitlines():
        d = rule_string_to_dict(line)
        if d:
            rule_dicts.append(d)
    return rule_dicts


def rules_to_text(rule_dicts):
    return "\n".join(rule_dict_to_string(d) for d in rule_dicts if d)


def validate_rule_dict(rule_dict):
    if not rule_dict:
        return False, "Rule is empty"

    pattern = rule_dict.get('pattern', '').strip()
    if not pattern:
        return False, "Pattern cannot be empty"

    tags = rule_dict.get('tags')
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]
    if not tags:
        return False, "At least one target tag must be specified"

    match_mode = (rule_dict.get('match_mode') or 'substring').lower()
    if match_mode == 'regex':
        try:
            re.compile(pattern, re.I)
        except re.error as exc:
            return False, f"Invalid regular expression: {exc}"

    return True, ""


def site_tuple_to_dict(site_tuple):
    name, hosts, keys = site_tuple
    return {
        'name': name,
        'hosts': list(hosts or []),
        'keys': list(keys or [])
    }


def site_dict_to_tuple(site_dict):
    name = site_dict.get('name', '').strip()
    hosts = [h.strip() for h in site_dict.get('hosts', []) if str(h).strip()]
    keys = [k.strip() for k in site_dict.get('keys', []) if str(k).strip()]
    return (name, hosts, keys)


def sites_to_text(site_tuples):
    lines = []
    for name, hosts, _ in site_tuples:
        if hosts:
            lines.append(f"{', '.join(hosts)} => {name}")
    return "\n".join(lines)


def text_to_sites(text):
    sites, _ = parse_sites(text)
    return sites


def deduplicate_rules(existing_rules, new_rules):
    """Merge new_rules into existing_rules, eliminating exact duplicate rules and merging unique tags for identical patterns.
    
    Returns (merged_rule_dicts, added_count).
    """
    merged = [dict(r) for r in existing_rules]
    added_count = 0

    for incoming in new_rules:
        inc = dict(incoming)
        if isinstance(inc.get('tags'), str):
            inc['tags'] = [t.strip() for t in inc['tags'].split(',') if t.strip()]

        inc_field = (inc.get('field') or 'any').lower()
        inc_mode = (inc.get('match_mode') or 'substring').lower()
        inc_pat = inc.get('pattern', '').strip()
        inc_tags = inc.get('tags') or []

        matched = False
        for ex in merged:
            ex_field = (ex.get('field') or 'any').lower()
            ex_mode = (ex.get('match_mode') or 'substring').lower()
            ex_pat = ex.get('pattern', '').strip()

            if ex_field == inc_field and ex_mode == inc_mode and ex_pat == inc_pat:
                matched = True
                existing_known = {t.casefold() for t in ex['tags']}
                new_tags_to_add = [t for t in inc_tags if t.casefold() not in existing_known]
                if new_tags_to_add:
                    ex['tags'].extend(new_tags_to_add)
                    added_count += 1
                break

        if not matched:
            merged.append(inc)
            added_count += 1

    return merged, added_count


def parse_preset_json(content):
    if isinstance(content, (str, bytes)):
        data = json.loads(content)
    else:
        data = content

    name = data.get('name', 'Custom Preset')
    category = data.get('category', 'Uncategorized')
    description = data.get('description', '')
    version = data.get('version', '1.0.0')

    raw_rules = data.get('rules', [])
    rule_dicts = []
    for r in raw_rules:
        if isinstance(r, str):
            d = rule_string_to_dict(r)
            if d:
                rule_dicts.append(d)
        elif isinstance(r, dict):
            rule_dicts.append({
                'field': r.get('field', 'any'),
                'match_mode': r.get('match_mode', 'substring'),
                'pattern': r.get('pattern', ''),
                'tags': r.get('tags', [])
            })

    raw_sites = data.get('sites', [])
    site_tuples = []
    for s in raw_sites:
        if isinstance(s, str):
            st, _ = parse_sites(s)
            site_tuples.extend(st)
        elif isinstance(s, dict):
            site_tuples.append(site_dict_to_tuple(s))

    return {
        'name': name,
        'category': category,
        'description': description,
        'version': version,
        'rules': rule_dicts,
        'sites': site_tuples
    }


def export_preset_json(name, category, description, rule_dicts, site_tuples, version="1.0.0"):
    data = {
        'name': name or 'Custom Preset',
        'category': category or 'Custom',
        'description': description or '',
        'version': version,
        'rules': rule_dicts or [],
        'sites': [site_tuple_to_dict(s) for s in (site_tuples or [])]
    }
    return json.dumps(data, indent=2)


def load_bundled_presets():
    presets = []
    preset_dir = Path(__file__).parent / 'presets'

    json_files = []
    if preset_dir.exists() and preset_dir.is_dir():
        json_files = sorted(preset_dir.glob('*.json'))

    for filepath in json_files:
        try:
            content = filepath.read_text(encoding='utf-8')
            presets.append(parse_preset_json(content))
        except Exception:
            pass

    if not presets:
        # Try loading via pkgutil if in zip
        for fname in ['genres_litrpg.json', 'genres_scifi_fantasy.json', 'tropes_story.json', 'web_formats.json']:
            try:
                raw = pkgutil.get_data(__name__, f"presets/{fname}")
                if raw:
                    presets.append(parse_preset_json(raw.decode('utf-8')))
            except Exception:
                pass

    return presets


def merge_preset_into_active(active_rules_text, active_sites_text, preset_data):
    """Merge a preset pack's rules and custom sites into active rules and sites text.
    
    Returns (updated_rules_text, updated_sites_text, rules_added_count, sites_added_count).
    """
    active_rules = text_to_rule_dicts(active_rules_text)
    preset_rules = preset_data.get('rules', [])
    merged_rules, rules_added = deduplicate_rules(active_rules, preset_rules)
    updated_rules_text = rules_to_text(merged_rules)

    active_sites = text_to_sites(active_sites_text)
    preset_sites = preset_data.get('sites', [])

    count_before = len(active_sites)
    merged_sites = merge_sites(active_sites, preset_sites)
    sites_added = len(merged_sites) - count_before
    updated_sites_text = sites_to_text(merged_sites)

    return updated_rules_text, updated_sites_text, rules_added, sites_added
