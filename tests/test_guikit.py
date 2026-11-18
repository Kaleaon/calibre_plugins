"""Unit tests for consolidated Qt/UI helper functions in common/guikit.py."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    import types

    from PyQt6 import QtCore, QtGui, QtWidgets

    qtcore = types.ModuleType("qt.core")
    for module in (QtWidgets, QtGui, QtCore):
        for name in dir(module):
            setattr(qtcore, name, getattr(module, name))
    qt_mod = types.ModuleType("qt")
    qt_mod.core = qtcore
    sys.modules["qt"] = qt_mod
    sys.modules["qt.core"] = qtcore
except ImportError:
    import types

    qt_mod = MagicMock()
    qt_core = MagicMock()

    class FakeItemDataRole:
        UserRole = 32

    class FakeCheckState:
        Unchecked = 0
        PartiallyChecked = 1
        Checked = 2

    class FakeQt:
        ItemDataRole = FakeItemDataRole
        CheckState = FakeCheckState

    qt_core.Qt = FakeQt
    qt_core.QApplication = MagicMock()
    qt_core.QCheckBox = MagicMock()
    qt_core.QLineEdit = MagicMock()
    qt_core.QProgressDialog = MagicMock()
    qt_core.QVBoxLayout = MagicMock()
    qt_core.QWidget = MagicMock()

    qt_mod.core = qt_core
    sys.modules["qt"] = qt_mod
    sys.modules["qt.core"] = qt_core

from common import guikit, log_utils, tree_utils  # noqa: E402
from common.guikit import (  # noqa: E402
    append_log,
    chosen_fields,
    clean_lines,
    collect_checked_fields,
    collect_tree_changes,
    is_item_checked,
    parse_lines,
    set_children_check_state,
    update_parent_check_state,
)

direct_append_log = log_utils.append_log
direct_clean_lines = log_utils.clean_lines
direct_parse_lines = log_utils.parse_lines
direct_chosen_fields = tree_utils.chosen_fields
direct_collect_checked_fields = tree_utils.collect_checked_fields
direct_collect_tree_changes = tree_utils.collect_tree_changes
direct_is_item_checked = tree_utils.is_item_checked
direct_set_children_check_state = tree_utils.set_children_check_state
direct_update_parent_check_state = tree_utils.update_parent_check_state


class MockTextEdit:
    def __init__(self, text):
        self._text = text

    def toPlainText(self):
        return self._text


class MockBox:
    def __init__(self, checked):
        self._checked = checked

    def isChecked(self):
        return self._checked


class MockTreeItem:
    def __init__(self, data_role_32=None, data_role_33=None, check_state=0, children=None):
        from qt.core import Qt

        role_user = getattr(Qt.ItemDataRole, "UserRole", 32)
        try:
            role_user_int = int(role_user)
        except (ValueError, TypeError):
            role_user_int = 32

        self._data = {
            role_user: data_role_32,
            role_user_int: data_role_32,
            role_user_int + 1: data_role_33,
            32: data_role_32,
            33: data_role_33,
        }
        self._check_state = check_state
        self._children = children or []
        self._parent = None
        for child in self._children:
            child._parent = self

    def childCount(self):
        return len(self._children)

    def child(self, index):
        return self._children[index]

    def checkState(self, column=0):
        return self._check_state

    def setCheckState(self, column, state):
        self._check_state = state

    def parent(self):
        return self._parent

    def data(self, column, role):
        if role in self._data:
            return self._data[role]
        try:
            return self._data.get(int(role))
        except (ValueError, TypeError):
            return None


class MockTreeWidget:
    def __init__(self, top_items):
        self._top_items = top_items

    def topLevelItemCount(self):
        return len(self._top_items)

    def topLevelItem(self, index):
        return self._top_items[index]


class GuikitHelperTests(unittest.TestCase):
    def test_facade_reexports_match_submodule_identities(self):
        """Verify backward-compatible facade exports in guikit point to canonical submodules."""
        self.assertIs(append_log, direct_append_log)
        self.assertIs(clean_lines, direct_clean_lines)
        self.assertIs(parse_lines, direct_parse_lines)
        self.assertIs(chosen_fields, direct_chosen_fields)
        self.assertIs(collect_checked_fields, direct_collect_checked_fields)
        self.assertIs(collect_tree_changes, direct_collect_tree_changes)
        self.assertIs(is_item_checked, direct_is_item_checked)
        self.assertIs(set_children_check_state, direct_set_children_check_state)
        self.assertIs(update_parent_check_state, direct_update_parent_check_state)

        self.assertIs(guikit.append_log, log_utils.append_log)
        self.assertIs(guikit.clean_lines, log_utils.clean_lines)
        self.assertIs(guikit.parse_lines, log_utils.parse_lines)
        self.assertIs(guikit.chosen_fields, tree_utils.chosen_fields)
        self.assertIs(guikit.collect_checked_fields, tree_utils.collect_checked_fields)
        self.assertIs(guikit.collect_tree_changes, tree_utils.collect_tree_changes)
        self.assertIs(guikit.is_item_checked, tree_utils.is_item_checked)
        self.assertIs(guikit.set_children_check_state, tree_utils.set_children_check_state)
        self.assertIs(guikit.update_parent_check_state, tree_utils.update_parent_check_state)

    def test_direct_log_utils_submodule(self):
        """Verify direct import and functionality of common/log_utils.py."""
        widget = MagicMock()
        formatted = direct_append_log(widget, "direct log message")
        self.assertIn("direct log message", formatted)
        widget.appendPlainText.assert_called_once_with(formatted)

        self.assertEqual(direct_clean_lines("  line1  \n\n  line2  "), ["line1", "line2"])
        self.assertEqual(direct_parse_lines("item1\n\n  item2\n"), ["item1", "item2"])
        self.assertEqual(direct_parse_lines(None), [])

    def test_direct_tree_utils_submodule(self):
        """Verify direct import and functionality of common/tree_utils.py."""
        child = MockTreeItem(data_role_32="field1", data_role_33=("val1", "val2"), check_state=2)
        top = MockTreeItem(data_role_32=201, children=[child])
        tree = MockTreeWidget([top])

        changes = direct_collect_tree_changes(tree)
        self.assertEqual(changes, [(201, {"field1": ("val1", "val2")})])

        boxes = {"a": MockBox(True), "b": MockBox(False), "c": MockBox(True)}
        self.assertEqual(direct_collect_checked_fields(boxes), ("a", "c"))
        self.assertEqual(
            direct_collect_checked_fields(boxes, [("c", "C label"), ("a", "A label")]),
            ("c", "a"),
        )
        self.assertEqual(direct_chosen_fields(boxes), ("a", "c"))
        self.assertTrue(direct_is_item_checked(child))

    def test_parse_lines_facade(self):
        self.assertEqual(parse_lines("a\n\nb\n"), ["a", "b"])
        self.assertEqual(parse_lines(""), [])
        self.assertEqual(parse_lines(None), [])
        text_edit = MockTextEdit(" foo \n\n bar ")
        self.assertEqual(parse_lines(text_edit), ["foo", "bar"])

    def test_collect_checked_fields_facade(self):
        boxes = {
            "title": MockBox(True),
            "authors": MockBox(False),
            "tags": MockBox(True),
        }
        labels = [("tags", "Tags"), ("title", "Titles")]
        self.assertEqual(collect_checked_fields(boxes, labels), ("tags", "title"))
        self.assertEqual(collect_checked_fields(boxes), ("title", "tags"))

    def test_tree_check_state_helpers(self):
        from qt.core import Qt

        child1 = MockTreeItem(check_state=0)
        child2 = MockTreeItem(check_state=0)
        parent = MockTreeItem(children=[child1, child2])

        # Recursively check children
        set_children_check_state(parent, 2)
        self.assertTrue(is_item_checked(child1))
        self.assertTrue(is_item_checked(child2))

        # Update parent check state when all children are checked
        update_parent_check_state(child1)
        self.assertTrue(is_item_checked(parent))
        self.assertIn(parent.checkState(), (getattr(Qt.CheckState, "Checked", 2), 2))

        # Uncheck one child
        child1.setCheckState(0, 0)
        update_parent_check_state(child1)
        self.assertFalse(is_item_checked(parent))
        self.assertIn(parent.checkState(), (getattr(Qt.CheckState, "PartiallyChecked", 1), 1))

        # Uncheck both
        child2.setCheckState(0, 0)
        update_parent_check_state(child2)
        self.assertFalse(is_item_checked(parent))
        self.assertIn(parent.checkState(), (getattr(Qt.CheckState, "Unchecked", 0), 0))

    def test_clean_lines_with_string(self):
        text = "  line one  \n\n   \n line two \t \n"
        self.assertEqual(clean_lines(text), ["line one", "line two"])

    def test_clean_lines_with_text_widget(self):
        widget = MockTextEdit("  alpha \n\n  beta  ")
        self.assertEqual(clean_lines(widget), ["alpha", "beta"])

    def test_clean_lines_with_empty_and_none(self):
        self.assertEqual(clean_lines(""), [])
        self.assertEqual(clean_lines(None), [])

    def test_chosen_fields_with_tuples(self):
        labels = [("title", "Titles"), ("authors", "Authors"), ("tags", "Tags")]
        boxes = {
            "title": MockBox(True),
            "authors": MockBox(False),
            "tags": MockBox(True),
        }
        self.assertEqual(chosen_fields(boxes, labels), ("title", "tags"))

    def test_chosen_fields_with_plain_keys_and_booleans(self):
        boxes = {"title": True, "authors": False, "tags": True}
        self.assertEqual(chosen_fields(boxes, ["title", "authors", "tags"]), ("title", "tags"))

    def test_chosen_fields_without_labels(self):
        boxes = {"a": True, "b": False}
        self.assertEqual(chosen_fields(boxes), ("a",))

    def test_append_log(self):
        widget = MagicMock()
        result = append_log(widget, "Test message")
        self.assertIn("Test message", result)
        widget.appendPlainText.assert_called_once()
        logged_arg = widget.appendPlainText.call_args[0][0]
        self.assertTrue(logged_arg.endswith("  Test message"))

    def test_collect_tree_changes(self):
        # 2 = Qt.CheckState.Checked
        child1 = MockTreeItem(data_role_32="title", data_role_33=("old_t", "new_t"), check_state=2)
        child2 = MockTreeItem(data_role_32="tags", data_role_33=("old_tag", "new_tag"), check_state=0)
        top1 = MockTreeItem(data_role_32=101, children=[child1, child2])

        child3 = MockTreeItem(data_role_32="authors", data_role_33=("old_a", "new_a"), check_state=2)
        top2 = MockTreeItem(data_role_32=102, children=[child3])

        tree = MockTreeWidget([top1, top2])
        changes = collect_tree_changes(tree)

        expected = [
            (101, {"title": ("old_t", "new_t")}),
            (102, {"authors": ("old_a", "new_a")}),
        ]
        self.assertEqual(changes, expected)


if __name__ == "__main__":
    unittest.main()
