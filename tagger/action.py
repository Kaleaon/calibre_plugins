from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                     QPushButton, QRadioButton, QVBoxLayout)
import json
from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                     QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                     QRadioButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget, Qt)
try:
    from calibre_plugins.story_collection_tagger.error_handler import show_friendly_error_dialog
except ImportError:
    try:
        from .error_handler import show_friendly_error_dialog
    except ImportError:
        try:
            from error_handler import show_friendly_error_dialog
        except ImportError:
            from common.error_handler import show_friendly_error_dialog

from calibre_plugins.story_collection_tagger import rules as rules_mod
from calibre_plugins.story_collection_tagger.config import prefs

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


class TaggerAction(InterfaceAction):
    name = 'Story Collection Tagger'
    action_spec = ('Auto-tag stories', None, 'Add source-site and keyword-rule tags to books', None)

    def genesis(self):
        self.qaction.triggered.connect(self.show_dialog)

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
        dialog.resize(680, 720)
        layout = QVBoxLayout(dialog)
        intro_label = QLabel('Only adds tags; existing tags are never removed or renamed. Nothing changes until you press Apply.')
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)
        scope_selected = QRadioButton(f'Selected books ({len(selected)})')
        scope_all = QRadioButton('Entire library')
        (scope_selected if selected else scope_all).setChecked(True)
        scope_selected.setEnabled(bool(selected))
        for w in (scope_selected, scope_all):
            layout.addWidget(w)

        row = QHBoxLayout()
        add_source = QCheckBox('Add a source-site tag, prefix:')
        add_source.setChecked(bool(prefs['add_source']))
        prefix = QLineEdit(prefs['source_prefix'])
        prefix.setToolTip('Use "Source." to nest under "Source" when Calibre\'s hierarchical tag browser is enabled for tags.')
        row.addWidget(add_source)
        row.addWidget(prefix)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel('Tag books in a series, prefix (blank = off):'))
        series_prefix = QLineEdit(prefs['series_prefix'])
        series_prefix.setPlaceholderText('Series.')
        row.addWidget(series_prefix)
        layout.addLayout(row)
        scan = QCheckBox('Also look for site addresses inside book descriptions (may over-match)')
        scan.setChecked(bool(prefs['scan_comments']))
        layout.addWidget(scan)

        rules_header_layout = QHBoxLayout()
        rules_label = QLabel('Keyword rules')
        rules_label.setWordWrap(True)
        manage_btn = QPushButton('Manage Rule Sets...')
        rules_header_layout.addWidget(rules_label, 1)
        rules_header_layout.addWidget(manage_btn)
        layout.addLayout(rules_header_layout)

        rules_edit = QPlainTextEdit(prefs['rules'])
        rules_edit.setPlaceholderText(RULES_HELP)
        layout.addWidget(rules_edit, 2)
        sites_label = QLabel('Additional source sites')
        sites_label.setWordWrap(True)
        layout.addWidget(sites_label)
        sites_edit = QPlainTextEdit(prefs['sites'])
        sites_edit.setPlaceholderText(SITES_HELP)
        layout.addWidget(sites_edit, 1)
        builtin_label = QLabel('Built-in sites: ' + ', '.join(s[0] for s in rules_mod.DEFAULT_SITES))
        builtin_label.setWordWrap(True)
        layout.addWidget(builtin_label)

        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlaceholderText('Press Preview to see which books would get which tags.')
        layout.addWidget(preview, 3)
        buttons = QDialogButtonBox()
        preview_btn = QPushButton('Preview')
        apply_btn = QPushButton('Apply')
        apply_btn.setEnabled(False)
        buttons.addButton(preview_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        state = {'plan': []}

        def save_prefs():
            prefs['add_source'] = add_source.isChecked()
            prefs['source_prefix'] = prefix.text()
            prefs['series_prefix'] = series_prefix.text()
            prefs['scan_comments'] = scan.isChecked()
            prefs['rules'] = rules_edit.toPlainText()
            prefs['sites'] = sites_edit.toPlainText()

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
                state['plan'] = []
                apply_btn.setEnabled(False)
                preview.setPlainText('Rule set configuration updated. Press Preview to see updated tag plan.')

        manage_btn.clicked.connect(do_manage)

        def do_preview():
            save_prefs()
            parsed, rule_errors = rules_mod.parse_rules(rules_edit.toPlainText())
            extra, site_errors = rules_mod.parse_sites(sites_edit.toPlainText())
            errors = rule_errors + site_errors
            if errors:
                preview.setPlainText('Fix these first:\n' + '\n'.join(errors))
                apply_btn.setEnabled(False)
                return
            ids = selected if scope_selected.isChecked() else sorted(self.gui.current_db.new_api.all_book_ids())
            plan = rules_mod.plan_changes(self.books_for(ids), prefix.text(), add_source.isChecked(), series_prefix.text(), parsed,
                                          rules_mod.merge_sites(rules_mod.DEFAULT_SITES, extra), scan.isChecked())
            state['plan'] = plan
            shown = [f"{p['title']}  +  {', '.join(p['add'])}" for p in plan[:300]]
            more = f'\n… and {len(plan) - 300} more' if len(plan) > 300 else ''
            preview.setPlainText(f'{len(plan)} of {len(ids)} book(s) would change.\n\n' + '\n'.join(shown) + more)
            apply_btn.setEnabled(bool(plan))

        def do_apply():
            plan = state['plan']
            if not plan:
                return
            try:
                db = self.gui.current_db.new_api
                db.set_field('tags', {p['id']: tuple(p['tags']) for p in plan})
                self.gui.library_view.model().refresh_ids([p['id'] for p in plan])
                self.gui.tags_view.recount()
            except Exception as exc:
                return show_friendly_error_dialog(self.gui, 'Auto-tag', exc, det_msg=f'Could not apply the tags to {len(plan)} book(s)', show=True)
            info_dialog(self.gui, 'Auto-tag', f'Tags added to {len(plan)} book(s).', show=True)
            dialog.accept()

        preview_btn.clicked.connect(do_preview)
        apply_btn.clicked.connect(do_apply)
        dialog.exec()
