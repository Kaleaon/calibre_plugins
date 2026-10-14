import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import reddit modules
spec_reg = importlib.util.spec_from_file_location('rregistry', Path(__file__).resolve().parents[1] / 'reddit/registry.py')
registry_mod = importlib.util.module_from_spec(spec_reg)
spec_reg.loader.exec_module(registry_mod)

spec_mig = importlib.util.spec_from_file_location('rmigration', Path(__file__).resolve().parents[1] / 'reddit/migration.py')
migration_mod = importlib.util.module_from_spec(spec_mig)
spec_mig.loader.exec_module(migration_mod)

spec_core = importlib.util.spec_from_file_location('rcore', Path(__file__).resolve().parents[1] / 'reddit/core.py')
core = importlib.util.module_from_spec(spec_core)
spec_core.loader.exec_module(core)


class DummySource:
    mode = 'rss'
    def __init__(self, pages_data):
        self.pages_data = pages_data
    def page(self, src_rule, after=None):
        key = (src_rule.get('kind'), src_rule.get('subreddit'), src_rule.get('user'), src_rule.get('query'))
        res = self.pages_data.get(key, ([], None))
        return res


class SQLiteRegistryTests(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, 'series_registry.db')
        self.reg = registry_mod.SeriesRegistry(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tables_initialized(self):
        conn = self.reg._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row['name'] for row in cursor.fetchall()}
        expected = {'series', 'feed_sources', 'authors', 'chapters', 'manifests'}
        self.assertTrue(expected.issubset(tables), f"Missing tables. Found: {tables}")

    def test_crud_multi_source_series(self):
        s_dict = {
            'id': 'series101',
            'name': 'Collaborative Universe',
            'layout': 'series',
            'author_note': True,
            'title_from_body': False,
            'sources': [
                {'kind': 'subreddit', 'subreddit': 'HFY', 'title_filter': 'Universe Part'},
                {'kind': 'user', 'user': 'GuestWriter', 'title_filter': 'Universe Part'}
            ]
        }
        self.reg.save_series(s_dict)

        retrieved = self.reg.get_series('series101')
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved['name'], 'Collaborative Universe')
        self.assertEqual(len(retrieved['sources']), 2)
        self.assertEqual(retrieved['sources'][0]['kind'], 'subreddit')
        self.assertEqual(retrieved['sources'][1]['user'], 'GuestWriter')

    def test_chapter_and_manifest_deduplication_and_sequencing(self):
        series_id = 'multi_author_series'
        self.reg.save_series({'id': series_id, 'name': 'Multi Author Tale'})

        post1 = {
            'id': 't3_post1',
            'title': 'Chapter 1: The Beginning',
            'author': 'AuthorAlpha',
            'created': 1000.0,
            'link': 'https://www.reddit.com/r/HFY/comments/post1/',
            'html': '<p>First chapter body by Alpha</p>'
        }
        post2 = {
            'id': 't3_post2',
            'title': 'Chapter 2: The Expansion',
            'author': 'AuthorBeta',
            'created': 2000.0,
            'link': 'https://www.reddit.com/r/HFY/comments/post2/',
            'html': '<p>Second chapter body by Beta</p>'
        }
        post1_dup = {
            'id': 't3_post1_dup',
            'title': 'Chapter 1: The Beginning (Crosspost)',
            'author': 'AuthorAlpha',
            'created': 1050.0,
            'link': 'https://www.reddit.com/r/HFY/comments/post1/', # Same link!
            'html': '<p>First chapter body by Alpha</p>'
        }

        self.reg.save_chapter_and_manifest(series_id, post1)
        self.reg.save_chapter_and_manifest(series_id, post2)
        self.reg.save_chapter_and_manifest(series_id, post1_dup)

        manifest = self.reg.get_manifest(series_id)
        # Deduplication should exclude duplicate links from manifest
        self.assertEqual(len(manifest), 2)
        self.assertEqual(manifest[0]['post_id'], 't3_post1')
        self.assertEqual(manifest[0]['author_attribution'], 'AuthorAlpha')
        self.assertEqual(manifest[1]['post_id'], 't3_post2')
        self.assertEqual(manifest[1]['author_attribution'], 'AuthorBeta')

        # Authors table check
        all_series = self.reg.get_series(series_id)
        self.assertIn('AuthorAlpha', all_series['authors'])
        self.assertIn('AuthorBeta', all_series['authors'])

    def test_multi_source_feed_polling_and_epub_building(self):
        follow = core.new_follow('Multi Feed Story', sources=[
            {'kind': 'subreddit', 'subreddit': 'HFY', 'user': '', 'query': '', 'title_filter': 'Part'},
            {'kind': 'user', 'subreddit': '', 'user': 'CoAuthorBob', 'query': '', 'title_filter': 'Part'}
        ])
        follow['id'] = 'multi_feed_001'

        self.reg.save_series(follow)
        cache = core.ChapterCache(self.temp_dir.name, follow['id'], registry=self.reg)

        p1 = {'id': 't3_a1', 'title': 'Part 1', 'author': 'AuthorAlice', 'created': 100.0, 'html': '<p>Content 1</p>', 'link': 'https://reddit.com/a1'}
        p2 = {'id': 't3_b1', 'title': 'Part 2', 'author': 'CoAuthorBob', 'created': 200.0, 'html': '<p>Content 2</p>', 'link': 'https://reddit.com/b1'}

        dummy_source = DummySource({
            ('subreddit', 'HFY', '', ''): ([p1], None),
            ('user', '', 'CoAuthorBob', ''): ([p2], None)
        })

        res = core.check_follow(dummy_source, follow, cache)
        self.assertEqual(len(res['new']), 2)

        # Build story and check multi-author attribution
        story = core.build_story(follow, cache)
        self.assertIn('AuthorAlice', story['author'])
        self.assertIn('CoAuthorBob', story['author'])
        self.assertEqual(len(story['sections']), 2)
        self.assertIn('by AuthorAlice', story['sections'][0][0])
        self.assertIn('by CoAuthorBob', story['sections'][1][0])

    def test_json_migration(self):
        prefs_mock = {
            'follows': [{
                'id': 'legacy_1',
                'name': 'Old JSON Follow',
                'source': {'kind': 'user', 'user': 'OldWriter'},
                'title_filter': 'Chapter',
                'author_filter': 'OldWriter',
                'layout': 'series'
            }]
        }

        cache_json_data = {
            'complete': True,
            'newest_created': 5000.0,
            'newest_id': 't3_old1',
            'posts': {
                't3_old1': {
                    'id': 't3_old1',
                    'title': 'Chapter 1',
                    'author': 'OldWriter',
                    'created': 5000.0,
                    'html': '<p>Old text</p>'
                }
            }
        }
        cache_file = os.path.join(self.temp_dir.name, 'legacy_1.json')
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_json_data, f)

        migration_mod.migrate_json_to_sqlite(self.reg, prefs_mock, self.temp_dir.name)

        migrated = self.reg.get_series('legacy_1')
        self.assertIsNotNone(migrated)
        self.assertEqual(migrated['name'], 'Old JSON Follow')
        chapters = self.reg.get_chapters('legacy_1')
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0]['title'], 'Chapter 1')

    def test_thread_safety(self):
        s_dict = {'id': 'concurrent_series', 'name': 'Concurrent Series'}
        self.reg.save_series(s_dict)

        errors = []

        def worker(thread_idx):
            try:
                for i in range(10):
                    pid = f"t3_t{thread_idx}_p{i}"
                    post = {
                        'id': pid,
                        'title': f'Chapter {thread_idx}-{i}',
                        'author': f'Author_{thread_idx}',
                        'created': time.time() + i,
                        'html': f'<p>Thread {thread_idx} post {i}</p>'
                    }
                    self.reg.save_chapter_and_manifest('concurrent_series', post)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        chapters = self.reg.get_chapters('concurrent_series')
        self.assertEqual(len(chapters), 40)


if __name__ == '__main__':
    unittest.main()
