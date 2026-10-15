from io import BytesIO
from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog
from calibre.utils.config import JSONConfig
from qt.core import (QAction, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QMenu,
                     QPlainTextEdit, QVBoxLayout)
try:
    from calibre_plugins.epub_promo_scrubber.guikit import MergeSelectedActionMixin, run_task
except ImportError:
    try:
        from .guikit import MergeSelectedActionMixin, run_task
    except ImportError:
        try:
            from guikit import MergeSelectedActionMixin, run_task
        except ImportError:
            from common.guikit import MergeSelectedActionMixin, run_task

try:
    from calibre_plugins.epub_promo_scrubber.core import Matcher, _snippet, scrub_epub, scrub_files, tidy
except ImportError:
    try:
        from .core import Matcher, _snippet, scrub_epub, scrub_files, tidy
    except ImportError:
        from core import Matcher, _snippet, scrub_epub, scrub_files, tidy

prefs = JSONConfig('plugins/epub_promo_scrubber')
prefs.defaults.update(extra_patterns='', heuristic=True, drop_empty_pages=True, backup=True, in_place=False, show_preview=True)


class ScrubAction(MergeSelectedActionMixin, InterfaceAction):
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
        extra = [line for line in prefs['extra_patterns'].splitlines() if line.strip()]
        return extra, prefs['heuristic'], prefs['drop_empty_pages']

    def settings(self):
        dialog = QDialog(self.gui)
        dialog.setWindowTitle('EPUB Promo Scrubber settings')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Built in: OceanofPDF, PDFDrive, Z-Library, LibGen and similar names, URLs and links.\n'
                                'Extra site names or domains to remove, one per line (prefix a line with re: for a regular expression):'))
        patterns = QPlainTextEdit(prefs['extra_patterns'])
        layout.addWidget(patterns)
        show_preview = QCheckBox('Show preview dialog before applying changes')
        show_preview.setChecked(prefs.get('show_preview', True))
        heuristic = QCheckBox('Also remove short "Downloaded from <website>" / "free ebooks" style blocks from any site')
        heuristic.setChecked(prefs['heuristic'])
        drop = QCheckBox('Delete pages that contain nothing but promotion (and their contents entries)')
        drop.setChecked(prefs['drop_empty_pages'])
        backup = QCheckBox('Library books: keep the unmodified EPUB as ORIGINAL_EPUB (untick to replace the original with the cleaned version and discard any existing backup)')
        backup.setChecked(prefs['backup'])
        in_place = QCheckBox('Files and folders: replace the original files with cleaned versions instead of writing copies to another folder')
        in_place.setChecked(prefs['in_place'])
        for box in (show_preview, heuristic, drop, backup, in_place):
            layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            prefs['extra_patterns'] = patterns.toPlainText()
            prefs['show_preview'] = show_preview.isChecked()
            prefs['heuristic'] = heuristic.isChecked()
            prefs['drop_empty_pages'] = drop.isChecked()
            prefs['backup'] = backup.isChecked()
            prefs['in_place'] = in_place.isChecked()

    def scrub_selected(self):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids:
            info_dialog(self.gui, 'EPUB Promo Scrubber', 'Select one or more books in Calibre first.', show=True)
            return
        extra, heuristic, drop = self.options()
        db = self.gui.current_db.new_api
        matcher = Matcher(extra, heuristic)

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
                    if data:
                        _, report = scrub_epub(data, extra, heuristic, drop, dry_run=True)
                        book_changes.extend(report.get('changes', []))
                    meta_changes = self.scrub_fields(db, book_id, matcher, dry_run=True)
                    book_changes.extend(meta_changes)
                    if book_changes:
                        scan_results.append({
                            'book_id': book_id,
                            'title': title,
                            'data': data,
                            'changes': book_changes
                        })
                return scan_results

            task = run_task(self.gui, 'Scanning EPUBs for promo content...', scan_worker)
            if task.cancelled() or task.error:
                if task.error:
                    error_dialog(self.gui, 'Scan failed', str(task.error), show=True)
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
                            new_bytes, report = scrub_epub(data, extra, heuristic, drop, allowed_change_ids=allowed_ids)
                            if new_bytes is not None:
                                if prefs['backup']:
                                    db.save_original_format(book_id, 'EPUB')
                                elif 'ORIGINAL_EPUB' in (db.formats(book_id) or ()):
                                    db.remove_formats({book_id: {'ORIGINAL_EPUB'}})
                                if not db.add_format(book_id, 'EPUB', BytesIO(new_bytes), replace=True):
                                    raise ValueError('Calibre could not save the scrubbed EPUB')
                                book_changed = True

                        field_changes = self.scrub_fields(db, book_id, matcher, allowed_change_ids=allowed_ids)
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
                        new, report = scrub_epub(data, extra, heuristic, drop)
                        if new is not None:
                            if prefs['backup']:
                                db.save_original_format(book_id, 'EPUB')
                            elif 'ORIGINAL_EPUB' in (db.formats(book_id) or ()):
                                db.remove_formats({book_id: {'ORIGINAL_EPUB'}})
                            if not db.add_format(book_id, 'EPUB', BytesIO(new), replace=True):
                                raise ValueError('Calibre could not save the scrubbed EPUB')
                            changed.append(book_id)
                        else:
                            unchanged += 1
                        self.scrub_fields(db, book_id, matcher)
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
                    'action': f'calibre metadata trimmed',
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
        from qt.core import QMessageBox
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
                        _, report = scrub_epub(data, extra, heuristic, drop, dry_run=True)
                        if report.get('changes'):
                            scan_results.append({
                                'path': str(p),
                                'title': p.name,
                                'changes': report['changes']
                            })
                    except Exception:
                        pass
                return scan_results

            task = run_task(self.gui, 'Scanning EPUB files for promo content...', scan_folder_worker)
            if task.cancelled() or task.error:
                if task.error:
                    error_dialog(self.gui, 'Scan failed', str(task.error), show=True)
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
                rows, report_path = scrub_files(source, output, extra, heuristic, drop, in_place, allowed_change_ids=allowed_ids)
                done = 'replaced' if in_place else 'scrubbed'
                counts = {s: sum(r['status'] == s for r in rows) for s in (done, 'unchanged', 'error')}
                info_dialog(self.gui, 'EPUB Promo Scrubber',
                            f'{counts[done]} {done}, {counts["unchanged"]} unchanged, {counts["error"]} errors.\n' +
                            ('Original files were overwritten.' if in_place else 'Originals were not modified.') + f'\nReport: {report_path}', show=True)
            except Exception as exc:
                error_dialog(self.gui, 'EPUB Promo Scrubber failed', str(exc), show=True)

        else:
            try:
                rows, report_path = scrub_files(source, output, extra, heuristic, drop, in_place)
                done = 'replaced' if in_place else 'scrubbed'
                counts = {s: sum(r['status'] == s for r in rows) for s in (done, 'unchanged', 'error')}
                info_dialog(self.gui, 'EPUB Promo Scrubber',
                            f'{counts[done]} {done}, {counts["unchanged"]} unchanged, {counts["error"]} errors.\n' +
                            ('Original files were overwritten.' if in_place else 'Originals were not modified.') + f'\nReport: {report_path}', show=True)
            except Exception as exc:
                error_dialog(self.gui, 'EPUB Promo Scrubber failed', str(exc), show=True)

    def merge_folder(self):
        files, _ = QFileDialog.getOpenFileNames(self.gui, 'Select EPUB files to merge', '', 'EPUB (*.epub)')
        if not files or len(files) < 2:
            info_dialog(self.gui, 'EPUB Merger', 'Select at least two EPUB files.', show=True)
            return
        out_path, _ = QFileDialog.getSaveFileName(self.gui, 'Save merged EPUB as', 'merged_omnibus.epub', 'EPUB (*.epub)')
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
            merged_bytes = merge_epubs(files)
            with open(out_path, 'wb') as f:
                f.write(merged_bytes)
            info_dialog(self.gui, 'EPUB Merger', f'Merged {len(files)} EPUBs into:\n{out_path}', show=True)
        except Exception as exc:
            error_dialog(self.gui, 'EPUB Merger failed', str(exc), show=True)
