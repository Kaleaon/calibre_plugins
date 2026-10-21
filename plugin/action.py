from calibre.gui2.actions import InterfaceAction
from calibre.gui2 import error_dialog, info_dialog
from qt.core import QAction, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QVBoxLayout, QProgressDialog, Qt
try:
    from calibre_plugins.takeout_book_fixer.error_handler import show_friendly_error_dialog
except ImportError:
    try:
        from .error_handler import show_friendly_error_dialog
    except ImportError:
        try:
            from error_handler import show_friendly_error_dialog
        except ImportError:
            try:
                from common.error_handler import show_friendly_error_dialog
            except ImportError:
                show_friendly_error_dialog = None

try:
    from calibre_plugins.takeout_book_fixer.core import recover
except ImportError:
    try:
        from .core import recover
    except ImportError:
        from core import recover

try:
    from calibre_plugins.takeout_book_fixer.watcher import WatchFolderManager
    from calibre_plugins.takeout_book_fixer.ui import ConfigWidget, ConfigDialog
except ImportError:
    try:
        from .watcher import WatchFolderManager
        from .ui import ConfigWidget, ConfigDialog
    except ImportError:
        from watcher import WatchFolderManager
        from ui import ConfigWidget, ConfigDialog

try:
    from calibre_plugins.takeout_book_fixer.guikit import MergeSelectedActionMixin, run_task
except ImportError:
    try:
        from .guikit import MergeSelectedActionMixin, run_task
    except (ImportError, ValueError):
        try:
            from guikit import MergeSelectedActionMixin, run_task
        except ImportError:
            from common.guikit import MergeSelectedActionMixin, run_task


class TakeoutAction(MergeSelectedActionMixin, InterfaceAction):
    name = 'Takeout Book Fixer'
    action_spec = ('Takeout Book Fixer', None, 'Recover books from Google Takeout', None)

    def genesis(self):
        self.daemon = None
        try:
            from calibre_plugins.takeout_book_fixer.daemon import ImportQueueDaemon
            self.daemon = ImportQueueDaemon(gui=self.gui)
            self.daemon.start()
        except Exception:
            pass

        menu = self.qaction.menu()
        from qt.core import QMenu
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        for title, archive in [('Recover from Takeout folder', False), ('Import book file or Takeout ZIP', True)]:
            action = QAction(title, self.gui)
            action.triggered.connect(lambda checked=False, archive=archive: self.run_recovery(archive))
            menu.addAction(action)

        queue_action = QAction('Import Queue Manager…', self.gui)
        queue_action.triggered.connect(self.show_queue_manager)
        menu.addAction(queue_action)

        settings_action = QAction('Configure Drop Folder…', self.gui)
        settings_action.triggered.connect(self.show_drop_settings)
        menu.addAction(settings_action)

        repair = QAction('Repair selected books already in Calibre', self.gui)
        repair.triggered.connect(self.repair_selected)
        menu.addAction(repair)
        merge = QAction('Merge selected books into one EPUB…', self.gui)
        merge.triggered.connect(self.merge_selected)
        menu.addAction(merge)
        menu.addSeparator()
        configure = QAction('Configure Watch Folder & Preferences…', self.gui)
        configure.triggered.connect(self.show_configuration)
        menu.addAction(configure)
        self.qaction.triggered.connect(lambda: self.run_recovery(False))

        self.watch_manager = WatchFolderManager(self.gui)
        self.watch_manager.start_watching()

    def show_queue_manager(self):
        try:
            from calibre_plugins.takeout_book_fixer.ui import QueueManagerDialog
            dlg = QueueManagerDialog(daemon=self.daemon, parent=self.gui)
            dlg.exec()
        except Exception as e:
            error_dialog(self.gui, 'Queue Manager Error', str(e), show=True)

    def show_drop_settings(self):
        try:
            from calibre_plugins.takeout_book_fixer.ui import DropFolderConfigDialog
            dlg = DropFolderConfigDialog(parent=self.gui)
            if dlg.exec():
                if self.daemon:
                    self.daemon.scan_drop_folder()
        except Exception as e:
            error_dialog(self.gui, 'Drop Folder Error', str(e), show=True)

    def is_customizable(self):
        return True

    def config_widget(self):
        return ConfigWidget(self.gui)

    def save_settings(self, config_widget):
        config_widget.save_settings()
        if hasattr(self, 'watch_manager'):
            self.watch_manager.start_watching()

    def show_configuration(self):
        dialog = ConfigDialog(self.gui)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if hasattr(self, 'watch_manager'):
                self.watch_manager.start_watching()

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

        policy_layout = QHBoxLayout()
        policy_label = QLabel('Duplicate collision policy:')
        policy_combo = QComboBox()
        policy_combo.addItem('Prompt on Conflict', 'prompt')
        policy_combo.addItem('Auto-Merge Formats', 'merge')
        policy_combo.addItem('Auto-Overwrite Existing', 'overwrite')
        policy_combo.addItem('Auto-Skip Duplicates', 'skip')
        policy_combo.addItem('Keep Both', 'keep_both')
        policy_layout.addWidget(policy_label)
        policy_layout.addWidget(policy_combo)
        layout.addLayout(policy_layout)

        import_library.toggled.connect(lambda checked: policy_combo.setEnabled(checked))
        import_library.toggled.connect(lambda checked: policy_label.setEnabled(checked))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        duplicate_policy = policy_combo.currentData() or 'prompt'
        def work(task):
            return recover(
                source,
                output,
                comics=comics.isChecked(),
                progress=lambda msg: setattr(task, 'status', f'Recovering {msg}…'),
                cancelled=task.cancelled,
            )

        task = run_task(self.gui, 'Recovering books…', work)
        if task.error:
            show_friendly_error_dialog(self.gui, 'Takeout recovery failed', task.error, show=True)
            return

        if task.result is None:
            return

        files, report, report_path = task.result

        if task.cancelled():
            info_dialog(
                self.gui,
                'Takeout recovery cancelled',
                f'Recovery cancelled by user.
{len(files)} books recovered or already present before cancellation.
Output: {output}
Report: {report_path}',
                show=True,
            )
            return

        imported, import_errors = [], []
        if import_library.isChecked():
            from calibre_plugins.takeout_book_fixer.importer import import_books
            imported, import_errors = import_books(self.gui, report, duplicate_policy=duplicate_policy)
            import json
            with open(report_path, 'w', encoding='utf-8') as stream:
                json.dump(report, stream, indent=2, ensure_ascii=False)
        errors = sum(r['status'] == 'error' for r in report)
        info_dialog(
            self.gui,
            'Takeout recovery complete',
            f'{len(files)} books recovered or already present; {errors} recovery errors.
{len(imported)} imported into Calibre; {len(import_errors)} import errors.
Output: {output}
Report: {report_path}',
            show=True,
        )

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
            show_friendly_error_dialog(self.gui, 'Calibre repair failed', exc, show=True)

