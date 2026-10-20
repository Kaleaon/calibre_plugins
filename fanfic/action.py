from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.fanfic_downloader import engine, flaresolverr, libkit
from calibre_plugins.fanfic_downloader.guikit import run_task
from calibre_plugins.fanfic_downloader.ui import DownloadDialog, FlareSolverrSetupWizard


class FanficAction(InterfaceAction):
    name = 'Fanfic Site Downloader'
    action_spec = ('Fanfic sites', None, 'Download stories from Royal Road, AO3 and many other sites as EPUB', None)

    def genesis(self):
        self.qaction.triggered.connect(self.download)

    def existing_urls(self):
        # Calibre stores ':' as '|' inside identifier values; FanFicFare's own plugin does the same.
        return {u.replace('|', ':') for u in libkit.existing_ids(self.gui, 'url')}

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

        # Pre-flight connectivity verification for Cloudflare protected domains
        cf_stories = [info for info in accepted if flaresolverr.is_cloudflare_domain(info['domain'])]
        if cf_stories:
            if not flaresolverr.is_enabled(opts['ini']):
                wizard = FlareSolverrSetupWizard(self.gui, advanced_edit=opts['ini'])
                if wizard.exec() == wizard.DialogCode.Accepted:
                    opts['ini'] = wizard.updated_ini()
                else:
                    if not question_dialog(self.gui, 'FlareSolverr Setup',
                                           'FlareSolverr proxy is not enabled. Cloudflare-protected sites may fail with HTTP 403.\n\nDo you want to continue downloading anyway?'):
                        return

            if flaresolverr.is_enabled(opts['ini']):
                settings = flaresolverr.settings_from_ini(opts['ini'])

                def preflight_work(task):
                    task.status = 'Checking FlareSolverr proxy connectivity…'
                    ok_srv, msg_srv = flaresolverr.check_server(settings, timeout=5)
                    if not ok_srv:
                        return False, msg_srv
                    task.status = 'Verifying site loading through FlareSolverr…'
                    ok_site, msg_site = flaresolverr.fetch_through(settings, url=cf_stories[0]['url'], max_timeout=10000)
                    if not ok_site:
                        return False, msg_site
                    return True, msg_site

                pf_task = run_task(self.gui, 'Verifying FlareSolverr proxy connectivity…', preflight_work)
                if pf_task.error or (pf_task.result and not pf_task.result[0]):
                    err = str(pf_task.error) if pf_task.error else (pf_task.result[1] if pf_task.result else 'Connectivity test failed')
                    if question_dialog(self.gui, 'FlareSolverr Connectivity Warning',
                                       f'Pre-flight verification check failed:\n{err}\n\nWould you like to open the FlareSolverr Setup Wizard to fix this?'):
                        wizard = FlareSolverrSetupWizard(self.gui, advanced_edit=opts['ini'], recovery_mode=True)
                        if wizard.exec() == wizard.DialogCode.Accepted:
                            opts['ini'] = wizard.updated_ini()
                        else:
                            if not question_dialog(self.gui, 'Continue Download?', 'Pre-flight check failed. Attempt downloads anyway?'):
                                return

        results, failures = [], []
        user_cancelled = False

        for n, info in enumerate(accepted, 1):
            if user_cancelled:
                break

            def download_item(task):
                task.status = f"[{n}/{len(accepted)}] {info['domain']}: {info['url']}"
                return engine.download_story(info, opts['ini'], opts['allow_adult'])

            dl_task = run_task(self.gui, 'Downloading…', download_item)
            if dl_task.cancelled():
                user_cancelled = True
                break

            if dl_task.error:
                exc = dl_task.error
                if flaresolverr.is_403_error(exc):
                    if question_dialog(self.gui, 'Cloudflare 403 Challenge',
                                       f"Download failed with HTTP 403 bot challenge for {info['url']}.\n\nWould you like to open the FlareSolverr Setup Wizard to resolve this and retry?"):
                        wizard = FlareSolverrSetupWizard(self.gui, advanced_edit=opts['ini'], recovery_mode=True, recovery_url=info['url'])
                        if wizard.exec() == wizard.DialogCode.Accepted:
                            opts['ini'] = wizard.updated_ini()
                            retry_task = run_task(self.gui, f"Retrying {info['domain']}…", download_item)
                            if not retry_task.error and retry_task.result:
                                results.append(retry_task.result)
                                continue
                            elif retry_task.error:
                                exc = retry_task.error
                failures.append(f"{info['url']}: {engine.explain(exc, info['url'])}")
            elif dl_task.result:
                results.append(dl_task.result)
        if opts.get('combine') and len(results) > 1:
            merged_epub = libkit.merge_epubs([r['epub'] for r in results])
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
        if user_cancelled:
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
