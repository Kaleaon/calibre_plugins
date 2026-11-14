import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Import reddit modules
spec_reg = importlib.util.spec_from_file_location(
    "rregistry", Path(__file__).resolve().parents[1] / "reddit/registry.py"
)
registry_mod = importlib.util.module_from_spec(spec_reg)
spec_reg.loader.exec_module(registry_mod)

spec_mig = importlib.util.spec_from_file_location(
    "rmigration", Path(__file__).resolve().parents[1] / "reddit/migration.py"
)
migration_mod = importlib.util.module_from_spec(spec_mig)
spec_mig.loader.exec_module(migration_mod)

spec_core = importlib.util.spec_from_file_location("rcore", Path(__file__).resolve().parents[1] / "reddit/core.py")
core = importlib.util.module_from_spec(spec_core)
spec_core.loader.exec_module(core)


class DummySource:
    mode = "rss"

    def __init__(self, pages_data):
        self.pages_data = pages_data

    def page(self, src_rule, after=None):
        key = (
            src_rule.get("kind"),
            src_rule.get("subreddit"),
            src_rule.get("user"),
            src_rule.get("query"),
        )
        res = self.pages_data.get(key, ([], None))
        return res


class SQLiteRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "series_registry.db")
        self.reg = registry_mod.SeriesRegistry(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tables_initialized(self):
        with self.reg._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = {row["name"] for row in cursor.fetchall()}
            expected = {"series", "feed_sources", "authors", "chapters", "manifests"}
            self.assertTrue(expected.issubset(tables), f"Missing tables. Found: {tables}")

    def test_crud_multi_source_series(self):
        s_dict = {
            "id": "series101",
            "name": "Collaborative Universe",
            "layout": "series",
            "author_note": True,
            "title_from_body": False,
            "sources": [
                {
                    "kind": "subreddit",
                    "subreddit": "HFY",
                    "title_filter": "Universe Part",
                },
                {
                    "kind": "user",
                    "user": "GuestWriter",
                    "title_filter": "Universe Part",
                },
            ],
        }
        self.reg.save_series(s_dict)

        retrieved = self.reg.get_series("series101")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved["name"], "Collaborative Universe")
        self.assertEqual(len(retrieved["sources"]), 2)
        self.assertEqual(retrieved["sources"][0]["kind"], "subreddit")
        self.assertEqual(retrieved["sources"][1]["user"], "GuestWriter")

    def test_chapter_and_manifest_deduplication_and_sequencing(self):
        series_id = "multi_author_series"
        self.reg.save_series({"id": series_id, "name": "Multi Author Tale"})

        post1 = {
            "id": "t3_post1",
            "title": "Chapter 1: The Beginning",
            "author": "AuthorAlpha",
            "created": 1000.0,
            "link": "https://www.reddit.com/r/HFY/comments/post1/",
            "html": "<p>First chapter body by Alpha</p>",
        }
        post2 = {
            "id": "t3_post2",
            "title": "Chapter 2: The Expansion",
            "author": "AuthorBeta",
            "created": 2000.0,
            "link": "https://www.reddit.com/r/HFY/comments/post2/",
            "html": "<p>Second chapter body by Beta</p>",
        }
        post1_dup = {
            "id": "t3_post1_dup",
            "title": "Chapter 1: The Beginning (Crosspost)",
            "author": "AuthorAlpha",
            "created": 1050.0,
            "link": "https://www.reddit.com/r/HFY/comments/post1/",  # Same link!
            "html": "<p>First chapter body by Alpha</p>",
        }

        self.reg.save_chapter_and_manifest(series_id, post1)
        self.reg.save_chapter_and_manifest(series_id, post2)
        self.reg.save_chapter_and_manifest(series_id, post1_dup)

        manifest = self.reg.get_manifest(series_id)
        # Deduplication should exclude duplicate links from manifest
        self.assertEqual(len(manifest), 2)
        self.assertEqual(manifest[0]["post_id"], "t3_post1")
        self.assertEqual(manifest[0]["author_attribution"], "AuthorAlpha")
        self.assertEqual(manifest[1]["post_id"], "t3_post2")
        self.assertEqual(manifest[1]["author_attribution"], "AuthorBeta")

        # Authors table check
        all_series = self.reg.get_series(series_id)
        self.assertIn("AuthorAlpha", all_series["authors"])
        self.assertIn("AuthorBeta", all_series["authors"])

    def test_multi_source_feed_polling_and_epub_building(self):
        follow = core.new_follow(
            "Multi Feed Story",
            sources=[
                {
                    "kind": "subreddit",
                    "subreddit": "HFY",
                    "user": "",
                    "query": "",
                    "title_filter": "Part",
                },
                {
                    "kind": "user",
                    "subreddit": "",
                    "user": "CoAuthorBob",
                    "query": "",
                    "title_filter": "Part",
                },
            ],
        )
        follow["id"] = "multi_feed_001"

        self.reg.save_series(follow)
        cache = core.ChapterCache(self.temp_dir.name, follow["id"], registry=self.reg)

        p1 = {
            "id": "t3_a1",
            "title": "Part 1",
            "author": "AuthorAlice",
            "created": 100.0,
            "html": "<p>Content 1</p>",
            "link": "https://reddit.com/a1",
        }
        p2 = {
            "id": "t3_b1",
            "title": "Part 2",
            "author": "CoAuthorBob",
            "created": 200.0,
            "html": "<p>Content 2</p>",
            "link": "https://reddit.com/b1",
        }

        dummy_source = DummySource(
            {
                ("subreddit", "HFY", "", ""): ([p1], None),
                ("user", "", "CoAuthorBob", ""): ([p2], None),
            }
        )

        res = core.check_follow(dummy_source, follow, cache)
        self.assertEqual(len(res["new"]), 2)

        # Build story and check multi-author attribution
        story = core.build_story(follow, cache)
        self.assertIn("AuthorAlice", story["author"])
        self.assertIn("CoAuthorBob", story["author"])
        self.assertEqual(len(story["sections"]), 2)
        self.assertIn("by AuthorAlice", story["sections"][0][0])
        self.assertIn("by CoAuthorBob", story["sections"][1][0])

    def test_json_migration(self):
        prefs_mock = {
            "follows": [
                {
                    "id": "legacy_1",
                    "name": "Old JSON Follow",
                    "source": {"kind": "user", "user": "OldWriter"},
                    "title_filter": "Chapter",
                    "author_filter": "OldWriter",
                    "layout": "series",
                }
            ]
        }

        cache_json_data = {
            "complete": True,
            "newest_created": 5000.0,
            "newest_id": "t3_old1",
            "posts": {
                "t3_old1": {
                    "id": "t3_old1",
                    "title": "Chapter 1",
                    "author": "OldWriter",
                    "created": 5000.0,
                    "html": "<p>Old text</p>",
                }
            },
        }
        cache_file = os.path.join(self.temp_dir.name, "legacy_1.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_json_data, f)

        migration_mod.migrate_json_to_sqlite(self.reg, prefs_mock, self.temp_dir.name)

        migrated = self.reg.get_series("legacy_1")
        self.assertIsNotNone(migrated)
        self.assertEqual(migrated["name"], "Old JSON Follow")
        chapters = self.reg.get_chapters("legacy_1")
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0]["title"], "Chapter 1")

    def test_thread_safety(self):
        s_dict = {"id": "concurrent_series", "name": "Concurrent Series"}
        self.reg.save_series(s_dict)

        errors = []

        def worker(thread_idx):
            try:
                for i in range(10):
                    pid = f"t3_t{thread_idx}_p{i}"
                    post = {
                        "id": pid,
                        "title": f"Chapter {thread_idx}-{i}",
                        "author": f"Author_{thread_idx}",
                        "created": time.time() + i,
                        "html": f"<p>Thread {thread_idx} post {i}</p>",
                    }
                    self.reg.save_chapter_and_manifest("concurrent_series", post)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        chapters = self.reg.get_chapters("concurrent_series")
        self.assertEqual(len(chapters), 40)

    def test_cascade_deletion_and_out_of_order_sequencing(self):
        s_id = "cascade_test"
        self.reg.save_series(
            {
                "id": s_id,
                "name": "Cascade Test Series",
                "sources": [{"kind": "subreddit", "subreddit": "HFY"}],
            }
        )

        # Save chapter 2 first, then chapter 1 (out of order)
        p2 = {
            "id": "t3_c2",
            "title": "Part 2",
            "author": "Author1",
            "created": 2000.0,
            "html": "<p>P2</p>",
        }
        p1 = {
            "id": "t3_c1",
            "title": "Part 1",
            "author": "Author1",
            "created": 1000.0,
            "html": "<p>P1</p>",
        }

        self.reg.save_chapter_and_manifest(s_id, p2)
        manifest_before = self.reg.get_manifest(s_id)
        self.assertEqual(len(manifest_before), 1)
        self.assertEqual(manifest_before[0]["post_id"], "t3_c2")

        self.reg.save_chapter_and_manifest(s_id, p1)
        manifest_after = self.reg.get_manifest(s_id)
        # Manifest sequence order should re-index Part 1 (created=1000) before Part 2 (created=2000)
        self.assertEqual(len(manifest_after), 2)
        self.assertEqual(manifest_after[0]["post_id"], "t3_c1")
        self.assertEqual(manifest_after[1]["post_id"], "t3_c2")

        # Test series deletion cascades to child tables
        self.reg.delete_series(s_id)
        self.assertIsNone(self.reg.get_series(s_id))
        self.assertEqual(len(self.reg.get_chapters(s_id)), 0)
        self.assertEqual(len(self.reg.get_manifest(s_id)), 0)

    def test_manifests_schema_and_columns(self):
        with self.reg._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(manifests);")
            columns = {row["name"]: row["dflt_value"] for row in cursor.fetchall()}
            self.assertIn("is_excluded", columns)
            self.assertIn("is_custom_order", columns)
            self.assertIn("reconciliation_status", columns)
            self.assertEqual(columns["is_excluded"], "0")
            self.assertEqual(columns["is_custom_order"], "0")
            self.assertIn("approved", columns["reconciliation_status"].lower())

    def test_custom_order_and_automated_reconciliation_pending_status(self):
        s_id = "custom_recon_series"
        self.reg.save_series({"id": s_id, "name": "Reconciliation Story"})

        p1 = {"id": "t3_post1", "title": "Ch 1: Start", "author": "AuthorA", "created": 1000.0, "html": "<p>1</p>"}
        p2 = {"id": "t3_post2", "title": "Ch 2: Middle", "author": "AuthorB", "created": 2000.0, "html": "<p>2</p>"}
        self.reg.save_chapter_and_manifest(s_id, p1)
        self.reg.save_chapter_and_manifest(s_id, p2)

        # Apply a custom order reversing the chapters and editing a title
        custom_manifest = [
            {
                "post_id": "t3_post2",
                "sequence_order": 1,
                "chapter_title": "Prequel Ch 2",
                "is_excluded": 0,
                "reconciliation_status": "approved",
            },
            {
                "post_id": "t3_post1",
                "sequence_order": 2,
                "chapter_title": "Sequel Ch 1",
                "is_excluded": 0,
                "reconciliation_status": "approved",
            },
        ]
        self.reg.save_custom_manifest(s_id, custom_manifest)

        manifest = self.reg.get_manifest(s_id)
        self.assertEqual(len(manifest), 2)
        self.assertEqual(manifest[0]["post_id"], "t3_post2")
        self.assertEqual(manifest[0]["chapter_title"], "Prequel Ch 2")
        self.assertEqual(manifest[0]["is_custom_order"], 1)
        self.assertEqual(manifest[0]["reconciliation_status"], "approved")
        self.assertEqual(manifest[1]["post_id"], "t3_post1")

        # Now simulate scraping a new post (created at 1500, between p1 and p2)
        p3 = {
            "id": "t3_post3",
            "title": "Ch 3: Newly Discovered",
            "author": "AuthorC",
            "created": 1500.0,
            "html": "<p>3</p>",
        }
        self.reg.save_chapter_and_manifest(s_id, p3)

        # Automated reconciliation must detect new post and append with reconciliation_status = 'pending'
        manifest_after = self.reg.get_manifest(s_id)
        self.assertEqual(len(manifest_after), 3)
        # Custom sequence order for existing chapters is preserved
        self.assertEqual(manifest_after[0]["post_id"], "t3_post2")
        self.assertEqual(manifest_after[0]["sequence_order"], 1)
        self.assertEqual(manifest_after[1]["post_id"], "t3_post1")
        self.assertEqual(manifest_after[1]["sequence_order"], 2)
        # New chapter appended at sequence_order 3 with pending status
        self.assertEqual(manifest_after[2]["post_id"], "t3_post3")
        self.assertEqual(manifest_after[2]["sequence_order"], 3)
        self.assertEqual(manifest_after[2]["reconciliation_status"], "pending")
        self.assertEqual(manifest_after[2]["is_custom_order"], 1)

        self.assertTrue(self.reg.has_pending_reconciliation(s_id))
        self.assertEqual(self.reg.get_pending_manifest_count(s_id), 1)

        # Approving the pending post
        self.reg.approve_manifest_item(s_id, "t3_post3")
        self.assertFalse(self.reg.has_pending_reconciliation(s_id))
        self.assertEqual(self.reg.get_pending_manifest_count(s_id), 0)

        # Resetting order to default chronological
        self.reg.reset_manifest_order(s_id)
        reset_manifest = self.reg.get_manifest(s_id)
        self.assertEqual([m["post_id"] for m in reset_manifest], ["t3_post1", "t3_post3", "t3_post2"])
        self.assertFalse(any(m["is_custom_order"] for m in reset_manifest))

    def test_chapter_cache_ordered_omits_excluded_and_pending_posts(self):
        s_id = "cache_filter_test"
        follow = {"id": s_id, "name": "Cache Filter Story", "sources": []}
        self.reg.save_series(follow)

        posts = [
            {
                "id": "t3_p1",
                "title": "Raw 1",
                "author": "A1",
                "created": 100.0,
                "html": "<p>P1</p>",
            },
            {
                "id": "t3_p2",
                "title": "Raw 2",
                "author": "A2",
                "created": 200.0,
                "html": "<p>P2</p>",
            },
            {
                "id": "t3_p3",
                "title": "Raw 3",
                "author": "A3",
                "created": 300.0,
                "html": "<p>P3</p>",
            },
            {
                "id": "t3_p4",
                "title": "Raw 4",
                "author": "A4",
                "created": 400.0,
                "html": "<p>P4</p>",
            },
        ]
        self.reg.save_chapters_batch(s_id, posts)

        # Configure custom manifest:
        # p2: seq 1, approved, custom title
        # p1: seq 2, is_excluded=1 (should be omitted)
        # p4: seq 3, pending (should be omitted)
        # p3: seq 4, approved
        custom = [
            {
                "post_id": "t3_p2",
                "sequence_order": 1,
                "chapter_title": "Custom Ch 2",
                "is_excluded": 0,
                "reconciliation_status": "approved",
            },
            {
                "post_id": "t3_p1",
                "sequence_order": 2,
                "chapter_title": "Excluded Ch 1",
                "is_excluded": 1,
                "reconciliation_status": "approved",
            },
            {
                "post_id": "t3_p4",
                "sequence_order": 3,
                "chapter_title": "Pending Ch 4",
                "is_excluded": 0,
                "reconciliation_status": "pending",
            },
            {
                "post_id": "t3_p3",
                "sequence_order": 4,
                "chapter_title": "Custom Ch 3",
                "is_excluded": 0,
                "reconciliation_status": "approved",
            },
        ]
        self.reg.save_custom_manifest(s_id, custom)

        cache = core.ChapterCache(self.temp_dir.name, s_id, registry=self.reg)
        ordered_posts = cache.ordered()

        # Should only contain p2 and p3 (omitting excluded p1 and pending p4)
        self.assertEqual(len(ordered_posts), 2)
        self.assertEqual(ordered_posts[0]["id"], "t3_p2")
        self.assertEqual(ordered_posts[0]["title"], "Custom Ch 2")
        self.assertEqual(ordered_posts[1]["id"], "t3_p3")
        self.assertEqual(ordered_posts[1]["title"], "Custom Ch 3")

        # Test EPUB story generation uses these verified, non-excluded chapters
        story = core.build_story(follow, cache)
        self.assertEqual(len(story["sections"]), 2)
        self.assertIn("Custom Ch 2", story["sections"][0][0])
        self.assertIn("Custom Ch 3", story["sections"][1][0])


if __name__ == "__main__":
    unittest.main()
