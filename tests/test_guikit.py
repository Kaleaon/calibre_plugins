"""Unit tests for consolidated Qt/UI helper functions in common/guikit.py."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    import types
    qtcore = types.ModuleType('qt.core')
    for module in (QtWidgets, QtGui, QtCore):
        for name in dir(module):
            setattr(qtcore, name, getattr(module, name))
    qt_mod = types.ModuleType('qt')
    qt_mod.core = qtcore
    sys.modules['qt'] = qt_mod
    sys.modules['qt.core'] = qtcore
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
    sys.modules['qt'] = qt_mod
    sys.modules['qt.core'] = qt_core

from common.guikit import append_log, chosen_fields, clean_lines, collect_tree_changes


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
    def __init__(self, data_role_32, data_role_33=None, check_state=0, children=None):
        self._data = {32: data_role_32, 33: data_role_33}
        self._check_state = check_state
        self._children = children or []

    def childCount(self):
        return len(self._children)

    def child(self, index):
        return self._children[index]

    def checkState(self, column):
        return self._check_state

    def data(self, column, role):
        return self._data.get(role)


class MockTreeWidget:
    def __init__(self, top_items):
        self._top_items = top_items

    def topLevelItemCount(self):
        return len(self._top_items)

    def topLevelItem(self, index):
        return self._top_items[index]


class GuikitHelperTests(unittest.TestCase):
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
        labels = [('title', 'Titles'), ('authors', 'Authors'), ('tags', 'Tags')]
        boxes = {
            'title': MockBox(True),
            'authors': MockBox(False),
            'tags': MockBox(True),
        }
        self.assertEqual(chosen_fields(boxes, labels), ('title', 'tags'))

    def test_chosen_fields_with_plain_keys_and_booleans(self):
        boxes = {'title': True, 'authors': False, 'tags': True}
        self.assertEqual(chosen_fields(boxes, ['title', 'authors', 'tags']), ('title', 'tags'))

    def test_chosen_fields_without_labels(self):
        boxes = {'a': True, 'b': False}
        self.assertEqual(chosen_fields(boxes), ('a',))

    def test_append_log(self):
        widget = MagicMock()
        result = append_log(widget, "Test message")
        self.assertIn("Test message", result)
        widget.appendPlainText.assert_called_once()
        logged_arg = widget.appendPlainText.call_args[0][0]
        self.assertTrue(logged_arg.endswith("  Test message"))

    def test_collect_tree_changes(self):
        # 2 = Qt.CheckState.Checked
        child1 = MockTreeItem(data_role_32='title', data_role_33=('old_t', 'new_t'), check_state=2)
        child2 = MockTreeItem(data_role_32='tags', data_role_33=('old_tag', 'new_tag'), check_state=0)
        top1 = MockTreeItem(data_role_32=101, children=[child1, child2])

        child3 = MockTreeItem(data_role_32='authors', data_role_33=('old_a', 'new_a'), check_state=2)
        top2 = MockTreeItem(data_role_32=102, children=[child3])

        tree = MockTreeWidget([top1, top2])
        changes = collect_tree_changes(tree)

        expected = [
            (101, {'title': ('old_t', 'new_t')}),
            (102, {'authors': ('old_a', 'new_a')})
        ]
        self.assertEqual(changes, expected)


if __name__ == '__main__':
    unittest.main()
