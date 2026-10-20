"""UI components for EPUB Promo Scrubber, including the interactive preview dialog and side-by-side visual diff viewer."""

import difflib
import html

from qt.core import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    Qt,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


def compute_side_by_side_html(orig_str, scrub_str, mode='full'):
    """Compute left and right HTML strings for side-by-side diff display."""
    orig_lines = orig_str.splitlines() if orig_str else []
    scrub_lines = scrub_str.splitlines() if scrub_str else []

    matcher = difflib.SequenceMatcher(None, orig_lines, scrub_lines)
    opcodes = matcher.get_opcodes()

    left_blocks = []
    right_blocks = []

    css = """<style>
        body { font-family: monospace; font-size: 11px; margin: 0; padding: 4px; background-color: #ffffff; white-space: pre-wrap; }
        .line { line-height: 1.3; white-space: pre-wrap; word-break: break-all; }
        .num { color: #888888; display: inline-block; min-width: 40px; text-align: right; margin-right: 8px; user-select: none; }
        .equal { background-color: #ffffff; color: #000000; }
        .delete { background-color: #ffcccc; color: #990000; }
        .insert { background-color: #ccffcc; color: #006600; }
        .pad { background-color: #f0f0f0; color: #aaaaaa; }
        .del-char { background-color: #ff8888; text-decoration: line-through; font-weight: bold; }
        .ins-char { background-color: #88ff88; font-weight: bold; }
        .separator { background-color: #e0e0e0; color: #555555; text-align: center; font-style: italic; font-weight: bold; padding: 2px 0; }
    </style>"""

    left_blocks.append(f"<html><head>{css}</head><body>")
    right_blocks.append(f"<html><head>{css}</head><body>")

    context_lines = 3

    for idx, (tag, i1, i2, j1, j2) in enumerate(opcodes):
        if mode == 'block':
            if tag == 'equal':
                count = i2 - i1
                if count > context_lines * 2:
                    for k in range(context_lines):
                        ln = i1 + k + 1
                        rn = j1 + k + 1
                        left_blocks.append(f'<div class="line equal"><span class="num">{ln}</span>{html.escape(orig_lines[i1 + k])}</div>')
                        right_blocks.append(f'<div class="line equal"><span class="num">{rn}</span>{html.escape(scrub_lines[j1 + k])}</div>')

                    hidden = count - context_lines * 2
                    left_blocks.append(f'<div class="line separator">--- {hidden} unchanged lines ---</div>')
                    right_blocks.append(f'<div class="line separator">--- {hidden} unchanged lines ---</div>')

                    for k in range(count - context_lines, count):
                        ln = i1 + k + 1
                        rn = j1 + k + 1
                        left_blocks.append(f'<div class="line equal"><span class="num">{ln}</span>{html.escape(orig_lines[i1 + k])}</div>')
                        right_blocks.append(f'<div class="line equal"><span class="num">{rn}</span>{html.escape(scrub_lines[j1 + k])}</div>')
                    continue

        if tag == 'equal':
            for k in range(i2 - i1):
                ln = i1 + k + 1
                rn = j1 + k + 1
                left_blocks.append(f'<div class="line equal"><span class="num">{ln}</span>{html.escape(orig_lines[i1 + k])}</div>')
                right_blocks.append(f'<div class="line equal"><span class="num">{rn}</span>{html.escape(scrub_lines[j1 + k])}</div>')
        elif tag == 'delete':
            left_len = i2 - i1
            for k in range(left_len):
                ln = i1 + k + 1
                left_blocks.append(f'<div class="line delete"><span class="num">{ln}</span>{html.escape(orig_lines[i1 + k])}</div>')
                right_blocks.append('<div class="line pad"><span class="num">&nbsp;</span></div>')
        elif tag == 'insert':
            right_len = j2 - j1
            for k in range(right_len):
                left_blocks.append('<div class="line pad"><span class="num">&nbsp;</span></div>')
                rn = j1 + k + 1
                right_blocks.append(f'<div class="line insert"><span class="num">{rn}</span>{html.escape(scrub_lines[j1 + k])}</div>')
        elif tag == 'replace':
            left_chunk = orig_lines[i1:i2]
            right_chunk = scrub_lines[j1:j2]
            max_len = max(len(left_chunk), len(right_chunk))
            for k in range(max_len):
                has_l = k < len(left_chunk)
                has_r = k < len(right_chunk)

                if has_l and has_r:
                    l_str = left_chunk[k]
                    r_str = right_chunk[k]
                    char_m = difflib.SequenceMatcher(None, l_str, r_str)
                    l_html_parts = []
                    r_html_parts = []
                    for c_tag, ci1, ci2, cj1, cj2 in char_m.get_opcodes():
                        sub_l = html.escape(l_str[ci1:ci2])
                        sub_r = html.escape(r_str[cj1:cj2])
                        if c_tag == 'equal':
                            l_html_parts.append(sub_l)
                            r_html_parts.append(sub_r)
                        elif c_tag == 'delete':
                            l_html_parts.append(f'<span class="del-char">{sub_l}</span>')
                        elif c_tag == 'insert':
                            r_html_parts.append(f'<span class="ins-char">{sub_r}</span>')
                        elif c_tag == 'replace':
                            l_html_parts.append(f'<span class="del-char">{sub_l}</span>')
                            r_html_parts.append(f'<span class="ins-char">{sub_r}</span>')

                    ln = i1 + k + 1
                    rn = j1 + k + 1
                    left_blocks.append(f'<div class="line delete"><span class="num">{ln}</span>{"".join(l_html_parts)}</div>')
                    right_blocks.append(f'<div class="line insert"><span class="num">{rn}</span>{"".join(r_html_parts)}</div>')
                elif has_l:
                    ln = i1 + k + 1
                    left_blocks.append(f'<div class="line delete"><span class="num">{ln}</span>{html.escape(left_chunk[k])}</div>')
                    right_blocks.append('<div class="line pad"><span class="num">&nbsp;</span></div>')
                else:
                    left_blocks.append('<div class="line pad"><span class="num">&nbsp;</span></div>')
                    rn = j1 + k + 1
                    right_blocks.append(f'<div class="line insert"><span class="num">{rn}</span>{html.escape(right_chunk[k])}</div>')

    left_blocks.append("</body></html>")
    right_blocks.append("</body></html>")

    return "".join(left_blocks), "".join(right_blocks)


