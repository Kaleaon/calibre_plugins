from io import BytesIO
from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog
from calibre.utils.config import JSONConfig
from qt.core import (QAction, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QMenu,
                     QMessageBox, QPlainTextEdit, QVBoxLayout)
try:
    from calibre_plugins.epub_promo_scrubber.error_handler import show_friendly_error_dialog
except ImportError:
    try:
        from .error_handler import show_friendly_error_dialog
    except ImportError:
        from error_handler import show_friendly_error_dialog
try:
    from calibre_plugins.epub_promo_scrubber.guikit import run_task
except ImportError:
    try:
        from .guikit import run_task
    except ImportError:
        from guikit import run_task

try:
    from calibre_plugins.epub_promo_scrubber.core import Matcher, _snippet, scrub_epub, scrub_files, tidy
except ImportError:
    try:
        from .core import Matcher, _snippet, scrub_epub, scrub_files, tidy
    except ImportError:
        from core import Matcher, _snippet, scrub_epub, scrub_files, tidy

try:
    from calibre_plugins.epub_promo_scrubber.profiles import (
        extract_domains_from_metadata,
        get_profiles_from_prefs,
        resolve_profile,
    )
except ImportError:
    try:
        from .profiles import (
            extract_domains_from_metadata,
            get_profiles_from_prefs,
            resolve_profile,
        )
    except ImportError:
        from profiles import (
            extract_domains_from_metadata,
            get_profiles_from_prefs,
            resolve_profile,
        )

prefs = JSONConfig('plugins/epub_promo_scrubber')
prefs.defaults.update(extra_patterns='', heuristic=True, drop_empty_pages=True, backup=True, in_place=False, show_preview=True)


class ScrubAction(InterfaceAction):
    name = 'EPUB Promo Scrubber'
    action_spec = ('EPUB Promo Scrubber', None, 'Remove site promotions from EPUB books', None)

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        for title, slot in [('Scrub selected books in Calibre', self.scrub_selected),
                            ('Scrub EPUB file or folder to a new folder…', self.scrub_folder),
                            ('Merge selected books into one EPUB…', self.merge_selected),
                            ('Merge EPUB files from folder…', self.merge_folder),
                            ('Settings…', self.settings)]:
            action = QAction(title, self.gui)
            action.triggered.connect(lambda checked=False, slot=slot: slot())
            menu.addAction(action)
        self.qaction.triggered.connect(self.scrub_selected)

    def options(self):
        profiles = get_profiles_from_prefs(prefs)
        default_prof = resolve_profile([], profiles)
        extra = [line.strip() for line in default_prof.get('extra_patterns', '').splitlines() if line.strip()]
        return extra, default_prof.get('heuristic', True), default_prof.get('drop_empty_pages', True)

    def settings(self):
        try:
            from .ui import ScrubSettingsDialog
        except ImportError:
            try:
                from calibre_plugins.epub_promo_scrubber.ui import ScrubSettingsDialog
            except ImportError:
                from ui import ScrubSettingsDialog

        dialog = ScrubSettingsDialog(self.gui, prefs)
        dialog.exec()

    def scrub_selected(self):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids:
            info_dialog(self.gui, 'EPUB Promo Scrubber', 'Select one or more books in Calibre first.', show=True)
            return
        profiles = get_profiles_from_prefs(prefs)
        extra, heuristic, drop = self.options()
        db = self.gui.current_db.new_api
<<<<<<< HEAD
=======
        matcher = Matcher(extra, heuristic)
        delete_backups = False
        if not prefs['backup'] and any('ORIGINAL_EPUB' in (db.formats(book_id) or ()) for book_id in ids):
            sure = QMessageBox.question(
                self.gui,
                'Delete existing backups?',
                'Backup creation is disabled in settings. Delete existing ORIGINAL_EPUB backup files for the selected book(s)?'
            )
            delete_backups = (sure == QMessageBox.StandardButton.Yes)
