from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                     QPushButton, QRadioButton, QVBoxLayout)
try:
    from calibre_plugins.story_collection_tagger.error_handler import show_friendly_error_dialog
except (ImportError, AttributeError):
    try:
        from .error_handler import show_friendly_error_dialog
    except (ImportError, AttributeError):
        from error_handler import show_friendly_error_dialog

from calibre_plugins.story_collection_tagger import rules as rules_mod
from calibre_plugins.story_collection_tagger.config import prefs

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

        def invalidate_preview(*args):
            if not state['plan']:
                return
            state['plan'] = []
            apply_btn.setEnabled(False)
            preview.clear()

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
