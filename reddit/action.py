import copy
import os
import threading
import time

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import QAction, QMenu, QTimer

from calibre_plugins.reddit_follower import core, libkit
from calibre_plugins.reddit_follower.config import prefs
from calibre_plugins.reddit_follower.guikit import run_task
try:
    from calibre_plugins.reddit_follower.error_handler import show_friendly_error_dialog
except ImportError:
    try:
        from .error_handler import show_friendly_error_dialog
    except ImportError:
        from error_handler import show_friendly_error_dialog
from calibre_plugins.reddit_follower.ui import DiscoverDialog, ManageDialog, SettingsDialog

IDENTIFIER = 'redditfollow'
TICK_MS = 15 * 60 * 1000       # how often we look at which stories are due; looking costs nothing
FIRST_CHECK_MS = 2 * 60 * 1000  # after startup, so Calibre opens quickly
MIN_BACKOFF = 15 * 60           # seconds to leave Reddit alone after it asks us to slow down


class FollowerAction(InterfaceAction):
    name = 'Reddit Story Follower'
    action_spec = ('Reddit stories', None, 'Follow story series on Reddit as books that grow', None)

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        for title, slot in (('Followed stories…', self.manage), ('Find stories…', self.discover), ('Queue manager…', self.open_queue_manager), ('Check for new chapters now', self.check_all_now), ('Settings…', self.settings)):
            action = QAction(title, self.gui)
            action.triggered.connect(slot)
            menu.addAction(action)
        self.qaction.triggered.connect(self.manage)
        self.busy = False
        self.backoff_until = 0.0
        self.stopping = False
        self._thread = None
        self._job = {}
        self._cache_dir = None
        self._queue_manager = None
        self.timer = QTimer(self.gui)
        self.timer.timeout.connect(self.auto_check)
        self.timer.start(TICK_MS)
        QTimer.singleShot(FIRST_CHECK_MS, self.auto_check)
        self.poll_timer = QTimer(self.gui)
        self.poll_timer.setInterval(500)
        self.poll_timer.timeout.connect(self._poll)

    def shutting_down(self):
        self.stopping = True  # a check in progress stops at the next page

    # -- helpers
    def cache_dir(self):
        if self._cache_dir is None:
            from calibre.constants import config_dir
            self._cache_dir = os.path.join(config_dir, 'plugins', 'reddit_follower_cache')
        return self._cache_dir

    def get_queue_manager(self):
        if self._queue_manager is None:
            try:
                from calibre_plugins.reddit_follower.task_queue import QueueManager
            except ImportError:
                from task_queue import QueueManager
            self._queue_manager = QueueManager(self.cache_dir())
        return self._queue_manager

    def open_queue_manager(self):
        try:
            from calibre_plugins.reddit_follower.queue_ui import QueueManagerDialog
        except ImportError:
            from queue_ui import QueueManagerDialog
        qm = self.get_queue_manager()
        QueueManagerDialog(self.gui, qm, run_now_callback=self.auto_check).exec()

    def registry(self):
        db_path = os.path.join(self.cache_dir(), 'series_registry.db')
        if getattr(self, '_registry', None) is None or self._registry.db_path != db_path:
            try:
                from .registry import SeriesRegistry
            except ImportError:
                try:
                    from registry import SeriesRegistry
                except ImportError:
                    import sys as _sys, os as _os
                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    from registry import SeriesRegistry
            self._registry = SeriesRegistry(db_path)
            self._migrate()
        return self._registry

    def _migrate(self):
        if getattr(self, '_registry', None) is None:
            return
        try:
            from calibre_plugins.reddit_follower.migration import migrate_json_to_sqlite
        except ImportError:
            try:
                from .migration import migrate_json_to_sqlite
            except ImportError:
                try:
                    from migration import migrate_json_to_sqlite
                except ImportError:
                    try:
                        import sys as _sys, os as _os
                        _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                        if _pkg_dir not in _sys.path:
                            _sys.path.insert(0, _pkg_dir)
                        from migration import migrate_json_to_sqlite
                    except ImportError:
                        return
        try:
            migrate_json_to_sqlite(self._registry, prefs, self.cache_dir())
        except Exception:
            pass

    def get_follows(self):
        self._migrate()
        series_list = self.registry().get_all_series()
        return series_list if series_list else prefs['follows']

    def chapter_count(self, follow):
        return len(core.ChapterCache(self.cache_dir(), follow['id'], registry=self.registry()).posts)

    def make_source(self, cancelled):
        return core.make_source(prefs['mode'], prefs['client_id'], prefs['client_secret'],
                                 prefs['account_name'] or prefs['username'], cancelled, prefs['refresh_token'])

    # -- entry points
    def manage(self):
        ManageDialog(self.gui, self).exec()

    def discover(self):
        """Search or browse a subreddit for stories and whole series; a chosen series is added and collected at once."""
        dialog = DiscoverDialog(self.gui, self)
        if dialog.exec() != DiscoverDialog.DialogCode.Accepted:
            return
        added = ([dialog.chosen] if dialog.chosen is not None else []) + list(dialog.chosen_many)
        if added:
            for s in added:
                self.registry().save_series(s)
            prefs['follows'] = prefs['follows'] + added
            self.check_now(added)

    def settings(self):
        SettingsDialog(self.gui).exec()

    def check_all_now(self):
        self.check_now(list(self.get_follows()))

    def check_now(self, follows):
        """Check the given follows while the user waits (with a Cancel button), then update the library."""
        if not follows:
            return info_dialog(self.gui, 'Reddit stories', 'Nothing is being followed yet. Use "Followed stories…" to add one.', show=True)
        if self.busy:
            return info_dialog(self.gui, 'Reddit stories', 'A check is already running in the background. Try again in a minute.', show=True)
        copies = copy.deepcopy(follows)
        try:
            task = run_task(self.gui, 'Checking Reddit…', lambda t: core.run_follows(
                self.make_source(t.cancelled), copies, self.cache_dir(), t.cancelled, lambda msg: setattr(t, 'status', msg)))
        except core.SourceError as exc:
            return show_friendly_error_dialog(self.gui, 'Reddit stories', exc, show=True)
        if task.error:
            return show_friendly_error_dialog(self.gui, 'Reddit stories', task.error, show=True)
        if task.result is None:
            return
        self.finish(task.result, interactive=True)

    def test_follow(self, follow):
        """One request for the first page of this follow, reported to the user. Changes nothing."""
        task = run_task(self.gui, 'Testing…', lambda t: core.probe(self.make_source(t.cancelled), follow['source'], follow))
        if task.error:
            return show_friendly_error_dialog(self.gui, 'Reddit stories', task.error, show=True)
        if task.result is not None:
            info_dialog(self.gui, f"Test: {follow['name']}", task.result[1], show=True)

    # -- automatic checks (quiet, in a background thread)
    def auto_check(self):
        if self.stopping or self.busy or not prefs['auto_check'] or time.time() < self.backoff_until:
            return
        hours = float(prefs['check_hours'])
        qm = self.get_queue_manager()

        for f in self.get_follows():
            if core.due(f, hours):
                qm.enqueue_task(f['id'], 'check_follow', f)

        eligible_tasks = qm.get_eligible_tasks('check_follow')
        if not eligible_tasks:
            return

        self.busy = True
        self._job = {'results': [], 'error': None}

        def work():
            results = []
            by_id = {f['id']: f for f in self.get_follows()}
            for task in eligible_tasks:
                if self.stopping:
                    break
                task_id = task['task_id']
                follow = task['payload'] if isinstance(task['payload'], dict) else by_id.get(task_id)
                if not follow:
                    continue
                qm.mark_processing(task_id)
                try:
                    res_list = core.run_follows(self.make_source(lambda: self.stopping), [copy.deepcopy(follow)], self.cache_dir(), lambda: self.stopping)
                    for res in res_list:
                        results.append(res)
                        if res.get('rate_limited'):
                            qm.mark_failed(task_id, "Rate limited by Reddit (HTTP 429)")
                        elif res.get('error'):
                            qm.mark_failed(task_id, res['error'])
                        else:
                            qm.mark_completed(task_id, "Check succeeded")
                except Exception as exc:
                    qm.mark_failed(task_id, exc)
                    self._job['error'] = exc

            self._job['results'] = results

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()
        self.poll_timer.start()

    def _poll(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self.poll_timer.stop()
        self.busy = False
        job, self._job = self._job, {}
        if job.get('results'):
            self.finish(job['results'], interactive=False)
        elif job.get('error') and not isinstance(job['error'], core.Cancelled):
            self.gui.status_bar.show_message(f"Reddit stories: check failed ({job['error']})", 10000)

    # -- applying results
    def finish(self, results, interactive):
        by_id = {f['id']: f for f in self.get_follows()}
        for r in results:
            live = by_id.get(r['follow']['id'])
            if live is not None and not r['skipped']:
                live['last_checked'] = r['follow'].get('last_checked', live.get('last_checked', 0.0))
                live['last_status'] = r['follow'].get('last_status', '')
                self.registry().save_series(live)
        prefs['follows'] = list(self.get_follows())  # assigning saves the settings
        if any(r['rate_limited'] for r in results):
            wait = max([r['retry_after'] or 0 for r in results] + [MIN_BACKOFF])
            self.backoff_until = time.time() + wait
        lines, changed_books = [], 0
        for r in results:
            name = r['follow']['name']
            if r['skipped']:
                lines.append(f'{name}: skipped (Reddit asked us to slow down)')
                continue
            if r['rate_limited']:
                lines.append(f"{name}: Reddit asked us to slow down; stopped. Will try again after about {max(r['retry_after'] or 0, MIN_BACKOFF) // 60} minutes.")
            elif r['error']:
                lines.append(f"{name}: {r['error']}")
            outcome = self.update_library(r['follow'], r['new'] or r['changed'])
            if outcome:
                lines.append(f"{name}: {outcome}")
                changed_books += 1
            elif not r['error'] and not r['rate_limited']:
                lines.append(f'{name}: up to date')
        if interactive:
            info_dialog(self.gui, 'Reddit stories', '\n'.join(lines) or 'Nothing to report.', show=True)
        elif changed_books:
            self.gui.status_bar.show_message('Reddit stories updated: ' + '; '.join(l for l in lines if 'up to date' not in l), 15000)

    def update_library(self, follow, has_changes):
        """Rebuild the book from the cached chapters when something changed or the book is missing. Returns a message or ''."""
        cache = core.ChapterCache(self.cache_dir(), follow['id'])
        if not cache.posts:
            return ''
        if follow.get('layout') == 'each':
            return self.update_each(follow, cache)
        story = core.build_story(follow, cache)
        if not has_changes and libkit.find_book(self.gui, IDENTIFIER, story['id']) is not None:
            return ''
        try:
            mi = libkit.metadata_for([story], None, IDENTIFIER, core.PUBLISHER)
            _, created = libkit.upsert_epub(self.gui, mi, core.build_epub(story), IDENTIFIER, story['id'])
        except Exception as exc:
            return f'could not update the book ({exc})'
        count = len(story['sections'])
        return f'added to your library with {count} chapter(s)' if created else f'book updated, now {count} chapter(s)'

    def update_each(self, follow, cache):
        """Make a separate library book from each cached post that has none yet. Returns a message or ''."""
        added, errors = 0, 0
        for post in core.pending_each(follow, cache):
            story = core.build_each(follow, post)
            try:
                mi = libkit.metadata_for([story], None, IDENTIFIER, core.PUBLISHER)
                libkit.upsert_epub(self.gui, mi, core.build_epub(story), IDENTIFIER, story['id'])
            except Exception:
                errors += 1
                continue
            cache.data.setdefault('added', []).append(post['id'])
            added += 1
        if added or errors:
            cache.save()
        left = len(core.pending_each(follow, cache, limit=10 ** 6))
        parts = ([f'{added} new book(s) added'] if added else []) + ([f'{errors} could not be added'] if errors else []) + \
                ([f'{left} more waiting for the next check'] if left else [])
        return ', '.join(parts)
