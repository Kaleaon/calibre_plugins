"""Decoupled Task Queue Manager with SQLite persistence and rich activity logging.

Provides state management (QUEUED, PROCESSING, BACKOFF, COMPLETED, DEAD_LETTER),
jittered exponential backoff retries, thread-safe database lock handling, and
automatic diagnostic log pruning for Calibre plugins.
"""

import json
import logging
import os
import random
import sqlite3
import threading
import time
import traceback

logger = logging.getLogger(__name__)


class TaskState:
    QUEUED = 'QUEUED'
    PROCESSING = 'PROCESSING'
    BACKOFF = 'BACKOFF'
    COMPLETED = 'COMPLETED'
    DEAD_LETTER = 'DEAD_LETTER'

    ALL_STATES = [QUEUED, PROCESSING, BACKOFF, COMPLETED, DEAD_LETTER]


class FailureCategory:
    RATE_LIMIT = 'RATE_LIMIT'
    NETWORK_ERROR = 'NETWORK_ERROR'
    AUTH_ERROR = 'AUTH_ERROR'
    PARSE_ERROR = 'PARSE_ERROR'
    SYSTEM_ERROR = 'SYSTEM_ERROR'
    UNKNOWN = 'UNKNOWN'

    ALL_CATEGORIES = [RATE_LIMIT, NETWORK_ERROR, AUTH_ERROR, PARSE_ERROR, SYSTEM_ERROR, UNKNOWN]


def categorize_error(error):
    """Categorize an exception or error message into a FailureCategory."""
    if error is None:
        return FailureCategory.UNKNOWN
    err_str = str(error).lower()
    err_type = type(error).__name__.lower() if isinstance(error, Exception) else ''

    if any(term in err_str or term in err_type for term in ['rate', '429', 'slow down', 'too many requests', 'backoff']):
        return FailureCategory.RATE_LIMIT
    if any(term in err_str or term in err_type for term in ['401', '403', 'unauthorized', 'forbidden', 'auth', 'login', 'credential']):
        return FailureCategory.AUTH_ERROR
    if any(term in err_str or term in err_type for term in ['timeout', 'connection', 'network', 'urlerror', 'httperror', 'socket', 'dns', 'unreachable']):
        return FailureCategory.NETWORK_ERROR
    if any(term in err_str or term in err_type for term in ['parse', 'json', 'xml', 'html', 'keyerror', 'valueerror', 'syntax']):
        return FailureCategory.PARSE_ERROR
    if any(term in err_str or term in err_type for term in ['permission', 'ioerror', 'oserror', 'disk', 'space']):
        return FailureCategory.SYSTEM_ERROR

    return FailureCategory.UNKNOWN


