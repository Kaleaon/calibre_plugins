"""UI components for EPUB Promo Scrubber, including preview dialog and profile settings manager."""

try:
    from qt.core import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        Qt,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
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
            return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
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
