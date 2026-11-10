import json
from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
                     QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                     QPushButton, QRadioButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
                     Qt, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
try:
    from calibre_plugins.story_collection_tagger.error_handler import show_friendly_error_dialog
except (ImportError, AttributeError):
    try:
        from .error_handler import show_friendly_error_dialog
    except ImportError:
        try:
            from error_handler import show_friendly_error_dialog
        except ImportError:
            from common.error_handler import show_friendly_error_dialog

try:
    from calibre_plugins.story_collection_tagger.ui import TaggerPreviewDialog
except ImportError:
    try:
        from .ui import TaggerPreviewDialog
    except ImportError:
        try:
            from ui import TaggerPreviewDialog
        except ImportError:
            TaggerPreviewDialog = None

try:
    from calibre_plugins.story_collection_tagger import rules as rules_mod
    from calibre_plugins.story_collection_tagger import presets as presets_mod
    from calibre_plugins.story_collection_tagger.config import prefs
    from calibre_plugins.story_collection_tagger.guikit import DialogAction
except ImportError:
    try:
        from . import rules as rules_mod
        from . import presets as presets_mod
        from .config import prefs
        from .guikit import DialogAction
    except ImportError:
        import rules as rules_mod
        try:
            import presets as presets_mod
        except ImportError:
            presets_mod = None
        from config import prefs
        try:
            from guikit import DialogAction
        except ImportError:
            from common.guikit import DialogAction

RULES_HELP = ('One rule per line:  [field:]pattern => Tag, Tag\n'
              'Fields: tags, title, author, series, comments, publisher, source, any (default: title, tags and series).\n'
              'A plain pattern is a case-insensitive substring; "=text" must match exactly; "re:..." is a regular expression.\n'
              'Examples:\n  tags:=Muscle Growth => Theme.Growth\n  source:Royal Road => Format.Web serial\n  re:litrpg|progression => LitRPG')
SITES_HELP = 'Extra sites, one per line:  host, host2 => Name     (for example: storiesonline.net => Stories Online)'


