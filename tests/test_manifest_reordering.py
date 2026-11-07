"""Tests for Interactive Chapter Manifest Reordering and Exclusion Management."""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "reddit"))

spec_reg = importlib.util.spec_from_file_location(
    "rreg", Path(__file__).resolve().parents[1] / "reddit/registry.py"
)
registry_mod = importlib.util.module_from_spec(spec_reg)
spec_reg.loader.exec_module(registry_mod)

spec_core = importlib.util.spec_from_file_location(
    "rcore", Path(__file__).resolve().parents[1] / "reddit/core.py"
)
core = importlib.util.module_from_spec(spec_core)
spec_core.loader.exec_module(core)

_GLOBAL_QT_APP = None


def ensure_qt():
    global _GLOBAL_QT_APP
    if "qt.core" not in sys.modules:
        try:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            from PyQt6 import QtCore, QtGui, QtWidgets

            _GLOBAL_QT_APP = (
                QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            )
            qtcore = types.ModuleType("qt.core")
            for module in (QtWidgets, QtGui, QtCore):
                for name in dir(module):
                    setattr(qtcore, name, getattr(module, name))
            qtcore._ = lambda s: s
            sys.modules["qt"] = types.ModuleType("qt")
            sys.modules["qt.core"] = qtcore
        except ImportError:
            pass

    class StubPrefs(dict):
        def __init__(self):
            super().__init__()
            self.defaults = {"mode": "rss", "client_id": ""}

        def __getitem__(self, key):
            try:
                return dict.__getitem__(self, key)
            except KeyError:
                return self.defaults.get(key, "")

    if "calibre" not in sys.modules:
        sys.modules["calibre"] = MagicMock()
        sys.modules["calibre.utils"] = MagicMock()
        sys.modules["calibre.utils.config"] = MagicMock()
        sys.modules["calibre.utils.config"].JSONConfig = lambda name: StubPrefs()
        sys.modules["calibre.customize"] = MagicMock()

    calibre_plugins_mod = sys.modules.get("calibre_plugins") or types.ModuleType(
        "calibre_plugins"
    )
    sys.modules["calibre_plugins"] = calibre_plugins_mod
    pkg = sys.modules.get("calibre_plugins.reddit_follower") or types.ModuleType(
        "calibre_plugins.reddit_follower"
    )
    pkg.core = core
    setattr(calibre_plugins_mod, "reddit_follower", pkg)
    sys.modules["calibre_plugins.reddit_follower"] = pkg
    sys.modules["calibre_plugins.reddit_follower.core"] = core
    if not hasattr(pkg, "login"):
        pkg.login = MagicMock()
        sys.modules["calibre_plugins.reddit_follower.login"] = pkg.login
    if not hasattr(pkg, "guikit"):
        spec_gk = importlib.util.spec_from_file_location(
            "guikit", Path(__file__).resolve().parents[1] / "common/guikit.py"
        )
        gk = importlib.util.module_from_spec(spec_gk)
        spec_gk.loader.exec_module(gk)
        pkg.guikit = gk
        sys.modules["calibre_plugins.reddit_follower.guikit"] = gk
    if not hasattr(pkg, "storykit"):
        pkg.storykit = MagicMock()
        sys.modules["calibre_plugins.reddit_follower.storykit"] = pkg.storykit
    if not hasattr(pkg, "config"):
        spec_cfg = importlib.util.spec_from_file_location(
            "config", Path(__file__).resolve().parents[1] / "reddit/config.py"
        )
        cfg = importlib.util.module_from_spec(spec_cfg)
        spec_cfg.loader.exec_module(cfg)
        pkg.config = cfg
        sys.modules["calibre_plugins.reddit_follower.config"] = cfg


