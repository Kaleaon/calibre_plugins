"""UI components for EPUB Promo Scrubber, including the interactive preview dialog, side-by-side visual diff viewer, and profile settings manager."""

import difflib
import html

import json
import logging

try:
    from qt.core import (
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QImage,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPixmap,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QSplitter,
        Qt,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextBrowser,
        QThread,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
        pyqtSignal,
    )
except ImportError:
    from PyQt6.QtCore import Qt, QThread, pyqtSignal
    from PyQt6.QtGui import QImage, QPixmap
    from PyQt6.QtWidgets import (
        QButtonGroup,
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextBrowser,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

try:
    from calibre_plugins.epub_promo_scrubber.scanner import LibraryScanner
except ImportError:
    try:
        from calibre_plugins.scrubber_plugin.scanner import LibraryScanner
    except ImportError:
        try:
            from .scanner import LibraryScanner
        except ImportError:
            try:
                from scrubber_plugin.scanner import LibraryScanner
            except ImportError:
                import importlib.util
                from pathlib import Path

                sc_path = Path(__file__).parent / 'scanner.py'
                if sc_path.exists():
                    spec = importlib.util.spec_from_file_location('scanner', sc_path)
                    sc_mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(sc_mod)
                    LibraryScanner = sc_mod.LibraryScanner
                else:
                    LibraryScanner = None


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
            prof_name = res.get('active_profile') or res.get('profile') or 'Global Default'
            changes = res.get('changes', [])
            book_item = QTreeWidgetItem(self.tree, [title, f"{len(changes)} items (Profile: {prof_name})"])
            book_item.setFlags(book_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            book_item.setCheckState(0, Qt.CheckState.Checked)
            book_item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {'type': 'book', 'book_id': res.get('book_id'), 'path': res.get('path'), 'active_profile': prof_name},
            )

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
                prof_name = data.get('active_profile') or 'Global Default'
                self.detail_edit.setPlainText(
                    f"Book/File: {item.text(0)}\nActive Profile: {prof_name}\nTotal detected items: {item.childCount()}"
                )
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


class ProfileEditorDialog(QDialog):
    """Dialog to create or edit a site scrubbing profile."""

    def __init__(self, parent, profile=None, is_default=False):
        super().__init__(parent)
        self.setWindowTitle('Edit Profile' if profile else 'New Profile')
        self.resize(500, 450)
        self.is_default = is_default

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setText(profile.get('name', '') if profile else '')
        if is_default:
            self.name_edit.setReadOnly(True)
        form.addRow('Profile Name:', self.name_edit)

        self.domains_edit = QPlainTextEdit()
        pats = profile.get('domain_patterns', []) if profile else []
        if isinstance(pats, list):
            pats_str = '\n'.join(pats)
        else:
            pats_str = str(pats)
        self.domains_edit.setPlainText(pats_str)
        if is_default:
            self.domains_edit.setPlainText('*')
            self.domains_edit.setReadOnly(True)
        self.domains_edit.setPlaceholderText(
            'Target domains/patterns, one per line (e.g. royalroad.com, *.royalroad.com, re:.*royalroad.*)'
        )
        form.addRow('Target Domain Patterns:', self.domains_edit)

        self.patterns_edit = QPlainTextEdit()
        self.patterns_edit.setPlainText(profile.get('extra_patterns', '') if profile else '')
        self.patterns_edit.setPlaceholderText(
            'Extra site names or regex rules, one per line (prefix with re: for regular expressions)'
        )
        form.addRow('Site Removal Patterns:', self.patterns_edit)

        self.heuristic_cb = QCheckBox('Also remove short "Downloaded from..." style promo blocks')
        self.heuristic_cb.setChecked(profile.get('heuristic', True) if profile else True)
        form.addRow('', self.heuristic_cb)

        self.drop_cb = QCheckBox('Delete empty promotional pages and TOC entries')
        self.drop_cb.setChecked(profile.get('drop_empty_pages', True) if profile else True)
        form.addRow('', self.drop_cb)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.result_profile = None

    def on_accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, 'Invalid Name', 'Profile name cannot be empty.')
            return

        pats_raw = self.domains_edit.toPlainText()
        pats = [p.strip() for p in pats_raw.splitlines() if p.strip()]
        if not pats and not self.is_default:
            QMessageBox.warning(self, 'Invalid Domains', 'At least one domain pattern is required.')
            return

        self.result_profile = {
            'id': f"profile_{name.lower().replace(' ', '_')}",
            'name': name,
            'domain_patterns': ['*'] if self.is_default else pats,
            'extra_patterns': self.patterns_edit.toPlainText(),
            'heuristic': self.heuristic_cb.isChecked(),
            'drop_empty_pages': self.drop_cb.isChecked(),
            'is_default': self.is_default,
        }
        self.accept()


