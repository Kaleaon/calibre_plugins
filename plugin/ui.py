import datetime
from pathlib import Path
import time

try:
    from qt.core import (
        QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
        QPushButton, QLabel, QFileDialog, QTimer, QCheckBox, QLineEdit,
        QHeaderView, QMessageBox, Qt, QAbstractItemView
    )
except ImportError:
    class QDialog:
        def __init__(self, parent=None):
            self.parent = parent
        def setWindowTitle(self, title):
            pass
        def resize(self, w, h):
            pass
        def exec(self):
            return 1
        def accept(self):
            pass
        def reject(self):
            pass

    class DummyWidget:
        def __init__(self, *args, **kwargs):
            self.clicked = self
            self.timeout = self
        def start(self, *args, **kwargs):
            pass
        def stop(self, *args, **kwargs):
            pass
        def addWidget(self, *args, **kwargs):
            pass
        def addLayout(self, *args, **kwargs):
            pass
        def addStretch(self, *args, **kwargs):
            pass
        def setHorizontalHeaderLabels(self, *args, **kwargs):
            pass
        def horizontalHeader(self):
            return DummyWidget()
        def setSectionResizeMode(self, *args, **kwargs):
            pass
        def setSelectionBehavior(self, *args, **kwargs):
            pass
        def setRowCount(self, count):
            pass
        def setItem(self, *args, **kwargs):
            pass
        def selectedItems(self):
            return []
        def item(self, row, col):
            return None
        def connect(self, func):
            pass
        def setChecked(self, val):
            pass
        def isChecked(self):
            return True
        def setText(self, text):
            pass
        def text(self):
            return ''
        def setToolTip(self, text):
            pass
        def setData(self, *args, **kwargs):
            pass
        def data(self, *args, **kwargs):
            return None

    QVBoxLayout = QHBoxLayout = QTableWidget = QTableWidgetItem = QPushButton = QLabel = QLineEdit = QCheckBox = QHeaderView = DummyWidget
    DummyWidget.ResizeMode = type('RM', (), {'Stretch': 1})()
    DummyWidget.SelectionBehavior = type('SB', (), {'SelectRows': 1})()
    QFileDialog = QMessageBox = QTimer = QAbstractItemView = DummyWidget
    Qt = type('Qt', (), {'WindowModality': type('WM', (), {'NonModal': 0})(), 'ItemDataRole': type('IDR', (), {'UserRole': 32})()})()

from calibre_plugins.takeout_book_fixer.config import prefs
from calibre_plugins.takeout_book_fixer.queue_db import (
    get_all_jobs, retry_job, retry_all_failed, clear_completed_jobs,
    clear_all_jobs, delete_job
)


class DropFolderConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Configure Drop Folder & Watch Settings')
        self.resize(500, 250)
        layout = QVBoxLayout(self)

        # Drop folder selection
        layout.addWidget(QLabel('Drop Folder (watches for incoming zip/book files):'))
        drop_layout = QHBoxLayout()
        self.drop_edit = QLineEdit(prefs.get('drop_folder', ''))
        drop_layout.addWidget(self.drop_edit)
        btn_browse_drop = QPushButton('Browse…')
        btn_browse_drop.clicked.connect(self.browse_drop)
        drop_layout.addWidget(btn_browse_drop)
        layout.addLayout(drop_layout)

        # Default output folder selection
        layout.addWidget(QLabel('Default Recovered Books Folder (leave blank for drop_folder/Recovered):'))
        out_layout = QHBoxLayout()
        self.out_edit = QLineEdit(prefs.get('default_output_dir', ''))
        out_layout.addWidget(self.out_edit)
        btn_browse_out = QPushButton('Browse…')
        btn_browse_out.clicked.connect(self.browse_out)
        out_layout.addWidget(btn_browse_out)
        layout.addLayout(out_layout)

        # Checkboxes
        self.auto_import_cb = QCheckBox('Automatically import recovered books into Calibre library')
        self.auto_import_cb.setChecked(prefs.get('auto_import', True))
        layout.addWidget(self.auto_import_cb)

        self.convert_comics_cb = QCheckBox('Convert text-free PDFs to CBZ comics')
        self.convert_comics_cb.setChecked(prefs.get('convert_comics', False))
        layout.addWidget(self.convert_comics_cb)

        # Action buttons
        btn_layout = QHBoxLayout()
        btn_save = QPushButton('Save')
        btn_save.clicked.connect(self.save)
        btn_cancel = QPushButton('Cancel')
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

    def browse_drop(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select Drop Folder')
        if folder:
            self.drop_edit.setText(folder)

    def browse_out(self):
        folder = QFileDialog.getExistingDirectory(self, 'Select Output Folder')
        if folder:
            self.out_edit.setText(folder)

    def save(self):
        prefs['drop_folder'] = self.drop_edit.text().strip()
        prefs['default_output_dir'] = self.out_edit.text().strip()
        prefs['auto_import'] = self.auto_import_cb.isChecked()
        prefs['convert_comics'] = self.convert_comics_cb.isChecked()
        self.accept()


class QueueManagerDialog(QDialog):
    def __init__(self, daemon=None, parent=None, db_path=None):
        super().__init__(parent)
        self.daemon = daemon
        self.db_path = db_path if db_path else (daemon.db_path if daemon else None)
        self.setWindowTitle('Takeout Import Queue Manager')
        self.resize(800, 450)

        layout = QVBoxLayout(self)

        # Top Bar Status & Controls
        top_layout = QHBoxLayout()
        self.lbl_status = QLabel('Queue Status: Active')
        top_layout.addWidget(self.lbl_status)
        top_layout.addStretch()

        self.btn_pause = QPushButton('Pause Queue')
        if self.daemon and self.daemon.is_paused:
            self.btn_pause.setText('Resume Queue')
        self.btn_pause.clicked.connect(self.toggle_pause)
        top_layout.addWidget(self.btn_pause)

        btn_settings = QPushButton('Drop Folder Settings')
        btn_settings.clicked.connect(self.open_settings)
        top_layout.addWidget(btn_settings)

        layout.addLayout(top_layout)

        # Jobs Table
        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(['ID', 'Source File', 'Status', 'Attempts', 'Error / Log', 'Updated'])
        if QHeaderView is not None:
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        if QAbstractItemView is not None:
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        # Bottom Action Buttons
        bot_layout = QHBoxLayout()

        btn_retry_sel = QPushButton('Retry Selected')
        btn_retry_sel.clicked.connect(self.retry_selected)
        bot_layout.addWidget(btn_retry_sel)

        btn_retry_all = QPushButton('Retry All Failed')
        btn_retry_all.clicked.connect(self.retry_all)
        bot_layout.addWidget(btn_retry_all)

        btn_clear_comp = QPushButton('Clear Completed')
        btn_clear_comp.clicked.connect(self.clear_completed)
        bot_layout.addWidget(btn_clear_comp)

        btn_clear_all = QPushButton('Clear All')
        btn_clear_all.clicked.connect(self.clear_all)
        bot_layout.addWidget(btn_clear_all)

        btn_details = QPushButton('View Details')
        btn_details.clicked.connect(self.view_details)
        bot_layout.addWidget(btn_details)

        btn_close = QPushButton('Close')
        btn_close.clicked.connect(self.accept)
        bot_layout.addWidget(btn_close)

        layout.addLayout(bot_layout)

        # Auto refresh timer
        if QTimer is not None:
            self.refresh_timer = QTimer(self)
            self.refresh_timer.timeout.connect(self.refresh)
            self.refresh_timer.start(1000)

        self.refresh()

    def toggle_pause(self):
        if not self.daemon:
            return
        if self.daemon.is_paused:
            self.daemon.resume()
            self.btn_pause.setText('Pause Queue')
            self.lbl_status.setText('Queue Status: Active')
        else:
            self.daemon.pause()
            self.btn_pause.setText('Resume Queue')
            self.lbl_status.setText('Queue Status: Paused')

    def open_settings(self):
        dlg = DropFolderConfigDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted if hasattr(QDialog, 'DialogCode') else 1:
            if self.daemon:
                self.daemon.scan_drop_folder()
            self.refresh()

    def refresh(self):
        if self.table is None:
            return
        jobs = get_all_jobs(self.db_path)
        self.table.setRowCount(len(jobs))

        if self.daemon:
            if self.daemon.is_paused:
                self.lbl_status.setText('Queue Status: Paused')
            else:
                self.lbl_status.setText('Queue Status: Active')

        for row_idx, job in enumerate(jobs):
            item_id = QTableWidgetItem(str(job['id']))
            item_file = QTableWidgetItem(Path(job['file_path']).name)
            item_file.setToolTip(job['file_path'])
            item_status = QTableWidgetItem(str(job['status']))
            item_attempts = QTableWidgetItem(f"{job['attempt_count']}/{job['max_attempts']}")
            item_err = QTableWidgetItem(str(job.get('error_log', '')))

            ts = job.get('updated_at', 0)
            dt_str = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else ''
            item_time = QTableWidgetItem(dt_str)

            # Store full job dict on row ID item for easy reference
            if hasattr(item_id, 'setData'):
                item_id.setData(Qt.ItemDataRole.UserRole if hasattr(Qt, 'ItemDataRole') else 32, job)

            self.table.setItem(row_idx, 0, item_id)
            self.table.setItem(row_idx, 1, item_file)
            self.table.setItem(row_idx, 2, item_status)
            self.table.setItem(row_idx, 3, item_attempts)
            self.table.setItem(row_idx, 4, item_err)
            self.table.setItem(row_idx, 5, item_time)

    def get_selected_job(self):
        if self.table is None:
            return None
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        if item and hasattr(item, 'data'):
            return item.data(Qt.ItemDataRole.UserRole if hasattr(Qt, 'ItemDataRole') else 32)
        return None

    def retry_selected(self):
        job = self.get_selected_job()
        if job:
            retry_job(job['id'], self.db_path)
            if self.daemon:
                self.daemon.tick()
            self.refresh()

    def retry_all(self):
        retry_all_failed(self.db_path)
        if self.daemon:
            self.daemon.tick()
        self.refresh()

    def clear_completed(self):
        clear_completed_jobs(self.db_path)
        self.refresh()

    def clear_all(self):
        clear_all_jobs(self.db_path)
        self.refresh()

    def view_details(self):
        job = self.get_selected_job()
        if not job:
            return
        msg = f"Job ID: {job['id']}\n" \
              f"File: {job['file_path']}\n" \
              f"Output: {job['output_dir']}\n" \
              f"Status: {job['status']}\n" \
              f"Attempts: {job['attempt_count']} / {job['max_attempts']}\n" \
              f"Error / Log: {job.get('error_log', 'None')}"
        if QMessageBox is not None:
            QMessageBox.information(self, f"Job Details #{job['id']}", msg)
        else:
            print(msg)