class ManifestReorderingAndExclusionTests(unittest.TestCase):
    def setUp(self):
        ensure_qt()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "series_registry.db")
        self.reg = registry_mod.SeriesRegistry(self.db_path)
        self.series_id = "series_test_reorder"
        self.reg.save_series(
            {
                "id": self.series_id,
                "name": "Reorder Test Story",
                "sources": [{"kind": "subreddit", "subreddit": "HFY"}],
            }
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manifest_schema_includes_exclusion_and_custom_order(self):
        """SQLite manifests table includes is_excluded and is_custom_order columns."""
        conn = self.reg._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(manifests);")
        columns = {row["name"]: row["type"] for row in cursor.fetchall()}
        self.assertIn("is_excluded", columns)
        self.assertIn("is_custom_order", columns)

    def test_save_manifest_order_and_persistence(self):
        """save_manifest_order persists custom sequence order, is_excluded, and is_duplicate."""
        p1 = {
            "id": "t3_ch1",
            "title": "Chapter 1",
            "author": "Author1",
            "created": 1000.0,
            "html": "<p>Ch 1</p>",
        }
        p2 = {
            "id": "t3_ch2",
            "title": "Chapter 2",
            "author": "Author1",
            "created": 2000.0,
            "html": "<p>Ch 2</p>",
        }
        p3 = {
            "id": "t3_ch3",
            "title": "Chapter 3 (Meta QA)",
            "author": "Author1",
            "created": 3000.0,
            "html": "<p>Ch 3</p>",
        }

        self.reg.save_chapter_and_manifest(self.series_id, p1)
        self.reg.save_chapter_and_manifest(self.series_id, p2)
        self.reg.save_chapter_and_manifest(self.series_id, p3)

        initial = self.reg.get_manifest(self.series_id, include_excluded=True)
        self.assertEqual(len(initial), 3)
        self.assertEqual(
            [m["post_id"] for m in initial], ["t3_ch1", "t3_ch2", "t3_ch3"]
        )

        # Custom reorder: move Chapter 2 first, Chapter 1 second, and exclude Chapter 3
        reordered_items = [
            {
                "post_id": "t3_ch2",
                "sequence_order": 1,
                "is_excluded": 0,
                "is_duplicate": 0,
            },
            {
                "post_id": "t3_ch1",
                "sequence_order": 2,
                "is_excluded": 0,
                "is_duplicate": 0,
            },
            {
                "post_id": "t3_ch3",
                "sequence_order": 3,
                "is_excluded": 1,
                "is_duplicate": 0,
            },
        ]
        self.reg.save_manifest_order(self.series_id, reordered_items)

        # get_manifest without include_excluded filters out Chapter 3
        active_manifest = self.reg.get_manifest(self.series_id, include_excluded=False)
        self.assertEqual(len(active_manifest), 2)
        self.assertEqual(active_manifest[0]["post_id"], "t3_ch2")
        self.assertEqual(active_manifest[0]["sequence_order"], 1)
        self.assertEqual(active_manifest[1]["post_id"], "t3_ch1")
        self.assertEqual(active_manifest[1]["sequence_order"], 2)

        # get_manifest with include_excluded returns all 3 with flags
        all_manifest = self.reg.get_manifest(self.series_id, include_excluded=True)
        self.assertEqual(len(all_manifest), 3)
        self.assertEqual(all_manifest[2]["post_id"], "t3_ch3")
        self.assertEqual(all_manifest[2]["is_excluded"], 1)
        self.assertEqual(all_manifest[0]["is_custom_order"], 1)

    def test_update_manifest_preserves_custom_order_and_exclusions(self):
        """_update_manifest_unlocked keeps custom sequence numbers and exclusion flags during feed checks."""
        # 1. Add Chapter 1 and Chapter 2
        p1 = {
            "id": "t3_post1",
            "title": "Prologue",
            "author": "Writer",
            "created": 100.0,
            "html": "<p>1</p>",
        }
        p2 = {
            "id": "t3_post2",
            "title": "Interlude",
            "author": "Writer",
            "created": 200.0,
            "html": "<p>2</p>",
        }
        self.reg.save_chapter_and_manifest(self.series_id, p1)
        self.reg.save_chapter_and_manifest(self.series_id, p2)

        # 2. Reorder so Interlude is seq=1, and Prologue is seq=2 and excluded
        custom_items = [
            {
                "post_id": "t3_post2",
                "sequence_order": 1,
                "is_excluded": 0,
                "is_duplicate": 0,
            },
            {
                "post_id": "t3_post1",
                "sequence_order": 2,
                "is_excluded": 1,
                "is_duplicate": 0,
            },
        ]
        self.reg.save_manifest_order(self.series_id, custom_items)

        # 3. Simulate feed check adding Chapter 3 (created at 300.0) and an out-of-order prequel (created at 50.0)
        p3 = {
            "id": "t3_post3",
            "title": "Chapter 1 Real",
            "author": "Writer",
            "created": 300.0,
            "html": "<p>3</p>",
        }
        p0 = {
            "id": "t3_post0",
            "title": "Early Note",
            "author": "Writer",
            "created": 50.0,
            "html": "<p>0</p>",
        }
        self.reg.save_chapters_batch(self.series_id, [p3, p0])

        all_manifest = self.reg.get_manifest(self.series_id, include_excluded=True)
        self.assertEqual(len(all_manifest), 4)

        manifest_map = {m["post_id"]: m for m in all_manifest}
        # Verify user custom order preserved:
        self.assertEqual(manifest_map["t3_post2"]["sequence_order"], 1)
        self.assertEqual(manifest_map["t3_post2"]["is_excluded"], 0)
        self.assertEqual(manifest_map["t3_post1"]["sequence_order"], 2)
        self.assertEqual(manifest_map["t3_post1"]["is_excluded"], 1)

        # Verify new feed posts appended to the end without overwriting user sequence
        self.assertGreater(manifest_map["t3_post0"]["sequence_order"], 2)
        self.assertGreater(manifest_map["t3_post3"]["sequence_order"], 2)

    def test_chapter_cache_ordered_and_epub_story_generation(self):
        """ChapterCache.ordered() returns non-excluded posts in custom order, and EPUB build respects it."""
        follow = {
            "id": self.series_id,
            "name": "Reorder Test Story",
            "source": {"kind": "subreddit", "subreddit": "HFY"},
            "layout": "series",
            "author_note": False,
            "title_from_body": False,
        }

        # Add 3 posts into registry first
        p1 = {
            "id": "t3_p1",
            "title": "Part 1",
            "author": "Alpha",
            "created": 100.0,
            "html": "<p>Story 1</p>",
            "link": "https://reddit.com/p1",
        }
        p2 = {
            "id": "t3_p2",
            "title": "Part 2",
            "author": "Alpha",
            "created": 200.0,
            "html": "<p>Story 2</p>",
            "link": "https://reddit.com/p2",
        }
        p3 = {
            "id": "t3_p3",
            "title": "Author Announcement",
            "author": "Alpha",
            "created": 300.0,
            "html": "<p>Announcement</p>",
            "link": "https://reddit.com/p3",
        }

        self.reg.save_chapters_batch(self.series_id, [p1, p2, p3])
        cache = core.ChapterCache(self.temp_dir.name, self.series_id, registry=self.reg)

        # Customize order: Part 2 first, Part 1 second, Author Announcement excluded
        self.reg.save_manifest_order(
            self.series_id,
            [
                {"post_id": "t3_p2", "sequence_order": 1, "is_excluded": 0},
                {"post_id": "t3_p1", "sequence_order": 2, "is_excluded": 0},
                {"post_id": "t3_p3", "sequence_order": 3, "is_excluded": 1},
            ],
        )

        ordered = cache.ordered()
        self.assertEqual(len(ordered), 2)
        self.assertEqual(ordered[0]["id"], "t3_p2")
        self.assertEqual(ordered[1]["id"], "t3_p1")

        # Build story structure for EPUB compilation
        story = core.build_story(follow, cache)
        self.assertEqual(len(story["sections"]), 2)
        self.assertEqual(story["sections"][0][0], "Part 2")
        self.assertEqual(story["sections"][1][0], "Part 1")

    def test_manifest_dialog_ui_interactions(self):
        """ManifestDialog UI supports Move Up/Down, drag/drop, toggle exclude/duplicate, and Save & Rebuild."""
        spec_ui = importlib.util.spec_from_file_location(
            "rui", Path(__file__).resolve().parents[1] / "reddit/ui.py"
        )
        ui = importlib.util.module_from_spec(spec_ui)
        spec_ui.loader.exec_module(ui)

        follow = {"id": self.series_id, "name": "Reorder Test Story"}
        p1 = {
            "id": "t3_u1",
            "title": "Chapter 1",
            "author": "Author",
            "created": 100.0,
            "html": "<p>1</p>",
            "link": "https://reddit.com/u1",
        }
        p2 = {
            "id": "t3_u2",
            "title": "Chapter 2",
            "author": "Author",
            "created": 200.0,
            "html": "<p>2</p>",
            "link": "https://reddit.com/u2",
        }
        p3 = {
            "id": "t3_u3",
            "title": "Chapter 3 (Meta Announcement)",
            "author": "Author",
            "created": 300.0,
            "html": "<p>3</p>",
            "link": "https://reddit.com/u3",
        }
        self.reg.save_chapters_batch(self.series_id, [p1, p2, p3])

        mock_action = MagicMock()
        mock_action.registry.return_value = self.reg

        dialog = ui.ManifestDialog(None, follow, mock_action)
        self.assertEqual(dialog.table.rowCount(), 3)
        self.assertEqual(dialog.table.columnCount(), 7)

        # Check headers
        headers = [dialog.table.horizontalHeaderItem(i).text() for i in range(7)]
        self.assertIn("Excluded", headers)
        self.assertIn("Seq", headers)

        # 1. Test Toggle Exclude on Chapter 3
        dialog.table.selectRow(2)
        dialog.toggle_exclude()
        self.assertEqual(dialog.entries[2]["is_excluded"], 1)
        self.assertEqual(dialog.table.item(2, 6).text(), "Yes")

        # 2. Test Move Down on Chapter 1
        dialog.table.selectRow(0)
        dialog.move_down()
        # Row 0 and Row 1 swapped: Chapter 2 is now first, Chapter 1 is second
        self.assertEqual(dialog.entries[0]["post_id"], "t3_u2")
        self.assertEqual(dialog.entries[1]["post_id"], "t3_u1")

        # 3. Test Move Up on Chapter 1
        dialog.table.selectRow(1)
        dialog.move_up()
        self.assertEqual(dialog.entries[0]["post_id"], "t3_u1")
        self.assertEqual(dialog.entries[1]["post_id"], "t3_u2")

        # 4. Test drag and drop reordering via reorder_row
        dialog.reorder_row(1, 0)  # Move Chapter 2 to top
        self.assertEqual(dialog.entries[0]["post_id"], "t3_u2")
        self.assertEqual(dialog.entries[1]["post_id"], "t3_u1")
        self.assertEqual(dialog.entries[2]["post_id"], "t3_u3")

        # 5. Capture visual screenshot of the dialog in offscreen mode
        dialog.show()
        pixmap = dialog.grab()
        pixmap.save("/tmp/manifest_reorder_dialog.png")

        # 6. Test Save & Rebuild
        dialog.save_and_rebuild()
        self.assertTrue(
            mock_action.rebuild_book.called or mock_action.update_library.called
        )

        # Verify saved in registry
        persisted = self.reg.get_manifest(self.series_id, include_excluded=True)
        self.assertEqual(persisted[0]["post_id"], "t3_u2")
        self.assertEqual(persisted[0]["sequence_order"], 1)
        self.assertEqual(persisted[1]["post_id"], "t3_u1")
        self.assertEqual(persisted[1]["sequence_order"], 2)
        self.assertEqual(persisted[2]["post_id"], "t3_u3")
        self.assertEqual(persisted[2]["is_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
