"""Loads each built plugin zip against stub calibre/Qt modules to catch import and name errors.

This does not exercise real Calibre or Qt behaviour; it only proves the packaged modules import and their pure helpers run.
"""

import importlib
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_plugins  # noqa: E402

STUBS = [
    "calibre",
    "calibre.customize",
    "calibre.gui2",
    "calibre.gui2.actions",
    "calibre.utils",
    "calibre.utils.config",
    "calibre.utils.date",
    "calibre.ebooks",
    "calibre.ebooks.metadata",
    "calibre.ebooks.metadata.meta",
    "calibre.ebooks.metadata.book",
    "calibre.ebooks.metadata.book.base",
    "qt",
    "qt.core",
]


class Meta:
    def __init__(self, title, authors):
        self.title, self.authors, self.ids = title, authors, {}

    def set_identifier(self, key, value):
        self.ids[key] = value


class PluginImportTests(unittest.TestCase):
    def setUp(self):
        self.saved = {k: v for k, v in sys.modules.items() if k.startswith(("calibre", "qt", "calibre_plugins"))}
        for name in STUBS:
            sys.modules[name] = MagicMock()
        sys.modules["calibre.ebooks.metadata.book.base"].Metadata = Meta
        sys.modules["calibre.gui2.actions"].InterfaceAction = object
        sys.modules["calibre.customize"].InterfaceActionBase = object
        sys.modules["calibre.utils.config"].JSONConfig = lambda name: type("Prefs", (dict,), {"defaults": {}})()
        self.tmp = tempfile.TemporaryDirectory()
        sys.modules["calibre_plugins"] = types.ModuleType("calibre_plugins")
        sys.modules["calibre_plugins"].__path__ = []

    def tearDown(self):
        for k in [k for k in sys.modules if k.startswith(("calibre", "qt"))]:
            del sys.modules[k]
        sys.modules.update(self.saved)
        self.tmp.cleanup()

    def load(self, import_name):
        """Extract the freshly built zip and register it as calibre_plugins.<import_name>."""
        if not hasattr(self, "built"):
            self.built = build_plugins.build_all(Path(self.tmp.name) / "dist")
        target = Path(self.tmp.name) / import_name
        zipfile.ZipFile(self.built[import_name]).extractall(target)
        package = f"calibre_plugins.{import_name}"
        module = types.ModuleType(package)
        module.__path__ = [str(target)]
        sys.modules[package] = module
        return package

    def test_every_plugin_module_imports(self):
        self.load("metabods_downloader")  # builds everything that can be built in this environment
        for import_name in self.built:
            package = self.load(import_name)
            modules = {
                "story_collection_tagger": ["config", "action", "rules"],
                "gemini_library_fixer": ["config", "action", "engine"],
                "media_matcher": ["config", "action", "tags", "sources", "matching"],
                "epub_promo_scrubber": ["action", "ui", "core"],
                "fanfic_downloader": ["config", "action", "ui", "engine", "flaresolverr"],
            }.get(import_name, ["config", "action", "ui", "core"])
            for module in modules:
                with self.subTest(plugin=import_name, module=module):
                    importlib.import_module(f"{package}.{module}")

    def test_library_metadata_helper(self):
        package = self.load("metabods_downloader")
        libkit = importlib.import_module(package + ".libkit")
        group = [
            {
                "id": "a",
                "title": "T",
                "author": "Au",
                "tags": ["x"],
                "categories": ["c"],
                "summary": "S & s",
                "pubdate": "2026-01-02",
            }
        ]
        mi = libkit.metadata_for(group, None, "metabods", "Metabods")
        self.assertEqual((mi.title, mi.authors, mi.ids, mi.tags), ("T", ["Au"], {"metabods": "a"}, ["x", "c"]))
        self.assertEqual(mi.comments, "<p>S &amp; s</p>")
        two = libkit.metadata_for(group + [dict(group[0], id="b", title="U")], "Omni", "metabods", "Metabods")
        self.assertEqual((two.title, two.ids), ("Omni", {}))

    def test_library_metadata_carries_series_fields(self):
        package = self.load("metabods_downloader")
        libkit = importlib.import_module(package + ".libkit")
        story = {
            "id": "a",
            "title": "Part B",
            "author": "Au",
            "tags": [],
            "categories": [],
            "series": "My Series",
            "series_index": 2.0,
        }
        mi = libkit.metadata_for([story], None, "nifty", "Nifty")
        self.assertEqual((mi.series, mi.series_index), ("My Series", 2.0))
        plain = libkit.metadata_for(
            [{"id": "b", "title": "Solo", "author": "Au", "tags": [], "categories": []}], None, "nifty", "Nifty"
        )
        self.assertFalse(hasattr(plain, "series"), "a story outside a series gets no series fields")

    def test_downloader_action_existing_ids(self):
        for plugin, ident in (("metabods_downloader", "metabods"), ("nifty_downloader", "nifty")):
            package = self.load(plugin)
            libkit = importlib.import_module(package + ".libkit")
            action_mod = importlib.import_module(package + ".action")

            # Identify action class
            action_cls = action_mod.MetabodsAction if plugin == "metabods_downloader" else action_mod.NiftyAction
            self.assertTrue(issubclass(action_cls, libkit.DownloaderAction))

            mock_gui = MagicMock()
            mock_gui.current_db.new_api.all_book_ids.return_value = [1, 2]
            mock_gui.current_db.new_api.all_field_for.return_value = {1: {ident: "id1"}, 2: {ident: "id2"}}

            action = action_cls()
            action.gui = mock_gui
            self.assertEqual(action.existing_ids(), {"id1", "id2"})

    def test_epub_builder_and_core_build_epub(self):
        for plugin in ("metabods_downloader", "nifty_downloader"):
            package = self.load(plugin)
            storykit = importlib.import_module(package + ".storykit")
            core = importlib.import_module(package + ".core")

            self.assertTrue(hasattr(storykit, "epub_builder"))
            self.assertTrue(callable(core.build_epub))

    def test_import_books_chunked_duplicate_checking(self):
        from plugin import importer

        content_a = b"A" * 70000  # larger than 64 KB chunk size
        content_b = b"B" * 70000

        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tf:
            tf.write(content_a)
            tf.flush()
            path_a = Path(tf.name)

        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tf_diff:
            tf_diff.write(content_b)
            tf_diff.flush()
            path_b = Path(tf_diff.name)

        try:
            # 1. Exact duplicate (title match, size match, SHA-256 hash match)
            mock_gui = MagicMock()
            mock_db = MagicMock()
            mock_gui.current_db.new_api = mock_db
            mock_db.all_book_ids.return_value = [10]
            mock_db.all_field_for.return_value = {10: "Test Book"}
            mock_db.format_metadata.return_value = {"size": len(content_a)}
            mock_db.format_abspath.return_value = None
            mock_db.format.return_value = content_a

            row_dup = {
                "output": str(path_a),
                "format": "epub",
                "source": "Test Book.epub",
                "metadata": {"title": "Test Book"},
            }
            imported, errors = importer.import_books(mock_gui, [row_dup])

            self.assertEqual(imported, [])
            self.assertEqual(row_dup.get("import_status"), "duplicate skipped")

            # 2. Size mismatch (title match, size mismatch -> duplicate check skips)
            mock_db.reset_mock()
            mock_db.all_book_ids.return_value = [10]
            mock_db.all_field_for.return_value = {10: "Test Book"}
            mock_db.format_metadata.return_value = {"size": 1234}  # different size
            mock_db.create_book_entry.return_value = 11
            mock_db.add_format.return_value = True

            row_size_mismatch = {
                "output": str(path_a),
                "format": "epub",
                "source": "Test Book.epub",
                "metadata": {"title": "Test Book"},
            }
            imported, errors = importer.import_books(mock_gui, [row_size_mismatch])

            self.assertEqual(imported, [11])
            self.assertEqual(row_size_mismatch.get("import_status"), "imported")
            self.assertEqual(row_size_mismatch.get("calibre_id"), 11)
            # Verify mock_db.format was NOT called because size mismatch aborted hash calculation
            mock_db.format.assert_not_called()

            # 3. Content mismatch (title match, size match, SHA-256 hash mismatch)
            mock_db.reset_mock()
            mock_db.all_book_ids.return_value = [10]
            mock_db.all_field_for.return_value = {10: "Test Book"}
            mock_db.format_metadata.return_value = {"size": len(content_b)}
            mock_db.format.return_value = content_a  # content_a != content_b
            mock_db.create_book_entry.return_value = 12
            mock_db.add_format.return_value = True

            row_content_mismatch = {
                "output": str(path_b),
                "format": "epub",
                "source": "Test Book.epub",
                "metadata": {"title": "Test Book"},
            }
            imported, errors = importer.import_books(mock_gui, [row_content_mismatch])

            self.assertEqual(imported, [12])
            self.assertEqual(row_content_mismatch.get("import_status"), "imported")

        finally:
            path_a.unlink(missing_ok=True)
            path_b.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
