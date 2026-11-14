"""Subscription exchange module for Reddit Story Follower.

Handles OPML and JSON serialization, schema validation, nested category folder parsing,
and conflict resolution / merge strategies. Uses Python standard library only.
"""
import json
import uuid
import xml.etree.ElementTree as ET
from xml.dom import minidom

try:
    from . import core
except ImportError:
    import core


STRATEGY_SKIP = 'skip'
STRATEGY_OVERWRITE = 'overwrite'
STRATEGY_ADD_SUFFIX = 'add_suffix'


def is_reddit_url(url):
    """Check if a URL string looks like a Reddit address or feed."""
    if not url or not isinstance(url, str):
        return False
    url_lower = url.lower()
    if any(domain in url_lower for domain in ('reddit.com', 'redd.it')):
        return True
    if url_lower.startswith('r/') or url_lower.startswith('u/') or url_lower.startswith('/r/') or url_lower.startswith('/u/'):
        return True
    return False


def _parse_source_safe(text):
    """Attempt to parse a source text, returning parsed source dict or None if invalid/non-reddit."""
    if not text:
        return None
    try:
        return core.parse_source(text)
    except Exception:
        return None


def parse_opml(xml_data):
    """Parse OPML XML content (str or bytes) and extract Reddit subscriptions.

    Traverses nested outline tags and preserves folder labels as category tags.
    Filters out non-Reddit feeds without throwing unhandled exceptions.
    Returns a list of subscription dictionaries.
    """
    if isinstance(xml_data, bytes):
        xml_data = xml_data.decode('utf-8', errors='replace')

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid OPML XML content: {exc}") from exc

    # Handle case where root is not opml
    if root.tag.lower() != 'opml':
        # Check if opml is inside
        opml_node = root.find('.//opml')
        if opml_node is not None:
            root = opml_node
        else:
            raise ValueError("Root element is not <opml>")

    body = root.find('body')
    if body is None:
        return []

    subscriptions = []

    def _traverse(element, folder_stack):
        for outline in element.findall('outline'):
            text_attr = (outline.get('text') or outline.get('title') or '').strip()
            xml_url = outline.get('xmlUrl') or outline.get('xmlurl') or outline.get('htmlUrl') or outline.get('url') or ''
            xml_url = xml_url.strip()

            # Determine if this outline is a feed or a category folder
            # If it has an xml_url or can be parsed as a reddit source, it's a feed.
            source_dict = None
            if xml_url:
                source_dict = _parse_source_safe(xml_url)
            elif text_attr and is_reddit_url(text_attr):
                source_dict = _parse_source_safe(text_attr)

            has_children = len(outline.findall('outline')) > 0

            if source_dict is not None and not has_children:
                # It's a valid Reddit feed
                title = text_attr or core.describe_source(source_dict)
                categories = list(folder_stack)
                # Check for explicit category attribute on outline
                cat_attr = outline.get('category') or outline.get('categories') or ''
                if cat_attr:
                    for c in cat_attr.split(','):
                        c_clean = c.strip()
                        if c_clean and c_clean not in categories:
                            categories.append(c_clean)

                sub = {
                    'name': title,
                    'source_text': xml_url or core.source_text(source_dict),
                    'source': source_dict,
                    'title_filter': outline.get('titleFilter') or outline.get('title_filter') or '',
                    'author_filter': outline.get('authorFilter') or outline.get('author_filter') or '',
                    'flair_filter': outline.get('flairFilter') or outline.get('flair_filter') or '',
                    'layout': outline.get('layout', 'series'),
                    'index_url': outline.get('indexUrl') or outline.get('index_url') or '',
                    'title_from_body': (outline.get('titleFromBody') or outline.get('title_from_body') or '').lower() in ('true', '1'),
                    'author_note': (outline.get('authorNote') or outline.get('author_note') or '').lower() in ('true', '1'),
                    'categories': categories,
                }
                subscriptions.append(sub)
            else:
                # It's a folder or container (or non-Reddit parent outline)
                new_stack = list(folder_stack)
                if text_attr and not xml_url:
                    new_stack.append(text_attr)
                _traverse(outline, new_stack)

    _traverse(body, [])
    return subscriptions


