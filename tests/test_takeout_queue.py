import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch
import zipfile

# Register calibre_plugins.takeout_book_fixer package path
if 'calibre_plugins' not in sys.modules:
    cp = types.ModuleType('calibre_plugins')
    cp.__path__ = []
    sys.modules['calibre_plugins'] = cp

plugin_dir = Path(__file__).resolve().parents[1] / 'plugin'
tbf = types.ModuleType('calibre_plugins.takeout_book_fixer')
tbf.__path__ = [str(plugin_dir)]
sys.modules['calibre_plugins.takeout_book_fixer'] = tbf

from calibre_plugins.takeout_book_fixer import queue_db, daemon, ui, config
from calibre_plugins.takeout_book_fixer.config import prefs


class TestTakeoutQueueDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'test_queue.db'

    def tearDown(self):
        self.tmp.cleanup()

    def test_init_db_and_enqueue(self):
        queue_db.init_db(self.db_path)
        job_id = queue_db.enqueue_job(
            file_path='/tmp/book1.zip',
            output_dir='/tmp/output',
            convert_comics=False,
            import_to_library=True,
            db_path=self.db_path
        )
        self.assertIsNotNone(job_id)

        # Enqueue same path returns existing job_id
        same_id = queue_db.enqueue_job(
            file_path='/tmp/book1.zip',
            output_dir='/tmp/output',
            db_path=self.db_path
        )
        self.assertEqual(job_id, same_id)

        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['status'], 'pending')
        self.assertEqual(jobs[0]['attempt_count'], 0)

    def test_status_transitions_and_backoff(self):
        queue_db.init_db(self.db_path)
        job_id = queue_db.enqueue_job('/tmp/test.zip', '/tmp/out', db_path=self.db_path)

        # Pending -> Processing
        queue_db.update_job_status(job_id, 'processing', db_path=self.db_path)
        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(jobs[0]['status'], 'processing')

        # First attempt failure -> backoff retry (pending, attempt=1, backoff=2s)
        status, attempts = queue_db.mark_job_attempt_failed(job_id, 'Locked file error', max_attempts=3, db_path=self.db_path)
        self.assertEqual(status, 'pending')
        self.assertEqual(attempts, 1)

        job = queue_db.get_all_jobs(self.db_path)[0]
        self.assertIn('Locked file error', job['error_log'])
        self.assertGreater(job['next_retry_at'], time.time())

        # Second attempt failure -> pending, attempt=2, backoff=4s
        status, attempts = queue_db.mark_job_attempt_failed(job_id, 'Second lock error', max_attempts=3, db_path=self.db_path)
        self.assertEqual(status, 'pending')
        self.assertEqual(attempts, 2)

        # Third attempt failure -> permanently failed (attempt=3)
        status, attempts = queue_db.mark_job_attempt_failed(job_id, 'Final error', max_attempts=3, db_path=self.db_path)
        self.assertEqual(status, 'failed')
        self.assertEqual(attempts, 3)

    def test_startup_recovery(self):
        queue_db.init_db(self.db_path)
        job_id = queue_db.enqueue_job('/tmp/crash.zip', '/tmp/out', db_path=self.db_path)
        queue_db.update_job_status(job_id, 'processing', db_path=self.db_path)

        # Reset processing jobs on startup
        queue_db.reset_processing_jobs_on_startup(self.db_path)
        job = queue_db.get_all_jobs(self.db_path)[0]
        self.assertEqual(job['status'], 'pending')

    def test_retry_and_clear_actions(self):
        queue_db.init_db(self.db_path)
        j1 = queue_db.enqueue_job('/tmp/j1.zip', '/tmp/out', db_path=self.db_path)
        j2 = queue_db.enqueue_job('/tmp/j2.zip', '/tmp/out', db_path=self.db_path)

        queue_db.update_job_status(j1, 'completed', db_path=self.db_path)
        queue_db.update_job_status(j2, 'failed', error_log='Failed', db_path=self.db_path)

        # Retry single
        queue_db.retry_job(j2, db_path=self.db_path)
        job2 = [j for j in queue_db.get_all_jobs(self.db_path) if j['id'] == j2][0]
        self.assertEqual(job2['status'], 'pending')
        self.assertEqual(job2['attempt_count'], 0)

        # Clear completed
        queue_db.clear_completed_jobs(self.db_path)
        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['id'], j2)


