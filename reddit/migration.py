"""Automated migration module from JSON follow definitions and cache files to SQLite series_registry.db.
"""
import json
import os


def migrate_json_to_sqlite(registry, prefs, cache_dir):
    """Migrates legacy JSON follows from prefs and cache_dir into SQLite series_registry.db."""
    legacy_follows = prefs.get('follows', [])
    if not legacy_follows:
        return

    for follow in legacy_follows:
        series_id = follow.get('id')
        if not series_id:
            continue

        # Check if already migrated
        existing = registry.get_series(series_id)
        if existing is None:
            # Build series dict and sources
            s_dict = {
                'id': series_id,
                'name': follow.get('name', ''),
                'layout': follow.get('layout', 'series'),
                'author_note': follow.get('author_note', False),
                'title_from_body': follow.get('title_from_body', False),
                'index_url': follow.get('index_url', ''),
                'last_checked': follow.get('last_checked', 0.0),
                'last_status': follow.get('last_status', ''),
                'sources': []
            }

            # Map feed sources
            if 'sources' in follow and follow['sources']:
                s_dict['sources'] = follow['sources']
            elif 'source' in follow and follow['source']:
                s_dict['sources'] = [{
                    'kind': follow['source'].get('kind', 'subreddit'),
                    'subreddit': follow['source'].get('subreddit', ''),
                    'user': follow['source'].get('user', ''),
                    'query': follow['source'].get('query', ''),
                    'title_filter': follow.get('title_filter', ''),
                    'author_filter': follow.get('author_filter', ''),
                    'flair_filter': follow.get('flair_filter', '')
                }]

            # Load chapter cache if present
            cache_file = os.path.join(cache_dir, f"{series_id}.json")
            cache_data = {}
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                except (OSError, ValueError):
                    pass

            if cache_data:
                s_dict['complete'] = cache_data.get('complete', False)
                s_dict['newest_created'] = cache_data.get('newest_created', 0.0)
                s_dict['newest_id'] = cache_data.get('newest_id', '')
                if 'index' in cache_data:
                    s_dict['index_data'] = json.dumps(cache_data['index'])

            registry.save_series(s_dict)

            # Migrate cached chapters
            posts = cache_data.get('posts', {})
            added_ids = set(cache_data.get('added', []))
            for pid, post in posts.items():
                post_obj = dict(post)
                if pid in added_ids:
                    post_obj['added'] = True
                registry.save_chapter_and_manifest(series_id, post_obj)

            if added_ids:
                registry.set_added_to_lib(series_id, added_ids)