class RuleSetManagerDialog(QDialog):
    def __init__(self, parent=None, current_rules="", current_sites=""):
        super().__init__(parent)
        self.setWindowTitle("Manage Rule Sets")
        self.resize(800, 580)
        self.committed_rules = current_rules
        self.committed_sites = current_sites

        self.local_rules, _ = rules_mod.parse_rules(current_rules)
        self.local_sites, _ = rules_mod.parse_sites(current_sites)

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter rules and sites...")
        self.search_edit.textChanged.connect(self._filter_tables)
        top_row.addWidget(self.search_edit)

        load_btn = QPushButton("Load Rules (Import JSON)...")
        load_btn.clicked.connect(self._load_json)
        top_row.addWidget(load_btn)

        save_btn = QPushButton("Save Rules (Export JSON)...")
        save_btn.clicked.connect(self._save_json)
        top_row.addWidget(save_btn)

        layout.addLayout(top_row)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Rules tab
        rules_widget = QWidget()
        rules_layout = QVBoxLayout(rules_widget)

        rules_controls = QHBoxLayout()
        select_all_rules_btn = QPushButton("Select All")
        select_all_rules_btn.clicked.connect(lambda: self._set_all_checked(self.rules_table, True))
        deselect_all_rules_btn = QPushButton("Deselect All")
        deselect_all_rules_btn.clicked.connect(lambda: self._set_all_checked(self.rules_table, False))
        add_rule_btn = QPushButton("Add Rule")
        add_rule_btn.clicked.connect(self._add_rule_row)
        delete_rule_btn = QPushButton("Delete Selected")
        delete_rule_btn.clicked.connect(lambda: self._delete_selected_rows(self.rules_table))

        rules_controls.addWidget(select_all_rules_btn)
        rules_controls.addWidget(deselect_all_rules_btn)
        rules_controls.addWidget(add_rule_btn)
        rules_controls.addWidget(delete_rule_btn)
        rules_controls.addStretch()
        rules_layout.addLayout(rules_controls)

        self.rules_table = QTableWidget()
        self.rules_table.setColumnCount(5)
        self.rules_table.setHorizontalHeaderLabels(["Include", "Field", "Pattern", "Target Tags", "Status"])
        if hasattr(QHeaderView, 'ResizeMode'):
            self.rules_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.rules_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self.rules_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.rules_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            self.rules_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.rules_table.itemChanged.connect(self._on_item_changed)
        rules_layout.addWidget(self.rules_table)

        self.tabs.addTab(rules_widget, "Keyword Rules")

        # Sites tab
        sites_widget = QWidget()
        sites_layout = QVBoxLayout(sites_widget)

        sites_controls = QHBoxLayout()
        select_all_sites_btn = QPushButton("Select All")
        select_all_sites_btn.clicked.connect(lambda: self._set_all_checked(self.sites_table, True))
        deselect_all_sites_btn = QPushButton("Deselect All")
        deselect_all_sites_btn.clicked.connect(lambda: self._set_all_checked(self.sites_table, False))
        add_site_btn = QPushButton("Add Site")
        add_site_btn.clicked.connect(self._add_site_row)
        delete_site_btn = QPushButton("Delete Selected")
        delete_site_btn.clicked.connect(lambda: self._delete_selected_rows(self.sites_table))

        sites_controls.addWidget(select_all_sites_btn)
        sites_controls.addWidget(deselect_all_sites_btn)
        sites_controls.addWidget(add_site_btn)
        sites_controls.addWidget(delete_site_btn)
        sites_controls.addStretch()
        sites_layout.addLayout(sites_controls)

        self.sites_table = QTableWidget()
        self.sites_table.setColumnCount(4)
        self.sites_table.setHorizontalHeaderLabels(["Include", "Hosts", "Site Name", "Status"])
        if hasattr(QHeaderView, 'ResizeMode'):
            self.sites_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.sites_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self.sites_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.sites_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.sites_table.itemChanged.connect(self._on_item_changed)
        sites_layout.addWidget(self.sites_table)

        self.tabs.addTab(sites_widget, "Source Sites")

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate_initial()

    def _populate_initial(self):
        self.rules_table.blockSignals(True)
        self.sites_table.blockSignals(True)

        self.rules_table.setRowCount(0)
        for r in self.local_rules:
            self._insert_rule_row(r.field, r.pattern, r.tags, checked=True, status="Existing")

        self.sites_table.setRowCount(0)
        for name, hosts, _ in self.local_sites:
            self._insert_site_row(hosts, name, checked=True, status="Existing")

        self.rules_table.blockSignals(False)
        self.sites_table.blockSignals(False)
        self._update_counts()

    def _insert_rule_row(self, field, pattern, tags, checked=True, status="Existing"):
        row = self.rules_table.rowCount()
        self.rules_table.insertRow(row)

        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        chk.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.rules_table.setItem(row, 0, chk)

        fld_str = field if field else "(default)"
        self.rules_table.setItem(row, 1, QTableWidgetItem(fld_str))
        self.rules_table.setItem(row, 2, QTableWidgetItem(pattern or ""))

        tags_str = ", ".join(tags) if isinstance(tags, (list, tuple)) else str(tags or "")
        self.rules_table.setItem(row, 3, QTableWidgetItem(tags_str))
        self.rules_table.setItem(row, 4, QTableWidgetItem(status))

    def _insert_site_row(self, hosts, name, checked=True, status="Existing"):
        row = self.sites_table.rowCount()
        self.sites_table.insertRow(row)

        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        chk.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.sites_table.setItem(row, 0, chk)

        hosts_str = ", ".join(hosts) if isinstance(hosts, (list, tuple)) else str(hosts or "")
        self.sites_table.setItem(row, 1, QTableWidgetItem(hosts_str))
        self.sites_table.setItem(row, 2, QTableWidgetItem(name or ""))
        self.sites_table.setItem(row, 3, QTableWidgetItem(status))

    def _add_rule_row(self):
        self._insert_rule_row("", "", "", checked=True, status="New")
        self._update_counts()

    def _add_site_row(self):
        self._insert_site_row([], "", checked=True, status="New")
        self._update_counts()

    def _delete_selected_rows(self, table):
        selected_rows = sorted(set(idx.row() for idx in table.selectedIndexes()), reverse=True)
        table.blockSignals(True)
        for row in selected_rows:
            table.removeRow(row)
        table.blockSignals(False)
        self._update_counts()

    def _set_all_checked(self, table, checked):
        table.blockSignals(True)
        for row in range(table.rowCount()):
            if not table.isRowHidden(row):
                item = table.item(row, 0)
                if item:
                    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        table.blockSignals(False)
        self._update_counts()

    def _on_item_changed(self, item):
        if item and item.column() == 0:
            self._update_counts()

    def _update_counts(self):
        rule_total = self.rules_table.rowCount()
        rule_checked = sum(1 for r in range(rule_total) if self.rules_table.item(r, 0) and self.rules_table.item(r, 0).checkState() == Qt.CheckState.Checked)

        site_total = self.sites_table.rowCount()
        site_checked = sum(1 for r in range(site_total) if self.sites_table.item(r, 0) and self.sites_table.item(r, 0).checkState() == Qt.CheckState.Checked)

        self.tabs.setTabText(0, f"Keyword Rules ({rule_checked}/{rule_total})")
        self.tabs.setTabText(1, f"Source Sites ({site_checked}/{site_total})")
        self.status_label.setText(f"Selected for configuration: {rule_checked} rule(s), {site_checked} site(s).")

    def _filter_tables(self, text):
        text = text.casefold()
        for table in (self.rules_table, self.sites_table):
            for r in range(table.rowCount()):
                match = False
                if not text:
                    match = True
                else:
                    for c in range(1, table.columnCount()):
                        item = table.item(r, c)
                        if item and text in item.text().casefold():
                            match = True
                            break
                table.setRowHidden(r, not match)

    def _load_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Rule Set JSON", "", "JSON Files (*.json);;All Files (*)")
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to read or parse JSON file:\n{exc}")
            return

        parsed_rules, parsed_sites, errors = rules_mod.import_rule_set_data(data)
        if errors:
            QMessageBox.warning(self, "Rule Validation Error", "The rule set file contains invalid or malformed rules and cannot be imported:\n\n" + "\n".join(errors))
            return

        self.rules_table.blockSignals(True)
        self.sites_table.blockSignals(True)
        self.rules_table.setUpdatesEnabled(False)
        self.sites_table.setUpdatesEnabled(False)

        new_rules = 0
        conflict_rules = 0
        for r in parsed_rules:
            status = rules_mod.classify_rule(r, self.local_rules)
            if status == 'New':
                self._insert_rule_row(r.field, r.pattern, r.tags, checked=True, status="New")
                new_rules += 1
            elif status == 'Conflict':
                self._insert_rule_row(r.field, r.pattern, r.tags, checked=False, status="Conflict (differs)")
                conflict_rules += 1
            elif status == 'Duplicate':
                self._insert_rule_row(r.field, r.pattern, r.tags, checked=False, status="Duplicate")

        for s in parsed_sites:
            name, hosts, _ = s
            status = rules_mod.classify_site(s, self.local_sites)
            if status == 'New':
                self._insert_site_row(hosts, name, checked=True, status="New")
            elif status == 'Conflict':
                self._insert_site_row(hosts, name, checked=False, status="Conflict (differs)")
            elif status == 'Duplicate':
                self._insert_site_row(hosts, name, checked=False, status="Duplicate")

        self.rules_table.setUpdatesEnabled(True)
        self.sites_table.setUpdatesEnabled(True)
        self.rules_table.blockSignals(False)
        self.sites_table.blockSignals(False)

        self._update_counts()
        QMessageBox.information(self, "Rule Set Loaded", f"Loaded {len(parsed_rules)} rule(s) and {len(parsed_sites)} site(s).\n\n"
                                                         f"New rules added: {new_rules}\nConflicts flagged: {conflict_rules}")

    def _save_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Rule Set JSON", "rule_set.json", "JSON Files (*.json);;All Files (*)")
        if not path:
            return

        rules_text, sites_text = self._get_checked_rules_and_sites_text()
        data = rules_mod.export_rule_set_data(rules_text, sites_text)

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            QMessageBox.information(self, "Export Successful", f"Rule set exported successfully to:\n{path}")
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Failed to save JSON file:\n{exc}")

    def _get_checked_rules_and_sites_text(self):
        rule_lines = []
        for r in range(self.rules_table.rowCount()):
            chk = self.rules_table.item(r, 0)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                fld_item = self.rules_table.item(r, 1)
                pat_item = self.rules_table.item(r, 2)
                tags_item = self.rules_table.item(r, 3)

                fld = fld_item.text().strip() if fld_item else ""
                pat = pat_item.text().strip() if pat_item else ""
                tags = tags_item.text().strip() if tags_item else ""

                if not pat or not tags:
                    continue

                field_prefix = f"{fld}:" if fld and fld != "(default)" else ""
                rule_lines.append(f"{field_prefix}{pat} => {tags}")

        site_lines = []
        for r in range(self.sites_table.rowCount()):
            chk = self.sites_table.item(r, 0)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                hosts_item = self.sites_table.item(r, 1)
                name_item = self.sites_table.item(r, 2)

                hosts = hosts_item.text().strip() if hosts_item else ""
                name = name_item.text().strip() if name_item else ""

                if not hosts or not name:
                    continue

                site_lines.append(f"{hosts} => {name}")

        return "\n".join(rule_lines), "\n".join(site_lines)

    def _on_accept(self):
        rules_text, sites_text = self._get_checked_rules_and_sites_text()
        _, rule_errors = rules_mod.parse_rules(rules_text)
        _, site_errors = rules_mod.parse_sites(sites_text)
        all_errors = rule_errors + site_errors

        if all_errors:
            QMessageBox.warning(self, "Invalid Rules", f"Cannot commit configuration due to rule syntax errors:\n\n" + "\n".join(all_errors))
            return

        self.committed_rules = rules_text
        self.committed_sites = sites_text
        self.accept()

    def get_committed_rules_and_sites(self):
        return self.committed_rules, self.committed_sites


