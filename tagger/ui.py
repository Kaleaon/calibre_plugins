"""UI components for Story Collection Tagger, including the interactive tag preview dialog."""

try:
    from qt.core import (
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        Qt,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
    )
except ImportError:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
    )

CHECKED = getattr(Qt.CheckState, 'Checked', getattr(Qt, 'Checked', 2))
UNCHECKED = getattr(Qt.CheckState, 'Unchecked', getattr(Qt, 'Unchecked', 0))
PARTIALLY_CHECKED = getattr(Qt.CheckState, 'PartiallyChecked', getattr(Qt, 'PartiallyChecked', 1))
ITEM_IS_USER_CHECKABLE = getattr(Qt.ItemFlag, 'ItemIsUserCheckable', getattr(Qt, 'ItemIsUserCheckable', 16))
USER_ROLE = getattr(Qt.ItemDataRole, 'UserRole', getattr(Qt, 'UserRole', 256))

try:
    from calibre_plugins.story_collection_tagger.guikit import TreeCheckStateMixin
    if not isinstance(TreeCheckStateMixin, type):
        raise ImportError
except (ImportError, AttributeError):
    try:
        from .guikit import TreeCheckStateMixin
        if not isinstance(TreeCheckStateMixin, type):
            raise ImportError
    except (ImportError, AttributeError):
        try:
            from guikit import TreeCheckStateMixin
            if not isinstance(TreeCheckStateMixin, type):
                raise ImportError
        except (ImportError, AttributeError):
            try:
                from common.guikit import TreeCheckStateMixin
                if not isinstance(TreeCheckStateMixin, type):
                    raise ImportError
            except (ImportError, AttributeError):
                import importlib.util
                from pathlib import Path

                _gk_path = Path(__file__).resolve().parents[1] / 'common' / 'guikit.py'
                if _gk_path.exists():
                    _spec = importlib.util.spec_from_file_location('guikit', _gk_path)
                    _gk_mod = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(_gk_mod)
                    TreeCheckStateMixin = _gk_mod.TreeCheckStateMixin
                else:
                    class TreeCheckStateMixin:
                        pass


if isinstance(QDialog, type):
    _TaggerDialogBase = QDialog
else:
    class _TaggerDialogBase:
        def __init__(self, *args, **kwargs):
            pass


class TaggerPreviewDialog(TreeCheckStateMixin, _TaggerDialogBase):
    """Modal dialog allowing users to review and selectively check/uncheck proposed tags before applying."""

    def __init__(self, parent, plan):
        super().__init__(parent)
        self.setWindowTitle('Auto-tag stories - Preview Proposed Changes')
        self.resize(750, 500)

        self.plan = plan or []

        layout = QVBoxLayout(self)

        # Header label
        header_label = QLabel(
            'Review proposed tag additions below. Uncheck tags or books you wish to exclude before applying.'
        )
        header_label.setWordWrap(True)
        layout.addWidget(header_label)

        # Tree Widget
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(['Book / Proposed Tag', 'Status / Count'])
        self.tree.setColumnWidth(0, 480)
        self.tree.setColumnWidth(1, 180)
        layout.addWidget(self.tree)

        # Toolbar layout
        tb_layout = QHBoxLayout()
        btn_select_all = QPushButton('Select All')
        btn_deselect_all = QPushButton('Deselect All')
        btn_select_all.clicked.connect(self.select_all)
        btn_deselect_all.clicked.connect(self.deselect_all)
        tb_layout.addWidget(btn_select_all)
        tb_layout.addWidget(btn_deselect_all)

        self.summary_label = QLabel()
        tb_layout.addSpacing(20)
        tb_layout.addWidget(self.summary_label)
        tb_layout.addStretch()
        layout.addLayout(tb_layout)

        # Dialog Button Box
        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button:
            ok_button.setText('Apply Selected Tags')
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        # Connect tree signals
        self.tree.itemChanged.connect(self.on_item_changed)

        self.populate_tree()
        self.update_summary()

    def populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        for p in self.plan:
            title = p.get('title') or f"Book ID {p.get('id')}"
            add_tags = p.get('add', [])
            book_item = QTreeWidgetItem(self.tree, [title, f"{len(add_tags)} tag(s) proposed"])
            book_item.setFlags(book_item.flags() | ITEM_IS_USER_CHECKABLE)
            book_item.setCheckState(0, CHECKED)
            book_item.setData(0, USER_ROLE, {'type': 'book', 'id': p.get('id'), 'plan_item': p})

            for tag in add_tags:
                tag_item = QTreeWidgetItem(book_item, [tag, 'Proposed Tag'])
                tag_item.setFlags(tag_item.flags() | ITEM_IS_USER_CHECKABLE)
                tag_item.setCheckState(0, CHECKED)
                tag_item.setData(0, USER_ROLE, {'type': 'tag', 'id': p.get('id'), 'tag': tag})

            book_item.setExpanded(True)

        self.tree.blockSignals(False)

    def select_all(self):
        self.tree.blockSignals(True)
        root = self.tree.invisibleRootItem()
        self._set_children_check_state(root, CHECKED)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, CHECKED)
        self.tree.blockSignals(False)
        self.update_summary()

    def deselect_all(self):
        self.tree.blockSignals(True)
        root = self.tree.invisibleRootItem()
        self._set_children_check_state(root, UNCHECKED)
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, UNCHECKED)
        self.tree.blockSignals(False)
        self.update_summary()

    def total_tag_count(self):
        count = 0
        for p in self.plan:
            count += len(p.get('add', []))
        return count

    def get_selected_plan(self):
        """Returns filtered plan containing only checked tags for each book."""
        filtered_plan = []
        for i in range(self.tree.topLevelItemCount()):
            book_item = self.tree.topLevelItem(i)
            data = book_item.data(0, USER_ROLE)
            if not isinstance(data, dict) or data.get('type') != 'book':
                continue
            p = data['plan_item']
            checked_tags = []
            for j in range(book_item.childCount()):
                child = book_item.child(j)
                if child.checkState(0) == CHECKED:
                    tag_data = child.data(0, USER_ROLE)
                    if isinstance(tag_data, dict) and 'tag' in tag_data:
                        checked_tags.append(tag_data['tag'])

            if checked_tags:
                add_set = set(p.get('add', []))
                have = [t for t in p.get('tags', []) if t not in add_set]
                filtered_plan.append({
                    'id': p['id'],
                    'title': p.get('title', ''),
                    'add': checked_tags,
                    'tags': have + checked_tags
                })
        return filtered_plan

    def update_summary(self):
        selected_plan = self.get_selected_plan()
        selected_tags_count = sum(len(p['add']) for p in selected_plan)
        selected_books_count = len(selected_plan)
        total_tags = self.total_tag_count()
        total_books = len(self.plan)

        self.summary_label.setText(
            f"Selected {selected_tags_count} of {total_tags} proposed tag(s) across {selected_books_count} of {total_books} book(s)"
        )
        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(selected_tags_count > 0)
