from html import escape

from calibre.gui2 import info_dialog, question_dialog
from calibre_plugins.inkbunny_sofurry_downloader import core, libkit, plog
from calibre_plugins.inkbunny_sofurry_downloader.guikit import run_task
from calibre_plugins.inkbunny_sofurry_downloader.ui import DownloadDialog
from qt.core import QAction, QMenu


class FurryAction(libkit.DownloaderAction):
    name = 'Inkbunny & SoFurry Downloader'
    action_spec = ('Inkbunny & SoFurry', None, 'Download Inkbunny and SoFurry writing as EPUB', None)

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        action = QAction('Download stories by link…', self.gui)
        action.triggered.connect(self.download_by_link)
        menu.addAction(action)
        self.qaction.triggered.connect(self.download_by_link)

    def existing_ids(self):
        """'inkbunny:<id>' / 'sofurry:<id>' for every book this plugin (either site) already added."""
        found = set()
        for name in (core.INKBUNNY, core.SOFURRY):
            found |= {f'{name}:{value}' for value in libkit.existing_ids(self.gui, name)}
        return found

    # -- entry point
    def download_by_link(self):
        plog.log('download_by_link start')
        dialog = DownloadDialog(self.gui, self.existing_ids())
        if dialog.exec() != dialog.DialogCode.Accepted:
            plog.log('dialog closed without download')
            return
        refs = dialog.selected_refs()
        plog.log('dialog accepted', f'refs={len(refs)}')
        skip, combine, title, chapter_range = dialog.options.values()
        user, password = dialog.credentials()
        if not refs:
            return info_dialog(self.gui, 'Inkbunny & SoFurry', 'Nothing checked to download. '
                               'Paste addresses and use Fetch story list first.', show=True)
        if skip:
            have = self.existing_ids()
            kept = [r for r in refs if r['id'] not in have
                    and not (r.get('group_id') and f"{r['site']}:{r['group_id']}" in have)]
            skipped = len(refs) - len(kept)
            refs = kept
        else:
            skipped = 0
        if not refs:
            return info_dialog(self.gui, 'Inkbunny & SoFurry', f'Nothing to download. {skipped} already in your library.', show=True)
        if len(refs) > 25 and not question_dialog(
                self.gui, 'Inkbunny & SoFurry', f'Download {len(refs)} stories? They are fetched about one per '
                                                'second or slower, so this can take a while.'):
            return

        stories, failures = [], []

        def progress(msg):
            # task.status is set by the caller's lambda below; milestone lines also go to the log
            if msg.startswith('Downloading ') and ' of ' in msg:
                try:
                    n = int(msg.split()[1])
                    total = int(msg.split(' of ')[1].split(':')[0])
                    if n == 1 or n % 10 == 0 or n == total:
                        plog.log('download progress', msg)
                except (ValueError, IndexError):
                    pass

        def work(task):
            def prog(msg):
                task.status = msg
                progress(msg)
            fetcher = core.Fetcher(cancelled=task.cancelled)
            got, _, failed = core.download_refs(fetcher, refs, progress=prog,
                                                chapter_range=chapter_range or None,
                                                inkbunny_username=user, inkbunny_password=password)
            stories.extend(got)
            failures.extend(failed)

        plog.log('download start', f'refs={len(refs)} combine={combine}')
        task = run_task(self.gui, 'Downloading stories…', work)  # on cancel, stories fetched so far are still imported
        plog.log('download end', f'stories={len(stories)} failures={len(failures)} error={task.error!r} '
                                 f'cancelled={task.cancelled()}')
        if task.error:
            failures.append(str(task.error))
        added, build_errors = self.import_stories(stories, combine, title)
        plog.log('import end', f'added={added} build_errors={len(build_errors)}')
        failures.extend(build_errors)
        lines = [f'{added} book(s) added.']
        if skipped:
            lines.append(f'{skipped} already in the library, skipped.')
        if task.cancelled():
            lines.append('Cancelled; stories downloaded before that were kept.')
        info_dialog(self.gui, 'Inkbunny & SoFurry download complete', '\n'.join(lines + failures), show=True)

    # -- import
    def import_stories(self, stories, combine, title):
        if not stories:
            return 0, []
        if combine:
            groups = [('', stories)]
        else:
            groups = core.compile_groups(stories)
        items, errors = [], []
        for group_id, members in groups:
            try:
                plog.log('build start', f'group={group_id or "(single/combined)"} members={len(members)} '
                                        f'first={members[0].get("title", "")[:60]!r}')
                book_title = title if combine else None
                if group_id and members[0].get('series'):
                    book_title = members[0]['series']  # a pool/folder book is titled after its collection
                data = core.build_epub(members, title=book_title, preserve_story_order=bool(group_id or combine))
                plog.log('build end', f'group={group_id or "(single/combined)"} bytes={len(data)}')
                items.append((self.metadata_for(members, book_title, group_id), data))
            except Exception as exc:
                errors.append(f"{', '.join(s['id'] for s in members)}: {exc}")
        plog.log('add_prebuilt start', f'items={len(items)}')
        added, add_errors = libkit.add_prebuilt(self.gui, items)
        plog.log('add_prebuilt end', f'added={added} errors={len(add_errors)}')
        return added, errors + add_errors

    @staticmethod
    def metadata_for(members, title, group_id=''):
        from calibre.ebooks.metadata.book.base import Metadata
        combined = len(members) > 1
        authors = list(dict.fromkeys(s['author'] for s in members))
        mi = Metadata(title or (members[0]['title'] if not combined else f"{members[0]['title']} and {len(members) - 1} more"), authors)
        sites = {s.get('site') for s in members}
        mi.publisher = core.PUBLISHERS.get(sites.pop(), 'Inkbunny / SoFurry') if len(sites) == 1 else 'Inkbunny / SoFurry'
        mi.languages = ['eng']
        mi.tags = list(dict.fromkeys(t for s in members for t in s.get('tags', []) + s.get('categories', [])))
        if combined:
            mi.comments = '<ol>' + ''.join(f"<li>{escape(s['title'])} — {escape(s['author'])}</li>" for s in members) + '</ol>'
            if group_id and len(sites) == 1:
                site = members[0].get('site')
                if site:
                    mi.set_identifier(site, group_id)
        else:
            story = members[0]
            if story.get('summary'):
                mi.comments = f"<p>{escape(story['summary'])}</p>"
            if story.get('site') and story.get('key'):
                mi.set_identifier(story['site'], story['key'])
            if story.get('pubdate'):
                try:
                    from calibre.utils.date import parse_date
                    mi.pubdate = parse_date(story['pubdate'], assume_utc=True)
                except Exception:
                    pass
        return mi