class TaggerAction(DialogAction):
    name = 'Story Collection Tagger'
    action_spec = ('Auto-tag stories', None, 'Add source-site and keyword-rule tags to books', None)

    def books_for(self, ids):
        db = self.gui.current_db.new_api
        books = []
        for i, book_id in enumerate(ids):
            mi = db.get_metadata(book_id, get_cover=False)
            books.append({'id': book_id, 'title': mi.title, 'authors': list(mi.authors or []), 'tags': list(mi.tags or []),
                          'identifiers': dict(mi.identifiers or {}), 'publisher': mi.publisher or '',
                          'comments': mi.comments or '', 'series': mi.series or ''})
            if i % 200 == 0:
                QApplication.processEvents()
        return books

    def show_dialog(self):
        selected = list(self.gui.library_view.get_selected_ids())
        dialog = QDialog(self.gui)
        dialog.setWindowTitle('Auto-tag stories')
        dialog.resize(780, 750)
        dialog.setMinimumWidth(680)
        layout = QVBoxLayout(dialog)

        intro_label = QLabel('Only adds tags; existing tags are never removed or renamed. Nothing changes until you press Apply.')
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        # Tabs
        tabs = QTabWidget()
        layout.addWidget(tabs, 4)

        # ----------------------------------------------------
        # TAB 1: Structured Rule Builder
        # ----------------------------------------------------
        tab_builder = QWidget()
        tab_builder_layout = QVBoxLayout(tab_builder)

        # Active Rules Table
        rules_table = QTableWidget()
        rules_table.setColumnCount(5)
        rules_table.setHorizontalHeaderLabels(['Field', 'Match Mode', 'Pattern', 'Target Tags', 'Actions'])
        header = rules_table.horizontalHeader()
        if hasattr(header, 'setSectionResizeMode'):
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        tab_builder_layout.addWidget(rules_table, 3)

        # Rule Builder Form Box
        form_box = QGroupBox("Visual Rule Builder Form")
        form_layout = QVBoxLayout(form_box)

        form_row1 = QHBoxLayout()
        form_row1.addWidget(QLabel("Field:"))
        field_combo = QComboBox()
        field_combo.addItems(['any', 'title', 'tags', 'series', 'authors', 'comments', 'publisher', 'source'])
        form_row1.addWidget(field_combo)

        form_row1.addWidget(QLabel("Match Mode:"))
        mode_combo = QComboBox()
        mode_combo.addItems(['Substring', 'Exact', 'Regex'])
        form_row1.addWidget(mode_combo)
        form_layout.addLayout(form_row1)

        form_row2 = QHBoxLayout()
        form_row2.addWidget(QLabel("Pattern:"))
        pat_edit = QLineEdit()
        pat_edit.setPlaceholderText("Target pattern (e.g., litrpg|progression)")
        form_row2.addWidget(pat_edit)

        form_row2.addWidget(QLabel("Target Tags:"))
        tags_edit = QLineEdit()
        tags_edit.setPlaceholderText("Comma-separated tags (e.g., Genre.LitRPG, Trope.Isekai)")
        form_row2.addWidget(tags_edit)
        form_layout.addLayout(form_row2)

        val_label = QLabel("")
        form_layout.addWidget(val_label)

        form_btns = QHBoxLayout()
        add_rule_btn = QPushButton("Add Rule")
        clear_btn = QPushButton("Clear")
        manage_btn = QPushButton("Manage Rule Sets...")
        form_btns.addWidget(add_rule_btn)
        form_btns.addWidget(clear_btn)
        form_btns.addWidget(manage_btn)
        form_btns.addStretch()
        form_layout.addLayout(form_btns)

        tab_builder_layout.addWidget(form_box)

        # Raw Text rules editor collapsible/toggle view
        raw_box = QGroupBox("Raw Text Rules (Legacy Format / Raw Text Editor)")
        raw_layout = QVBoxLayout(raw_box)
        rules_edit = QPlainTextEdit(prefs['rules'])
        rules_edit.setPlaceholderText(RULES_HELP)
        raw_layout.addWidget(rules_edit)
        tab_builder_layout.addWidget(raw_box, 2)

        tabs.addTab(tab_builder, "Structured Rule Builder")

        # ----------------------------------------------------
        # TAB 2: Preset Library Manager
        # ----------------------------------------------------
        tab_presets = QWidget()
        tab_presets_layout = QVBoxLayout(tab_presets)

        splitter = QSplitter()

        # Left pane: preset packs list
        preset_list_table = QTableWidget()
        preset_list_table.setColumnCount(2)
        preset_list_table.setHorizontalHeaderLabels(['Category', 'Preset Pack'])
        if hasattr(preset_list_table.horizontalHeader(), 'setSectionResizeMode'):
            preset_list_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            preset_list_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        splitter.addWidget(preset_list_table)

        # Right pane: preset preview details
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        preset_title_label = QLabel("<b>Select a Preset Pack to preview</b>")
        preset_desc_label = QLabel("")
        preset_desc_label.setWordWrap(True)
        preset_counts_label = QLabel("")

        preset_preview_edit = QPlainTextEdit()
        preset_preview_edit.setReadOnly(True)
        preset_preview_edit.setPlaceholderText("Pack rules and custom sites will appear here.")

        merge_preset_btn = QPushButton("Merge Preset Pack into Active Rules")
        merge_preset_btn.setEnabled(False)

        right_layout.addWidget(preset_title_label)
        right_layout.addWidget(preset_desc_label)
        right_layout.addWidget(preset_counts_label)
        right_layout.addWidget(preset_preview_edit, 1)
        right_layout.addWidget(merge_preset_btn)

        splitter.addWidget(right_widget)
        tab_presets_layout.addWidget(splitter, 1)

        # Import/Export bar
        impexp_layout = QHBoxLayout()
        import_preset_btn = QPushButton("Import Preset Pack (JSON)...")
        export_preset_btn = QPushButton("Export Rules as Preset Pack (JSON)...")
        impexp_layout.addWidget(import_preset_btn)
        impexp_layout.addWidget(export_preset_btn)
        impexp_layout.addStretch()
        tab_presets_layout.addLayout(impexp_layout)

        tabs.addTab(tab_presets, "Preset Library")

        # ----------------------------------------------------
        # TAB 3: Settings & Custom Sites
        # ----------------------------------------------------
        tab_settings = QWidget()
        tab_settings_layout = QVBoxLayout(tab_settings)

        row = QHBoxLayout()
        add_source = QCheckBox('Add a source-site tag, prefix:')
        add_source.setChecked(bool(prefs['add_source']))
        prefix = QLineEdit(prefs['source_prefix'])
        prefix.setToolTip('Use "Source." to nest under "Source" when Calibre\'s hierarchical tag browser is enabled for tags.')
        row.addWidget(add_source)
        row.addWidget(prefix)
        tab_settings_layout.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel('Tag books in a series, prefix (blank = off):'))
        series_prefix = QLineEdit(prefs['series_prefix'])
        series_prefix.setPlaceholderText('Series.')
        row.addWidget(series_prefix)
        tab_settings_layout.addLayout(row)

        scan = QCheckBox('Also look for site addresses inside book descriptions (may over-match)')
        scan.setChecked(bool(prefs['scan_comments']))
        tab_settings_layout.addWidget(scan)


        sites_label = QLabel('Additional source sites')
        sites_label.setWordWrap(True)
        tab_settings_layout.addWidget(sites_label)

        sites_edit = QPlainTextEdit(prefs['sites'])
        sites_edit.setPlaceholderText(SITES_HELP)
        tab_settings_layout.addWidget(sites_edit, 1)

        builtin_label = QLabel('Built-in sites: ' + ', '.join(s[0] for s in rules_mod.DEFAULT_SITES))
        builtin_label.setWordWrap(True)
        tab_settings_layout.addWidget(builtin_label)

        tabs.addTab(tab_settings, "Settings & Sites")

        # Scope buttons
        scope_row = QHBoxLayout()
        scope_selected = QRadioButton(f'Selected books ({len(selected)})')
        scope_all = QRadioButton('Entire library')
        (scope_selected if selected else scope_all).setChecked(True)
        scope_selected.setEnabled(bool(selected))
        scope_row.addWidget(scope_selected)
        scope_row.addWidget(scope_all)
        scope_row.addStretch()
        layout.addLayout(scope_row)

        summary_label = QLabel('Press Preview to see which books would get which tags.')
        layout.addWidget(summary_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel('Global Tag Rules'))
        global_tree = QTreeWidget()
        global_tree.setHeaderLabels(['Proposed Tag', 'Books'])
        left_layout.addWidget(global_tree)

        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel('Book Tag Overrides'))
        books_tree = QTreeWidget()
        books_tree.setHeaderLabels(['Book / Tag'])
        right_layout.addWidget(books_tree)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([260, 420])
        layout.addWidget(splitter, 3)

        rule_set_box = QHBoxLayout()
        export_btn = QPushButton('Export Rule Set')
        import_btn = QPushButton('Import Rule Set')
        rule_set_box.addWidget(export_btn)
        rule_set_box.addWidget(import_btn)
        rule_set_box.addStretch()
        tab_settings_layout.addLayout(rule_set_box)
        buttons = QDialogButtonBox()
        preview_btn = QPushButton('Preview')
        apply_btn = QPushButton('Apply')
        apply_btn.setEnabled(False)
        buttons.addButton(preview_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        # Internal State Management
        state = {
            'selection': None,
            'total_ids': 0,
            'updating': False,
            'active_rules': presets_mod.text_to_rule_dicts(rules_edit.toPlainText()),
            'editing_index': -1,
            'bundled_presets': presets_mod.load_bundled_presets(),
            'syncing': False
        }

        # --- Helper functions ---
        def populate_table_from_rules():
            if state['syncing']:
                return
            state['syncing'] = True
            rules_table.setRowCount(0)
            for idx, r_dict in enumerate(state['active_rules']):
                rules_table.insertRow(idx)

                field_item = QTableWidgetItem(r_dict.get('field', 'any'))
                mode_item = QTableWidgetItem((r_dict.get('match_mode') or 'substring').title())
                pat_item = QTableWidgetItem(r_dict.get('pattern', ''))
                tags_list = r_dict.get('tags') or []
                tags_item = QTableWidgetItem(", ".join(tags_list) if isinstance(tags_list, list) else str(tags_list))

                rules_table.setItem(idx, 0, field_item)
                rules_table.setItem(idx, 1, mode_item)
                rules_table.setItem(idx, 2, pat_item)
                rules_table.setItem(idx, 3, tags_item)

                btn_widget = QWidget()
                btn_layout = QHBoxLayout(btn_widget)
                btn_layout.setContentsMargins(2, 2, 2, 2)

                edit_btn = QPushButton("Edit")
                del_btn = QPushButton("Del")

                def make_edit_cb(row_idx):
                    return lambda: on_edit_rule(row_idx)

                def make_del_cb(row_idx):
                    return lambda: on_delete_rule(row_idx)

                edit_btn.clicked.connect(make_edit_cb(idx))
                del_btn.clicked.connect(make_del_cb(idx))

                btn_layout.addWidget(edit_btn)
                btn_layout.addWidget(del_btn)
                rules_table.setCellWidget(idx, 4, btn_widget)

            raw_text = presets_mod.rules_to_text(state['active_rules'])
            rules_edit.setPlainText(raw_text)
            state['syncing'] = False

        def validate_form():
            field = field_combo.currentText().strip()
            mode = mode_combo.currentText().strip().lower()
            pattern = pat_edit.text().strip()
            raw_tags = tags_edit.text().strip()
            tags = [t.strip() for t in raw_tags.split(',') if t.strip()]

            r_dict = {'field': field, 'match_mode': mode, 'pattern': pattern, 'tags': tags}
            ok, err = presets_mod.validate_rule_dict(r_dict)

            if ok:
                val_label.setText("<font color='green'>✓ Valid rule format</font>")
                add_rule_btn.setEnabled(True)
            else:
                val_label.setText(f"<font color='red'>⚠ {err}</font>")
                add_rule_btn.setEnabled(False)

        def clear_form():
            state['editing_index'] = -1
            pat_edit.clear()
            tags_edit.clear()
            field_combo.setCurrentIndex(0)
            mode_combo.setCurrentIndex(0)
            add_rule_btn.setText("Add Rule")
            validate_form()

        def on_add_or_update_rule():
            field = field_combo.currentText().strip()
            mode = mode_combo.currentText().strip().lower()
            pattern = pat_edit.text().strip()
            raw_tags = tags_edit.text().strip()
            tags = [t.strip() for t in raw_tags.split(',') if t.strip()]

            r_dict = {'field': field, 'match_mode': mode, 'pattern': pattern, 'tags': tags}
            ok, _ = presets_mod.validate_rule_dict(r_dict)
            if not ok:
                return

            if state['editing_index'] >= 0 and state['editing_index'] < len(state['active_rules']):
                state['active_rules'][state['editing_index']] = r_dict
            else:
                state['active_rules'].append(r_dict)

            clear_form()
            populate_table_from_rules()

        def on_edit_rule(idx):
            if idx < 0 or idx >= len(state['active_rules']):
                return
            state['editing_index'] = idx
            r_dict = state['active_rules'][idx]

            f_idx = field_combo.findText(r_dict.get('field', 'any'))
            if f_idx >= 0:
                field_combo.setCurrentIndex(f_idx)

            mode_str = (r_dict.get('match_mode') or 'substring').title()
            m_idx = mode_combo.findText(mode_str)
            if m_idx >= 0:
                mode_combo.setCurrentIndex(m_idx)

            pat_edit.setText(r_dict.get('pattern', ''))
            tags_list = r_dict.get('tags') or []
            tags_edit.setText(", ".join(tags_list) if isinstance(tags_list, list) else str(tags_list))

            add_rule_btn.setText("Save Changes")
            validate_form()

        def on_delete_rule(idx):
            if idx < 0 or idx >= len(state['active_rules']):
                return
            del state['active_rules'][idx]
            if state['editing_index'] == idx:
                clear_form()
            elif state['editing_index'] > idx:
                state['editing_index'] -= 1
            populate_table_from_rules()

        def on_raw_text_changed():
            if state['syncing']:
                return
            state['syncing'] = True
            raw_text = rules_edit.toPlainText()
            state['active_rules'] = presets_mod.text_to_rule_dicts(raw_text)
            state['syncing'] = False
            populate_table_from_rules()

        def populate_preset_list():
            preset_list_table.setRowCount(0)
            for idx, p in enumerate(state['bundled_presets']):
                preset_list_table.insertRow(idx)
                cat_item = QTableWidgetItem(p.get('category', 'Custom'))
                name_item = QTableWidgetItem(p.get('name', 'Preset Pack'))
                preset_list_table.setItem(idx, 0, cat_item)
                preset_list_table.setItem(idx, 1, name_item)

        def on_preset_selected():
            row_idx = preset_list_table.currentRow()
            if row_idx < 0 or row_idx >= len(state['bundled_presets']):
                merge_preset_btn.setEnabled(False)
                return

            p = state['bundled_presets'][row_idx]
            preset_title_label.setText(f"<b>{p.get('name', 'Preset Pack')}</b>")
            preset_desc_label.setText(f"Category: {p.get('category', 'General')}\n{p.get('description', '')}")

            rules_count = len(p.get('rules', []))
            sites_count = len(p.get('sites', []))
            preset_counts_label.setText(f"Pack contains: {rules_count} rule(s), {sites_count} custom site definition(s)")

            lines = ["--- RULES ---"]
            for r in p.get('rules', []):
                lines.append(presets_mod.rule_dict_to_string(r))
            if p.get('sites'):
                lines.append("\n--- SITES ---")
                lines.append(presets_mod.sites_to_text(p.get('sites')))

            preset_preview_edit.setPlainText("\n".join(lines))
            merge_preset_btn.setEnabled(True)

        def do_merge_preset(preset_pack=None):
            if preset_pack is None:
                row_idx = preset_list_table.currentRow()
                if row_idx < 0 or row_idx >= len(state['bundled_presets']):
                    return
                preset_pack = state['bundled_presets'][row_idx]

            active_rules_txt = rules_edit.toPlainText()
            active_sites_txt = sites_edit.toPlainText()

            up_rules_txt, up_sites_txt, r_added, s_added = presets_mod.merge_preset_into_active(
                active_rules_txt, active_sites_txt, preset_pack
            )

            rules_edit.setPlainText(up_rules_txt)
            sites_edit.setPlainText(up_sites_txt)

            state['active_rules'] = presets_mod.text_to_rule_dicts(up_rules_txt)
            populate_table_from_rules()

            info_dialog(self.gui, "Preset Merged",
                        f"Merged preset '{preset_pack.get('name')}' into active preferences.\n\n"
                        f"Rules added: {r_added}\nSites added: {s_added}\n"
                        f"Redundant or duplicate rules were automatically skipped.",
                        show=True)

        def do_import_preset():
            filepath, _ = QFileDialog.getOpenFileName(dialog, "Import Preset Pack (JSON)", "", "JSON Files (*.json)")
            if not filepath:
                return
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                preset_pack = presets_mod.parse_preset_json(content)
                do_merge_preset(preset_pack)
            except Exception as exc:
                error_dialog(self.gui, "Import Failed", f"Could not parse preset JSON file: {exc}", show=True)

        def do_export_preset():
            filepath, _ = QFileDialog.getSaveFileName(dialog, "Export Rules as Preset Pack (JSON)", "custom_preset.json", "JSON Files (*.json)")
            if not filepath:
                return
            try:
                active_rules = state['active_rules']
                active_sites, _ = rules_mod.parse_sites(sites_edit.toPlainText())
                json_str = presets_mod.export_preset_json(
                    name="Custom User Rules",
                    category="Custom",
                    description="User exported rule configuration and extra site definitions.",
                    rule_dicts=active_rules,
                    site_tuples=active_sites
                )
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(json_str)
                info_dialog(self.gui, "Preset Exported", f"Successfully exported preset configuration to:\n{filepath}", show=True)
            except Exception as exc:
                error_dialog(self.gui, "Export Failed", f"Could not export preset file: {exc}", show=True)

        def save_prefs():
            prefs['add_source'] = add_source.isChecked()
            prefs['source_prefix'] = prefix.text()
            prefs['series_prefix'] = series_prefix.text()
            prefs['scan_comments'] = scan.isChecked()
            prefs['rules'] = rules_edit.toPlainText()
            prefs['sites'] = sites_edit.toPlainText()

        def update_summary():
            sel = state.get('selection')
            if not sel or not sel.plan:
                summary_label.setText('0 of 0 book(s) will change.')
                apply_btn.setEnabled(False)
                return
            active_assignments, affected_books = sel.get_summary_counts()
            summary_label.setText(
                f'{affected_books} of {state["total_ids"]} book(s) will change ({active_assignments} active tag assignment(s)).'
            )
            apply_btn.setEnabled(affected_books > 0)

        def on_global_item_changed(item, column):
            if state['updating']:
                return
            tag = getattr(item, '_tag_name', None)
            if tag is None:
                return
            checked = (item.checkState(0) == Qt.CheckState.Checked)
            sel = state.get('selection')
            if sel:
                sel.set_global_tag(tag, checked)

            state['updating'] = True
            try:
                root = books_tree.invisibleRootItem()
                for i in range(root.childCount()):
                    book_item = root.child(i)
                    for j in range(book_item.childCount()):
                        tag_item = book_item.child(j)
                        if getattr(tag_item, '_tag', None) == tag:
                            tag_item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            finally:
                state['updating'] = False

            update_summary()

        def on_books_item_changed(item, column):
            if state['updating']:
                return
            sel = state.get('selection')
            if not sel:
                return

            tag = getattr(item, '_tag', None)
            book_id = getattr(item, '_book_id', None)

            state['updating'] = True
            try:
                if tag is not None and book_id is not None:
                    checked = (item.checkState(0) == Qt.CheckState.Checked)
                    sel.set_book_tag(book_id, tag, checked)
                elif book_id is not None and item.childCount() > 0:
                    checked = (item.checkState(0) == Qt.CheckState.Checked)
                    for j in range(item.childCount()):
                        tag_item = item.child(j)
                        tag_item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                        child_tag = getattr(tag_item, '_tag', None)
                        if child_tag:
                            sel.set_book_tag(book_id, child_tag, checked)
            finally:
                state['updating'] = False

            update_summary()

        global_tree.itemChanged.connect(on_global_item_changed)
        books_tree.itemChanged.connect(on_books_item_changed)

        def do_manage():
            dlg = RuleSetManagerDialog(dialog, current_rules=rules_edit.toPlainText(), current_sites=sites_edit.toPlainText())
            res = dlg.exec()
            accepted_val = getattr(QDialog.DialogCode, 'Accepted', 1)
            if res in (accepted_val, 1):
                new_rules, new_sites = dlg.get_committed_rules_and_sites()
                prefs['rules'] = new_rules
                prefs['sites'] = new_sites
                rules_edit.setPlainText(new_rules)
                sites_edit.setPlainText(new_sites)
                state['active_rules'] = presets_mod.text_to_rule_dicts(new_rules)
                populate_table_from_rules()
                invalidate_preview()

        manage_btn.clicked.connect(do_manage)

        def do_export():
            path, _ = QFileDialog.getSaveFileName(dialog, 'Export Rule Set', 'tagger_rules.json', 'JSON files (*.json);;All files (*)')
            if not path:
                return
            package = rules_mod.export_rule_set_package(
                rules_text=rules_edit.toPlainText(),
                sites_text=sites_edit.toPlainText(),
                add_source=add_source.isChecked(),
                source_prefix=prefix.text(),
                series_prefix=series_prefix.text(),
                scan_comments=scan.isChecked(),
            )
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(package, f, indent=2)
                info_dialog(dialog, 'Export Rule Set', 'Rule set exported successfully.', show=True)
            except Exception as exc:
                show_friendly_error_dialog(dialog, 'Export Rule Set', exc, det_msg='Could not write export file.', show=True)

        def do_import():
            path, _ = QFileDialog.getOpenFileName(dialog, 'Import Rule Set', '', 'JSON files (*.json);;All files (*)')
            if not path:
                return
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError('Imported file must contain a JSON object.')
            except Exception as exc:
                show_friendly_error_dialog(dialog, 'Import Rule Set', exc, det_msg='Could not read or parse the JSON file.', show=True)
                return

            imported_rules = data.get('rules', '')
            if isinstance(imported_rules, list):
                imported_rules = '\n'.join(str(r) for r in imported_rules)
            elif not isinstance(imported_rules, str):
                imported_rules = str(imported_rules or '')

            imported_sites = data.get('sites', '')
            if isinstance(imported_sites, list):
                imported_sites = '\n'.join(str(s) for s in imported_sites)
            elif not isinstance(imported_sites, str):
                imported_sites = str(imported_sites or '')

            msg = QMessageBox(dialog)
            msg.setWindowTitle('Import Rule Set')
            msg.setText('Choose how to apply the imported rule set:')
            msg.setInformativeText('Merge: Combine imported rules and sites with current active preferences.\n'
                                   'Replace: Overwrite current active rules, sites, and prefix settings.')
            merge_btn = msg.addButton('Merge', QMessageBox.ButtonRole.AcceptRole)
            replace_btn = msg.addButton('Replace', QMessageBox.ButtonRole.DestructiveRole)
            cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)
            msg.exec()

            clicked = msg.clickedButton()
            if clicked == cancel_btn or clicked is None:
                return

            mode = 'merge' if clicked == merge_btn else 'replace'

            if mode == 'merge':
                target_rules = rules_mod.merge_text_lines(rules_edit.toPlainText(), imported_rules)
                target_sites = rules_mod.merge_text_lines(sites_edit.toPlainText(), imported_sites)
            else:
                target_rules = imported_rules
                target_sites = imported_sites

            errors = rules_mod.validate_rule_set(target_rules, target_sites)
            if errors:
                show_friendly_error_dialog(
                    dialog,
                    'Import Rule Set - Syntax Error',
                    'The rule set contains invalid syntax and was not applied:',
                    det_msg='\n'.join(errors),
                    show=True
                )
                return

            rules_edit.setPlainText(target_rules)
            sites_edit.setPlainText(target_sites)

            if mode == 'replace':
                if 'add_source' in data:
                    add_source.setChecked(bool(data['add_source']))
                if 'source_prefix' in data:
                    prefix.setText(str(data['source_prefix']))
                if 'series_prefix' in data:
                    series_prefix.setText(str(data['series_prefix']))
                if 'scan_comments' in data:
                    scan.setChecked(bool(data['scan_comments']))

            save_prefs()
            info_dialog(dialog, 'Import Rule Set', 'Rule set imported successfully.', show=True)

        export_btn.clicked.connect(do_export)
        import_btn.clicked.connect(do_import)

        def do_preview():
            save_prefs()
            parsed, rule_errors = rules_mod.parse_rules(rules_edit.toPlainText())
            extra, site_errors = rules_mod.parse_sites(sites_edit.toPlainText())
            errors = rule_errors + site_errors

            state['updating'] = True
            global_tree.clear()
            books_tree.clear()
            state['updating'] = False

            if errors:
                summary_label.setText('Fix these first:\n' + '\n'.join(errors))
                state['selection'] = None
                apply_btn.setEnabled(False)
                return

            ids = selected if scope_selected.isChecked() else sorted(self.gui.current_db.new_api.all_book_ids())
            state['total_ids'] = len(ids)
            plan = rules_mod.plan_changes(self.books_for(ids), prefix.text(), add_source.isChecked(), series_prefix.text(), parsed,
                                          rules_mod.merge_sites(rules_mod.DEFAULT_SITES, extra), scan.isChecked())

            sel = rules_mod.TagSelectionState(plan)
            state['selection'] = sel

            state['updating'] = True
            try:
                for tag, count in sel.get_distinct_tags():
                    g_item = QTreeWidgetItem([tag, str(count)])
                    g_item.setFlags(g_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    g_item.setCheckState(0, Qt.CheckState.Checked)
                    g_item._tag_name = tag
                    global_tree.addTopLevelItem(g_item)

                for p in plan:
                    b_item = QTreeWidgetItem([p['title']])
                    b_item.setFlags(b_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    b_item.setCheckState(0, Qt.CheckState.Checked)
                    b_item._book_id = p['id']
                    for tag in p.get('add', []):
                        t_item = QTreeWidgetItem([tag])
                        t_item.setFlags(t_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        t_item.setCheckState(0, Qt.CheckState.Checked)
                        t_item._book_id = p['id']
                        t_item._tag = tag
                        b_item.addChild(t_item)
                    books_tree.addTopLevelItem(b_item)

                books_tree.expandAll()
            finally:
                state['updating'] = False

            update_summary()

        def do_apply():
            sel = state.get('selection')
            if not sel:
                return
            active_plan = sel.get_active_plan()
            if not active_plan:
                return
            try:
                db = self.gui.current_db.new_api
                db.set_field('tags', {p['id']: tuple(p['tags']) for p in active_plan})
                self.gui.library_view.model().refresh_ids([p['id'] for p in active_plan])
                self.gui.tags_view.recount()
            except Exception as exc:
                return show_friendly_error_dialog(self.gui, 'Auto-tag', exc, det_msg=f'Could not apply the tags to {len(active_plan)} book(s)', show=True)
            info_dialog(self.gui, 'Auto-tag', f'Tags added to {len(active_plan)} book(s).', show=True)
            dialog.accept()

        def invalidate_preview(*args):
            if not state.get('selection'):
                return
            state['selection'] = None
            apply_btn.setEnabled(False)
            state['updating'] = True
            global_tree.clear()
            books_tree.clear()
            state['updating'] = False
            summary_label.setText('0 of 0 book(s) will change.')

        # Connect signals
        pat_edit.textChanged.connect(validate_form)
        tags_edit.textChanged.connect(validate_form)
        mode_combo.currentIndexChanged.connect(validate_form)
        field_combo.currentIndexChanged.connect(validate_form)

        add_rule_btn.clicked.connect(on_add_or_update_rule)
        clear_btn.clicked.connect(clear_form)
        rules_edit.textChanged.connect(on_raw_text_changed)

        preset_list_table.itemSelectionChanged.connect(on_preset_selected)
        merge_preset_btn.clicked.connect(lambda: do_merge_preset())
        import_preset_btn.clicked.connect(do_import_preset)
        export_preset_btn.clicked.connect(do_export_preset)

        rules_edit.textChanged.connect(invalidate_preview)
        sites_edit.textChanged.connect(invalidate_preview)
        prefix.textChanged.connect(invalidate_preview)
        series_prefix.textChanged.connect(invalidate_preview)
        add_source.toggled.connect(invalidate_preview)
        scan.toggled.connect(invalidate_preview)
        scope_selected.toggled.connect(invalidate_preview)
        scope_all.toggled.connect(invalidate_preview)

        preview_btn.clicked.connect(do_preview)
        apply_btn.clicked.connect(do_apply)

        # Initial UI Setup
        populate_table_from_rules()
        populate_preset_list()
        validate_form()

        dialog.exec()