def parse_json(json_data):
    """Parse JSON content (str or bytes) and extract Reddit subscriptions.

    Schema validation ensures required fields exist.
    Filters out non-Reddit feeds without throwing unhandled exceptions.
    Returns a list of subscription dictionaries.
    """
    if isinstance(json_data, bytes):
        json_data = json_data.decode('utf-8', errors='replace')

    try:
        data = json.loads(json_data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON content: {exc}") from exc

    if isinstance(data, dict):
        items = data.get('subscriptions', data.get('follows', []))
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("JSON content must be an object with 'subscriptions' array or an array of subscriptions.")

    if not isinstance(items, list):
        raise ValueError("'subscriptions' field must be an array.")

    subscriptions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (item.get('name') or item.get('title') or '').strip()
        src_obj = item.get('source')
        src_text = item.get('source_text') or item.get('url') or item.get('xmlUrl') or ''

        if isinstance(src_obj, dict) and src_obj.get('kind'):
            source_dict = src_obj
        elif src_text:
            source_dict = _parse_source_safe(src_text)
        elif name:
            source_dict = _parse_source_safe(name)
        else:
            source_dict = None

        if source_dict is None:
            continue

        categories = item.get('categories') or item.get('tags') or []
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.split(',') if c.strip()]
        elif not isinstance(categories, list):
            categories = []

        sub = {
            'name': name or core.describe_source(source_dict),
            'source_text': src_text or core.source_text(source_dict),
            'source': source_dict,
            'title_filter': item.get('title_filter', item.get('titleFilter', '')),
            'author_filter': item.get('author_filter', item.get('authorFilter', '')),
            'flair_filter': item.get('flair_filter', item.get('flairFilter', '')),
            'layout': item.get('layout', 'series'),
            'index_url': item.get('index_url', item.get('indexUrl', '')),
            'title_from_body': bool(item.get('title_from_body', item.get('titleFromBody', False))),
            'author_note': bool(item.get('author_note', item.get('authorNote', False))),
            'categories': [str(c) for c in categories if c],
        }
        subscriptions.append(sub)

    return subscriptions


def subscription_to_follow(sub):
    """Convert a subscription dictionary into a core follow dictionary."""
    src_text = sub.get('source_text') or (core.source_text(sub['source']) if sub.get('source') else '')
    follow = core.new_follow(
        sub.get('name', ''),
        src_text,
        sub.get('title_filter', ''),
        sub.get('author_filter', ''),
        sub.get('author_note', False),
        sub.get('layout', 'series'),
        sub.get('flair_filter', ''),
        sub.get('index_url', ''),
        sub.get('title_from_body', False),
    )
    if sub.get('categories'):
        follow['categories'] = list(sub['categories'])
    return follow


def generate_opml(follows, title="Reddit Story Follower Subscriptions"):
    """Generate OPML 2.0 XML string from a list of follow dictionaries.

    Groups follows by category folders if categories are present.
    """
    root = ET.Element('opml', version='2.0')
    head = ET.SubElement(root, 'head')
    title_elem = ET.SubElement(head, 'title')
    title_elem.text = title

    body = ET.SubElement(root, 'body')

    # Group follows by categories (multi-level hierarchy)
    # A category path is a tuple of strings e.g. ('Sci-Fi', 'Reddit Series')
    category_tree = {}  # folder_name -> dict or list
    uncategorized = []

    for follow in follows:
        categories = follow.get('categories') or follow.get('tags') or []
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.split(',') if c.strip()]

        if not categories:
            uncategorized.append(follow)
        else:
            curr = category_tree
            for cat in categories:
                if cat not in curr:
                    curr[cat] = {'__items__': [], '__subfolders__': {}}
                if cat == categories[-1]:
                    curr[cat]['__items__'].append(follow)
                curr = curr[cat]['__subfolders__']

    def _build_outline_for_follow(parent_elem, follow):
        src_url = core.source_text(follow['source']) if isinstance(follow.get('source'), dict) else follow.get('source_text', '')
        outline = ET.SubElement(parent_elem, 'outline')
        outline.set('text', follow.get('name', ''))
        outline.set('title', follow.get('name', ''))
        outline.set('type', 'rss')
        outline.set('xmlUrl', src_url)
        outline.set('htmlUrl', src_url)
        if follow.get('title_filter'):
            outline.set('titleFilter', follow['title_filter'])
        if follow.get('author_filter'):
            outline.set('authorFilter', follow['author_filter'])
        if follow.get('flair_filter'):
            outline.set('flairFilter', follow['flair_filter'])
        if follow.get('layout'):
            outline.set('layout', follow['layout'])
        if follow.get('index_url'):
            outline.set('indexUrl', follow['index_url'])
        if follow.get('title_from_body'):
            outline.set('titleFromBody', 'true')
        if follow.get('author_note'):
            outline.set('authorNote', 'true')

    def _write_category_node(parent_elem, cat_dict):
        for cat_name, node_data in cat_dict.items():
            cat_outline = ET.SubElement(parent_elem, 'outline', text=cat_name, title=cat_name)
            for item in node_data['__items__']:
                _build_outline_for_follow(cat_outline, item)
            if node_data['__subfolders__']:
                _write_category_node(cat_outline, node_data['__subfolders__'])

    _write_category_node(body, category_tree)

    for follow in uncategorized:
        _build_outline_for_follow(body, follow)

    # Pretty print XML using minidom
    raw_xml = ET.tostring(root, encoding='utf-8')
    parsed = minidom.parseString(raw_xml)
    return parsed.toprettyxml(indent='  ')