class TestTakeoutDaemon(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.drop_dir = Path(self.tmp.name) / 'drop'
        self.drop_dir.mkdir()
        self.out_dir = Path(self.tmp.name) / 'out'
        self.out_dir.mkdir()
        self.db_path = Path(self.tmp.name) / 'daemon_queue.db'

        prefs['drop_folder'] = str(self.drop_dir)
        prefs['default_output_dir'] = str(self.out_dir)
        prefs['auto_import'] = False
        prefs['convert_comics'] = False

    def tearDown(self):
        self.tmp.cleanup()

    def test_drop_folder_scanning_and_execution(self):
        # Create dummy EPUB in drop folder
        dummy_epub = self.drop_dir / 'test_book.epub'
        with zipfile.ZipFile(dummy_epub, 'w') as z:
            z.writestr('mimetype', 'application/epub+zip')
            z.writestr('META-INF/container.xml', '<container/>')

        d = daemon.ImportQueueDaemon(gui=None, db_path=self.db_path)
        d.tick()

        # Job should be enqueued and executed
        time.sleep(0.1)
        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(len(jobs), 1)
        self.assertIn(jobs[0]['status'], ('completed', 'processing'))

        # Wait for executor
        d.executor.shutdown(wait=True)
        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(jobs[0]['status'], 'completed')

        # Check recovered output
        recovered_files = list(self.out_dir.glob('book-*'))
        self.assertGreaterEqual(len(recovered_files), 1)

    def test_transient_error_handling_and_retry(self):
        # Create non-existent file path job
        job_id = queue_db.enqueue_job('/tmp/non_existent_file.zip', str(self.out_dir), db_path=self.db_path)

        d = daemon.ImportQueueDaemon(gui=None, db_path=self.db_path)
        d.process_next_job()
        d.executor.shutdown(wait=True)

        job = queue_db.get_all_jobs(self.db_path)[0]
        self.assertEqual(job['status'], 'pending')
        self.assertEqual(job['attempt_count'], 1)
        self.assertIn('Source file does not exist', job['error_log'])

    def test_pause_and_resume(self):
        d = daemon.ImportQueueDaemon(gui=None, db_path=self.db_path)
        d.pause()
        self.assertTrue(d.is_paused)

        dummy_file = self.drop_dir / 'book.pdf'
        dummy_file.write_bytes(b'%PDF-1.4 header text')

        d.tick()
        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(len(jobs), 0)

        d.resume()
        d.tick()
        time.sleep(0.1)
        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(len(jobs), 1)


class TestTakeoutUI(unittest.TestCase):
    def setUp(self):
        try:
            from qt.core import QApplication
            self.app = QApplication.instance()
            if self.app is None:
                self.app = QApplication([])
        except Exception:
            self.app = None
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'ui_queue.db'
        queue_db.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_queue_manager_dialog_headless(self):
        queue_db.enqueue_job('/tmp/file1.zip', '/tmp/out', db_path=self.db_path)
        queue_db.enqueue_job('/tmp/file2.zip', '/tmp/out', db_path=self.db_path)

        d = daemon.ImportQueueDaemon(gui=None, db_path=self.db_path)
        dlg = ui.QueueManagerDialog(daemon=d, parent=None, db_path=self.db_path)

        # Refresh populates table or internal items
        dlg.refresh()

        # Test retry all button action
        dlg.retry_all()

        # Test clear completed button action
        dlg.clear_completed()

        # Test clear all button action
        dlg.clear_all()
        jobs = queue_db.get_all_jobs(self.db_path)
        self.assertEqual(len(jobs), 0)


if __name__ == '__main__':
    unittest.main()