class SideBySideDiffDialog(QDialog):
    """Dual-pane side-by-side visual diff viewer with synchronized scrolling and line/character highlighting."""

    def __init__(self, parent=None, book_title="", file_name="", original_html="", scrubbed_html="",
                 changes=None, checked_change_ids=None, raw_data=None, matcher=None):
        super().__init__(parent)
        self.setWindowTitle(f'EPUB Promo Scrubber - Visual Diff: {file_name}')
        self.resize(1200, 750)
        self.setMinimumWidth(1200)

        self.book_title = book_title
        self.file_name = file_name
        self.original_html = original_html or ""
        self.scrubbed_html = scrubbed_html or ""
        self.changes = changes or []
        self.checked_change_ids = set(checked_change_ids) if checked_change_ids is not None else set()
        self.raw_data = raw_data
        self.matcher = matcher

        self.diff_mode = 'full'

        self._init_ui()
        self._connect_signals()
        self.refresh_diff()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)

        # Header controls
        header_layout = QHBoxLayout()
        header_label = QLabel(f"<b>Comparing File:</b> {self.file_name} &nbsp;|&nbsp; <b>Book:</b> <i>{self.book_title}</i>")
        header_layout.addWidget(header_label)
        header_layout.addStretch()

        mode_label = QLabel("Diff Mode:")
        header_layout.addWidget(mode_label)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Full Document Diff", "Block Diff Only"])
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        header_layout.addWidget(self.mode_combo)

        main_layout.addLayout(header_layout)

        # Main horizontal splitter
        main_splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Dual diff container
        diff_widget = QWidget(main_splitter)
        diff_layout = QVBoxLayout(diff_widget)
        diff_layout.setContentsMargins(0, 0, 0, 0)

        pane_header_layout = QHBoxLayout()
        pane_header_layout.addWidget(QLabel("<b>Original XHTML (Left)</b>"))
        pane_header_layout.addWidget(QLabel("<b>Scrubbed XHTML (Right)</b>"))
        diff_layout.addLayout(pane_header_layout)

        dual_splitter = QSplitter(Qt.Orientation.Horizontal, diff_widget)

        self.left_browser = QTextBrowser(dual_splitter)
        self.left_browser.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)

        self.right_browser = QTextBrowser(dual_splitter)
        self.right_browser.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)

        dual_splitter.addWidget(self.left_browser)
        dual_splitter.addWidget(self.right_browser)
        dual_splitter.setSizes([550, 550])

        diff_layout.addWidget(dual_splitter)
        main_splitter.addWidget(diff_widget)

        # Sidebar with change toggles
        sidebar_widget = QWidget(main_splitter)
        sidebar_layout = QVBoxLayout(sidebar_widget)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)

        sidebar_title = QLabel("<b>Document Changes:</b>")
        sidebar_layout.addWidget(sidebar_title)

        self.changes_tree = QTreeWidget(sidebar_widget)
        self.changes_tree.setHeaderLabels(["Action / Text", "Category"])
        self.changes_tree.setColumnWidth(0, 180)
        sidebar_layout.addWidget(self.changes_tree)

        self.populate_changes_tree()

        main_splitter.addWidget(sidebar_widget)
        main_splitter.setSizes([880, 320])

        main_layout.addWidget(main_splitter)

        # Dialog Buttons
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText("Apply & Close")
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        main_layout.addWidget(btn_box)

    def _connect_signals(self):
        sb_left_v = self.left_browser.verticalScrollBar()
        sb_right_v = self.right_browser.verticalScrollBar()
        sb_left_h = self.left_browser.horizontalScrollBar()
        sb_right_h = self.right_browser.horizontalScrollBar()

        self._updating_scroll = False

        def sync_v_left(val):
            if not self._updating_scroll:
                self._updating_scroll = True
                sb_right_v.setValue(val)
                self._updating_scroll = False

        def sync_v_right(val):
            if not self._updating_scroll:
                self._updating_scroll = True
                sb_left_v.setValue(val)
                self._updating_scroll = False

        def sync_h_left(val):
            if not self._updating_scroll:
                self._updating_scroll = True
                sb_right_h.setValue(val)
                self._updating_scroll = False

        def sync_h_right(val):
            if not self._updating_scroll:
                self._updating_scroll = True
                sb_left_h.setValue(val)
                self._updating_scroll = False

        sb_left_v.valueChanged.connect(sync_v_left)
        sb_right_v.valueChanged.connect(sync_v_right)
        sb_left_h.valueChanged.connect(sync_h_left)
        sb_right_h.valueChanged.connect(sync_h_right)

        self.changes_tree.itemChanged.connect(self._on_change_item_toggled)

    def populate_changes_tree(self):
        self.changes_tree.blockSignals(True)
        self.changes_tree.clear()

        file_changes = [c for c in self.changes if c.get('file') == self.file_name or not self.file_name]
        if not file_changes:
            file_changes = self.changes

        for ch in file_changes:
            disp_text = ch.get('text') or ch.get('action') or 'Change'
            item = QTreeWidgetItem(self.changes_tree, [disp_text, ch.get('category', '')])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            ch_id = ch.get('id')
            is_checked = ch_id in self.checked_change_ids
            item.setCheckState(0, Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, ch)

        self.changes_tree.blockSignals(False)

    def _on_change_item_toggled(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, dict) and 'id' in data:
            ch_id = data['id']
            if item.checkState(0) == Qt.CheckState.Checked:
                self.checked_change_ids.add(ch_id)
            else:
                self.checked_change_ids.discard(ch_id)

            if self.raw_data and self.matcher:
                try:
                    from core import get_document_diff_buffers
                except ImportError:
                    from scrubber_plugin.core import get_document_diff_buffers
                bufs = get_document_diff_buffers(self.raw_data, self.matcher, self.file_name, allowed_change_ids=self.checked_change_ids)
                self.scrubbed_html = bufs['scrubbed']

            self.refresh_diff()

    def _on_mode_changed(self, index):
        self.diff_mode = 'block' if index == 1 else 'full'
        self.refresh_diff()

    def refresh_diff(self):
        left_html, right_html = compute_side_by_side_html(
            self.original_html, self.scrubbed_html, mode=self.diff_mode
        )
        self.left_browser.setHtml(left_html)
        self.right_browser.setHtml(right_html)


