from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import QAction, QMenu

from calibre_plugins.metabods_downloader import core
from calibre_plugins.metabods_downloader import libkit
from calibre_plugins.metabods_downloader.guikit import run_task
from calibre_plugins.metabods_downloader.ui import DownloadDialog, TagSearchDialog

IDENTIFIER = 'metabods'


class MetabodsAction(libkit.DownloaderAction):
    identifier = IDENTIFIER
    name = 'Metabods Downloader'
    action_spec = ('Metabods', None, 'Download Metabods stories as EPUB', None)

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        for title, slot in [('Download stories by link…', self.download_by_link), ('Search by tag…', self.search_by_tag)]:
            action = QAction(title, self.gui)
            action.triggered.connect(slot)
            menu.addAction(action)
        self.qaction.triggered.connect(self.download_by_link)

    # -- entry points
    def download_by_link(self):
        dialog = DownloadDialog(self.gui)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        lines = dialog.lines()
        if not lines:
            return
        skip, combine, title, chapter_range = dialog.options.values()

        def expand(task):
            task.status = 'Reading links…'
            return core.expand_targets(core.Fetcher(cancelled=task.cancelled), lines, lambda msg: setattr(task, 'status', msg))

        task = run_task(self.gui, 'Reading links…', expand)
        if task.error:
            return error_dialog(self.gui, 'Metabods', f'Could not read the links: {task.error}', show=True)
        if task.result is None:
            return
        rows, problems = task.result
        ids = [r['id'] for r in rows]
        if skip:
            have = self.existing_ids()
            ids = [i for i in ids if i not in have]
        self.download(ids, combine, title, problems, len(rows) - len(ids), chapter_range=chapter_range)

    def search_by_tag(self):
        dialog = TagSearchDialog(self.gui, self.existing_ids())
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        ids = dialog.selected_ids()
        _, combine, title, chapter_range = dialog.options.values()
        self.download(ids, combine, title, [], 0, chapter_range=chapter_range)

    # -- download + import
    def download(self, ids, combine, title, problems, skipped, chapter_range=None):
        if not ids:
            return info_dialog(self.gui, 'Metabods', 'Nothing to download.' + (f' {skipped} already in your library.' if skipped else '') +
                               ''.join('\n' + p for p in problems), show=True)
        if len(ids) > 25 and not question_dialog(
                self.gui, 'Metabods', f'Download {len(ids)} stories? They are fetched about one per two seconds, so this takes roughly {len(ids) * 2 // 60 + 1} minutes.'):
            return
        stories, failures = [], []

        def work(task):
            fetcher = core.Fetcher(cancelled=task.cancelled)
            for n, sid in enumerate(ids, 1):
                task.status = f'Downloading {n} of {len(ids)}: {sid}'
                try:
                    stories.append(core.fetch_story(fetcher, sid, chapter_range=chapter_range))
                except core.Cancelled:
                    raise
                except Exception as exc:
                    failures.append(f'{sid}: {exc}')

        task = run_task(self.gui, 'Downloading stories…', work)  # on cancel, stories fetched so far are still imported
        if task.error:
            failures.append(str(task.error))
        imported, build_errors = libkit.import_stories(self.gui, stories, combine, title, core.build_epub, IDENTIFIER, core.PUBLISHER)
        failures.extend(build_errors)
        lines = [f'{imported} book(s) added.']
        if skipped:
            lines.append(f'{skipped} already in the library, skipped.')
        if task.cancelled():
            lines.append('Cancelled; stories downloaded before that were kept.')
        lines += problems + failures
        info_dialog(self.gui, 'Metabods download complete', '\n'.join(lines), show=True)