>>>>>>> origin/stitch/confirm-backup-deletion-jm0-d22c6e31-575b-47bf-aba8-b2aed62ab8bf

        if prefs.get('show_preview', True):
            def scan_worker(task):
                scan_results = []
                for idx, book_id in enumerate(ids):
                    if task.cancelled():
                        break
                    task.status = f'Scanning book {idx + 1} of {len(ids)}...'
                    mi = db.get_metadata(book_id)
                    title = mi.title if mi else f'Book {book_id}'
                    data = db.format(book_id, 'EPUB')
                    book_changes = []
                    active_profile_name = 'Global Default'
                    if data:
                        _, report = scrub_epub(data, extra, heuristic, drop, dry_run=True, profiles=profiles)
                        book_changes.extend(report.get('changes', []))
                        active_profile_name = report.get('active_profile', 'Global Default')
                    else:
                        doms = extract_domains_from_metadata(mi)
                        active_prof = resolve_profile(doms, profiles)
                        active_profile_name = active_prof.get('name', 'Global Default')

                    doms = extract_domains_from_metadata(mi)
                    active_prof = resolve_profile(doms, profiles)
                    p_extra = active_prof.get('extra_patterns', '')
                    if isinstance(p_extra, str):
                        p_extra = [line.strip() for line in p_extra.splitlines() if line.strip()]
                    p_heuristic = active_prof.get('heuristic', True)
                    field_matcher = Matcher(p_extra, p_heuristic)

                    meta_changes = self.scrub_fields(db, book_id, field_matcher, dry_run=True)
                    book_changes.extend(meta_changes)
                    if book_changes:
                        scan_results.append({
                            'book_id': book_id,
                            'title': title,
                            'data': data,
                            'changes': book_changes,
                            'active_profile': active_profile_name
                        })
                return scan_results

            task = run_task(self.gui, 'Scanning EPUBs for promo content...', scan_worker)
            if task.cancelled() or task.error:
                if task.error:
                    show_friendly_error_dialog(self.gui, 'Scan failed', task.error, show=True)
                return
            scan_results = task.result or []
            if not scan_results:
                info_dialog(self.gui, 'EPUB Promo Scrubber', 'No promotional items found in selected books.', show=True)
                return

            try:
                from .ui import ScrubPreviewDialog
            except ImportError:
                from calibre_plugins.epub_promo_scrubber.ui import ScrubPreviewDialog

            dialog = ScrubPreviewDialog(self.gui, scan_results)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

            allowed_ids = dialog.get_selected_change_ids()
            if not allowed_ids:
                return

            changed, unchanged, errors = [], 0, []

            def apply_worker(task):
                nonlocal unchanged
                total = len(scan_results)
                for idx, res in enumerate(scan_results):
                    if task.cancelled():
                        break
                    task.status = f'Scrubbing book {idx + 1} of {total}…'
                    book_id = res['book_id']
                    data = res['data']
                    try:
                        book_changed = False
                        if data:
                            new_bytes, report = scrub_epub(data, extra, heuristic, drop, allowed_change_ids=allowed_ids, profiles=profiles)
                            if new_bytes is not None:
                                if prefs['backup']:
                                    db.save_original_format(book_id, 'EPUB')
                                elif 'ORIGINAL_EPUB' in (db.formats(book_id) or ()):
                                    db.remove_formats({book_id: {'ORIGINAL_EPUB'}})
                                if not db.add_format(book_id, 'EPUB', BytesIO(new_bytes), replace=True):
                                    raise ValueError('Calibre could not save the scrubbed EPUB')
                                book_changed = True

                        mi = db.get_metadata(book_id)
                        doms = extract_domains_from_metadata(mi)
                        active_prof = resolve_profile(doms, profiles)
                        p_extra = active_prof.get('extra_patterns', '')
                        if isinstance(p_extra, str):
                            p_extra = [line.strip() for line in p_extra.splitlines() if line.strip()]
                        p_heuristic = active_prof.get('heuristic', True)
                        field_matcher = Matcher(p_extra, p_heuristic)

                        field_changes = self.scrub_fields(db, book_id, field_matcher, allowed_change_ids=allowed_ids)
                        if field_changes or book_changed:
                            changed.append(book_id)
                        else:
                            unchanged += 1
                    except Exception as exc:
                        errors.append(f'{book_id}: {exc}')

            task = run_task(self.gui, 'EPUB Promo Scrubber', apply_worker)
            self.gui.library_view.model().refresh()
            self.gui.tags_view.recount()
            if task.error:
                errors.append(f'Task error: {task.error}')

            msg = f'{len(changed)} EPUB(s) scrubbed; {unchanged} had nothing to remove; {len(errors)} error(s).'
            if task.cancelled():
                msg += '\nOperation cancelled.'
            if errors:
                msg += f'\n{"; ".join(errors)}'
            info_dialog(self.gui, 'EPUB Promo Scrubber', msg, show=True)

        else:
            changed, unchanged, errors = [], 0, []

            def work(task):
                nonlocal unchanged
                total = len(ids)
                for i, book_id in enumerate(ids):
                    if task.cancelled():
                        break
                    task.status = f'Scrubbing book {i + 1} of {total}…'
                    try:
                        data = db.format(book_id, 'EPUB')
                        if not data:
                            continue
                        new, report = scrub_epub(data, extra, heuristic, drop, profiles=profiles)
                        if new is not None:
                            if prefs['backup']:
                                db.save_original_format(book_id, 'EPUB')
                            elif delete_backups and 'ORIGINAL_EPUB' in (db.formats(book_id) or ()):
                                db.remove_formats({book_id: {'ORIGINAL_EPUB'}})
                            if not db.add_format(book_id, 'EPUB', BytesIO(new), replace=True):
                                raise ValueError('Calibre could not save the scrubbed EPUB')
                            changed.append(book_id)
                        else:
                            unchanged += 1

                        mi = db.get_metadata(book_id)
                        doms = extract_domains_from_metadata(mi)
                        active_prof = resolve_profile(doms, profiles)
                        p_extra = active_prof.get('extra_patterns', '')
                        if isinstance(p_extra, str):
                            p_extra = [line.strip() for line in p_extra.splitlines() if line.strip()]
                        p_heuristic = active_prof.get('heuristic', True)
                        field_matcher = Matcher(p_extra, p_heuristic)

                        self.scrub_fields(db, book_id, field_matcher)
                    except Exception as exc:
                        errors.append(f'{book_id}: {exc}')

            task = run_task(self.gui, 'EPUB Promo Scrubber', work)
            self.gui.library_view.model().refresh()
            self.gui.tags_view.recount()
            if task.error:
                errors.append(f'Task error: {task.error}')

            msg = f'{len(changed)} EPUB(s) scrubbed; {unchanged} had nothing to remove; {len(errors)} error(s).'
            if task.cancelled():
                msg += '\nOperation cancelled.'
            if errors:
                msg += f'\n{"; ".join(errors)}'
            info_dialog(self.gui, 'EPUB Promo Scrubber', msg, show=True)

    def scrub_fields(self, db, book_id, matcher, allowed_change_ids=None, dry_run=False):
        """Remove the same marks from the library's own title, publisher and comments fields."""
        mi = db.get_metadata(book_id)
        field_changes = []
        if not mi:
            return field_changes
        for field, value in (('title', mi.title), ('publisher', mi.publisher), ('comments', mi.comments)):
            if value and matcher.hit(value):
                new = tidy(matcher.clean_text(value))
                if field == 'title' and not new:
                    continue
                change_id = f"book_{book_id}:field_{field}"
                item = {
                    'file': f'Metadata: {field}',
                    'action': 'calibre metadata trimmed',
                    'text': _snippet(value),
                    'id': change_id,
                    'detail': f'Calibre metadata field "{field}" trimmed:\nBefore: {value}\nAfter: {new or "(empty)"}',
                    'category': 'Calibre Metadata Fields'
                }
                if dry_run:
                    field_changes.append(item)
                elif allowed_change_ids is None or change_id in allowed_change_ids:
                    db.set_field(field, {book_id: new or None})
                    field_changes.append(item)
        return field_changes

    def scrub_folder(self):
        choice = QMessageBox.question(self.gui, 'EPUB Promo Scrubber', 'Scrub a whole folder? Choose No to pick a single EPUB file.')
        if choice == QMessageBox.StandardButton.Yes:
            source = QFileDialog.getExistingDirectory(self.gui, 'Select folder containing EPUB files')
        else:
            source, _ = QFileDialog.getOpenFileName(self.gui, 'Select EPUB file', '', 'EPUB (*.epub)')
        if not source:
            return
        in_place = prefs['in_place']
        output = None
        if in_place:
            sure = QMessageBox.question(self.gui, 'Replace original files?',
                                        'Cleaned versions will overwrite the original EPUB files and the originals cannot be recovered. Continue?')
            if sure != QMessageBox.StandardButton.Yes:
                return
        else:
            output = QFileDialog.getExistingDirectory(self.gui, 'Select a separate folder for scrubbed copies')
            if not output:
                return

        profiles = get_profiles_from_prefs(prefs)
        extra, heuristic, drop = self.options()

        if prefs.get('show_preview', True):
            from pathlib import Path
            src_path = Path(source)
            paths = [src_path] if src_path.is_file() else sorted(src_path.rglob('*.epub'))

            def scan_folder_worker(task):
                scan_results = []
                for idx, p in enumerate(paths):
                    if task.cancelled():
                        break
                    task.status = f'Scanning file {idx + 1} of {len(paths)}...'
                    try:
                        data = p.read_bytes()
                        _, report = scrub_epub(data, extra, heuristic, drop, dry_run=True, profiles=profiles)
                        if report.get('changes'):
                            scan_results.append({
                                'path': str(p),
                                'title': p.name,
                                'changes': report['changes'],
                                'active_profile': report.get('active_profile', 'Global Default')
                            })
                    except Exception:
                        pass
                return scan_results

            task = run_task(self.gui, 'Scanning EPUB files for promo content...', scan_folder_worker)
            if task.cancelled() or task.error:
                if task.error:
                    show_friendly_error_dialog(self.gui, 'Scan failed', task.error, show=True)
                return

            scan_results = task.result or []
            if not scan_results:
                info_dialog(self.gui, 'EPUB Promo Scrubber', 'No promotional items found in selected file(s).', show=True)
                return

            try:
                from .ui import ScrubPreviewDialog
            except ImportError:
                from calibre_plugins.epub_promo_scrubber.ui import ScrubPreviewDialog

            dialog = ScrubPreviewDialog(self.gui, scan_results, is_folder=True)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return

            allowed_ids = dialog.get_selected_change_ids()
            if not allowed_ids:
                return

            try:
                rows, report_path = scrub_files(source, output, extra, heuristic, drop, in_place, allowed_change_ids=allowed_ids, profiles=profiles)
                done = 'replaced' if in_place else 'scrubbed'
                counts = {s: sum(r['status'] == s for r in rows) for s in (done, 'unchanged', 'error')}
                info_dialog(self.gui, 'EPUB Promo Scrubber',
                            f'{counts[done]} {done}, {counts["unchanged"]} unchanged, {counts["error"]} errors.\n' +
                            ('Original files were overwritten.' if in_place else 'Originals were not modified.') + f'\nReport: {report_path}', show=True)
            except Exception as exc:
                show_friendly_error_dialog(self.gui, 'EPUB Promo Scrubber failed', exc, show=True)

        else:
            try:
                rows, report_path = scrub_files(source, output, extra, heuristic, drop, in_place, profiles=profiles)
                done = 'replaced' if in_place else 'scrubbed'
                counts = {s: sum(r['status'] == s for r in rows) for s in (done, 'unchanged', 'error')}
                info_dialog(self.gui, 'EPUB Promo Scrubber',
                            f'{counts[done]} {done}, {counts["unchanged"]} unchanged, {counts["error"]} errors.\n' +
                            ('Original files were overwritten.' if in_place else 'Originals were not modified.') + f'\nReport: {report_path}', show=True)
            except Exception as exc:
                show_friendly_error_dialog(self.gui, 'EPUB Promo Scrubber failed', exc, show=True)

    def merge_selected(self):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids or len(ids) < 2:
            info_dialog(self.gui, 'EPUB Merger', 'Select two or more books in Calibre first to merge.', show=True)
            return

        try:
            try:
                from .guikit import scan_library_books_for_merge, MergePreviewDialog
            except ImportError:
                from calibre_plugins.epub_promo_scrubber.guikit import scan_library_books_for_merge, MergePreviewDialog
        except ImportError:
            from guikit import scan_library_books_for_merge, MergePreviewDialog

        scan_results = scan_library_books_for_merge(self.gui, ids)
        if not scan_results:
            info_dialog(self.gui, 'EPUB Merger', 'No books found to merge.', show=True)
            return

        dialog = MergePreviewDialog(self.gui, scan_results)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        active_stories = dialog.get_active_stories()
        meta = dialog.get_metadata()
        if not active_stories:
            info_dialog(self.gui, 'EPUB Merger', 'No valid books or chapters selected to merge.', show=True)
            return

        try:
            try:
                from .libkit import merge_library_books
            except ImportError:
                from calibre_plugins.epub_promo_scrubber.libkit import merge_library_books
        except ImportError:
            from libkit import merge_library_books

        book_id, errors = merge_library_books(
            self.gui,
            ids,
            title=meta['title'],
            author=meta['author'],
            publisher=meta['publisher'],
            tags=meta['tags'],
            description=meta['description'],
            stories_override=active_stories,
            preserve_story_order=True
        )
        if book_id:
            info_dialog(self.gui, 'EPUB Merger', 'Successfully merged books into a new omnibus EPUB in your library.' +
                        (f'\nNotes: {"; ".join(errors)}' if errors else ''), show=True)
        else:
            show_friendly_error_dialog(self.gui, 'EPUB Merger', 'Could not merge books:\n' + '\n'.join(errors), show=True)

    def merge_folder(self):
        files, _ = QFileDialog.getOpenFileNames(self.gui, 'Select EPUB files to merge', '', 'EPUB (*.epub)')
        if not files or len(files) < 2:
            info_dialog(self.gui, 'EPUB Merger', 'Select at least two EPUB files.', show=True)
            return

        try:
            try:
                from .guikit import scan_epub_files_for_merge, MergePreviewDialog
            except ImportError:
                from calibre_plugins.epub_promo_scrubber.guikit import scan_epub_files_for_merge, MergePreviewDialog
        except ImportError:
            from guikit import scan_epub_files_for_merge, MergePreviewDialog

        scan_results = scan_epub_files_for_merge(files)
        if not scan_results:
            info_dialog(self.gui, 'EPUB Merger', 'No EPUB files found to merge.', show=True)
            return

        dialog = MergePreviewDialog(self.gui, scan_results)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        active_stories = dialog.get_active_stories()
        meta = dialog.get_metadata()
        if not active_stories:
            info_dialog(self.gui, 'EPUB Merger', 'No valid EPUB files or chapters selected to merge.', show=True)
            return

        out_path, _ = QFileDialog.getSaveFileName(self.gui, 'Save merged EPUB as', f"{meta['title'] or 'merged_omnibus'}.epub", 'EPUB (*.epub)')
        if not out_path:
            return

        try:
            try:
                from .storykit import merge_epubs
            except ImportError:
                from calibre_plugins.epub_promo_scrubber.storykit import merge_epubs
        except ImportError:
            from storykit import merge_epubs

        try:
            merged_bytes = merge_epubs(
                files,
                title=meta['title'],
                author=meta['author'],
                publisher=meta['publisher'],
                tags=meta['tags'],
                summary=meta['description'],
                stories_override=active_stories,
                preserve_story_order=True
            )
            with open(out_path, 'wb') as f:
                f.write(merged_bytes)
            info_dialog(self.gui, 'EPUB Merger', f'Merged {len(active_stories)} EPUBs into:\n{out_path}', show=True)
        except Exception as exc:
            show_friendly_error_dialog(self.gui, 'EPUB Merger failed', exc, show=True)
