"""Unit tests for common/opml_kit.py OPML 2.0 and JSON export/import engine,
including schema validation, conflict resolution, and UI dialog integration.
"""

import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "common"))

from common import opml_kit  # noqa: E402
from common.error_handler import format_exception  # noqa: E402

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PyQt6 import QtCore, QtGui, QtWidgets

    HAVE_QT = True
except ImportError:
    HAVE_QT = False
    QtCore = QtGui = QtWidgets = None

import build_plugins  # noqa: E402


class OpmlKitSerializationTests(unittest.TestCase):
    """Tests for core serialization routines in common/opml_kit.py."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp_dir, ignore_errors=True))

    def test_nifty_categories_opml_roundtrip(self):
        categories = ["gay/college", "gay/camping", "journal-entries", "trans/romance"]
        out_file = os.path.join(self.tmp_dir, "categories.opml")

        xml_str = opml_kit.export_list_to_opml(
            categories,
            target=out_file,
            title="Nifty Favorite Categories",
            item_type="nifty",
        )

        self.assertIn('<opml version="2.0">', xml_str)
        self.assertIn("<title>Nifty Favorite Categories</title>", xml_str)
        self.assertIn('text="gay/college"', xml_str)
        self.assertTrue(os.path.exists(out_file))

        # Import from string
        imported_from_str = opml_kit.import_list_from_opml(xml_str, item_type="nifty")
        self.assertEqual(imported_from_str, categories)

        # Import from file path
        imported_from_file = opml_kit.import_list_from_opml(out_file, item_type="nifty")
        self.assertEqual(imported_from_file, categories)

    def test_nifty_categories_json_roundtrip(self):
        categories = ["gay/college", "gay/camping", "journal-entries"]
        out_file = os.path.join(self.tmp_dir, "categories.json")

        json_str = opml_kit.export_list_to_json(
            categories,
            target=out_file,
            title="Nifty Favorite Categories",
            item_type="nifty",
        )

        self.assertIn('"type": "categories"', json_str)
        self.assertIn('"gay/college"', json_str)
        self.assertTrue(os.path.exists(out_file))

        # Import from string
        imported_from_str = opml_kit.import_list_from_json(json_str, item_type="nifty")
        self.assertEqual(imported_from_str, categories)

        # Import from file path
        imported_from_file = opml_kit.import_list_from_json(out_file, item_type="nifty")
        self.assertEqual(imported_from_file, categories)

    def test_metabods_tags_opml_roundtrip(self):
        tags = [[1, "Alpha Tag"], [2, "Beta Muscle"], [99, "Locker Room"]]
        out_file = os.path.join(self.tmp_dir, "tags.opml")

        xml_str = opml_kit.export_list_to_opml(
            tags, target=out_file, title="Metabods Favorite Tags", item_type="metabods"
        )

        self.assertIn('<opml version="2.0">', xml_str)
        self.assertIn('text="Alpha Tag"', xml_str)
        self.assertIn('id="1"', xml_str)
        self.assertTrue(os.path.exists(out_file))

        # Import from string
        imported_from_str = opml_kit.import_list_from_opml(xml_str, item_type="metabods")
        self.assertEqual(imported_from_str, tags)

        # Import from file
        imported_from_file = opml_kit.import_list_from_opml(out_file, item_type="metabods")
        self.assertEqual(imported_from_file, tags)

    def test_metabods_tags_json_roundtrip(self):
        tags = [[1, "Alpha Tag"], [2, "Beta Muscle"], [99, "Locker Room"]]
        out_file = os.path.join(self.tmp_dir, "tags.json")

        json_str = opml_kit.export_list_to_json(
            tags, target=out_file, title="Metabods Favorite Tags", item_type="metabods"
        )

        self.assertIn('"type": "tags"', json_str)
        self.assertIn('"Alpha Tag"', json_str)
        self.assertTrue(os.path.exists(out_file))

        # Import from string
        imported_from_str = opml_kit.import_list_from_json(json_str, item_type="metabods")
        self.assertEqual(imported_from_str, tags)

        # Import from file
        imported_from_file = opml_kit.import_list_from_json(out_file, item_type="metabods")
        self.assertEqual(imported_from_file, tags)

    def test_nested_category_folder_opml_import(self):
        nested_opml = """<?xml version="1.0" encoding="utf-8"?>
