import json
from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction

try:
    from qt.core import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
                         QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                         QPushButton, QRadioButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
                         QVBoxLayout, QWidget)
except ImportError:
    try:
        from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
                                      QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                                      QPushButton, QRadioButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
                                      QVBoxLayout, QWidget)
    except ImportError:
        from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGroupBox,
                                      QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                                      QPushButton, QRadioButton, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
                                      QVBoxLayout, QWidget)

try:
    from calibre_plugins.story_collection_tagger import rules as rules_mod
    from calibre_plugins.story_collection_tagger import presets as presets_mod
    from calibre_plugins.story_collection_tagger.config import prefs
except ImportError:
    import rules as rules_mod
    import presets as presets_mod
    from config import prefs

RULES_HELP = ('One rule per line:  [field:]pattern => Tag, Tag\n'
              'Fields: tags, title, author, series, comments, publisher, source, any (default: title, tags and series).\n'
              'A plain pattern is a case-insensitive substring; "=text" must match exactly; "re:..." is a regular expression.\n'
              'Examples:\n  tags:=Muscle Growth => Theme.Growth\n  source:Royal Road => Format.Web serial\n  re:litrpg|progression => LitRPG')
SITES_HELP = 'Extra sites, one per line:  host, host2 => Name     (for example: storiesonline.net => Stories Online)'


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
        dialog.resize(780, 750)
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
        form_btns.addWidget(add_rule_btn)
        form_btns.addWidget(clear_btn)
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

        # Match Preview Box
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setPlaceholderText('Press Preview to see which books would get which tags.')
        layout.addWidget(preview, 2)

        # Dialog Buttons
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
            'plan': [],
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
                return error_dialog(self.gui, 'Auto-tag', f'Could not apply the tags: {exc}', show=True)
            info_dialog(self.gui, 'Auto-tag', f'Tags added to {len(plan)} book(s).', show=True)
            dialog.accept()

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

        preview_btn.clicked.connect(do_preview)
        apply_btn.clicked.connect(do_apply)

        # Initial UI Setup
        populate_table_from_rules()
        populate_preset_list()
        validate_form()

        dialog.exec()
