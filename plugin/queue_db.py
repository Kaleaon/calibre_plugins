import os
from pathlib import Path
import sqlite3
import threading
import time

_DB_LOCK = threading.RLock()
_OVERRIDE_DB_PATH = None


def set_override_db_path(path):
    global _OVERRIDE_DB_PATH
    _OVERRIDE_DB_PATH = path


def get_queue_db_path(db_path=None):
    if db_path:
        return Path(db_path)
    if _OVERRIDE_DB_PATH:
        return Path(_OVERRIDE_DB_PATH)
    try:
        from calibre.utils.config import config_dir
        base_dir = Path(config_dir) / 'plugins' / 'takeout_book_fixer'
    except Exception:
        base_dir = Path.home() / '.config' / 'calibre' / 'plugins' / 'takeout_book_fixer'
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / 'queue.db'


def get_db_connection(db_path=None):
    path = get_queue_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA journal_mode=WAL;')
    except Exception:
        pass
    return conn


def init_db(db_path=None):
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        file_path TEXT NOT NULL UNIQUE,
                        output_dir TEXT NOT NULL,
                        convert_comics INTEGER DEFAULT 0,
                        import_to_library INTEGER DEFAULT 1,
                        status TEXT NOT NULL DEFAULT 'pending',
                        attempt_count INTEGER DEFAULT 0,
                        max_attempts INTEGER DEFAULT 3,
                        next_retry_at REAL DEFAULT 0.0,
                        error_log TEXT DEFAULT '',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                ''')
        finally:
            conn.close()


def enqueue_job(file_path, output_dir, convert_comics=False, import_to_library=True, max_attempts=3, db_path=None):
    file_path = str(Path(file_path).resolve())
    output_dir = str(Path(output_dir).resolve())
    now = time.time()
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT id, status FROM jobs WHERE file_path = ?', (file_path,))
            existing = cursor.fetchone()
            if existing:
                job_id, status = existing['id'], existing['status']
                if status in ('completed', 'pending', 'processing'):
                    return job_id
                # Re-enqueue failed or paused jobs if explicitly re-enqueued
                with conn:
                    cursor.execute('''
                        UPDATE jobs
                        SET output_dir = ?, convert_comics = ?, import_to_library = ?,
                            status = 'pending', attempt_count = 0, next_retry_at = 0.0,
                            error_log = '', updated_at = ?
                        WHERE id = ?
                    ''', (output_dir, int(convert_comics), int(import_to_library), now, job_id))
                return job_id
            with conn:
                cursor.execute('''
                    INSERT INTO jobs (file_path, output_dir, convert_comics, import_to_library,
                                      status, attempt_count, max_attempts, next_retry_at,
                                      error_log, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'pending', 0, ?, 0.0, '', ?, ?)
                ''', (file_path, output_dir, int(convert_comics), int(import_to_library),
                      max_attempts, now, now))
                return cursor.lastrowid
        finally:
            conn.close()


def get_next_pending_job(db_path=None):
    init_db(db_path)
    now = time.time()
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM jobs
                WHERE status = 'pending' AND next_retry_at <= ?
                ORDER BY id ASC LIMIT 1
            ''', (now,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        finally:
            conn.close()


def update_job_status(job_id, status, error_log=None, db_path=None):
    init_db(db_path)
    now = time.time()
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                if error_log is not None:
                    conn.execute('''
                        UPDATE jobs
                        SET status = ?, error_log = ?, updated_at = ?
                        WHERE id = ?
                    ''', (status, error_log, now, job_id))
                else:
                    conn.execute('''
                        UPDATE jobs
                        SET status = ?, updated_at = ?
                        WHERE id = ?
                    ''', (status, now, job_id))
        finally:
            conn.close()


def mark_job_attempt_failed(job_id, error_msg, max_attempts=3, db_path=None):
    init_db(db_path)
    now = time.time()
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT attempt_count, max_attempts FROM jobs WHERE id = ?', (job_id,))
            row = cursor.fetchone()
            if not row:
                return ('failed', 0)
            attempts = row['attempt_count'] + 1
            limit = row['max_attempts'] if row['max_attempts'] else max_attempts
            if attempts < limit:
                new_status = 'pending'
                # Exponential backoff: 2^attempts seconds (2s, 4s, 8s...)
                backoff_sec = 2 ** attempts
                next_retry = now + backoff_sec
            else:
                new_status = 'failed'
                next_retry = 0.0

            with conn:
                conn.execute('''
                    UPDATE jobs
                    SET status = ?, attempt_count = ?, next_retry_at = ?,
                        error_log = ?, updated_at = ?
                    WHERE id = ?
                ''', (new_status, attempts, next_retry, str(error_msg), now, job_id))
            return (new_status, attempts)
        finally:
            conn.close()


def retry_job(job_id, db_path=None):
    init_db(db_path)
    now = time.time()
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute('''
                    UPDATE jobs
                    SET status = 'pending', attempt_count = 0, next_retry_at = 0.0,
                        error_log = '', updated_at = ?
                    WHERE id = ?
                ''', (now, job_id))
        finally:
            conn.close()


def retry_all_failed(db_path=None):
    init_db(db_path)
    now = time.time()
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute('''
                    UPDATE jobs
                    SET status = 'pending', attempt_count = 0, next_retry_at = 0.0, updated_at = ?
                    WHERE status = 'failed'
                ''', (now,))
        finally:
            conn.close()


def clear_completed_jobs(db_path=None):
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM jobs WHERE status = 'completed'")
        finally:
            conn.close()


def clear_all_jobs(db_path=None):
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM jobs")
        finally:
            conn.close()


def delete_job(job_id, db_path=None):
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        finally:
            conn.close()


def get_all_jobs(db_path=None):
    init_db(db_path)
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM jobs ORDER BY id DESC')
            return [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()


def reset_processing_jobs_on_startup(db_path=None):
    init_db(db_path)
    now = time.time()
    with _DB_LOCK:
        conn = get_db_connection(db_path)
        try:
            with conn:
                conn.execute('''
                    UPDATE jobs
                    SET status = 'pending', updated_at = ?
                    WHERE status = 'processing'
                ''', (now,))
        finally:
            conn.close()