def open_side_by_side_diff(parent, book_title, file_name, original_html, scrubbed_html,
                           changes=None, checked_change_ids=None, raw_data=None, matcher=None):
    """Open side-by-side diff dialog, with fallback when Calibre Tweak Book modules are absent."""
    try:
        from calibre.gui2.tweak_book.diff.main import DiffWindow as CalibreDiffWindow
        # If Calibre diff window exists and works, attempt usage
        # Otherwise fallback
    except Exception:
        pass

    return SideBySideDiffDialog(
        parent=parent,
        book_title=book_title,
        file_name=file_name,
        original_html=original_html,
        scrubbed_html=scrubbed_html,
        changes=changes,
        checked_change_ids=checked_change_ids,
        raw_data=raw_data,
        matcher=matcher
    )


class ScrubPreviewDialog(QDialog):
    """Modal dialog allowing users to review and selectively filter promo changes before applying."""

    def __init__(self, parent, scan_results, is_folder=False):
        super().__init__(parent)
        self.setWindowTitle('EPUB Promo Scrubber - Preview Proposed Changes')
        self.resize(800, 550)

        self.scan_results = scan_results
        self.is_folder = is_folder

        layout = QVBoxLayout(self)

        # Header label
        header_label = QLabel(
            'Review detected promotional content. Uncheck items or books you wish to exclude before applying.'
        )
        layout.addWidget(header_label)

        # Splitter between tree and detail view
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Tree Widget on the left
        self.tree = QTreeWidget(splitter)
        self.tree.setHeaderLabels(['Item / Target', 'Action Category'])
        self.tree.setColumnWidth(0, 380)
        self.tree.setColumnWidth(1, 180)

        # Detail Pane on the right
        detail_widget = QWidget(splitter)
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        detail_title = QLabel('<b>Selected Item Details:</b>')
        detail_layout.addWidget(detail_title)

        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        detail_layout.addWidget(self.detail_edit)

        # Compare HTML Side-by-Side button
        self.btn_compare_html = QPushButton('Compare HTML Side-by-Side')
        self.btn_compare_html.setEnabled(False)
        self.btn_compare_html.clicked.connect(self.open_visual_diff)
        detail_layout.addWidget(self.btn_compare_html)

        splitter.addWidget(self.tree)
        splitter.addWidget(detail_widget)
        splitter.setSizes([480, 320])

        layout.addWidget(splitter)

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
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setText('Apply Selected Changes')
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
            title = res.get('title') or res.get('path') or f"Book ID {res.get('book_id')}"
            changes = res.get('changes', [])
            book_item = QTreeWidgetItem(self.tree, [title, f"{len(changes)} items"])
            book_item.setFlags(book_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            book_item.setCheckState(0, Qt.CheckState.Checked)
            book_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'book', 'book_id': res.get('book_id'), 'path': res.get('path')})

            grouped = {}
            for ch in changes:
                cat = ch.get('category') or ch.get('file') or 'General'
                grouped.setdefault(cat, []).append(ch)

            for cat_name, cat_changes in grouped.items():
                cat_item = QTreeWidgetItem(book_item, [cat_name, f"{len(cat_changes)} items"])
                cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                cat_item.setCheckState(0, Qt.CheckState.Checked)
                cat_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'category', 'name': cat_name})

                for ch in cat_changes:
                    disp_text = ch.get('text') or ch.get('action') or 'Promo Item'
                    child_item = QTreeWidgetItem(cat_item, [disp_text, ch.get('action', '')])
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
            self.detail_edit.setPlainText('')
            self.btn_compare_html.setEnabled(False)
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        self.btn_compare_html.setEnabled(True)
        if isinstance(data, dict):
            if 'detail' in data:
                self.detail_edit.setPlainText(
                    f"Target File: {data.get('file', 'N/A')}\n"
                    f"Action: {data.get('action', 'N/A')}\n"
                    f"Change ID: {data.get('id', 'N/A')}\n\n"
                    f"{data.get('detail', '')}"
                )
            elif data.get('type') == 'book':
                self.detail_edit.setPlainText(f"Book/File: {item.text(0)}\nTotal detected items: {item.childCount()}")
            elif data.get('type') == 'category':
                self.detail_edit.setPlainText(f"Category: {data.get('name')}\nTotal items: {item.childCount()}")
            else:
                self.detail_edit.setPlainText(f"Selected: {item.text(0)}")
        else:
            self.detail_edit.setPlainText(f"Selected: {item.text(0)}")

    def open_visual_diff(self):
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)

        book_item = item
        while book_item.parent() is not None:
            book_item = book_item.parent()

        book_data = book_item.data(0, Qt.ItemDataRole.UserRole)
        book_id = book_data.get('book_id') if isinstance(book_data, dict) else None
        book_path = book_data.get('path') if isinstance(book_data, dict) else None

        res = None
        for r in self.scan_results:
            if book_id is not None and r.get('book_id') == book_id:
                res = r
                break
            if book_path is not None and r.get('path') == book_path:
                res = r
                break
        if res is None and self.scan_results:
            res = self.scan_results[0]

        file_name = None
        if isinstance(data, dict):
            file_name = data.get('file') or data.get('name')

        if not file_name:
            for ch in res.get('changes', []):
                if ch.get('file'):
                    file_name = ch['file']
                    break

        if not file_name:
            file_name = "OEBPS/c1.xhtml"

        bufs = res.get('xhtml_buffers', {}).get(file_name) or res.get('buffers', {}).get(file_name)
        raw_data = res.get('data')

        matcher = None
        try:
            from core import Matcher, get_document_diff_buffers
            matcher = Matcher()
        except ImportError:
            try:
                from scrubber_plugin.core import Matcher, get_document_diff_buffers
                matcher = Matcher()
            except ImportError:
                pass

        if not bufs and raw_data and matcher:
            checked_ids = self.get_selected_change_ids()
            bufs = get_document_diff_buffers(raw_data, matcher, file_name, allowed_change_ids=checked_ids)

        orig_html = bufs.get('original', '') if bufs else ''
        scrub_html = bufs.get('scrubbed', '') if bufs else ''

        book_title = res.get('title') or item.text(0)
        checked_ids = self.get_selected_change_ids()

        diff_dialog = open_side_by_side_diff(
            parent=self,
            book_title=book_title,
            file_name=file_name,
            original_html=orig_html,
            scrubbed_html=scrub_html,
            changes=res.get('changes', []),
            checked_change_ids=checked_ids,
            raw_data=raw_data,
            matcher=matcher
        )

        diff_dialog.exec()

        self.update_change_checkboxes(diff_dialog.checked_change_ids)

    def update_change_checkboxes(self, checked_ids):
        self.tree.blockSignals(True)

        def _sync(item):
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and 'id' in data:
                ch_id = data['id']
                state = Qt.CheckState.Checked if ch_id in checked_ids else Qt.CheckState.Unchecked
                item.setCheckState(0, state)
            for i in range(item.childCount()):
                _sync(item.child(i))

        root = self.tree.invisibleRootItem()
        _sync(root)

        def _update_parents(item):
            for i in range(item.childCount()):
                _update_parents(item.child(i))
            if item.parent() is not None:
                self._update_parent_check_state(item)

        for i in range(root.childCount()):
            _update_parents(root.child(i))

        self.tree.blockSignals(False)
        self.update_summary()

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
            count += len(res.get('changes', []))
        return count

    def get_selected_change_ids(self):
        selected_ids = set()

        def _collect(item):
            for i in range(item.childCount()):
                child = item.child(i)
                _collect(child)
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and 'id' in data:
                if item.checkState(0) == Qt.CheckState.Checked:
                    selected_ids.add(data['id'])

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
