"""Unit tests for TreeCheckStateMixin in common/guikit.py and its integration across preview dialogs."""

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if "calibre" not in sys.modules:
    for name in ("calibre", "calibre.customize", "calibre.gui2", "calibre.utils"):
        sys.modules.setdefault(name, types.ModuleType(name))

    class InterfaceActionBase:
        pass

    sys.modules["calibre.customize"].InterfaceActionBase = InterfaceActionBase

try:
    from qt.core import (
        QApplication,
        QDialog,
        Qt,
        QTreeWidget,
        QTreeWidgetItem,
    )
except ImportError:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QApplication,
        QDialog,
        QTreeWidget,
        QTreeWidgetItem,
    )

from common.guikit import MergePreviewDialog, TreeCheckStateMixin  # noqa: E402
from scrubber_plugin.ui import LibraryScannerDialog, ScrubPreviewDialog  # noqa: E402
from tagger.ui import TaggerPreviewDialog  # noqa: E402


class DummyDialogWithSummary(TreeCheckStateMixin, QDialog):
    def __init__(self):
        super().__init__()
        self.summary_call_count = 0
        self.tree = QTreeWidget(self)
        self.tree.itemChanged.connect(self.on_item_changed)

    def update_summary(self):
        self.summary_call_count += 1


class DummyDialogWithoutSummary(TreeCheckStateMixin, QDialog):
    def __init__(self):
        super().__init__()
        self.tree = QTreeWidget(self)
        self.tree.itemChanged.connect(self.on_item_changed)


class TreeCheckStateMixinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_target_classes_inherit_from_mixin(self):
        self.assertTrue(issubclass(MergePreviewDialog, TreeCheckStateMixin))
        self.assertTrue(issubclass(ScrubPreviewDialog, TreeCheckStateMixin))
        self.assertTrue(issubclass(LibraryScannerDialog, TreeCheckStateMixin))
        self.assertTrue(issubclass(TaggerPreviewDialog, TreeCheckStateMixin))

    def test_duplicate_methods_removed_from_dialog_dicts(self):
        for cls in (
            MergePreviewDialog,
            ScrubPreviewDialog,
            LibraryScannerDialog,
            TaggerPreviewDialog,
        ):
            self.assertNotIn("_set_children_check_state", cls.__dict__)
            self.assertNotIn("_update_parent_check_state", cls.__dict__)
            self.assertNotIn("on_item_changed", cls.__dict__)
            self.assertTrue(hasattr(cls, "_set_children_check_state"))
            self.assertTrue(hasattr(cls, "_update_parent_check_state"))
            self.assertTrue(hasattr(cls, "on_item_changed"))

    def test_to_check_state_coercion(self):
        checked_enum = TreeCheckStateMixin._to_check_state(2)
        unchecked_enum = TreeCheckStateMixin._to_check_state(0)
        partial_enum = TreeCheckStateMixin._to_check_state(1)

        expected_checked = getattr(getattr(Qt, "CheckState", Qt), "Checked", 2)
        expected_unchecked = getattr(getattr(Qt, "CheckState", Qt), "Unchecked", 0)
        expected_partial = getattr(getattr(Qt, "CheckState", Qt), "PartiallyChecked", 1)

        self.assertEqual(checked_enum, expected_checked)
        self.assertEqual(unchecked_enum, expected_unchecked)
        self.assertEqual(partial_enum, expected_partial)

        # Check idempotency when already Qt.CheckState
        self.assertEqual(
            TreeCheckStateMixin._to_check_state(checked_enum), checked_enum
        )
        self.assertEqual(
            TreeCheckStateMixin._to_check_state(unchecked_enum), unchecked_enum
        )
        self.assertEqual(
            TreeCheckStateMixin._to_check_state(partial_enum), partial_enum
        )

    def test_recursive_children_check_state(self):
        dialog = DummyDialogWithSummary()
        root = QTreeWidgetItem(dialog.tree, ["Root"])
        child1 = QTreeWidgetItem(root, ["Child 1"])
        child2 = QTreeWidgetItem(root, ["Child 2"])
        grandchild = QTreeWidgetItem(child1, ["Grandchild"])

        dialog._set_children_check_state(root, Qt.CheckState.Checked)
        self.assertEqual(child1.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child2.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(grandchild.checkState(0), Qt.CheckState.Checked)

        # Uncheck recursively with integer 0
        dialog._set_children_check_state(root, 0)
        self.assertEqual(child1.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child2.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(grandchild.checkState(0), Qt.CheckState.Unchecked)

    def test_parent_state_updates_all_combinations(self):
        dialog = DummyDialogWithSummary()
        root = QTreeWidgetItem(dialog.tree, ["Root"])
        child1 = QTreeWidgetItem(root, ["Child 1"])
        child2 = QTreeWidgetItem(root, ["Child 2"])

        # Both unchecked -> parent unchecked
        child1.setCheckState(0, Qt.CheckState.Unchecked)
        child2.setCheckState(0, Qt.CheckState.Unchecked)
        dialog._update_parent_check_state(child1)
        self.assertEqual(root.checkState(0), Qt.CheckState.Unchecked)

        # One checked -> parent partially checked
        child1.setCheckState(0, Qt.CheckState.Checked)
        dialog._update_parent_check_state(child1)
        self.assertEqual(root.checkState(0), Qt.CheckState.PartiallyChecked)

        # Both checked -> parent checked
        child2.setCheckState(0, Qt.CheckState.Checked)
        dialog._update_parent_check_state(child2)
        self.assertEqual(root.checkState(0), Qt.CheckState.Checked)

        # One partially checked -> parent partially checked
        child1.setCheckState(0, Qt.CheckState.PartiallyChecked)
        dialog._update_parent_check_state(child1)
        self.assertEqual(root.checkState(0), Qt.CheckState.PartiallyChecked)

    def test_multilevel_ancestor_propagation(self):
        dialog = DummyDialogWithSummary()
        grandparent = QTreeWidgetItem(dialog.tree, ["Grandparent"])
        parent = QTreeWidgetItem(grandparent, ["Parent"])
        child1 = QTreeWidgetItem(parent, ["Child 1"])
        child2 = QTreeWidgetItem(parent, ["Child 2"])

        child1.setCheckState(0, Qt.CheckState.Checked)
        child2.setCheckState(0, Qt.CheckState.Checked)
        dialog._update_parent_check_state(child1)

        self.assertEqual(parent.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(grandparent.checkState(0), Qt.CheckState.Checked)

        # Uncheck child2 -> parent and grandparent become partially checked
        child2.setCheckState(0, Qt.CheckState.Unchecked)
        dialog._update_parent_check_state(child2)

        self.assertEqual(parent.checkState(0), Qt.CheckState.PartiallyChecked)
        self.assertEqual(grandparent.checkState(0), Qt.CheckState.PartiallyChecked)

        # Uncheck child1 -> parent and grandparent become unchecked
        child1.setCheckState(0, Qt.CheckState.Unchecked)
        dialog._update_parent_check_state(child1)

        self.assertEqual(parent.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(grandparent.checkState(0), Qt.CheckState.Unchecked)

    def test_on_item_changed_blocks_signals_and_invokes_update_summary(self):
        dialog = DummyDialogWithSummary()
        parent = QTreeWidgetItem(dialog.tree, ["Parent"])
        parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        child = QTreeWidgetItem(parent, ["Child"])
        child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)

        # Initial state unchecked
        parent.setCheckState(0, Qt.CheckState.Unchecked)
        child.setCheckState(0, Qt.CheckState.Unchecked)
        initial_calls = dialog.summary_call_count

        # Check parent -> on_item_changed should check child and invoke update_summary once
        parent.setCheckState(0, Qt.CheckState.Checked)
        dialog.on_item_changed(parent)

        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)
        self.assertGreater(dialog.summary_call_count, initial_calls)

    def test_on_item_changed_without_update_summary_succeeds(self):
        dialog = DummyDialogWithoutSummary()
        parent = QTreeWidgetItem(dialog.tree, ["Parent"])
        child = QTreeWidgetItem(parent, ["Child"])

        parent.setCheckState(0, Qt.CheckState.Checked)
        # Should not raise AttributeError
        dialog.on_item_changed(parent)
        self.assertEqual(child.checkState(0), Qt.CheckState.Checked)

    def test_tagger_preview_dialog_integration(self):
        plan = [
            {
                "id": 1,
                "title": "Test Story",
                "add": ["Tag1", "Tag2"],
                "tags": ["Tag1", "Tag2"],
            }
        ]
        dialog = TaggerPreviewDialog(None, plan)
        self.assertIsInstance(dialog, TreeCheckStateMixin)

        book_item = dialog.tree.topLevelItem(0)
        self.assertEqual(book_item.childCount(), 2)
        child0 = book_item.child(0)
        child1 = book_item.child(1)

        # Uncheck first tag -> book becomes partially checked
        child0.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(book_item.checkState(0), Qt.CheckState.PartiallyChecked)

        # Uncheck second tag -> book becomes unchecked
        child1.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(book_item.checkState(0), Qt.CheckState.Unchecked)

        # Test select all
        dialog.select_all()
        self.assertEqual(book_item.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child0.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child1.checkState(0), Qt.CheckState.Checked)

        # Test deselect all
        dialog.deselect_all()
        self.assertEqual(book_item.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child0.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child1.checkState(0), Qt.CheckState.Unchecked)

    def test_merge_preview_dialog_integration(self):
        scan_results = [
            {
                "status": "ok",
                "title": "Merge Book 1",
                "story": {
                    "title": "Merge Book 1",
                    "author": "Author 1",
                    "sections": [("Ch1", "Text 1"), ("Ch2", "Text 2")],
                },
            }
        ]
        dialog = MergePreviewDialog(None, scan_results)
        self.assertIsInstance(dialog, TreeCheckStateMixin)

        book_item = dialog.tree.topLevelItem(0)
        self.assertEqual(book_item.childCount(), 2)
        ch0 = book_item.child(0)
        ch1 = book_item.child(1)

        # Toggle chapter
        ch0.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(book_item.checkState(0), Qt.CheckState.PartiallyChecked)

        dialog.deselect_all()
        self.assertEqual(book_item.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(ch0.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(ch1.checkState(0), Qt.CheckState.Unchecked)

        dialog.select_all()
        self.assertEqual(book_item.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(ch0.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(ch1.checkState(0), Qt.CheckState.Checked)

    def test_scrub_preview_dialog_integration(self):
        scan_results = [
            {
                "book_id": 101,
                "title": "Scrub Book",
                "changes": [
                    {
                        "file": "c1.xhtml",
                        "action": "link removed",
                        "id": "chg1",
                        "category": "Links",
                    },
                    {
                        "file": "c2.xhtml",
                        "action": "text removed",
                        "id": "chg2",
                        "category": "Links",
                    },
                ],
            }
        ]
        dialog = ScrubPreviewDialog(None, scan_results)
        self.assertIsInstance(dialog, TreeCheckStateMixin)

        book_item = dialog.tree.topLevelItem(0)
        cat_item = book_item.child(0)
        self.assertEqual(cat_item.childCount(), 2)

        # Uncheck one item
        item0 = cat_item.child(0)
        item0.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(cat_item.checkState(0), Qt.CheckState.PartiallyChecked)
        self.assertEqual(book_item.checkState(0), Qt.CheckState.PartiallyChecked)

        # Bulk operations
        dialog.deselect_all()
        self.assertEqual(book_item.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(cat_item.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(item0.checkState(0), Qt.CheckState.Unchecked)

        dialog.select_all()
        self.assertEqual(book_item.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(cat_item.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(item0.checkState(0), Qt.CheckState.Checked)

    def test_library_scanner_dialog_integration(self):
        from unittest.mock import MagicMock

        db = MagicMock()
        db.all_book_ids.return_value = [1, 2]
        dialog = LibraryScannerDialog(None, db, {})
        self.assertIsInstance(dialog, TreeCheckStateMixin)

        top_item = QTreeWidgetItem(dialog.tree, ["Book 1"])
        top_item.setCheckState(0, Qt.CheckState.Checked)
        child1 = QTreeWidgetItem(top_item, ["Ad 1"])
        child1.setCheckState(0, Qt.CheckState.Checked)
        child2 = QTreeWidgetItem(top_item, ["Ad 2"])
        child2.setCheckState(0, Qt.CheckState.Checked)

        # Uncheck child1 -> top_item partially checked
        child1.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertEqual(top_item.checkState(0), Qt.CheckState.PartiallyChecked)

        # Bulk deselect
        dialog.deselect_all()
        self.assertEqual(top_item.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child1.checkState(0), Qt.CheckState.Unchecked)
        self.assertEqual(child2.checkState(0), Qt.CheckState.Unchecked)

        # Bulk select
        dialog.select_all()
        self.assertEqual(top_item.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child1.checkState(0), Qt.CheckState.Checked)
        self.assertEqual(child2.checkState(0), Qt.CheckState.Checked)


if __name__ == "__main__":
    unittest.main()
