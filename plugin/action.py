from pathlib import Path
from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog
from qt.core import QAction, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QLabel, QVBoxLayout, QProgressDialog, Qt
from calibre_plugins.takeout_book_fixer.core import recover

class TakeoutAction(InterfaceAction):
    name = 'Takeout Book Fixer'
    action_spec = ('Takeout Book Fixer', None, 'Recover books from Google Takeout', None)

    def genesis(self):
        menu = self.qaction.menu()
        from qt.core import QMenu
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        for title, archive in [('Recover from Takeout folder', False), ('Import book file or Takeout ZIP', True)]:
            action = QAction(title, self.gui)
            action.triggered.connect(lambda checked=False, archive=archive: self.run_recovery(archive))
            menu.addAction(action)
        repair = QAction('Repair selected books already in Calibre', self.gui)
        repair.triggered.connect(self.repair_selected)
        menu.addAction(repair)
        merge = QAction('Merge selected books into one EPUB…', self.gui)
        merge.triggered.connect(self.merge_selected)
        menu.addAction(merge)
        self.qaction.triggered.connect(lambda: self.run_recovery(False))

    def run_recovery(self, archive=False):
        if archive:
            source, _ = QFileDialog.getOpenFileName(self.gui, 'Select book file or Google Takeout ZIP', '', 'Books and Takeout (*.zip *.pdf *.epub *.mobi *.azw *.azw3 *.cbz *.cbr *.djvu);;All files (*)')
        else:
            source = QFileDialog.getExistingDirectory(self.gui, 'Select extracted Google Takeout folder')
        if not source:
            return
        output = QFileDialog.getExistingDirectory(self.gui, 'Select a separate folder for recovered books')
        if not output:
            return
        dialog = QDialog(self.gui)
        dialog.setWindowTitle('Takeout recovery options')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Original files are preserved. Unknown files are skipped.\nCorrected copies and a JSON report are written to the output folder.'))
        comics = QCheckBox('Convert text-free PDFs to CBZ comics (also affects scanned novels)')
        layout.addWidget(comics)
        import_library = QCheckBox('Import recovered books into the current Calibre library')
        import_library.setChecked(True)
        layout.addWidget(import_library)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        progress = QProgressDialog('Recovering books…', '', 0, 0, self.gui)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.show()
        try:
            # Worker keeps Calibre responsive while files and PDF pages are processed.
            from concurrent.futures import ThreadPoolExecutor
            from qt.core import QApplication
            import time
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(recover, source, output, comics.isChecked())
                while not future.done():
                    QApplication.processEvents()
                    time.sleep(0.05)
                files, report, report_path = future.result()
            imported, import_errors = [], []
            if import_library.isChecked():
                progress.setLabelText('Importing books and reading metadata…')
                from calibre_plugins.takeout_book_fixer.importer import import_books
                imported, import_errors = import_books(self.gui, report)
                import json
                with open(report_path, 'w', encoding='utf-8') as stream:
                    json.dump(report, stream, indent=2, ensure_ascii=False)
            errors = sum(r['status'] == 'error' for r in report)
            info_dialog(self.gui, 'Takeout recovery complete',
                        f'{len(files)} books recovered or already present; {errors} recovery errors.\n{len(imported)} imported into Calibre; {len(import_errors)} import errors.\nOutput: {output}\nReport: {report_path}', show=True)
        except Exception as e:
            error_dialog(self.gui, 'Takeout recovery failed', str(e), show=True)
        finally:
            progress.close()

    def repair_selected(self):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids:
            info_dialog(self.gui, 'Takeout book fixer', 'Select one or more books in Calibre first.', show=True)
            return
        dialog = QDialog(self.gui)
        dialog.setWindowTitle('Repair selected Calibre books')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('Corrected formats replace mislabeled formats in the selected books.'))
        comics = QCheckBox('Convert text-free PDFs to CBZ comics')
        layout.addWidget(comics)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            from calibre_plugins.takeout_book_fixer.importer import repair_library_books
            repaired, skipped, errors = repair_library_books(self.gui, ids, comics.isChecked())
            info_dialog(self.gui, 'Calibre repair complete',
                        f'{len(repaired)} format(s) repaired; {len(skipped)} already correct; {len(errors)} errors.' +
                        (f'\nErrors: {"; ".join(errors)}' if errors else ''), show=True)
        except Exception as exc:
            error_dialog(self.gui, 'Calibre repair failed', str(exc), show=True)

    def merge_selected(self):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids or len(ids) < 2:
            info_dialog(self.gui, 'EPUB Merger', 'Select two or more books in Calibre first to merge.', show=True)
            return

        try:
            try:
                from .guikit import scan_library_books_for_merge, MergePreviewDialog
            except ImportError:
                from calibre_plugins.takeout_book_fixer.guikit import scan_library_books_for_merge, MergePreviewDialog
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
                from calibre_plugins.takeout_book_fixer.libkit import merge_library_books
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
            error_dialog(self.gui, 'EPUB Merger', 'Could not merge books:\n' + '\n'.join(errors), show=True)