def generate_json(follows, indent=2):
    """Generate JSON string representation from a list of follow dictionaries."""
    export_items = []
    for follow in follows:
        categories = follow.get('categories') or follow.get('tags') or []
        if isinstance(categories, str):
            categories = [c.strip() for c in categories.split(',') if c.strip()]

        src_dict = follow.get('source') if isinstance(follow.get('source'), dict) else core.parse_source(follow.get('source_text', ''))
        item = {
            'id': follow.get('id', uuid.uuid4().hex[:10]),
            'name': follow.get('name', ''),
            'source_text': core.source_text(src_dict) if src_dict else follow.get('source_text', ''),
            'source': src_dict,
            'title_filter': follow.get('title_filter', ''),
            'author_filter': follow.get('author_filter', ''),
            'flair_filter': follow.get('flair_filter', ''),
            'layout': follow.get('layout', 'series'),
            'index_url': follow.get('index_url', ''),
            'title_from_body': bool(follow.get('title_from_body', False)),
            'author_note': bool(follow.get('author_note', False)),
            'categories': list(categories),
        }
        export_items.append(item)

    data = {
        'version': 1,
        'plugin': 'Reddit Story Follower',
        'subscriptions': export_items,
    }
    return json.dumps(data, indent=indent, ensure_ascii=False)


def is_duplicate(sub, existing_follow):
    """Check if an imported subscription matches an existing follow."""
    sub_src = sub.get('source') or _parse_source_safe(sub.get('source_text'))
    ex_src = existing_follow.get('source') or _parse_source_safe(existing_follow.get('source_text'))

    if sub_src and ex_src:
        if (sub_src.get('kind') == ex_src.get('kind') and
            sub_src.get('subreddit', '').casefold() == ex_src.get('subreddit', '').casefold() and
            sub_src.get('user', '').casefold() == ex_src.get('user', '').casefold() and
            sub_src.get('query', '').casefold() == ex_src.get('query', '').casefold()):
            return True

    sub_name = (sub.get('name') or '').strip().casefold()
    ex_name = (existing_follow.get('name') or '').strip().casefold()
    if sub_name and sub_name == ex_name:
        return True

    return False


def merge_subscriptions(existing_follows, imported_subs, strategy=STRATEGY_SKIP, prefix='[Imported] ', suffix=' (Imported)'):
    """Execute conflict resolution / merge strategies during batch import.

    Strategies:
    - STRATEGY_SKIP ('skip'): Skip duplicate entries.
    - STRATEGY_OVERWRITE ('overwrite'): Overwrite existing follow definitions.
    - STRATEGY_ADD_SUFFIX ('add_suffix' / 'prefix'): Add suffix/prefix to duplicate entries and import as new.

    Returns (updated_follows_list, stats_dict) where stats_dict is
    {'added': int, 'updated': int, 'skipped': int}.
    """
    updated_follows = [dict(f) for f in existing_follows]
    stats = {'added': 0, 'updated': 0, 'skipped': 0}

    # Normalize strategy name
    strat = (strategy or STRATEGY_SKIP).lower().strip()
    if strat in ('skip duplicates', 'skip_duplicates'):
        strat = STRATEGY_SKIP
    elif strat in ('overwrite existing', 'overwrite_existing'):
        strat = STRATEGY_OVERWRITE
    elif strat in ('add suffix', 'add_prefix_suffix', 'prefix', 'suffix'):
        strat = STRATEGY_ADD_SUFFIX

    for sub in imported_subs:
        # Find matching existing follow
        matched_idx = None
        for idx, ex in enumerate(updated_follows):
            if is_duplicate(sub, ex):
                matched_idx = idx
                break

        if matched_idx is not None:
            if strat == STRATEGY_SKIP:
                stats['skipped'] += 1
                continue
            elif strat == STRATEGY_OVERWRITE:
                ex = updated_follows[matched_idx]
                new_follow = subscription_to_follow(sub)
                new_follow['id'] = ex['id']
                new_follow['last_checked'] = ex.get('last_checked', 0.0)
                new_follow['last_status'] = ex.get('last_status', '')
                updated_follows[matched_idx] = new_follow
                stats['updated'] += 1
            elif strat == STRATEGY_ADD_SUFFIX:
                new_follow = subscription_to_follow(sub)
                base_name = sub.get('name', 'Series')
                if prefix and not base_name.startswith(prefix.strip()):
                    base_name = f"{prefix}{base_name}"
                if suffix and not base_name.endswith(suffix.strip()):
                    base_name = f"{base_name}{suffix}"
                if not prefix and not suffix:
                    base_name = f"{base_name} (Copy)"
                new_follow['name'] = base_name
                new_follow['id'] = uuid.uuid4().hex[:10]
                updated_follows.append(new_follow)
                stats['added'] += 1
        else:
            new_follow = subscription_to_follow(sub)
            updated_follows.append(new_follow)
            stats['added'] += 1

    return updated_follows, stats