class ScrubSettingsDialog(QDialog):
    """Plugin settings dialog containing General settings and Profile Manager tabs."""

    def __init__(self, parent, prefs):
        super().__init__(parent)
        self.setWindowTitle('EPUB Promo Scrubber Settings')
        self.resize(750, 520)

        self.prefs = prefs
        try:
            from calibre_plugins.epub_promo_scrubber.profiles import (
                export_profiles_to_json,
                get_profiles_from_prefs,
                import_profiles_from_json,
                save_profiles_to_prefs,
            )
        except ImportError:
            try:
                from calibre_plugins.scrubber_plugin.profiles import (
                    export_profiles_to_json,
                    get_profiles_from_prefs,
                    import_profiles_from_json,
                    save_profiles_to_prefs,
                )
            except ImportError:
                try:
                    from .profiles import (
                        export_profiles_to_json,
                        get_profiles_from_prefs,
                        import_profiles_from_json,
                        save_profiles_to_prefs,
                    )
                except ImportError:
                    import importlib.util
                    from pathlib import Path

                    prof_path = Path(__file__).parent / 'profiles.py'
                    if prof_path.exists():
                        spec = importlib.util.spec_from_file_location('profiles', prof_path)
                        prof_mod = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(prof_mod)
                        export_profiles_to_json = prof_mod.export_profiles_to_json
                        get_profiles_from_prefs = prof_mod.get_profiles_from_prefs
                        import_profiles_from_json = prof_mod.import_profiles_from_json
                        save_profiles_to_prefs = prof_mod.save_profiles_to_prefs
                    else:
                        from profiles import (
                            export_profiles_to_json,
                            get_profiles_from_prefs,
                            import_profiles_from_json,
                            save_profiles_to_prefs,
                        )

        self.export_profiles_to_json = export_profiles_to_json
        self.get_profiles_from_prefs = get_profiles_from_prefs
        self.import_profiles_from_json = import_profiles_from_json
        self.save_profiles_to_prefs = save_profiles_to_prefs

        self.profiles = [dict(p) for p in get_profiles_from_prefs(prefs)]

        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()

        # Tab 1: General Settings
        gen_tab = QWidget()
        gen_layout = QVBoxLayout(gen_tab)

        self.show_preview = QCheckBox('Show preview dialog before applying changes')
        self.show_preview.setChecked(prefs.get('show_preview', True))

        self.backup = QCheckBox('Library books: keep unmodified EPUB as ORIGINAL_EPUB')
        self.backup.setChecked(prefs.get('backup', True))

        self.in_place = QCheckBox('Files and folders: replace original files with cleaned versions')
        self.in_place.setChecked(prefs.get('in_place', False))

        gen_layout.addWidget(self.show_preview)
        gen_layout.addWidget(self.backup)
        gen_layout.addWidget(self.in_place)
        gen_layout.addStretch()

        self.tabs.addTab(gen_tab, 'General Settings')

        # Tab 2: Profile Manager
        prof_tab = QWidget()
        prof_layout = QHBoxLayout(prof_tab)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['Profile Name', 'Domain Patterns', 'Heuristics', 'Drop Pages'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        prof_layout.addWidget(self.table)

        btn_layout = QVBoxLayout()
        btn_new = QPushButton('New Profile...')
        btn_clone = QPushButton('Clone Profile')
        btn_edit = QPushButton('Edit Profile...')
        btn_delete = QPushButton('Delete Profile')
        btn_import = QPushButton('Import Profiles...')
        btn_export = QPushButton('Export Profiles...')

        btn_new.clicked.connect(self.on_new_profile)
        btn_clone.clicked.connect(self.on_clone_profile)
        btn_edit.clicked.connect(self.on_edit_profile)
        btn_delete.clicked.connect(self.on_delete_profile)
        btn_import.clicked.connect(self.on_import_profiles)
        btn_export.clicked.connect(self.on_export_profiles)

        for b in (btn_new, btn_clone, btn_edit, btn_delete, btn_import, btn_export):
            btn_layout.addWidget(b)
        btn_layout.addStretch()

        prof_layout.addLayout(btn_layout)

        self.tabs.addTab(prof_tab, 'Profile Manager')

        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.populate_table()

    def populate_table(self):
        self.table.setRowCount(len(self.profiles))
        for row, prof in enumerate(self.profiles):
            name_item = QTableWidgetItem(prof.get('name', ''))
            if prof.get('is_default'):
                name_item.setText(f"{prof.get('name', '')} (Default)")

            pats = prof.get('domain_patterns', [])
            if isinstance(pats, list):
                pats_str = ', '.join(pats)
            else:
                pats_str = str(pats)
            dom_item = QTableWidgetItem(pats_str)

            heur_item = QTableWidgetItem('Yes' if prof.get('heuristic', True) else 'No')
            drop_item = QTableWidgetItem('Yes' if prof.get('drop_empty_pages', True) else 'No')

            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, dom_item)
            self.table.setItem(row, 2, heur_item)
            self.table.setItem(row, 3, drop_item)

    def selected_profile_index(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return -1
        return rows[0].row()

    def on_new_profile(self):
        dlg = ProfileEditorDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_profile:
            self.profiles.append(dlg.result_profile)
            self.populate_table()

    def on_clone_profile(self):
        idx = self.selected_profile_index()
        if idx < 0:
            QMessageBox.information(self, 'Clone Profile', 'Select a profile to clone first.')
            return
        orig = self.profiles[idx]
        cloned = dict(orig)
        cloned['id'] = f"profile_{orig.get('name', '').lower()}_copy"
        cloned['name'] = f"Copy of {orig.get('name', '')}"
        cloned['is_default'] = False
        if cloned.get('domain_patterns') == ['*']:
            cloned['domain_patterns'] = ['example.com']

        dlg = ProfileEditorDialog(self, profile=cloned)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_profile:
            self.profiles.append(dlg.result_profile)
            self.populate_table()

    def on_edit_profile(self):
        idx = self.selected_profile_index()
        if idx < 0:
            QMessageBox.information(self, 'Edit Profile', 'Select a profile to edit first.')
            return
        prof = self.profiles[idx]
        dlg = ProfileEditorDialog(self, profile=prof, is_default=prof.get('is_default', False))
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_profile:
            self.profiles[idx] = dlg.result_profile
            self.populate_table()

    def on_delete_profile(self):
        idx = self.selected_profile_index()
        if idx < 0:
            QMessageBox.information(self, 'Delete Profile', 'Select a profile to delete first.')
            return
        prof = self.profiles[idx]
        if prof.get('is_default') or prof.get('name') == 'Global Default':
            QMessageBox.warning(self, 'Cannot Delete', 'The Global Default profile cannot be deleted.')
            return
        ans = QMessageBox.question(self, 'Confirm Delete', f"Delete profile '{prof.get('name')}'?")
        if ans == QMessageBox.StandardButton.Yes:
            del self.profiles[idx]
            self.populate_table()

    def on_import_profiles(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import Profiles JSON', '', 'JSON Files (*.json)')
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            imported = self.import_profiles_from_json(content)
            count = len(imported)
            for imp in imported:
                match_idx = next((i for i, p in enumerate(self.profiles) if p['name'] == imp['name']), None)
                if match_idx is not None and not self.profiles[match_idx].get('is_default'):
                    self.profiles[match_idx] = imp
                elif match_idx is None:
                    self.profiles.append(imp)
            self.populate_table()
            QMessageBox.information(self, 'Import Successful', f'Successfully imported {count} profile(s).')
        except Exception as exc:
            QMessageBox.critical(self, 'Import Failed', f'Could not import profiles:\n{exc}')

    def on_export_profiles(self):
        idx = self.selected_profile_index()
        to_export = [self.profiles[idx]] if idx >= 0 else self.profiles
        path, _ = QFileDialog.getSaveFileName(self, 'Export Profiles JSON', 'scrub_profiles.json', 'JSON Files (*.json)')
        if not path:
            return
        try:
            json_data = self.export_profiles_to_json(to_export)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(json_data)
            QMessageBox.information(self, 'Export Successful', f'Exported {len(to_export)} profile(s) to:\n{path}')
        except Exception as exc:
            QMessageBox.critical(self, 'Export Failed', f'Could not export profiles:\n{exc}')

    def on_accept(self):
        self.prefs['show_preview'] = self.show_preview.isChecked()
        self.prefs['backup'] = self.backup.isChecked()
        self.prefs['in_place'] = self.in_place.isChecked()
        self.save_profiles_to_prefs(self.prefs, self.profiles)
        self.accept()



# -------------------------------------------------------------------------------------------------
# Automated Library Scanner UI & Background Workers
# -------------------------------------------------------------------------------------------------

class LibraryScanWorker(QThread):
    progress = pyqtSignal(int, int, int, str, int)  # cur, total, book_id, title, count
    book_flagged = pyqtSignal(dict)
    scan_finished = pyqtSignal(dict)
    scan_error = pyqtSignal(str)

    def __init__(self, scanner, book_ids):
        super().__init__()
        self.scanner = scanner
        self.book_ids = book_ids
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            res = self.scanner.scan_library(
                self.book_ids,
                on_progress=lambda cur, tot, bid, title, cnt: self.progress.emit(cur, tot, bid, title, cnt),
                should_cancel=lambda: self._cancelled
            )
            self.scan_finished.emit(res)
        except Exception as exc:
            self.scan_error.emit(str(exc))


class LibraryCleanWorker(QThread):
    progress = pyqtSignal(int, int, str)
    clean_finished = pyqtSignal(dict)
    clean_error = pyqtSignal(str)

    def __init__(self, scanner, scan_results, allowed_ids, backup):
        super().__init__()
        self.scanner = scanner
        self.scan_results = scan_results
        self.allowed_ids = allowed_ids
        self.backup = backup
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            res = self.scanner.apply_clean(
                self.scan_results,
                allowed_change_ids=self.allowed_ids,
                backup=self.backup,
                on_progress=lambda cur, tot, title: self.progress.emit(cur, tot, title),
                should_cancel=lambda: self._cancelled
            )
            self.clean_finished.emit(res)
        except Exception as exc:
            self.clean_error.emit(str(exc))


class LibraryScannerDialog(QDialog):
    """Full-library and selective promo scanner dialog with image previews and batch scrubbing."""

    def __init__(self, parent, db, prefs, selected_book_ids=None):
        super().__init__(parent)
        self.setWindowTitle('EPUB Promo Scrubber - Automated Library Scanner')
        self.resize(950, 680)

        self.db = db
        self.prefs = prefs
        self.selected_book_ids = list(selected_book_ids or [])
        self.all_book_ids = sorted(list(self.db.all_book_ids()))
        self.scanner = LibraryScanner(self.db, self.prefs)

        self.scan_results = []
        self.scan_worker = None
        self.clean_worker = None

        main_layout = QVBoxLayout(self)

        # 1. Top Scope & Scanning Controls
        ctrl_box = QGroupBox('Scan Configuration & Controls')
        ctrl_layout = QVBoxLayout(ctrl_box)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel('<b>Scan Target:</b>'))
        self.rb_all = QRadioButton(f'Entire Calibre Library ({len(self.all_book_ids)} books)')
        self.rb_selected = QRadioButton(f'Selected Books ({len(self.selected_book_ids)} books)')
        self.scope_group = QButtonGroup(self)
        self.scope_group.addButton(self.rb_all)
        self.scope_group.addButton(self.rb_selected)

        if self.selected_book_ids:
            self.rb_selected.setChecked(True)
        else:
            self.rb_all.setChecked(True)
            self.rb_selected.setEnabled(False)

        scope_row.addWidget(self.rb_all)
        scope_row.addWidget(self.rb_selected)
        scope_row.addStretch()

        self.btn_start = QPushButton('Start Automated Scan')
        self.btn_start.setStyleSheet('font-weight: bold; padding: 4px 12px;')
        self.btn_start.clicked.connect(self.start_scan)
        self.btn_stop = QPushButton('Stop')
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scan)
        scope_row.addWidget(self.btn_start)
        scope_row.addWidget(self.btn_stop)
        ctrl_layout.addLayout(scope_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        ctrl_layout.addWidget(self.progress_bar)

        self.status_label = QLabel('Ready to scan.')
        self.status_label.setStyleSheet('color: #555555;')
        ctrl_layout.addWidget(self.status_label)

        main_layout.addWidget(ctrl_box)

        # 2. Metrics Strip
        metrics_layout = QHBoxLayout()
        self.pill_scanned = QLabel('Scanned: 0')
        self.pill_clean = QLabel('Clean: 0')
        self.pill_flagged = QLabel('Flagged: 0')
        self.pill_ads = QLabel('Ad Banners & Images: 0')
        self.pill_pages = QLabel('Dropped Pages: 0')
        self.pill_meta = QLabel('Metadata Fields: 0')

        pill_style = 'padding: 3px 8px; border-radius: 4px; background: #f0f0f0; border: 1px solid #d0d0d0;'
        for p in (self.pill_scanned, self.pill_clean, self.pill_flagged, self.pill_ads, self.pill_pages, self.pill_meta):
            p.setStyleSheet(pill_style)
            metrics_layout.addWidget(p)
        metrics_layout.addStretch()
        main_layout.addLayout(metrics_layout)

        # 3. Filter Row
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel('Filter:'))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText('Search by book title or pattern keyword...')
        self.filter_edit.textChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.filter_edit)

        filter_layout.addWidget(QLabel('Category:'))
        self.cat_filter = QComboBox()
        self.cat_filter.addItems([
            'All Categories',
            'Promotional Images & Ads',
            'Dropped Pages',
            'Content Blocks',
            'Hyperlinks',
            'Calibre Metadata Fields'
        ])
        self.cat_filter.currentIndexChanged.connect(self.apply_filter)
        filter_layout.addWidget(self.cat_filter)
        main_layout.addLayout(filter_layout)

        # 4. Main Splitter: Tree on left, Detail & Thumbnail on right
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Tree Widget
        self.tree = QTreeWidget(splitter)
        self.tree.setHeaderLabels(['Target / Item', 'Action Category'])
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 180)
        self.tree.itemChanged.connect(self.on_item_changed)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)

        # Right-hand Detail & Thumbnail Widget
        detail_widget = QWidget(splitter)
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)

        # Image Preview Box
        self.img_box = QGroupBox('Detected Image / Watermark Preview')
        img_box_layout = QVBoxLayout(self.img_box)
        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setMinimumHeight(120)
        self.thumb_label.setStyleSheet('background: #fafafa; border: 1px dashed #cccccc;')
        img_box_layout.addWidget(self.thumb_label)

        self.img_info_label = QLabel()
        self.img_info_label.setWordWrap(True)
        img_box_layout.addWidget(self.img_info_label)

        self.img_reason_label = QLabel()
        self.img_reason_label.setWordWrap(True)
        img_box_layout.addWidget(self.img_reason_label)
        self.img_box.setVisible(False)
        detail_layout.addWidget(self.img_box)

        # Detail text
        detail_layout.addWidget(QLabel('<b>Finding Details:</b>'))
        self.detail_edit = QPlainTextEdit()
        self.detail_edit.setReadOnly(True)
        detail_layout.addWidget(self.detail_edit)

        # Compare HTML button
        self.btn_compare_html = QPushButton('Compare HTML Side-by-Side')
        self.btn_compare_html.setEnabled(False)
        self.btn_compare_html.clicked.connect(self.open_visual_diff)
        detail_layout.addWidget(self.btn_compare_html)

        splitter.addWidget(self.tree)
        splitter.addWidget(detail_widget)
        splitter.setSizes([550, 400])
        main_layout.addWidget(splitter)

        # 5. Batch Selection Row
        sel_row = QHBoxLayout()
        btn_sel_all = QPushButton('Select All')
        btn_sel_all.clicked.connect(self.select_all)
        btn_desel_all = QPushButton('Deselect All')
        btn_desel_all.clicked.connect(self.deselect_all)
        btn_sel_ads = QPushButton('Select Only Ads & Watermarks')
        btn_sel_ads.clicked.connect(self.select_only_ads)
        btn_sel_pages = QPushButton('Select Only Dropped Pages')
        btn_sel_pages.clicked.connect(self.select_only_pages)

        sel_row.addWidget(btn_sel_all)
        sel_row.addWidget(btn_desel_all)
        sel_row.addWidget(btn_sel_ads)
        sel_row.addWidget(btn_sel_pages)

        self.summary_label = QLabel('Selected: 0 items')
        sel_row.addSpacing(15)
        sel_row.addWidget(self.summary_label)
        sel_row.addStretch()
        main_layout.addLayout(sel_row)

        # 6. Bottom Action Bar
        bottom_row = QHBoxLayout()
        self.chk_backup = QCheckBox('Create ORIGINAL_EPUB backup before scrubbing')
        self.chk_backup.setChecked(bool(self.prefs.get('backup', True)))
        bottom_row.addWidget(self.chk_backup)
        bottom_row.addStretch()

        self.btn_export = QPushButton('Export Report (JSON)...')
        self.btn_export.setEnabled(False)
        self.btn_export.clicked.connect(self.export_report)
        bottom_row.addWidget(self.btn_export)

        self.btn_apply = QPushButton('Scrub Selected Findings (0 items)')
        self.btn_apply.setStyleSheet('font-weight: bold; background-color: #2b78e4; color: white; padding: 6px 14px;')
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self.start_clean)
        bottom_row.addWidget(self.btn_apply)

        self.btn_close = QPushButton('Close')
        self.btn_close.clicked.connect(self.reject)
        bottom_row.addWidget(self.btn_close)

        main_layout.addLayout(bottom_row)

    def start_scan(self):
        target_ids = self.selected_book_ids if self.rb_selected.isChecked() else self.all_book_ids
        if not target_ids:
            QMessageBox.information(self, 'No Books', 'There are no books to scan.')
            return

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_apply.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.progress_bar.setValue(0)
        self.status_label.setText(f'Starting scan of {len(target_ids)} book(s)...')

        self.scan_results = []
        self.tree.clear()

        self.scan_worker = LibraryScanWorker(self.scanner, target_ids)
        self.scan_worker.progress.connect(self.on_scan_progress)
        self.scan_worker.scan_finished.connect(self.on_scan_finished)
        self.scan_worker.scan_error.connect(self.on_scan_error)
        self.scan_worker.start()

    def stop_scan(self):
        if self.scan_worker and self.scan_worker.isRunning():
            self.status_label.setText('Stopping scan...')
            self.scan_worker.cancel()
            self.btn_stop.setEnabled(False)

    def on_scan_progress(self, current, total, book_id, title, count):
        pct = int((current / total) * 100) if total else 0
        self.progress_bar.setValue(pct)
        if count > 0:
            self.status_label.setText(f"[{current}/{total}] Found {count} promo item(s) in: {title[:40]}")
        else:
            self.status_label.setText(f"[{current}/{total}] Clean: {title[:40]}")

    def on_scan_finished(self, summary):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_export.setEnabled(True)
        self.scan_results = summary.get('books', [])

        tot_scanned = summary.get('total_scanned', 0)
        clean_cnt = summary.get('clean_count', 0)
        flagged_cnt = summary.get('flagged_count', 0)
        ads_cnt = summary.get('total_ad_images', 0)
        dropped_cnt = summary.get('total_dropped_pages', 0)
        tot_findings = summary.get('total_findings', 0)
        dur = summary.get('duration_seconds', 0)

        self.pill_scanned.setText(f"Scanned: {tot_scanned}")
        self.pill_clean.setText(f"Clean: {clean_cnt}")
        self.pill_flagged.setText(f"Flagged: {flagged_cnt}")
        self.pill_ads.setText(f"Ad Banners & Images: {ads_cnt}")
        self.pill_pages.setText(f"Dropped Pages: {dropped_cnt}")

        self.status_label.setText(
            f"Scan completed in {dur}s. Found {tot_findings} promo item(s) across {flagged_cnt} book(s)."
        )

        self.populate_tree()
        self.update_summary()

    def on_scan_error(self, err_msg):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status_label.setText('Scan encountered an error.')
        QMessageBox.critical(self, 'Scan Error', "An error occurred during library scanning:\n" + str(err_msg))

    def populate_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()

        cat_filter = self.cat_filter.currentText()
        text_filter = self.filter_edit.text().strip().lower()

        for res in self.scan_results:
            book_id = res['book_id']
            title = res['title']
            all_findings = res.get('findings', [])

            # Apply category and search filters
            filtered_findings = []
            for f in all_findings:
                f_cat = f.get('category', 'General')
                if cat_filter != 'All Categories' and f_cat != cat_filter:
                    continue
                if text_filter:
                    target_blob = f"{title} {f.get('file', '')} {f.get('text', '')} {f.get('detail', '')}".lower()
                    if text_filter not in target_blob:
                        continue
                filtered_findings.append(f)

            if not filtered_findings:
                continue

            book_item = QTreeWidgetItem(self.tree, [f"{title} (#{book_id})", f"{len(filtered_findings)} item(s)"])
            book_item.setFlags(book_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            book_item.setCheckState(0, Qt.CheckState.Checked)
            book_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'book', 'book_id': book_id, 'res': res})

            grouped = {}
            for f in filtered_findings:
                grouped.setdefault(f.get('category', 'General'), []).append(f)

            for cat_name, cat_findings in grouped.items():
                cat_item = QTreeWidgetItem(book_item, [cat_name, f"{len(cat_findings)} item(s)"])
                cat_item.setFlags(cat_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                cat_item.setCheckState(0, Qt.CheckState.Checked)
                cat_item.setData(0, Qt.ItemDataRole.UserRole, {'type': 'category', 'name': cat_name})

                for f in cat_findings:
                    leaf_text = f.get('text') or f.get('file') or f.get('action')
                    leaf_item = QTreeWidgetItem(cat_item, [leaf_text, f.get('action', '')])
                    leaf_item.setFlags(leaf_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    leaf_item.setCheckState(0, Qt.CheckState.Checked)
                    leaf_item.setData(0, Qt.ItemDataRole.UserRole, f)

            book_item.setExpanded(True)

        self.tree.blockSignals(False)

    def apply_filter(self):
        self.populate_tree()
        self.update_summary()

    def _get_book_id_for_item(self, item):
        curr = item
        while curr is not None:
            data = curr.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data.get('type') == 'book':
                return data.get('book_id')
            curr = curr.parent()
        return None

    def on_selection_changed(self):
        items = self.tree.selectedItems()
        if not items:
            self.detail_edit.setPlainText('')
            self.img_box.setVisible(False)
            self.btn_compare_html.setEnabled(False)
            return

        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)

        if not isinstance(data, dict):
            self.detail_edit.setPlainText(item.text(0))
            self.img_box.setVisible(False)
            return

        # Handle image preview if Promotional Images & Ads finding
        if data.get('category') == 'Promotional Images & Ads':
            book_id = self._get_book_id_for_item(item)
            img_href = data.get('text') or data.get('file')
            if book_id and img_href:
                img_bytes = self.scanner.get_image_data(book_id, img_href)
                if img_bytes:
                    pix = QPixmap()
                    if pix.loadFromData(img_bytes):
                        scaled = pix.scaled(220, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                        self.thumb_label.setPixmap(scaled)
                        cls = data.get('classification', {})
                        info = cls.get('image_info', {})
                        w = info.get('width') or pix.width()
                        h = info.get('height') or pix.height()
                        fmt = info.get('format', 'image').upper()
                        conf = cls.get('confidence', 'high').capitalize()
                        reason = cls.get('reason') or data.get('detail', '')
                        self.img_info_label.setText(f"<b>Dimensions:</b> {w}x{h} ({fmt}) | <b>Confidence:</b> {conf}")
                        self.img_reason_label.setText(f"<b>Category:</b> {cls.get('category', 'ad').upper()}<br><b>Reason:</b> {reason}")
                        self.img_box.setVisible(True)
                else:
                    self.img_box.setVisible(False)
            else:
                self.img_box.setVisible(False)
        else:
            self.img_box.setVisible(False)

        # Populate detail text
        if 'detail' in data:
            details_str = (
                f"File: {data.get('file', 'N/A')}\n"
                f"Action: {data.get('action', 'N/A')}\n"
                f"Category: {data.get('category', 'N/A')}\n"
                f"Change ID: {data.get('id', 'N/A')}\n\n"
                f"{data.get('detail', '')}"
            )
            self.detail_edit.setPlainText(details_str)
            self.btn_compare_html.setEnabled(True)
        elif data.get('type') == 'book':
            res = data.get('res', {})
            prof = res.get('active_profile', 'Global Default')
            book_str = (
                f"Book: {item.text(0)}\n"
                f"Active Profile: {prof}\n"
                f"Total findings: {res.get('total_findings', 0)}\n"
                f"Ad Banners: {res.get('categories', {}).get('ads_and_banners', 0)}\n"
                f"Dropped Pages: {res.get('categories', {}).get('dropped_pages', 0)}"
            )
            self.detail_edit.setPlainText(book_str)
            self.btn_compare_html.setEnabled(False)
        elif data.get('type') == 'category':
            self.detail_edit.setPlainText(f"Category: {data.get('name')}\nTotal items: {item.childCount()}")
            self.btn_compare_html.setEnabled(False)

    def open_visual_diff(self):
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        book_id = self._get_book_id_for_item(item)
        if not book_id:
            return

        book_res = next((r for r in self.scan_results if r['book_id'] == book_id), None)
        if not book_res:
            return

        epub_report = book_res.get('epub_report') or {}
        buffers = epub_report.get('buffers', {}) or epub_report.get('xhtml_buffers', {})

        target_file = data.get('file') if isinstance(data, dict) else None
        target_buf = None
        if target_file and target_file in buffers:
            target_buf = buffers[target_file]
        elif buffers:
            first_key = next(iter(buffers))
            target_buf = buffers[first_key]
            target_file = first_key

        if not target_buf:
            QMessageBox.information(self, 'No Diff', 'No XHTML markup diff is available for this item.')
            return

        orig_str = target_buf.get('original', '')
        scrub_str = target_buf.get('scrubbed', '')
        diff_dialog = SideBySideDiffDialog(self, orig_str, scrub_str, target_file)
        diff_dialog.exec()

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

    def select_all(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Checked)
            self._set_children_check_state(top, Qt.CheckState.Checked)
        self.tree.blockSignals(False)
        self.update_summary()

    def deselect_all(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            top.setCheckState(0, Qt.CheckState.Unchecked)
            self._set_children_check_state(top, Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self.update_summary()

    def select_only_ads(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                cat_item = top.child(j)
                cat_name = cat_item.text(0)
                target_state = Qt.CheckState.Checked if cat_name == 'Promotional Images & Ads' else Qt.CheckState.Unchecked
                cat_item.setCheckState(0, target_state)
                self._set_children_check_state(cat_item, target_state)
            self._update_parent_check_state(top.child(0)) if top.childCount() > 0 else None
        self.tree.blockSignals(False)
        self.update_summary()

    def select_only_pages(self):
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                cat_item = top.child(j)
                cat_name = cat_item.text(0)
                target_state = Qt.CheckState.Checked if cat_name == 'Dropped Pages' else Qt.CheckState.Unchecked
                cat_item.setCheckState(0, target_state)
                self._set_children_check_state(cat_item, target_state)
            self._update_parent_check_state(top.child(0)) if top.childCount() > 0 else None
        self.tree.blockSignals(False)
        self.update_summary()

    def get_selected_change_ids(self):
        selected_ids = set()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                cat = top.child(j)
                for k in range(cat.childCount()):
                    leaf = cat.child(k)
                    if leaf.checkState(0) == Qt.CheckState.Checked:
                        data = leaf.data(0, Qt.ItemDataRole.UserRole)
                        if isinstance(data, dict) and data.get('id'):
                            selected_ids.add(data['id'])
        return selected_ids

    def update_summary(self):
        sel_ids = self.get_selected_change_ids()
        count = len(sel_ids)
        self.summary_label.setText(f"Selected: {count} item(s)")
        self.btn_apply.setText(f"Scrub Selected Findings ({count} items)")
        self.btn_apply.setEnabled(count > 0)

    def start_clean(self):
        allowed_ids = self.get_selected_change_ids()
        if not allowed_ids:
            QMessageBox.information(self, 'Nothing Selected', 'Select at least one promotional finding to scrub.')
            return

        backup = self.chk_backup.isChecked()
        sure = QMessageBox.question(
            self,
            'Confirm Scrubbing',
            confirm_text
        )
        if sure != QMessageBox.StandardButton.Yes:
            return

        self.btn_apply.setEnabled(False)
        self.btn_start.setEnabled(False)
        self.status_label.setText('Applying scrubbing to selected books...')

        self.clean_worker = LibraryCleanWorker(self.scanner, self.scan_results, allowed_ids, backup)
        self.clean_worker.progress.connect(lambda cur, tot, title: self.status_label.setText(f"Scrubbing [{cur}/{tot}]: {title[:40]}..."))
        self.clean_worker.clean_finished.connect(self.on_clean_finished)
        self.clean_worker.clean_error.connect(lambda err: QMessageBox.critical(self, 'Scrubbing Error', err))
        self.clean_worker.start()

    def on_clean_finished(self, summary):
        self.btn_apply.setEnabled(True)
        self.btn_start.setEnabled(True)
        cnt = summary.get('cleaned_count', 0)
        errors = summary.get('errors', [])

        msg = f"Successfully scrubbed {cnt} book(s)!"
        if errors:
            msg += "\n\nEncountered " + str(len(errors)) + " error(s):\n" + "\n".join(errors[:5])

        self.status_label.setText(f"Cleaning complete: {cnt} book(s) updated.")
        QMessageBox.information(self, 'Scrubbing Complete', msg)

        if hasattr(self.parent(), 'current_db'):
            try:
                self.parent().current_db.data.refresh()
                if hasattr(self.parent(), 'library_view'):
                    self.parent().library_view.model().refresh()
            except Exception:
                pass

        self.accept()

    def export_report(self):
        if not self.scan_results:
            QMessageBox.information(self, 'No Report', 'No scan results available to export.')
            return

        path, _ = QFileDialog.getSaveFileName(self, 'Export Library Scan Report', 'promo_scan_report.json', 'JSON Files (*.json)')
        if not path:
            return

        try:
            report_data = {
                'total_scanned_books': len(self.scan_results),
                'books': self.scan_results
            }
            clean_str = json.dumps(report_data, indent=2, default=str)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(clean_str)
            QMessageBox.information(self, 'Export Successful', "Scan report successfully exported to:\n" + str(path))
        except Exception as exc:
            QMessageBox.critical(self, 'Export Failed', "Could not export scan report:\n" + str(exc))