class QueueManager:
    """SQLite-backed Task Queue Manager."""

    LOG_RETENTION_DAYS = 30
    LOG_RETENTION_COUNT = 1000

    def __init__(self, cache_dir, db_name='queue.db', base_backoff=60.0, max_backoff=86400.0):
        self.cache_dir = cache_dir
        self.db_path = os.path.join(cache_dir, db_name)
        self.base_backoff = float(base_backoff)
        self.max_backoff = float(max_backoff)
        self._lock = threading.RLock()

        os.makedirs(self.cache_dir, exist_ok=True)
        self._init_db()

    def _get_connection(self):
        """Returns a sqlite3 connection with timeout for locking handling."""
        os.makedirs(self.cache_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            # WAL mode might fail on some network shares; fallback to default
            pass
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='task_queue';")
        if not cursor.fetchone():
            conn.close()
            self._init_db()
            conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except sqlite3.OperationalError:
                pass
        return conn

    def _init_db(self):
        """Initialize SQLite database schema and handle startup recovery."""
        with self._lock:
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                conn = sqlite3.connect(self.db_path, timeout=10.0, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                try:
                    conn.execute("PRAGMA journal_mode=WAL;")
                except sqlite3.OperationalError:
                    pass
                try:
                    with conn:
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS task_queue (
                                task_id TEXT PRIMARY KEY,
                                task_type TEXT NOT NULL,
                                payload TEXT NOT NULL,
                                status TEXT NOT NULL DEFAULT 'QUEUED',
                                attempts INTEGER NOT NULL DEFAULT 0,
                                max_attempts INTEGER NOT NULL DEFAULT 5,
                                next_run_at REAL NOT NULL DEFAULT 0.0,
                                created_at REAL NOT NULL,
                                updated_at REAL NOT NULL,
                                last_error TEXT,
                                failure_category TEXT
                            )
                        """)
                        conn.execute("""
                            CREATE TABLE IF NOT EXISTS activity_log (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                task_id TEXT NOT NULL,
                                timestamp REAL NOT NULL,
                                from_status TEXT NOT NULL,
                                to_status TEXT NOT NULL,
                                message TEXT,
                                failure_category TEXT,
                                diagnostic_trace TEXT
                            )
                        """)
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status ON task_queue(status)")
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_next_run ON task_queue(next_run_at)")
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_log_timestamp ON activity_log(timestamp)")
                        conn.execute("CREATE INDEX IF NOT EXISTS idx_log_category ON activity_log(failure_category)")

                        # Startup recovery: reset any task left in 'PROCESSING' state back to 'QUEUED'
                        now = time.time()
                        conn.execute("""
                            UPDATE task_queue
                            SET status = ?, updated_at = ?
                            WHERE status = ?
                        """, (TaskState.QUEUED, now, TaskState.PROCESSING))

                    self.prune_activity_logs(conn=conn)
                finally:
                    conn.close()
            except sqlite3.OperationalError as exc:
                logger.warning("QueueManager failed to initialize database (%s). Check filesystem locks.", exc)

    def calculate_backoff(self, attempts):
        """Calculate jittered exponential backoff in seconds."""
        if attempts <= 0:
            attempts = 1
        raw_backoff = min(self.max_backoff, self.base_backoff * (2 ** (attempts - 1)))
        # Apply random jitter (+/- 20%)
        jittered = raw_backoff * random.uniform(0.8, 1.2)
        return round(jittered, 2)

    def enqueue_task(self, task_id, task_type, payload, max_attempts=5):
        """Enqueue or reset a task to QUEUED status."""
        with self._lock:
            now = time.time()
            payload_str = json.dumps(payload) if isinstance(payload, (dict, list)) else str(payload)
            conn = self._get_connection()
            try:
                with conn:
                    # Check existing state for activity logging
                    row = conn.execute("SELECT status FROM task_queue WHERE task_id = ?", (task_id,)).fetchone()
                    from_status = row['status'] if row else 'NONE'

                    conn.execute("""
                        INSERT INTO task_queue (
                            task_id, task_type, payload, status, attempts, max_attempts,
                            next_run_at, created_at, updated_at, last_error, failure_category
                        ) VALUES (?, ?, ?, ?, 0, ?, 0.0, ?, ?, NULL, NULL)
                        ON CONFLICT(task_id) DO UPDATE SET
                            task_type = excluded.task_type,
                            payload = excluded.payload,
                            status = excluded.status,
                            attempts = 0,
                            max_attempts = excluded.max_attempts,
                            next_run_at = 0.0,
                            updated_at = excluded.updated_at,
                            last_error = NULL,
                            failure_category = NULL
                    """, (task_id, task_type, payload_str, TaskState.QUEUED, max_attempts, now, now))

                    self._log_activity_internal(conn, task_id, from_status, TaskState.QUEUED, "Task enqueued")
            finally:
                conn.close()

    def get_eligible_tasks(self, task_type=None):
        """Get tasks that are ready for execution (QUEUED or BACKOFF with next_run_at <= now)."""
        with self._lock:
            now = time.time()
            conn = self._get_connection()
            try:
                query = """
                    SELECT * FROM task_queue
                    WHERE (status = ? OR (status = ? AND next_run_at <= ?))
                """
                params = [TaskState.QUEUED, TaskState.BACKOFF, now]
                if task_type:
                    query += " AND task_type = ?"
                    params.append(task_type)

                query += " ORDER BY created_at ASC"
                rows = conn.execute(query, params).fetchall()
                results = []
                for row in rows:
                    task_dict = dict(row)
                    try:
                        task_dict['payload'] = json.loads(task_dict['payload'])
                    except Exception:
                        pass
                    results.append(task_dict)
                return results
            finally:
                conn.close()

    def mark_processing(self, task_id):
        """Transition task to PROCESSING state."""
        with self._lock:
            now = time.time()
            conn = self._get_connection()
            try:
                with conn:
                    row = conn.execute("SELECT status FROM task_queue WHERE task_id = ?", (task_id,)).fetchone()
                    if not row:
                        return False
                    from_status = row['status']

                    conn.execute("""
                        UPDATE task_queue
                        SET status = ?, updated_at = ?
                        WHERE task_id = ?
                    """, (TaskState.PROCESSING, now, task_id))

                    self._log_activity_internal(conn, task_id, from_status, TaskState.PROCESSING, "Execution started")
                    return True
            finally:
                conn.close()

    def mark_completed(self, task_id, message="Task completed successfully"):
        """Transition task to COMPLETED state."""
        with self._lock:
            now = time.time()
            conn = self._get_connection()
            try:
                with conn:
                    row = conn.execute("SELECT status FROM task_queue WHERE task_id = ?", (task_id,)).fetchone()
                    if not row:
                        return False
                    from_status = row['status']

                    conn.execute("""
                        UPDATE task_queue
                        SET status = ?, updated_at = ?, last_error = NULL, failure_category = NULL
                        WHERE task_id = ?
                    """, (TaskState.COMPLETED, now, task_id))

                    self._log_activity_internal(conn, task_id, from_status, TaskState.COMPLETED, message)
                    return True
            finally:
                conn.close()

    def mark_failed(self, task_id, error, category=None, diagnostic_trace=None):
        """Handle task failure: calculate backoff or transition to DEAD_LETTER if max attempts reached."""
        with self._lock:
            now = time.time()
            error_msg = str(error)
            if category is None:
                category = categorize_error(error)
            if diagnostic_trace is None and isinstance(error, Exception):
                diagnostic_trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            elif diagnostic_trace is None:
                diagnostic_trace = error_msg

            conn = self._get_connection()
            try:
                with conn:
                    row = conn.execute("SELECT status, attempts, max_attempts FROM task_queue WHERE task_id = ?", (task_id,)).fetchone()
                    if not row:
                        return False
                    from_status = row['status']
                    attempts = row['attempts'] + 1
                    max_attempts = row['max_attempts']

                    if attempts >= max_attempts:
                        to_status = TaskState.DEAD_LETTER
                        next_run = 0.0
                        log_msg = f"Task failed repeatedly ({attempts}/{max_attempts} attempts). Moved to DEAD_LETTER: {error_msg}"
                    else:
                        to_status = TaskState.BACKOFF
                        backoff_sec = self.calculate_backoff(attempts)
                        next_run = now + backoff_sec
                        log_msg = f"Task failed attempt {attempts}/{max_attempts}. Backoff {backoff_sec}s: {error_msg}"

                    conn.execute("""
                        UPDATE task_queue
                        SET status = ?, attempts = ?, next_run_at = ?, updated_at = ?, last_error = ?, failure_category = ?
                        WHERE task_id = ?
                    """, (to_status, attempts, next_run, now, error_msg, category, task_id))

                    self._log_activity_internal(conn, task_id, from_status, to_status, log_msg, failure_category=category, diagnostic_trace=diagnostic_trace)
                    return True
            finally:
                conn.close()

    def retry_task(self, task_id):
        """Manually retry a task from DEAD_LETTER or BACKOFF state, moving it to QUEUED."""
        with self._lock:
            now = time.time()
            conn = self._get_connection()
            try:
                with conn:
                    row = conn.execute("SELECT status FROM task_queue WHERE task_id = ?", (task_id,)).fetchone()
                    if not row:
                        return False
                    from_status = row['status']

                    conn.execute("""
                        UPDATE task_queue
                        SET status = ?, attempts = 0, next_run_at = 0.0, updated_at = ?, last_error = NULL, failure_category = NULL
                        WHERE task_id = ?
                    """, (TaskState.QUEUED, now, task_id))

                    self._log_activity_internal(conn, task_id, from_status, TaskState.QUEUED, "Manual retry initiated by user")
                    return True
            finally:
                conn.close()

    def purge_task(self, task_id):
        """Purge a task from the queue."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    row = conn.execute("SELECT status FROM task_queue WHERE task_id = ?", (task_id,)).fetchone()
                    if not row:
                        return False
                    from_status = row['status']

                    conn.execute("DELETE FROM task_queue WHERE task_id = ?", (task_id,))
                    self._log_activity_internal(conn, task_id, from_status, "DELETED", "Task purged from queue")
                    return True
            finally:
                conn.close()

    def _log_activity_internal(self, conn, task_id, from_status, to_status, message, failure_category=None, diagnostic_trace=None):
        """Internal log writer using existing open transaction connection."""
        now = time.time()
        conn.execute("""
            INSERT INTO activity_log (task_id, timestamp, from_status, to_status, message, failure_category, diagnostic_trace)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (task_id, now, from_status, to_status, message, failure_category, diagnostic_trace))
        self.prune_activity_logs(conn=conn)

    def prune_activity_logs(self, conn=None):
        """Prune activity logs older than 30 days or exceeding 1,000 entries."""
        cutoff_time = time.time() - (self.LOG_RETENTION_DAYS * 86400)

        def _do_prune(c):
            # Delete logs older than retention period
            c.execute("DELETE FROM activity_log WHERE timestamp < ?", (cutoff_time,))
            # Delete logs beyond 1000 count limit
            c.execute("""
                DELETE FROM activity_log
                WHERE id NOT IN (
                    SELECT id FROM activity_log ORDER BY id DESC LIMIT ?
                )
            """, (self.LOG_RETENTION_COUNT,))

        if conn is not None:
            _do_prune(conn)
        else:
            with self._lock:
                conn = self._get_connection()
                try:
                    with conn:
                        _do_prune(conn)
                finally:
                    conn.close()

    def get_tasks(self, status_filter=None):
        """Query tasks with optional status filter."""
        with self._lock:
            conn = self._get_connection()
            try:
                if status_filter and status_filter != 'ALL':
                    rows = conn.execute("SELECT * FROM task_queue WHERE status = ? ORDER BY created_at DESC", (status_filter,)).fetchall()
                else:
                    rows = conn.execute("SELECT * FROM task_queue ORDER BY created_at DESC").fetchall()

                results = []
                for row in rows:
                    task_dict = dict(row)
                    try:
                        task_dict['payload'] = json.loads(task_dict['payload'])
                    except Exception:
                        pass
                    results.append(task_dict)
                return results
            finally:
                conn.close()

    def get_activity_logs(self, category_filter=None, task_id=None, limit=500):
        """Query activity logs with optional category filter or task_id filter."""
        with self._lock:
            conn = self._get_connection()
            try:
                query = "SELECT * FROM activity_log WHERE 1=1"
                params = []

                if category_filter and category_filter != 'ALL':
                    query += " AND failure_category = ?"
                    params.append(category_filter)

                if task_id:
                    query += " AND task_id = ?"
                    params.append(task_id)

                query += " ORDER BY timestamp DESC LIMIT ?"
                params.append(limit)

                rows = conn.execute(query, params).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def clear_activity_logs(self):
        """Clear all activity history logs."""
        with self._lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("DELETE FROM activity_log")
            finally:
                conn.close()
