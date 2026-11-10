"""Stage logging for crash diagnosis (added v1.0.1, Nov 6, 2026).

Writes one timestamped line per stage to inkbunny_sofurry_downloader.log in calibre's config
plugins folder, flushing every line. If calibre ever dies hard during a fetch or download,
the last line in the log names the stage it died in. Logging must never break the plugin:
every failure here is swallowed.
"""
import os
import time


def _log_path():
    try:
        from calibre.utils.config import config_dir
        folder = os.path.join(config_dir, 'plugins')
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, 'inkbunny_sofurry_downloader.log')
    except Exception:
        return None


def log(stage, detail=''):
    try:
        path = _log_path()
        if not path:
            return
        line = time.strftime('%Y-%m-%d %H:%M:%S') + f'  {stage}' + (f'  {detail}' if detail else '') + '\n'
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass
