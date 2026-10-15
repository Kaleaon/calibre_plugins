from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.fanfic_downloader import engine, libkit
from calibre_plugins.fanfic_downloader.guikit import run_task
from calibre_plugins.fanfic_downloader.ui import DownloadDialog


class FanficAction(InterfaceAction):
    name = 'Fanfic Site Downloader'
    action_spec = ('Fanfic sites', None, 'Download stories from Royal Road, AO3 and many other sites as EPUB', None)

    def genesis(self):
        from qt.core import QAction, QMenu
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)

        dl_action = QAction('Download new stories from URLs...', self.gui)
        dl_action.triggered.connect(self.download)
        menu.addAction(dl_action)

        update_action = QAction('Update selected library books', self.gui)
        update_action.triggered.connect(self.update_selected)
        menu.addAction(update_action)

        self.qaction.triggered.connect(self.download)

    def existing_urls(self):
        # Calibre stores ':' as '|' inside identifier values; FanFicFare's own plugin does the same.
        return {u.replace('|', ':') for u in libkit.existing_ids(self.gui, 'url')}

    def update_selected(self):
        try:
            from calibre_plugins.fanfic_downloader.config import prefs
        except ImportError:
            try:
                from .config import prefs
            except ImportError:
                from config import prefs

        selected_ids = list(self.gui.library_view.get_selected_ids())
        if not selected_ids:
            info_dialog(self.gui, 'Fanfic downloader', 'No books selected in the library.', show=True)
            return

        db = self.gui.current_db.new_api
        all_idents = db.all_field_for('identifiers', selected_ids, {})
        allow_adult = prefs['allow_adult']
        extra_adult = engine.parse_extra_adult(prefs['extra_adult'])
        ini = prefs['advanced_ini']

        valid_targets = []
        skipped = []
        problems = []

        for book_id in selected_ids:
            title = db.field_for('title', book_id, f'Book ID {book_id}') or f'Book ID {book_id}'
            idents = all_idents.get(book_id) or {}
            raw_url = idents.get('url', '')
            if raw_url:
                raw_url = raw_url.replace('|', ':').strip()
            if not raw_url:
                skipped.append(f"Skipped '{title}' (ID {book_id}): No 'url' identifier.")
                continue

            try:
                info = engine.inspect_url(raw_url, allow_adult, extra_adult)
                info['book_id'] = book_id
                info['book_title'] = title
                valid_targets.append(info)
            except engine.EngineError as exc:
                problems.append(f"'{title}' ({raw_url}): {exc}")
            except Exception as exc:
                problems.append(f"'{title}' ({raw_url}): {exc}")

        if not valid_targets:
            lines = ['No valid books to update.']
            if skipped:
                lines.extend(skipped)
            if problems:
                lines.extend(problems)
            info_dialog(self.gui, 'Fanfic downloader', '\n'.join(lines), show=True)
            return

        results, failures = [], []

        def work(task):
            for n, info in enumerate(valid_targets, 1):
                if task.cancelled():
                    return
                task.status = f"[{n}/{len(valid_targets)}] {info['domain']}: {info['book_title']}"
                try:
                    res = engine.download_story(info, ini, allow_adult)
                    res['target_info'] = info
                    results.append(res)
                except Exception as exc:
                    failures.append(f"{info['book_title']} ({info['url']}): {engine.explain(exc, info['url'])}")

        task = run_task(self.gui, 'Updating selected books…', work)
        if task.error:
            failures.append(str(task.error))

        updated_count = 0
        update_errors = []
        for res in results:
            mi = self.metadata_for(res)
            info = res['target_info']
            try:
                libkit.upsert_epub(self.gui, mi, res['epub'], 'url', info['url'])
                updated_count += 1
            except Exception as exc:
                update_errors.append(f"{info['book_title']}: {exc}")

        summary_lines = [f'{updated_count} book(s) updated.']
        if skipped:
            summary_lines.extend(skipped)
        if task.cancelled():
            summary_lines.append('Cancelled; stories finished before that were kept.')
        partial = [r['title'] for r in results if r.get('chapter_errors')]
        if partial:
            summary_lines.append('Some chapters failed to download in: ' + '; '.join(partial))
        if problems:
            summary_lines.extend(problems)
        if failures:
            summary_lines.extend(failures)
        if update_errors:
            summary_lines.extend(update_errors)

        info_dialog(self.gui, 'Fanfic downloader', '\n'.join(summary_lines), show=True)

    def download(self):
        dialog = DownloadDialog(self.gui)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        opts = dialog.values()
        if not opts['lines']:
            return
        try:
            accepted, problems = engine.check_urls(opts['lines'], opts['allow_adult'], opts['extra_adult'])
        except engine.EngineError as exc:
            return error_dialog(self.gui, 'Fanfic downloader', str(exc), show=True)
        skipped = 0
        if opts['skip']:
            have = self.existing_urls()
            kept = [a for a in accepted if a['url'] not in have or a['begin'] or a['end']]
            skipped = len(accepted) - len(kept)
            accepted = kept
        if not accepted:
            return info_dialog(self.gui, 'Fanfic downloader', 'Nothing to download.\n' + '\n'.join(problems) +
                               (f'\n{skipped} already in the library.' if skipped else ''), show=True)
        if len(accepted) > 5 and not question_dialog(
                self.gui, 'Fanfic downloader', f'Download {len(accepted)} stories? Long serials can take many minutes each; '
                                               'cancelling takes effect between stories.'):
            return

        results, failures = [], []

        def work(task):
            for n, info in enumerate(accepted, 1):
                if task.cancelled():
                    return
                task.status = f"[{n}/{len(accepted)}] {info['domain']}: {info['url']}"
                try:
                    results.append(engine.download_story(info, opts['ini'], opts['allow_adult']))
                except Exception as exc:
                    failures.append(f"{info['url']}: {engine.explain(exc, info['url'])}")

        task = run_task(self.gui, 'Downloading…', work)
        if task.error:
            failures.append(str(task.error))
        if opts.get('combine') and len(results) > 1:
            try:
                from .storykit import merge_epubs
            except ImportError:
                from storykit import merge_epubs
            merged_epub = merge_epubs([r['epub'] for r in results])
            merged_title = f"{results[0]['title']} and {len(results) - 1} more"
            merged_authors = list(dict.fromkeys(a for r in results for a in r['authors']))
            from calibre.ebooks.metadata.book.base import Metadata
            from html import escape
            mi = Metadata(merged_title, merged_authors)
            mi.publisher = 'Fanfic Omnibus'
            mi.tags = list(dict.fromkeys(t for r in results for t in r.get('tags', [])))
            mi.comments = '<ol>' + ''.join(f"<li>{escape(r['title'])} — {escape(' & '.join(r['authors']))}</li>" for r in results) + '</ol>'
            items = [(mi, merged_epub)]
        else:
            items = [(self.metadata_for(r), r['epub']) for r in results]
        added, errors = libkit.add_prebuilt(self.gui, items)
        lines = [f'{added} book(s) added.']
        if skipped:
            lines.append(f'{skipped} already in the library, skipped.')
        if task.cancelled():
            lines.append('Cancelled; stories finished before that were kept.')
        partial = [r['title'] for r in results if r['chapter_errors']]
        if partial:
            lines.append('Some chapters failed to download in: ' + '; '.join(partial))
        info_dialog(self.gui, 'Fanfic downloader', '\n'.join(lines + problems + failures + errors), show=True)

    @staticmethod
    def metadata_for(result):
        from calibre.ebooks.metadata.book.base import Metadata
        mi = Metadata(result['title'], result['authors'])
        mi.publisher = result['site']
        mi.tags = list(result['tags'])
        if result['description']:
            mi.comments = result['description']
        if result['series']:
            mi.series = result['series']
            if result['series_index'] is not None:
                mi.series_index = result['series_index']
        if result['language']:
            mi.languages = [result['language']]
        if result['published']:
            try:
                from calibre.utils.date import parse_date
                mi.pubdate = parse_date(result['published'], assume_utc=True)
            except Exception:
                pass
        mi.set_identifier('url', result['url'])  # same identifier FanFicFare's own Calibre plugin uses
        return mi
