"""UI components for EPUB Promo Scrubber, including the interactive preview dialog."""

from qt.core import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    Qt,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ScrubPreviewDialog(QDialog):
    """Modal dialog allowing users to review and selectively filter promo changes before applying."""

    def __init__(self, parent, scan_results, is_folder=False):
        super().__init__(parent)
        self.setWindowTitle("EPUB Promo Scrubber - Preview Proposed Changes")
        self.resize(800, 550)

        self.scan_results = scan_results
        self.is_folder = is_folder

        layout = QVBoxLayout(self)

        # Header label
        header_label = QLabel(
            "Review detected promotional content. Uncheck items or books you wish to exclude before applying."
        )
        layout.addWidget(header_label)

        # Splitter between tree and detail view
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Tree Widget on the left
        self.tree = QTreeWidget(splitter)
        self.tree.setHeaderLabels(["Item / Target", "Action Category"])
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(1, 180)

        # Detail Pane on the right
        detail_widget = QWidget(splitter)
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        detail_title = QLabel("<b>Selected Item Details:</b>")
        detail_layout.addWidget(detail_title)

        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        detail_layout.addWidget(self.detail_edit)

        splitter.addWidget(self.tree)
        splitter.addWidget(detail_widget)
        splitter.setSizes([480, 320])

        layout.addWidget(splitter)

        # Toolbar layout
        tb_layout = QHBoxLayout()
        btn_select_all = QPushButton("Select All")
        btn_deselect_all = QPushButton("Deselect All")
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
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText("Apply Selected Changes")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        # Connect tree signals
        self.tree.itemChanged.connect(self.on_item_changed)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)

        self.populate_tree()
        self.update_summary()

    def populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        for res in self.scan_results:
            title = res.get("title") or res.get("path") or f"Book ID {res.get('book_id')}"
            changes = res.get("changes", [])
            book_item = QTreeWidgetItem(self.tree, [title, f"{len(changes)} items"])
            book_item.setFlags(book_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            book_item.setCheckState(0, Qt.CheckState.Checked)
            book_item.setData(
                0, Qt.ItemDataRole.UserRole, {"type": "book", "book_id": res.get("book_id"), "path": res.get("path")}
            )

            # Group changes by file or category
            grouped = {}
            for ch in changes:
                cat = ch.get("category") or ch.get("file") or "General"
                grouped.setdefault(cat, []).append(ch)

            for cat_name, cat_changes in grouped.items():
                cat_item = QTreeWidgetItem(book_item, [cat_name, f"{len(cat_changes)} items"])
                cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                cat_item.setCheckState(0, Qt.CheckState.Checked)
                cat_item.setData(0, Qt.ItemDataRole.UserRole, {"type": "category", "name": cat_name})

                for ch in cat_changes:
                    disp_text = ch.get("text") or ch.get("action") or "Promo Item"
                    child_item = QTreeWidgetItem(cat_item, [disp_text, ch.get("action", "")])
                    child_item.setFlags(child_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child_item.setCheckState(0, Qt.CheckState.Checked)
                    child_item.setData(0, Qt.ItemDataRole.UserRole, ch)

            book_item.setExpanded(True)

        self.tree.blockSignals(False)

    def _set_children_check_state(self, item, state):
        for i in range(item.childCount()):
            child = item.child(i)
            child.setCheckState(0, state)
            self._set_children_check_state(child, state)

    def _update_parent_check_state(self, item):
        parent = item.parent()
        if parent is None:
            return
        checked_count = 0
        unchecked_count = 0
        total = parent.childCount()
        for i in range(total):
            st = parent.child(i).checkState(0)
            if st == Qt.CheckState.Checked:
                checked_count += 1
            elif st == Qt.CheckState.Unchecked:
                unchecked_count += 1

        if checked_count == total:
            parent.setCheckState(0, Qt.CheckState.Checked)
        elif unchecked_count == total:
            parent.setCheckState(0, Qt.CheckState.Unchecked)
        else:
            parent.setCheckState(0, Qt.CheckState.PartiallyChecked)

        self._update_parent_check_state(parent)

    def on_item_changed(self, item, column):
        self.tree.blockSignals(True)
        state = item.checkState(0)
        if item.childCount() > 0 and state != Qt.CheckState.PartiallyChecked:
            self._set_children_check_state(item, state)
        self._update_parent_check_state(item)
        self.tree.blockSignals(False)
        self.update_summary()

    def on_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            self.detail_edit.setPlainText("")
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            if "detail" in data:
                self.detail_edit.setPlainText(
                    f"Target File: {data.get('file', 'N/A')}\n"
                    f"Action: {data.get('action', 'N/A')}\n"
                    f"Change ID: {data.get('id', 'N/A')}\n\n"
                    f"{data.get('detail', '')}"
                )
            elif data.get("type") == "book":
                self.detail_edit.setPlainText(f"Book/File: {item.text(0)}\nTotal detected items: {item.childCount()}")
            elif data.get("type") == "category":
                self.detail_edit.setPlainText(f"Category: {data.get('name')}\nTotal items: {item.childCount()}")
            else:
                self.detail_edit.setPlainText(f"Selected: {item.text(0)}")
        else:
            self.detail_edit.setPlainText(f"Selected: {item.text(0)}")

    def select_all(self):
        self.tree.blockSignals(True)
        root = self.tree.invisibleRootItem()
        self._set_children_check_state(root, Qt.CheckState.Checked)
        self.tree.blockSignals(False)
        self.update_summary()

    def deselect_all(self):
        self.tree.blockSignals(True)
        root = self.tree.invisibleRootItem()
        self._set_children_check_state(root, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.update_summary()

    def total_item_count(self):
        count = 0
        for res in self.scan_results:
            count += len(res.get("changes", []))
        return count

    def get_selected_change_ids(self):
        selected_ids = set()

        def _collect(item):
            for i in range(item.childCount()):
                child = item.child(i)
                _collect(child)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and "id" in data:
                if item.checkState(0) == Qt.CheckState.Checked:
                    selected_ids.add(data["id"])

        root = self.tree.invisibleRootItem()
        _collect(root)
        return selected_ids

    def update_summary(self):
        selected_ids = self.get_selected_change_ids()
        total_count = self.total_item_count()
        self.summary_label.setText(f"Selected {len(selected_ids)} of {total_count} detected change(s)")
        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(len(selected_ids) > 0)
