"""SQLite Series Registry and Manifest Subsystem for Reddit Story Follower.

Maintains thread-safe persistent state for cross-author series, multi-source feed rules,
chapter manifests, deduplication, and author attributions.
"""
import json
import os
import sqlite3
import threading
import time


class _ConnectionWrapper:
    """Wrapper that supports both context manager protocol and direct connection attribute access."""

    def __init__(self, db_path, setup_callback=None):
        self.db_path = db_path
        self._setup_callback = setup_callback
        self._conn = None

    def _ensure(self):
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
            conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON;")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("PRAGMA journal_mode = WAL;")
            except sqlite3.OperationalError:
                pass
            if self._setup_callback:
                self._setup_callback(conn)
            self._conn = conn
        return self._conn

    def __enter__(self):
        conn = self._ensure()
        conn.__enter__()
        return conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._conn is not None:
            try:
                return self._conn.__exit__(exc_type, exc_val, exc_tb)
            finally:
                self._conn.close()
                self._conn = None

    def cursor(self):
        return self._ensure().cursor()

    def execute(self, *args, **kwargs):
        return self._ensure().execute(*args, **kwargs)

    def commit(self):
        return self._ensure().commit()

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __getattr__(self, item):
        return getattr(self._ensure(), item)


class SeriesRegistry:
    """Thread-safe manager for series_registry.db."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    def _get_connection(self):
        def _check_setup(conn):
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='series';")
            if not cursor.fetchone():
                self._create_tables(conn)
            else:
                self._ensure_columns(conn)

        return _ConnectionWrapper(self.db_path, _check_setup)

    def _ensure_columns(self, conn):
        cursor = conn.cursor()
        try:
            cursor.execute("ALTER TABLE series ADD COLUMN signature TEXT DEFAULT '';")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE manifests ADD COLUMN is_excluded INTEGER DEFAULT 0;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE manifests ADD COLUMN is_custom_order INTEGER DEFAULT 0;")
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE manifests ADD COLUMN reconciliation_status TEXT DEFAULT 'approved';")
        except Exception:
            pass
        conn.commit()

    def _create_tables(self, conn):
        cursor = conn.cursor()

        # 1. Series table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS series (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            layout TEXT DEFAULT 'series',
            author_note INTEGER DEFAULT 0,
            title_from_body INTEGER DEFAULT 0,
            index_url TEXT DEFAULT '',
            last_checked REAL DEFAULT 0.0,
            last_status TEXT DEFAULT '',
            complete INTEGER DEFAULT 0,
            newest_created REAL DEFAULT 0.0,
            newest_id TEXT DEFAULT '',
            index_data TEXT DEFAULT '',
            signature TEXT DEFAULT '',
            created_at REAL
        );
        """)

        # 2. Feed sources table (multi-source feed aggregation)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS feed_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            subreddit TEXT DEFAULT '',
            user TEXT DEFAULT '',
            query TEXT DEFAULT '',
            title_filter TEXT DEFAULT '',
            author_filter TEXT DEFAULT '',
            flair_filter TEXT DEFAULT '',
            FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE
        );
        """)

        # 3. Authors table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS authors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE,
            UNIQUE(series_id, author_name)
        );
        """)

        # 4. Chapters table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS chapters (
            id TEXT PRIMARY KEY,
            series_id TEXT NOT NULL,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            created REAL NOT NULL,
            link TEXT DEFAULT '',
            html TEXT DEFAULT '',
            flair TEXT DEFAULT '',
            note TEXT DEFAULT '',
            sequence_number REAL DEFAULT 0.0,
            added_to_lib INTEGER DEFAULT 0,
            FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE
        );
        """)

        # 5. Manifests table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS manifests (
            series_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            sequence_order INTEGER NOT NULL,
            chapter_title TEXT DEFAULT '',
            author_attribution TEXT DEFAULT '',
            is_duplicate INTEGER DEFAULT 0,
            canonical_post_id TEXT DEFAULT '',
            is_excluded INTEGER DEFAULT 0,
            is_custom_order INTEGER DEFAULT 0,
            reconciliation_status TEXT DEFAULT 'approved',
            PRIMARY KEY (series_id, post_id),
            FOREIGN KEY(series_id) REFERENCES series(id) ON DELETE CASCADE,
            FOREIGN KEY(post_id) REFERENCES chapters(id) ON DELETE CASCADE
        );
        """)

        self._ensure_columns(conn)

        conn.commit()

    def init_db(self):
        with self._lock, self._get_connection() as conn:
            pass

    def get_all_series(self):
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM series ORDER BY rowid ASC;")
            rows = cursor.fetchall()
            series_list = []
            for r in rows:
                s_dict = dict(r)
                s_id = s_dict['id']

                # Fetch feed sources
                cursor.execute("SELECT * FROM feed_sources WHERE series_id = ?", (s_id,))
                s_dict['sources'] = [dict(src) for src in cursor.fetchall()]

                # Fetch authors
                cursor.execute("SELECT author_name FROM authors WHERE series_id = ? ORDER BY author_name", (s_id,))
                s_dict['authors'] = [a['author_name'] for a in cursor.fetchall()]

                sig = s_dict.get('signature', '')
                if sig and isinstance(sig, str):
                    try:
                        s_dict['signature'] = json.loads(sig)
                    except Exception:
                        pass

                # Backward compatibility: populate legacy fields from first feed source
                if s_dict['sources']:
                    first_src = s_dict['sources'][0]
                    s_dict['source'] = {
                        'kind': first_src['kind'],
                        'subreddit': first_src['subreddit'],
                        'user': first_src['user'],
                        'query': first_src['query']
                    }
                    s_dict['title_filter'] = first_src['title_filter']
                    s_dict['author_filter'] = first_src['author_filter']
                    s_dict['flair_filter'] = first_src['flair_filter']
                else:
                    s_dict['source'] = {'kind': 'subreddit', 'subreddit': '', 'user': '', 'query': ''}
                    s_dict['title_filter'] = ''
                    s_dict['author_filter'] = ''
                    s_dict['flair_filter'] = ''

                series_list.append(s_dict)
            return series_list

    def get_series(self, series_id):
        all_s = self.get_all_series()
        for s in all_s:
            if s['id'] == series_id:
                return s
        return None

    def save_series(self, series_dict):
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            s_id = series_dict['id']
            now = series_dict.get('created_at') or time.time()
            sig = series_dict.get('signature')
            sig_str = json.dumps(sig) if sig is not None else ''

            cursor.execute("""
            INSERT INTO series (id, name, layout, author_note, title_from_body, index_url, last_checked, last_status, complete, newest_created, newest_id, index_data, signature, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                layout = excluded.layout,
                author_note = excluded.author_note,
                title_from_body = excluded.title_from_body,
                index_url = excluded.index_url,
                last_checked = excluded.last_checked,
                last_status = excluded.last_status,
                complete = excluded.complete,
                newest_created = excluded.newest_created,
                newest_id = excluded.newest_id,
                index_data = excluded.index_data,
                signature = excluded.signature;
            """, (
                s_id,
                series_dict['name'],
                series_dict.get('layout', 'series'),
                1 if series_dict.get('author_note') else 0,
                1 if series_dict.get('title_from_body') else 0,
                series_dict.get('index_url', ''),
                float(series_dict.get('last_checked') or 0.0),
                series_dict.get('last_status', ''),
                1 if series_dict.get('complete') else 0,
                float(series_dict.get('newest_created') or 0.0),
                series_dict.get('newest_id', ''),
                series_dict.get('index_data', ''),
                sig_str,
                now
            ))

            # Replace feed sources
            cursor.execute("DELETE FROM feed_sources WHERE series_id = ?", (s_id,))
            sources = series_dict.get('sources')
            if not sources and 'source' in series_dict:
                # Legacy or single source input
                sources = [{
                    'kind': series_dict['source'].get('kind', 'subreddit'),
                    'subreddit': series_dict['source'].get('subreddit', ''),
                    'user': series_dict['source'].get('user', ''),
                    'query': series_dict['source'].get('query', ''),
                    'title_filter': series_dict.get('title_filter', ''),
                    'author_filter': series_dict.get('author_filter', ''),
                    'flair_filter': series_dict.get('flair_filter', '')
                }]

            for src in (sources or []):
                cursor.execute("""
                INSERT INTO feed_sources (series_id, kind, subreddit, user, query, title_filter, author_filter, flair_filter)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    s_id,
                    src.get('kind', 'subreddit'),
                    src.get('subreddit', ''),
                    src.get('user', ''),
                    src.get('query', ''),
                    src.get('title_filter', ''),
                    src.get('author_filter', ''),
                    src.get('flair_filter', '')
                ))

            conn.commit()

    def delete_series(self, series_id):
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM series WHERE id = ?", (series_id,))
            conn.commit()

    def save_chapter_and_manifest(self, series_id, post):
        """Save a post into chapters, register author, and update manifest entry."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            post_id = post['id']
            author = (post.get('author') or 'Unknown').strip()

            # Save chapter
            cursor.execute("""
            INSERT INTO chapters (id, series_id, title, author, created, link, html, flair, note, sequence_number, added_to_lib)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                author = excluded.author,
                created = excluded.created,
                link = excluded.link,
                html = excluded.html,
                flair = excluded.flair,
                note = CASE WHEN excluded.note != '' THEN excluded.note ELSE chapters.note END,
                sequence_number = excluded.sequence_number,
                added_to_lib = excluded.added_to_lib;
            """, (
                post_id,
                series_id,
                post['title'],
                author,
                float(post['created']),
                post.get('link', ''),
                post.get('html', ''),
                post.get('flair', ''),
                post.get('note', ''),
                float(post.get('sequence_number', 0.0)),
                1 if post.get('added') else 0
            ))

            # Register author
            if author:
                cursor.execute("""
                INSERT OR IGNORE INTO authors (series_id, author_name)
                VALUES (?, ?);
                """, (series_id, author))

            # Re-generate/update manifests for series
            self._update_manifest_unlocked(conn, series_id)
            conn.commit()

    def save_chapters_batch(self, series_id, posts):
        """Save multiple posts into chapters in a single transaction, then update manifest once."""
        if not posts:
            return
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            for post in posts:
                post_id = post['id']
                author = (post.get('author') or 'Unknown').strip()
                cursor.execute("""
                INSERT INTO chapters (id, series_id, title, author, created, link, html, flair, note, sequence_number, added_to_lib)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    author = excluded.author,
                    created = excluded.created,
                    link = excluded.link,
                    html = excluded.html,
                    flair = excluded.flair,
                    note = CASE WHEN excluded.note != '' THEN excluded.note ELSE chapters.note END,
                    sequence_number = excluded.sequence_number,
                    added_to_lib = excluded.added_to_lib;
                """, (
                    post_id,
                    series_id,
                    post['title'],
                    author,
                    float(post['created']),
                    post.get('link', ''),
                    post.get('html', ''),
                    post.get('flair', ''),
                    post.get('note', ''),
                    float(post.get('sequence_number', 0.0)),
                    1 if post.get('added') else 0
                ))
                if author:
                    cursor.execute("""
                    INSERT OR IGNORE INTO authors (series_id, author_name)
                    VALUES (?, ?);
                    """, (series_id, author))
            self._update_manifest_unlocked(conn, series_id)
            conn.commit()

    def _update_manifest_unlocked(self, conn, series_id):
        cursor = conn.cursor()

        # Check if this series has a custom order manifest
        cursor.execute("SELECT 1 FROM manifests WHERE series_id = ? AND is_custom_order = 1 LIMIT 1", (series_id,))
        is_custom = cursor.fetchone() is not None

        if is_custom:
            # Custom ordered series:
            cursor.execute("SELECT * FROM manifests WHERE series_id = ? ORDER BY sequence_order ASC", (series_id,))
            existing_manifests = [dict(r) for r in cursor.fetchall()]
            existing_pids = {r['post_id'] for r in existing_manifests}
            max_seq = max([r['sequence_order'] for r in existing_manifests], default=0)

            # Build canonical link lookup from existing chapters
            seen_canonical = {}
            for m in existing_manifests:
                if not m.get('is_duplicate'):
                    cursor.execute("SELECT link FROM chapters WHERE id = ?", (m['post_id'],))
                    row = cursor.fetchone()
                    if row and row['link']:
                        seen_canonical[row['link']] = m['post_id']

            # Fetch all chapters for series
            cursor.execute("SELECT * FROM chapters WHERE series_id = ? ORDER BY created ASC, title ASC", (series_id,))
            all_chapters = [dict(c) for c in cursor.fetchall()]

            # Detect and append new chapters with reconciliation_status = 'pending'
            new_chapters = [ch for ch in all_chapters if ch['id'] not in existing_pids]
            for ch in new_chapters:
                pid = ch['id']
                link = ch.get('link', '')
                is_dup = 0
                canonical_id = ''
                if link and link in seen_canonical:
                    is_dup = 1
                    canonical_id = seen_canonical[link]
                else:
                    if link:
                        seen_canonical[link] = pid

                max_seq += 1
                cursor.execute("""
                INSERT INTO manifests (
                    series_id, post_id, sequence_order, chapter_title, author_attribution,
                    is_duplicate, canonical_post_id, is_excluded, is_custom_order, reconciliation_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    series_id,
                    pid,
                    max_seq,
                    ch['title'],
                    ch['author'],
                    is_dup,
                    canonical_id,
                    0,
                    1,
                    'pending'
                ))
        else:
            # Standard chronological order series
            cursor.execute("SELECT * FROM manifests WHERE series_id = ?", (series_id,))
            existing_by_pid = {r['post_id']: dict(r) for r in cursor.fetchall()}

            cursor.execute("SELECT * FROM chapters WHERE series_id = ? ORDER BY created ASC, title ASC", (series_id,))
            chapters = [dict(c) for c in cursor.fetchall()]

            seen_canonical = {}
            for idx, ch in enumerate(chapters, start=1):
                pid = ch['id']
                link = ch.get('link', '')
                is_dup = 0
                canonical_id = ''
                if link and link in seen_canonical:
                    is_dup = 1
                    canonical_id = seen_canonical[link]
                else:
                    if link:
                        seen_canonical[link] = pid

                existing = existing_by_pid.get(pid, {})
                is_excluded = existing.get('is_excluded', 0)
                title = existing.get('chapter_title') or ch['title']

                cursor.execute("""
                INSERT INTO manifests (
                    series_id, post_id, sequence_order, chapter_title, author_attribution,
                    is_duplicate, canonical_post_id, is_excluded, is_custom_order, reconciliation_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id, post_id) DO UPDATE SET
                    sequence_order = excluded.sequence_order,
                    chapter_title = excluded.chapter_title,
                    author_attribution = excluded.author_attribution,
                    is_duplicate = excluded.is_duplicate,
                    canonical_post_id = excluded.canonical_post_id,
                    is_excluded = excluded.is_excluded,
                    is_custom_order = excluded.is_custom_order,
                    reconciliation_status = excluded.reconciliation_status;
                """, (
                    series_id,
                    pid,
                    idx,
                    title,
                    ch['author'],
                    is_dup,
                    canonical_id,
                    is_excluded,
                    0,
                    'approved'
                ))

    def get_chapters(self, series_id):
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chapters WHERE series_id = ? ORDER BY created ASC, title ASC", (series_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_manifest(self, series_id, include_duplicates=False):
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            dup_clause = "" if include_duplicates else "AND m.is_duplicate = 0"
            cursor.execute(f"""
            SELECT m.*, c.created, c.link, c.html, c.flair, c.note
            FROM manifests m
            JOIN chapters c ON m.post_id = c.id
            WHERE m.series_id = ? {dup_clause}
            ORDER BY m.sequence_order ASC;
            """, (series_id,))
            return [dict(r) for r in cursor.fetchall()]

    def save_custom_manifest(self, series_id, manifest_entries):
        """Persist user-specified custom manifest order, titles, exclusions, and reconciliation statuses."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            for entry in manifest_entries:
                post_id = entry['post_id']
                seq = int(entry.get('sequence_order', 1))
                title = entry.get('chapter_title', '').strip()
                is_excl = 1 if entry.get('is_excluded') else 0
                recon_status = entry.get('reconciliation_status', 'approved')
                cursor.execute("""
                UPDATE manifests
                SET sequence_order = ?,
                    chapter_title = ?,
                    is_excluded = ?,
                    is_custom_order = 1,
                    reconciliation_status = ?
                WHERE series_id = ? AND post_id = ?;
                """, (seq, title, is_excl, recon_status, series_id, post_id))
            conn.commit()

    def reset_manifest_order(self, series_id):
        """Reset manifest order to chronological order, clearing custom order and pending status."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE manifests
            SET is_custom_order = 0,
                reconciliation_status = 'approved'
            WHERE series_id = ?;
            """, (series_id,))
            self._update_manifest_unlocked(conn, series_id)
            conn.commit()

    def has_pending_reconciliation(self, series_id):
        """Return True if any non-duplicate manifest entry has reconciliation_status == 'pending'."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT 1 FROM manifests
            WHERE series_id = ? AND is_duplicate = 0 AND reconciliation_status = 'pending'
            LIMIT 1;
            """, (series_id,))
            return cursor.fetchone() is not None

    def get_pending_manifest_count(self, series_id):
        """Return number of pending manifest entries for a series."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT COUNT(*) AS cnt FROM manifests
            WHERE series_id = ? AND is_duplicate = 0 AND reconciliation_status = 'pending';
            """, (series_id,))
            row = cursor.fetchone()
            return row['cnt'] if row else 0

    def approve_manifest_item(self, series_id, post_id):
        """Approve a pending chapter manifest item."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE manifests
            SET reconciliation_status = 'approved'
            WHERE series_id = ? AND post_id = ?;
            """, (series_id, post_id))
            conn.commit()

    def approve_all_manifest_items(self, series_id):
        """Approve all pending chapter manifest items for a series."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE manifests
            SET reconciliation_status = 'approved'
            WHERE series_id = ? AND reconciliation_status = 'pending';
            """, (series_id,))
            conn.commit()

    def set_added_to_lib(self, series_id, post_ids):
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            for pid in post_ids:
                cursor.execute("UPDATE chapters SET added_to_lib = 1 WHERE series_id = ? AND id = ?", (series_id, pid))
            conn.commit()

    def get_added_post_ids(self, series_id):
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM chapters WHERE series_id = ? AND added_to_lib = 1", (series_id,))
            return {r['id'] for r in cursor.fetchall()}
