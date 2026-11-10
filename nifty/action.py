from calibre.gui2 import error_dialog, info_dialog, question_dialog
from qt.core import QAction, QMenu

from calibre_plugins.nifty_downloader import core
from calibre_plugins.nifty_downloader import libkit
from calibre_plugins.nifty_downloader.config import prefs
from calibre_plugins.nifty_downloader.guikit import run_task
from calibre_plugins.nifty_downloader.ui import AuthorDialog, BrowseDialog, LinksDialog, NiftyPreviewDialog, QDialog


IDENTIFIER = 'nifty'


class NiftyAction(libkit.DownloaderAction):
    identifier = IDENTIFIER
    name = 'Nifty Downloader'
    action_spec = ('Nifty', None, 'Download Nifty archive stories as EPUB', None)

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        for title, slot in [('Download stories by address…', self.download_by_link), ('Browse by category…', self.browse),
                            ('Browse by author…', self.browse_authors),
                            ('Organize/condense into posting-order omnibus…', self.organize_omnibus),
                            ('Settings…', self.settings)]:
            action = QAction(title, self.gui)
            action.triggered.connect(slot)
            menu.addAction(action)
        self.qaction.triggered.connect(self.browse)

    def settings(self):
        from qt.core import QCheckBox, QDialogButtonBox, QVBoxLayout
        dialog = QDialog(self.gui)
        dialog.setWindowTitle('Nifty Downloader settings')
        layout = QVBoxLayout(dialog)

        show_preview = QCheckBox('Show interactive preview dialog before downloading multi-chapter series')
        show_preview.setChecked(prefs.get('show_preview', True))
        layout.addWidget(show_preview)

        bypass_single = QCheckBox('Bypass preview dialog for single-chapter story downloads')
        bypass_single.setChecked(prefs.get('bypass_single_chapter_preview', True))
        layout.addWidget(bypass_single)

        skip_existing = QCheckBox('Skip stories already in Calibre library by default')
        skip_existing.setChecked(prefs.get('skip_existing', True))
        layout.addWidget(skip_existing)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            prefs['show_preview'] = show_preview.isChecked()
            prefs['bypass_single_chapter_preview'] = bypass_single.isChecked()
            prefs['skip_existing'] = skip_existing.isChecked()

    def organize_omnibus(self):
        rows = self.gui.library_view.selectionModel().selectedRows()
        selected_ids = [self.gui.library_view.model().id(r) for r in rows] if rows else None

        def work(task):
            task.status = 'Scanning library Nifty stories…'
            return libkit.consolidate_nifty_omnibuses(self.gui, selected_ids)

        task = run_task(self.gui, 'Organizing Nifty stories…', work)
        if task.error:
            return error_dialog(self.gui, 'Nifty', f'Could not organize stories into omnibus: {task.error}', show=True)
        if task.result is None:
            return
        added, errors = task.result
        if added:
            info_dialog(self.gui, 'Nifty', f'{len(added)} posting-order omnibus(es) created/updated.' + ''.join('\n' + e for e in errors), show=True)
        else:
            info_dialog(self.gui, 'Nifty', 'No related Nifty stories found to combine.' + ''.join('\n' + e for e in errors), show=True)

    def download_by_link(self):
        dialog = LinksDialog(self.gui)
        if dialog.exec() != dialog.DialogCode.Accepted or not dialog.lines():
            return
        skip, combine, title, chapter_range = dialog.options.values()
        lines = dialog.lines()

        def expand(task):
            return core.expand_targets(core.Fetcher(cancelled=task.cancelled), lines, lambda msg: setattr(task, 'status', msg))

        task = run_task(self.gui, 'Reading addresses…', expand)
        if task.error:
            return error_dialog(self.gui, 'Nifty', f'Could not read the addresses: {task.error}', show=True)
        if task.result is None:
            return
        refs, problems = task.result
        self.download(refs, skip, combine, title, problems, chapter_range)

    def browse(self):
        dialog = BrowseDialog(self.gui, self.existing_ids())
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        skip, combine, title, chapter_range = dialog.options.values()
        self.download(dialog.selected_refs(), skip, combine, title, [], chapter_range)

    def download(self, refs, skip, combine, title, problems, chapter_range=None):
        if not refs:
            return info_dialog(self.gui, 'Nifty', 'Nothing to download.' + ''.join('\n' + p for p in problems), show=True)

        def resolve_all(task):
            fetcher = core.Fetcher(cancelled=task.cancelled)
            resolved = []
            for ref in refs:
                resolved.extend(core.resolve_ref(fetcher, ref))
            return resolved

        task = run_task(self.gui, 'Resolving story references…', resolve_all)
        if task.error:
            return error_dialog(self.gui, 'Nifty', f'Could not resolve story references: {task.error}', show=True)
        if task.cancelled() or task.result is None:
            return
        resolved_refs = task.result

        groups = core.build_preview_groups(resolved_refs, combine=combine, title=title, chapter_range=chapter_range)

        show_preview = prefs.get('show_preview', True)
        bypass_single = prefs.get('bypass_single_chapter_preview', True)
        is_multi = len(groups) > 1 or any(len(g.get('chapters', [])) > 1 for g in groups)

        if show_preview and (is_multi or not bypass_single):
            preview_dialog = NiftyPreviewDialog(self.gui, groups)
            if preview_dialog.exec() != QDialog.DialogCode.Accepted:
                return
            groups = preview_dialog.get_groups()
            if not groups:
                return info_dialog(self.gui, 'Nifty', 'No chapters selected to download.' + ''.join('\n' + p for p in problems), show=True)

        total_chapters = sum(len(g.get('chapters', [])) for g in groups)
        if total_chapters > 15 and not question_dialog(
                self.gui, 'Nifty', f'Download {total_chapters} chapters? Each chapter is a separate request (about one per second), '
                                   'so large series take a while. You can cancel and keep what was fetched.'):
            return

        skip_ids = self.existing_ids() if skip else set()
        stories, failures, counters = [], [], {'skipped': 0}

        def work(task):
            fetcher = core.Fetcher(cancelled=task.cancelled)
            for n, group in enumerate(groups, 1):
                if all(ch['file'] in skip_ids or group['id'] in skip_ids for ch in group.get('chapters', [])):
                    counters['skipped'] += 1
                    continue
                def progress(msg, n=n):
                    task.status = f'[{n}/{len(groups)}] {msg}'
                task.status = f"[{n}/{len(groups)}] {group['title']}"
                try:
                    story = core.fetch_group(fetcher, group, progress)
                    if story:
                        stories.append(story)
                except core.Cancelled:
                    raise
                except Exception as exc:
                    failures.append(f"{group['id']}: {exc}")

        task = run_task(self.gui, 'Downloading stories…', work)
        if task.error:
            failures.append(str(task.error))

        added, build_errors = libkit.import_stories(self.gui, stories, False, None, core.build_epub, IDENTIFIER, core.PUBLISHER)
        lines = [f'{added} book(s) added.']
        if counters['skipped']:
            lines.append(f"{counters['skipped']} already in the library, skipped.")
        if task.cancelled():
            lines.append('Cancelled; books downloaded before that were kept.')
        info_dialog(self.gui, 'Nifty download complete', '\n'.join(lines + problems + failures + build_errors), show=True)

    def browse_authors(self):
        dialog = AuthorDialog(self.gui, self.existing_ids())
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        selection = dialog.selection()
        if not selection:
            return info_dialog(self.gui, 'Nifty', 'Nothing was ticked.', show=True)
        self.download_selection(selection, dialog.skip.isChecked())

    def download_selection(self, selection, skip):
        """Download author selections: each item is one combined book or separate single books."""
        count = sum(len(item['addresses']) for item in selection)
        if count > 15 and not question_dialog(
                self.gui, 'Nifty', f'Download {count} stories or series? Each chapter is a separate request (about one per second). '
                                   'You can cancel and keep what was fetched.'):
            return
        skip_ids = self.existing_ids() if skip else set()
        groups, stats, failures = [], {}, []

        def work(task):
            fetcher = core.Fetcher(cancelled=task.cancelled)
            for group in core.iter_selection(fetcher, selection, skip_ids, lambda msg: setattr(task, 'status', msg), stats):
                groups.append(group)  # kept even if a later item is cancelled

        task = run_task(self.gui, 'Downloading stories…', work)
        if task.error:
            failures.append(str(task.error))
        added, errors = 0, []
        for group in groups:
            combine = bool(group['title']) and len(group['stories']) >= 2
            n, e = libkit.import_stories(self.gui, group['stories'], combine, group['title'] if combine else None,
                                         core.build_epub, IDENTIFIER, core.PUBLISHER)
            added += n
            errors += e
        lines = [f'{added} book(s) added.']
        if stats.get('skipped'):
            lines.append(f"{stats['skipped']} already in the library, skipped.")
        if task.cancelled():
            lines.append('Cancelled; books downloaded before that were kept.')
        info_dialog(self.gui, 'Nifty download complete', '\n'.join(lines + stats.get('problems', []) + failures + errors), show=True)
