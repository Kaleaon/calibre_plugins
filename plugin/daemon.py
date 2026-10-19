from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import sys
import time

try:
    from qt.core import QObject, QTimer
except ImportError:
    class QObject:
        pass
    QTimer = None

from calibre_plugins.takeout_book_fixer.config import prefs
from calibre_plugins.takeout_book_fixer.core import recover, BOOK_SUFFIXES
from calibre_plugins.takeout_book_fixer.queue_db import (
    enqueue_job, get_next_pending_job, update_job_status,
    mark_job_attempt_failed, reset_processing_jobs_on_startup,
    get_all_jobs
)


class ImportQueueDaemon(QObject):
    def __init__(self, gui=None, db_path=None):
        if QObject is not None and issubclass(QObject, type):
            super().__init__()
        self.gui = gui
        self.db_path = db_path
        self.is_paused = False
        self.is_running_job = False
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='TakeoutDaemonWorker')
        self.timer = None

        # Reset any crashed/interrupted processing state from previous Calibre runs
        reset_processing_jobs_on_startup(self.db_path)

        if QTimer is not None:
            self.timer = QTimer()
            self.timer.timeout.connect(self.tick)

    def start(self, interval_sec=None):
        if interval_sec is None:
            interval_sec = prefs.get('watch_interval_sec', 5)
        if self.timer is not None:
            self.timer.start(int(interval_sec * 1000))

    def stop(self):
        if self.timer is not None:
            self.timer.stop()
        self.executor.shutdown(wait=False)

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def tick(self):
        """Called periodically by QTimer or manual test calls."""
        if self.is_paused:
            return
        self.scan_drop_folder()
        if not self.is_running_job:
            self.process_next_job()

    def scan_drop_folder(self):
        drop_folder = prefs.get('drop_folder', '')
        if not drop_folder:
            return
        drop_path = Path(drop_folder)
        if not drop_path.is_dir():
            return

        default_out = prefs.get('default_output_dir', '')
        output_dir = Path(default_out) if default_out else drop_path / 'Recovered'

        try:
            for item in drop_path.iterdir():
                if item.is_file() and not item.name.startswith('.'):
                    ext = item.suffix.lower()
                    if ext in BOOK_SUFFIXES or ext == '.zip':
                        enqueue_job(
                            file_path=item,
                            output_dir=output_dir,
                            convert_comics=prefs.get('convert_comics', False),
                            import_to_library=prefs.get('auto_import', True),
                            db_path=self.db_path
                        )
        except Exception:
            pass

    def process_next_job(self):
        if self.is_paused or self.is_running_job:
            return
        job = get_next_pending_job(db_path=self.db_path)
        if not job:
            return

        self.is_running_job = True
        update_job_status(job['id'], 'processing', db_path=self.db_path)
        self.executor.submit(self._execute_job, job)

    def _execute_job(self, job):
        try:
            file_path = Path(job['file_path'])
            if not file_path.exists():
                raise FileNotFoundError(f"Source file does not exist: {file_path}")

            output_dir = Path(job['output_dir'])
            output_dir.mkdir(parents=True, exist_ok=True)

            written, report, report_path = recover(
                source=file_path,
                output=output_dir,
                comics=bool(job['convert_comics'])
            )

            if job['import_to_library'] and self.gui is not None:
                from calibre_plugins.takeout_book_fixer.importer import import_books
                import json
                imported, import_errors = import_books(self.gui, report)
                with open(report_path, 'w', encoding='utf-8') as f:
                    json.dump(report, f, indent=2, ensure_ascii=False)

            # Check if recovery/import was successful or if critical errors occurred
            errors = [r for r in report if r.get('status') == 'error']
            if errors and not written:
                first_err = errors[0].get('reason', 'Recovery error')
                raise RuntimeError(first_err)

            update_job_status(job['id'], 'completed', db_path=self.db_path)
        except Exception as exc:
            mark_job_attempt_failed(
                job['id'],
                str(exc),
                max_attempts=job.get('max_attempts', 3),
                db_path=self.db_path
            )
        finally:
            self.is_running_job = False
