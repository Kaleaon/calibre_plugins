"""PyQt6 GUI tests for RuleSetManagerDialog in Story Collection Tagger."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from PyQt6 import QtCore, QtGui, QtWidgets

# Set up qt.core mapping to PyQt6
qtcore = types.ModuleType("qt.core")
for mod in (QtCore, QtGui, QtWidgets):
    for name in dir(mod):
        if not name.startswith("_"):
            setattr(qtcore, name, getattr(mod, name))
sys.modules["qt"] = types.ModuleType("qt")
sys.modules["qt.core"] = qtcore

# Set up Calibre mocks
calibre_mod = types.ModuleType("calibre")
calibre_mod.__path__ = []
sys.modules["calibre"] = calibre_mod
sys.modules["calibre.customize"] = types.ModuleType("calibre.customize")
sys.modules["calibre.customize"].InterfaceActionBase = object
sys.modules["calibre.gui2"] = types.ModuleType("calibre.gui2")
sys.modules["calibre.gui2"].error_dialog = lambda *a, **k: None
sys.modules["calibre.gui2"].info_dialog = lambda *a, **k: None
sys.modules["calibre.gui2.actions"] = types.ModuleType("calibre.gui2.actions")
sys.modules["calibre.gui2.actions"].InterfaceAction = object
sys.modules["calibre.utils"] = types.ModuleType("calibre.utils")
sys.modules["calibre.utils.config"] = types.ModuleType("calibre.utils.config")
mock_cfg = MagicMock()
mock_cfg.defaults = {}
sys.modules["calibre.utils.config"].JSONConfig = lambda *a, **k: mock_cfg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tagger"))

import action  # noqa: E402


class RuleSetManagerRealQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_dialog_initialization(self):
        current_rules = "tags:=Muscle Growth => Theme.Growth\nlitrpg => LitRPG"
        current_sites = "storiesonline.net, sol.net => Stories Online"

        dlg = action.RuleSetManagerDialog(None, current_rules=current_rules, current_sites=current_sites)
        self.assertEqual(dlg.rules_table.rowCount(), 2)
        self.assertEqual(dlg.sites_table.rowCount(), 1)
        self.assertEqual(dlg.get_commit_mode(), "merge")

        # Both rules should be checked and have status 'Existing'
        for r in range(2):
            self.assertEqual(dlg.rules_table.item(r, 0).checkState(), QtCore.Qt.CheckState.Checked)
            self.assertEqual(dlg.rules_table.item(r, 4).text(), "Existing")

        self.assertEqual(dlg.sites_table.item(0, 0).checkState(), QtCore.Qt.CheckState.Checked)
        self.assertEqual(dlg.sites_table.item(0, 3).text(), "Existing")

        self.assertIn("Keyword Rules (2/2)", dlg.tabs.tabText(0))
        self.assertIn("Source Sites (1/1)", dlg.tabs.tabText(1))

    def test_filter_live_search(self):
        current_rules = "tags:=Muscle Growth => Theme.Growth\nlitrpg => LitRPG\nprogression => LitRPG"
        dlg = action.RuleSetManagerDialog(None, current_rules=current_rules, current_sites="")

        # Search for Growth
        dlg.search_edit.setText("growth")
        self.assertFalse(dlg.rules_table.isRowHidden(0))
        self.assertTrue(dlg.rules_table.isRowHidden(1))
        self.assertTrue(dlg.rules_table.isRowHidden(2))

        # Search for litrpg (matches pattern in row 1 and tags in row 1 and row 2)
        dlg.search_edit.setText("litrpg")
        self.assertTrue(dlg.rules_table.isRowHidden(0))
        self.assertFalse(dlg.rules_table.isRowHidden(1))
        self.assertFalse(dlg.rules_table.isRowHidden(2))

        # Clear search
        dlg.search_edit.setText("")
        for r in range(3):
            self.assertFalse(dlg.rules_table.isRowHidden(r))

    def test_select_all_deselect_all_toggles(self):
        current_rules = "rule1 => Tag1\nrule2 => Tag2"
        dlg = action.RuleSetManagerDialog(None, current_rules=current_rules, current_sites="")

        # Deselect all
        dlg._set_all_checked(dlg.rules_table, False)
        self.assertEqual(dlg.rules_table.item(0, 0).checkState(), QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(dlg.rules_table.item(1, 0).checkState(), QtCore.Qt.CheckState.Unchecked)
        self.assertIn("(0/2)", dlg.tabs.tabText(0))

        # Select all
        dlg._set_all_checked(dlg.rules_table, True)
        self.assertEqual(dlg.rules_table.item(0, 0).checkState(), QtCore.Qt.CheckState.Checked)
        self.assertEqual(dlg.rules_table.item(1, 0).checkState(), QtCore.Qt.CheckState.Checked)
        self.assertIn("(2/2)", dlg.tabs.tabText(0))

    def test_load_json_merge_mode_with_conflicts(self):
        current_rules = "litrpg => LitRPG\ntags:=Muscle Growth => Theme.Growth"
        current_sites = "storiesonline.net, sol.net => Stories Online"

        dlg = action.RuleSetManagerDialog(None, current_rules=current_rules, current_sites=current_sites)

        # Incoming package:
        # 1. New rule: cultivation => Xianxia
        # 2. Conflict rule: litrpg => GameLit
        # 3. Duplicate rule: tags:=Muscle Growth => Theme.Growth
        # 4. New site: royalroad.com => Royal Road
        # 5. Conflict site: sol.net => Stories Online Differ
        # 6. Duplicate site: storiesonline.net => Stories Online
        incoming_data = {
            "version": 1,
            "metadata": {"title": "Test Pack", "description": "Unit test rule set"},
            "rules": [
                {"field": "", "pattern": "cultivation", "tags": ["Xianxia"]},
                {"field": "", "pattern": "litrpg", "tags": ["GameLit"]},
                {
                    "field": "tags",
                    "pattern": "=Muscle Growth",
                    "tags": ["Theme.Growth"],
                },
            ],
            "sites": [
                {"name": "Royal Road", "hosts": ["royalroad.com"]},
                {"name": "Stories Online Differ", "hosts": ["sol.net"]},
                {"name": "Stories Online", "hosts": ["storiesonline.net"]},
            ],
        }

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as tf:
            json.dump(incoming_data, tf)
            json_path = tf.name

        dlg._load_json(json_path)

        # 2 existing + 3 incoming = 5 rows
        self.assertEqual(dlg.rules_table.rowCount(), 5)
        # 1 existing + 3 incoming = 4 rows
        self.assertEqual(dlg.sites_table.rowCount(), 4)

        # In Merge mode:
        # Existing rules are Checked
        self.assertEqual(dlg.rules_table.item(0, 0).checkState(), QtCore.Qt.CheckState.Checked)
        self.assertEqual(dlg.rules_table.item(1, 0).checkState(), QtCore.Qt.CheckState.Checked)

        # New rule (row 2) is Checked
        self.assertEqual(dlg.rules_table.item(2, 4).text(), "New")
        self.assertEqual(dlg.rules_table.item(2, 0).checkState(), QtCore.Qt.CheckState.Checked)

        # Conflict rule (row 3) is Unchecked
        self.assertEqual(dlg.rules_table.item(3, 4).text(), "Conflict (differs)")
        self.assertEqual(dlg.rules_table.item(3, 0).checkState(), QtCore.Qt.CheckState.Unchecked)

        # Duplicate rule (row 4) is Unchecked in Merge mode
        self.assertEqual(dlg.rules_table.item(4, 4).text(), "Duplicate")
        self.assertEqual(dlg.rules_table.item(4, 0).checkState(), QtCore.Qt.CheckState.Unchecked)

        # Test commit in Merge mode
        dlg._on_accept()
        rules_out, sites_out = dlg.get_committed_rules_and_sites()
        self.assertIn("litrpg => LitRPG", rules_out)
        self.assertIn("tags:=Muscle Growth => Theme.Growth", rules_out)
        self.assertIn("cultivation => Xianxia", rules_out)
        self.assertNotIn("GameLit", rules_out)
        self.assertIn("Royal Road", sites_out)

    def test_switch_to_replace_mode(self):
        current_rules = "litrpg => LitRPG\noldrule => OldTag"
        current_sites = "oldsite.net => Old Site"

        dlg = action.RuleSetManagerDialog(None, current_rules=current_rules, current_sites=current_sites)

        incoming_data = {
            "version": 1,
            "rules": [
                {"field": "", "pattern": "litrpg", "tags": ["LitRPG"]},  # Duplicate
                {"field": "", "pattern": "newrule", "tags": ["NewTag"]},  # New
            ],
            "sites": [
                {"name": "New Site", "hosts": ["newsite.org"]}  # New
            ],
        }

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as tf:
            json.dump(incoming_data, tf)
            json_path = tf.name

        dlg._load_json(json_path)

        # Switch to replace mode
        dlg.set_commit_mode("replace")
        self.assertEqual(dlg.get_commit_mode(), "replace")

        # Existing rules should be unchecked
        self.assertEqual(dlg.rules_table.item(0, 0).checkState(), QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(dlg.rules_table.item(1, 0).checkState(), QtCore.Qt.CheckState.Unchecked)

        # Duplicate rule from imported file should be checked
        self.assertEqual(dlg.rules_table.item(2, 0).checkState(), QtCore.Qt.CheckState.Checked)

        # New rule should be checked
        self.assertEqual(dlg.rules_table.item(3, 0).checkState(), QtCore.Qt.CheckState.Checked)

        dlg._on_accept()
        rules_out, sites_out = dlg.get_committed_rules_and_sites()

        # oldrule was replaced!
        self.assertNotIn("oldrule", rules_out)
        self.assertNotIn("OldTag", rules_out)
        self.assertIn("litrpg => LitRPG", rules_out)
        self.assertIn("newrule => NewTag", rules_out)
        self.assertNotIn("Old Site", sites_out)
        self.assertIn("New Site", sites_out)

    def test_error_handling_invalid_json_and_regex(self):
        current_rules = "litrpg => LitRPG"
        dlg = action.RuleSetManagerDialog(None, current_rules=current_rules, current_sites="")

        # 1. Invalid JSON
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as tf:
            tf.write("{ NOT VALID JSON }")
            path1 = tf.name
        dlg._load_json(path1)
        self.assertEqual(dlg.rules_table.rowCount(), 1)

        # 2. Unsupported schema version
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as tf:
            json.dump({"version": 42, "rules": []}, tf)
            path2 = tf.name
        dlg._load_json(path2)
        self.assertEqual(dlg.rules_table.rowCount(), 1)

        # 3. Invalid regex syntax
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as tf:
            json.dump({"version": 1, "rules": ["re:(( => BadRegex"]}, tf)
            path3 = tf.name
        dlg._load_json(path3)
        self.assertEqual(dlg.rules_table.rowCount(), 1)

        # 4. Missing file
        dlg._load_json("/does/not/exist/missing_rules.json")
        self.assertEqual(dlg.rules_table.rowCount(), 1)

    def test_export_and_import_roundtrip(self):
        initial_rules = "tags:=Muscle Growth => Theme.Growth\nlitrpg => LitRPG"
        initial_sites = "storiesonline.net, sol.net => Stories Online"

        dlg1 = action.RuleSetManagerDialog(None, current_rules=initial_rules, current_sites=initial_sites)

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as tf:
            export_path = tf.name

        dlg1._save_json(export_path)

        with open(export_path, encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["rules"]), 2)
        self.assertEqual(len(data["sites"]), 1)

        # Now import into a clean dialog
        dlg2 = action.RuleSetManagerDialog(None, current_rules="", current_sites="", mode="replace")
        dlg2._load_json(export_path)
        dlg2._on_accept()

        committed_rules, committed_sites = dlg2.get_committed_rules_and_sites()
        self.assertEqual(committed_rules, initial_rules)
        self.assertEqual(committed_sites, initial_sites)
