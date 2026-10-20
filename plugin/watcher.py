import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

try:
    from qt.core import QObject, QFileSystemWatcher, QTimer, QApplication
except ImportError:
    try:
        from PyQt6.QtCore import QObject, QFileSystemWatcher, QTimer, QApplication
    except ImportError:
        QObject = object
        QFileSystemWatcher = None
        QTimer = None
        QApplication = None

try:
    from calibre_plugins.takeout_book_fixer.config import prefs
    from calibre_plugins.takeout_book_fixer.core import recover
    from calibre_plugins.takeout_book_fixer.importer import import_books
except ImportError:
    try:
        from .config import prefs
        from .core import recover
        from .importer import import_books
    except ImportError:
        from config import prefs
        from core import recover
        from importer import import_books

TEMP_EXTENSIONS = {'.tmp', '.part', '.crdownload', '.download', '.partial'}


class WatchFolderManager(QObject if QObject != object else object):
    """Monitors watch_dir for new files/folders, debounces changes, runs recovery & import, and relocates processed files."""

    def __init__(self, gui=None):
        if QObject != object:
            super().__init__()
        self.gui = gui
        self.watcher = None
        self.timer = None
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.is_processing = False
        self._file_snapshots = {}  # str(path) -> (size, mtime)

        if QTimer is not None:
            self.timer = QTimer()
            self.timer.setSingleShot(True)
            self.timer.timeout.connect(self._on_timer_timeout)

        if QFileSystemWatcher is not None:
            self.watcher = QFileSystemWatcher()
            self.watcher.directoryChanged.connect(self._on_fs_change)
            self.watcher.fileChanged.connect(self._on_fs_change)

    def start_watching(self):
        """Start watching watch_dir according to preferences."""
        if not prefs['auto_watch']:
            self.stop_watching()
            return False

        watch_path_str = prefs['watch_dir']
        if not watch_path_str:
            return False

        watch_path = Path(watch_path_str).resolve()
        try:
            watch_path.mkdir(parents=True, exist_ok=True)
        except Exception:
            return False

        if self.watcher is not None:
            watched = set(self.watcher.directories())
            if str(watch_path) not in watched:
                self.watcher.addPath(str(watch_path))

        # Schedule initial scan (covers offline file drops when Calibre starts)
        self.schedule_scan(delay_ms=500)
        return True

    def stop_watching(self):
        """Stop watching directories."""
        if self.timer is not None and self.timer.isActive():
            self.timer.stop()
        if self.watcher is not None:
            dirs = list(self.watcher.directories())
            if dirs:
                self.watcher.removePaths(dirs)

    def _on_fs_change(self, path=None):
        quiet_sec = max(1, int(prefs['quiet_period'] or 3))
        self.schedule_scan(delay_ms=quiet_sec * 1000)

    def schedule_scan(self, delay_ms=3000):
        if self.timer is not None:
            self.timer.start(delay_ms)
        else:
            # Fallback if Qt timer unavailable
            self.process_watch_dir()

    def _on_timer_timeout(self):
        if self.is_processing:
            # Reschedule if already processing a previous batch
            self.schedule_scan(delay_ms=2000)
            return
        self.executor.submit(self.process_watch_dir)

    def _is_file_stable(self, path):
        """Verify that a file is complete and not actively being written to."""
        if path.is_dir():
            # For directories, check stability of all child files
            for child in path.rglob('*'):
                if child.is_file() and not self._is_file_stable(child):
                    return False
            return True

        try:
            stat = path.stat()
            size, mtime = stat.st_size, stat.st_mtime
            path_str = str(path)

            # Attempt to open file for append/read to check lock
            with open(path, 'rb') as f:
                f.seek(0, os.SEEK_END)

            prev = self._file_snapshots.get(path_str)
            self._file_snapshots[path_str] = (size, mtime)

            if prev is not None:
                prev_size, prev_mtime = prev
                if prev_size != size or prev_mtime != mtime:
                    return False
            return True
        except (PermissionError, OSError, IOError):
            return False

    def get_candidate_items(self):
        """Find new files/directories dropped into watch_dir, excluding processed_dir."""
        watch_path_str = prefs['watch_dir']
        if not watch_path_str:
            return [], None, None

        watch_dir = Path(watch_path_str).resolve()
        if not watch_dir.exists():
            return [], None, None

        output_path_str = prefs['output_dir'] or str(watch_dir / 'output')
        output_dir = Path(output_path_str).resolve()

        processed_path_str = prefs['processed_dir'] or str(watch_dir / 'processed')
        processed_dir = Path(processed_path_str).resolve()

        output_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)

        candidates = []
        for item in watch_dir.iterdir():
            item_res = item.resolve()
            if item_res == output_dir or output_dir in item_res.parents or item.name.lower() == 'output':
                continue
            if item_res == processed_dir or processed_dir in item_res.parents or item.name.lower() == 'processed':
                continue
            if item.name.startswith('.'):
                continue
            if item.suffix.lower() in TEMP_EXTENSIONS:
                continue
            candidates.append(item)

        return candidates, output_dir, processed_dir

    def process_watch_dir(self):
        """Scan watch folder, run recovery & import on stable items, and move to processed_dir."""
        if self.is_processing:
            return
        self.is_processing = True
        try:
            candidates, output_dir, processed_dir = self.get_candidate_items()
            if not candidates or output_dir is None or processed_dir is None:
                return

            unstable_found = False
            stable_items = []

            for item in candidates:
                if self._is_file_stable(item):
                    stable_items.append(item)
                else:
                    unstable_found = True

            if unstable_found:
                # Reschedule to check again once files stabilize
                self.schedule_scan(delay_ms=2000)

            if not stable_items:
                return

            convert_comics = bool(prefs['convert_comics'])
            auto_import = bool(prefs['auto_import'])

            for item in stable_items:
                try:
                    written_files, report, report_path = recover(item, output_dir, comics=convert_comics)

                    if auto_import and self.gui is not None:
                        # Perform library import
                        def do_import():
                            try:
                                imported, import_errors = import_books(self.gui, report)
                                with open(report_path, 'w', encoding='utf-8') as stream:
                                    json.dump(report, stream, indent=2, ensure_ascii=False)
                            except Exception as exc:
                                pass

                        if QApplication is not None and QApplication.instance() is not None:
                            # Safely post/process on main thread if needed
                            do_import()
                        else:
                            do_import()

                    # Relocate processed item to processed_dir to avoid loops
                    self._relocate_processed(item, processed_dir)
                    self._file_snapshots.pop(str(item), None)

                except Exception as exc:
                    pass

        finally:
            self.is_processing = False

    def _relocate_processed(self, item, processed_dir):
        """Safely move processed file/directory into processed_dir."""
        try:
            processed_dir.mkdir(parents=True, exist_ok=True)
            target = processed_dir / item.name
            if target.exists():
                stem = item.stem if item.is_file() else item.name
                suffix = item.suffix if item.is_file() else ''
                timestamp = time.strftime('%Y%m%d_%H%M%S')
                target = processed_dir / f"{stem}_{timestamp}{suffix}"
                counter = 1
                while target.exists():
                    target = processed_dir / f"{stem}_{timestamp}_{counter}{suffix}"
                    counter += 1

            shutil.move(str(item), str(target))
        except Exception as exc:
            pass
