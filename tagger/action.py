from calibre.gui2 import error_dialog, info_dialog
from qt.core import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                     QPushButton, QRadioButton, QSplitter, Qt, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
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
        from ui import TaggerPreviewDialog

from calibre_plugins.story_collection_tagger import rules as rules_mod
from calibre_plugins.story_collection_tagger.config import prefs
from calibre_plugins.story_collection_tagger.guikit import DialogAction

RULES_HELP = ('One rule per line:  [field:]pattern => Tag, Tag\n'
              'Fields: tags, title, author, series, comments, publisher, source, any (default: title, tags and series).\n'
              'A plain pattern is a case-insensitive substring; "=text" must match exactly; "re:..." is a regular expression.\n'
              'Examples:\n  tags:=Muscle Growth => Theme.Growth\n  source:Royal Road => Format.Web serial\n  re:litrpg|progression => LitRPG')
SITES_HELP = 'Extra sites, one per line:  host, host2 => Name     (for example: storiesonline.net => Stories Online)'


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
        dialog.resize(680, 720)
        dialog.setMinimumWidth(680)
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

        rules_label = QLabel('Keyword rules')
        rules_label.setWordWrap(True)
        layout.addWidget(rules_label)
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

        buttons = QDialogButtonBox()
        preview_btn = QPushButton('Preview')
        apply_btn = QPushButton('Apply')
        apply_btn.setEnabled(False)
        buttons.addButton(preview_btn, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(apply_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        state = {'selection': None, 'total_ids': 0, 'updating': False}

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
        dialog.exec()