<opml version="2.0">
  <head><title>External Reader Export</title></head>
  <body>
    <outline text="gay" title="gay">
      <outline text="college" title="college" />
      <outline text="camping" title="camping" />
    </outline>
    <outline text="straight" title="straight">
      <outline text="romance" title="romance" />
    </outline>
  </body>
</opml>
"""
        imported = opml_kit.import_list_from_opml(nested_opml, item_type="categories")
        self.assertEqual(imported, ["gay/college", "gay/camping", "straight/romance"])

    def test_import_list_from_file_auto_detects_format(self):
        opml_path = os.path.join(self.tmp_dir, "list.opml")
        json_path = os.path.join(self.tmp_dir, "list.json")

        opml_kit.export_list_to_opml(["gay/college"], target=opml_path, item_type="nifty")
        opml_kit.export_list_to_json(["gay/camping"], target=json_path, item_type="nifty")

        res_opml = opml_kit.import_list_from_file(opml_path, item_type="nifty")
        res_json = opml_kit.import_list_from_file(json_path, item_type="nifty")

        self.assertEqual(res_opml, ["gay/college"])
        self.assertEqual(res_json, ["gay/camping"])


class OpmlKitConflictResolutionTests(unittest.TestCase):
    """Tests for auto-merge, skip existing, and overwrite strategies."""

    def test_categories_auto_merge_strategy(self):
        existing = ["gay/college", "gay/camping"]
        imported = ["gay/camping", "gay/romance", "trans/sci-fi"]

        result, stats = opml_kit.resolve_conflicts(
            existing, imported, item_type="categories", strategy=opml_kit.STRATEGY_MERGE
        )

        self.assertEqual(result, ["gay/college", "gay/camping", "gay/romance", "trans/sci-fi"])
        self.assertEqual(stats["added"], 2)
        self.assertEqual(stats["skipped"], 1)

    def test_categories_skip_existing_strategy(self):
        existing = ["gay/college", "gay/camping"]
        imported = ["gay/college", "gay/romance"]

        result, stats = opml_kit.resolve_conflicts(
            existing, imported, item_type="categories", strategy=opml_kit.STRATEGY_SKIP
        )

        self.assertEqual(result, ["gay/college", "gay/camping", "gay/romance"])
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["skipped"], 1)

    def test_categories_overwrite_strategy(self):
        existing = ["gay/college", "gay/camping"]
        imported = ["straight/romance", "journal-entries"]

        result, stats = opml_kit.resolve_conflicts(
            existing,
            imported,
            item_type="categories",
            strategy=opml_kit.STRATEGY_OVERWRITE,
        )

        self.assertEqual(result, ["straight/romance", "journal-entries"])
        self.assertEqual(stats["added"], 2)
        self.assertEqual(stats["updated"], 2)

    def test_tags_auto_merge_strategy(self):
        existing = [[1, "Alpha Original"], [2, "Beta"]]
        imported = [[2, "Beta Renamed"], [3, "Gamma"]]

        result, stats = opml_kit.resolve_conflicts(
            existing, imported, item_type="tags", strategy=opml_kit.STRATEGY_MERGE
        )

        self.assertEqual(result, [[1, "Alpha Original"], [2, "Beta Renamed"], [3, "Gamma"]])
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["updated"], 1)

    def test_tags_skip_existing_strategy(self):
        existing = [[1, "Alpha Original"], [2, "Beta"]]
        imported = [[1, "Alpha New"], [3, "Gamma"]]

        result, stats = opml_kit.resolve_conflicts(
            existing, imported, item_type="tags", strategy=opml_kit.STRATEGY_SKIP
        )

        self.assertEqual(result, [[1, "Alpha Original"], [2, "Beta"], [3, "Gamma"]])
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["skipped"], 1)

    def test_tags_overwrite_strategy(self):
        existing = [[1, "Alpha"], [2, "Beta"]]
        imported = [[3, "Gamma"], [4, "Delta"]]

        result, stats = opml_kit.resolve_conflicts(
            existing, imported, item_type="tags", strategy=opml_kit.STRATEGY_OVERWRITE
        )

        self.assertEqual(result, [[3, "Gamma"], [4, "Delta"]])
        self.assertEqual(stats["added"], 2)
        self.assertEqual(stats["updated"], 2)


class OpmlKitErrorHandlingTests(unittest.TestCase):
    """Tests for validating malformed input and error diagnostics."""

    def test_bad_opml_xml_raises_value_error(self):
        bad_xml = "<opml><head><title>Unclosed"
        with self.assertRaises(ValueError) as ctx:
            opml_kit.import_list_from_opml(bad_xml)
        self.assertIn("Invalid OPML XML content", str(ctx.exception))

    def test_non_opml_root_raises_value_error(self):
        html_content = "<html><body><h1>Not OPML</h1></body></html>"
        with self.assertRaises(ValueError) as ctx:
            opml_kit.import_list_from_opml(html_content)
        self.assertIn("root element is <html>", str(ctx.exception))

    def test_opml_missing_body_raises_value_error(self):
        no_body = "<opml version='2.0'><head><title>Test</title></head></opml>"
        with self.assertRaises(ValueError) as ctx:
            opml_kit.import_list_from_opml(no_body)
        self.assertIn("missing <body>", str(ctx.exception))

    def test_empty_opml_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            opml_kit.import_list_from_opml("")
        self.assertIn("input is empty", str(ctx.exception))

    def test_bad_json_raises_value_error(self):
        bad_json = '{"items": [1, 2, '
        with self.assertRaises(ValueError) as ctx:
            opml_kit.import_list_from_json(bad_json)
        self.assertIn("Invalid JSON content", str(ctx.exception))

    def test_json_missing_items_raises_value_error(self):
        bad_structure = '{"version": 1, "unrelated_field": "test"}'
        with self.assertRaises(ValueError) as ctx:
            opml_kit.import_list_from_json(bad_structure)
        self.assertIn("missing 'items'", str(ctx.exception))

    def test_json_items_not_a_list_raises_value_error(self):
        bad_items = '{"version": 1, "items": "not a list"}'
        with self.assertRaises(ValueError) as ctx:
            opml_kit.import_list_from_json(bad_items)
        self.assertIn("must be a list/array", str(ctx.exception))

    def test_friendly_error_formatter_detects_data_parsing_error(self):
        try:
            opml_kit.import_list_from_opml("<invalid>xml")
        except ValueError as exc:
            friendly = format_exception(exc)
            self.assertEqual(friendly.category, "Data Parsing Error")
            self.assertIn("Troubleshooting steps", friendly.user_message)


# -----------------------------------------------------------------------------
# UI Dialog Integration Tests
# -----------------------------------------------------------------------------


class StubPrefs(dict):
    def __init__(self):
        super().__init__()
        self.defaults = {}

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return self.defaults[key]


@unittest.skipUnless(HAVE_QT, "PyQt6 is required for GUI dialog tests")
class OpmlKitUIDialogIntegrationTests(unittest.TestCase):
    """Verifies BrowseDialog and TagSearchDialog import/export buttons and workflows."""

    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.built = build_plugins.build_all(Path(cls.tmp.name) / "dist")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.saved_modules = {
            k: v for k, v in sys.modules.items() if k.startswith(("calibre", "qt", "calibre_plugins"))
        }
        qtcore = types.ModuleType("qt.core")
        for module in (QtWidgets, QtGui, QtCore):
            for name in dir(module):
                setattr(qtcore, name, getattr(module, name))
        sys.modules["qt"] = types.ModuleType("qt")
        sys.modules["qt.core"] = qtcore
        for name in ("calibre", "calibre.customize", "calibre.gui2", "calibre.utils"):
            sys.modules[name] = MagicMock()
        config = types.ModuleType("calibre.utils.config")
        config.JSONConfig = lambda name: StubPrefs()
        sys.modules["calibre.utils.config"] = config
        sys.modules["calibre_plugins"] = types.ModuleType("calibre_plugins")
        sys.modules["calibre_plugins"].__path__ = []

        self.patch_msg_info = patch.object(
            QtWidgets.QMessageBox,
            "information",
            return_value=QtWidgets.QMessageBox.StandardButton.Ok,
        )
        self.patch_msg_info.start()
        self.patch_msg_warn = patch.object(
            QtWidgets.QMessageBox,
            "warning",
            return_value=QtWidgets.QMessageBox.StandardButton.Ok,
        )
        self.patch_msg_warn.start()

    def tearDown(self):
        self.patch_msg_info.stop()
        self.patch_msg_warn.stop()
        for key in [k for k in list(sys.modules) if k.startswith(("calibre", "qt", "calibre_plugins"))]:
            del sys.modules[key]
        sys.modules.update(self.saved_modules)

    def _load_plugin(self, import_name):
        import zipfile

        prefix = f"calibre_plugins.{import_name}"
        for key in list(sys.modules):
            if key == prefix or key.startswith(prefix + "."):
                del sys.modules[key]
        target = Path(self.tmp.name) / import_name
        if target.exists():
            shutil.rmtree(target)
        zipfile.ZipFile(self.built[import_name]).extractall(target)
        package = types.ModuleType(f"calibre_plugins.{import_name}")
        package.__path__ = [str(target)]
        sys.modules[package.__name__] = package
        return package.__name__

    def test_nifty_browse_dialog_buttons_and_export_import(self):
        import importlib

        package = self._load_plugin("nifty_downloader")
        core = importlib.import_module(package + ".core")

        class DummyFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url):
                return '<a href="/nifty/gay/college/">College</a><a href="/nifty/gay/camping/">Camping</a>'

        core.Fetcher = DummyFetcher
        ui = importlib.import_module(package + ".ui")
        config = importlib.import_module(package + ".config")

        config.prefs["favorite_categories"] = ["gay/college", "gay/camping"]

        dialog = ui.BrowseDialog(None, existing_ids=set())

        # Verify buttons exist in UI
        self.assertTrue(hasattr(dialog, "export_btn"))
        self.assertTrue(hasattr(dialog, "import_btn"))
        self.assertEqual(dialog.export_btn.text(), "Export List...")
        self.assertEqual(dialog.import_btn.text(), "Import List...")

        # Test export
        out_file = os.path.join(self.tmp.name, "nifty_exported.opml")
        with patch.object(
            ui.QFileDialog,
            "getSaveFileName",
            return_value=(out_file, "OPML Files (*.opml *.xml)"),
        ):
            dialog.export_favorites()

        self.assertTrue(os.path.exists(out_file))
        content = Path(out_file).read_text(encoding="utf-8")
        self.assertIn('text="gay/college"', content)

        # Test import with merge
        in_file = os.path.join(self.tmp.name, "nifty_to_import.json")
        opml_kit.export_list_to_json(["trans/romance", "gay/camping"], target=in_file, item_type="nifty")

        with patch.object(
            ui.QFileDialog,
            "getOpenFileName",
            return_value=(in_file, "JSON Files (*.json)"),
        ):
            with patch.object(ui.opml_kit, "ask_import_strategy", return_value=opml_kit.STRATEGY_MERGE):
                dialog.import_favorites()

        # Both existing and new items should be merged
        self.assertIn("gay/college", dialog.favorites)
        self.assertIn("gay/camping", dialog.favorites)
        self.assertIn("trans/romance", dialog.favorites)
        self.assertEqual(config.prefs["favorite_categories"], dialog.favorites)

    def test_nifty_browse_dialog_import_error_handling(self):
        import importlib

        package = self._load_plugin("nifty_downloader")
        core = importlib.import_module(package + ".core")

        class DummyFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url):
                return '<a href="/nifty/gay/college/">College</a>'

        core.Fetcher = DummyFetcher
        ui = importlib.import_module(package + ".ui")

        dialog = ui.BrowseDialog(None, existing_ids=set())

        bad_file = os.path.join(self.tmp.name, "bad_nifty.opml")
        Path(bad_file).write_text("<invalid>xml", encoding="utf-8")

        with patch.object(
            ui.QFileDialog,
            "getOpenFileName",
            return_value=(bad_file, "OPML Files (*.opml *.xml)"),
        ):
            with patch.object(ui, "show_friendly_error_dialog") as mock_err:
                dialog.import_favorites()
                self.assertTrue(mock_err.called)
                args = mock_err.call_args[0]
                self.assertIn("Failed to import categories", args[1])

    def test_metabods_tag_search_dialog_buttons_and_export_import(self):
        import importlib

        package = self._load_plugin("metabods_downloader")
        core = importlib.import_module(package + ".core")

        class DummyFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url):
                return '<a href="?list=tag&id=1">Alpha Tag</a>'

        core.Fetcher = DummyFetcher
        ui = importlib.import_module(package + ".ui")
        config = importlib.import_module(package + ".config")

        config.prefs["favorite_tags"] = [[1, "Alpha Tag"], [2, "Beta Muscle"]]
        config.prefs["tag_cache"] = [
            [1, "Alpha Tag"],
            [2, "Beta Muscle"],
            [3, "Gamma Gym"],
        ]

        dialog = ui.TagSearchDialog(None, existing_ids=set())

        # Verify buttons exist in UI
        self.assertTrue(hasattr(dialog, "export_btn"))
        self.assertTrue(hasattr(dialog, "import_btn"))
        self.assertEqual(dialog.export_btn.text(), "Export List...")
        self.assertEqual(dialog.import_btn.text(), "Import List...")

        # Test export
        out_file = os.path.join(self.tmp.name, "metabods_exported.opml")
        with patch.object(
            ui.QFileDialog,
            "getSaveFileName",
            return_value=(out_file, "OPML Files (*.opml *.xml)"),
        ):
            dialog.export_favorites()

        self.assertTrue(os.path.exists(out_file))
        content = Path(out_file).read_text(encoding="utf-8")
        self.assertIn('id="1"', content)

        # Test import with overwrite
        in_file = os.path.join(self.tmp.name, "metabods_to_import.json")
        opml_kit.export_list_to_json([[3, "Gamma Gym"]], target=in_file, item_type="metabods")

        with patch.object(
            ui.QFileDialog,
            "getOpenFileName",
            return_value=(in_file, "JSON Files (*.json)"),
        ):
            with patch.object(
                ui.opml_kit,
                "ask_import_strategy",
                return_value=opml_kit.STRATEGY_OVERWRITE,
            ):
                dialog.import_favorites()

        self.assertEqual(dialog.favorites, {3: "Gamma Gym"})
        self.assertEqual(config.prefs["favorite_tags"], [[3, "Gamma Gym"]])

    def test_metabods_tag_search_dialog_import_error_handling(self):
        import importlib

        package = self._load_plugin("metabods_downloader")
        core = importlib.import_module(package + ".core")

        class DummyFetcher:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, url):
                return '<a href="?list=tag&id=1">Alpha Tag</a>'

        core.Fetcher = DummyFetcher
        ui = importlib.import_module(package + ".ui")

        dialog = ui.TagSearchDialog(None, existing_ids=set())

        bad_file = os.path.join(self.tmp.name, "bad_tags.json")
        Path(bad_file).write_text("{broken json", encoding="utf-8")

        with patch.object(
            ui.QFileDialog,
            "getOpenFileName",
            return_value=(bad_file, "JSON Files (*.json)"),
        ):
            with patch.object(ui, "show_friendly_error_dialog") as mock_err:
                dialog.import_favorites()
                self.assertTrue(mock_err.called)
                args = mock_err.call_args[0]
                self.assertIn("Failed to import tags", args[1])


if __name__ == "__main__":
    unittest.main()
