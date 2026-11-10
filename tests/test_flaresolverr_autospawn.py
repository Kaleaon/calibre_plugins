import importlib.util
import os
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

# Stub Calibre and Qt modules before importing plugin code
class StubMetadata:
    def __init__(self, title, authors):
        self.title, self.authors, self.ids, self.comments, self.tags = title, authors, {}, None, []

    def set_identifier(self, key, value):
        self.ids[key] = value

class StubPrefs(dict):
    def __init__(self):
        super().__init__()
        self.defaults = {}

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            return self.defaults[key]

sys.modules['calibre.gui2'] = MagicMock()
sys.modules['calibre.gui2.actions'] = MagicMock()
sys.modules['calibre.gui2.actions'].InterfaceAction = object

if 'calibre' not in sys.modules:
    for name in ('calibre', 'calibre.customize', 'calibre.utils',
                 'calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.book'):
        sys.modules[name] = MagicMock()

if 'calibre.ebooks.metadata.book.base' not in sys.modules:
    base = types.ModuleType('calibre.ebooks.metadata.book.base')
    base.Metadata = StubMetadata
    sys.modules['calibre.ebooks.metadata.book.base'] = base

if 'calibre.utils.config' not in sys.modules:
    config_mod = types.ModuleType('calibre.utils.config')
    config_mod.JSONConfig = lambda name: StubPrefs()
    sys.modules['calibre.utils.config'] = config_mod

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    qtcore = types.ModuleType('qt.core')
    for module in (QtWidgets, QtGui, QtCore):
        for attr in dir(module):
            setattr(qtcore, attr, getattr(module, attr))
    sys.modules['qt'] = types.ModuleType('qt')
    sys.modules['qt.core'] = qtcore
except ImportError:
    qt_mock = MagicMock()
    sys.modules['qt'] = qt_mock
    sys.modules['qt.core'] = qt_mock

# Setup calibre_plugins package layout including common modules
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

pkg = types.ModuleType('calibre_plugins.fanfic_downloader')
pkg.__path__ = [str(ROOT / 'fanfic'), str(ROOT / 'common')]
sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
sys.modules['calibre_plugins'].__path__ = []
sys.modules['calibre_plugins.fanfic_downloader'] = pkg

from calibre_plugins.fanfic_downloader import action as action_mod
from calibre_plugins.fanfic_downloader import flaresolverr as fs
from calibre_plugins.fanfic_downloader.config import prefs


class FlareSolverrAutoSpawnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        exe_name = 'flaresolverr.exe' if sys.platform.startswith('win') else 'flaresolverr'
        self.dummy_exe = Path(self.tmp.name) / exe_name
        self.dummy_exe.write_text('#!/bin/sh\nexit 0\n')
        self.dummy_exe.chmod(self.dummy_exe.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def test_preflight_autospawns_when_offline_and_path_exists(self):
        """Pre-flight checks auto-start local FlareSolverr when flaresolverr_path exists and server is offline."""
        prefs['flaresolverr_path'] = str(self.dummy_exe)
        prefs['advanced_ini'] = ''
        opts = {'lines': ['https://www.royalroad.com/fiction/12345'], 'allow_adult': False, 'extra_adult': [], 'skip': False, 'combine': False, 'ini': ''}

        action = action_mod.FanficAction()
        action.gui = MagicMock()

        accepted = [{'domain': 'royalroad.com', 'url': 'https://www.royalroad.com/fiction/12345', 'begin': None, 'end': None}]

        def dummy_run_task(gui, label, func):
            task = MagicMock()
            task.cancelled.return_value = False
            task.error = None
            task.status = ''
            task.result = func(task)
            return task

        with patch.object(action_mod.engine, 'check_urls', return_value=(accepted, [])):
            with patch.object(action_mod.engine, 'download_story', return_value={'title': 'Test Story', 'authors': ['Author'], 'site': 'Royal Road', 'tags': [], 'description': '', 'series': None, 'series_index': None, 'language': 'en', 'published': None, 'url': 'https://www.royalroad.com/fiction/12345', 'chapter_errors': [], 'epub': b'dummy'}):
                with patch.object(action_mod.libkit, 'add_prebuilt', return_value=(1, [])):
                    with patch.object(action_mod, 'run_task', side_effect=dummy_run_task):
                        with patch.object(action_mod, 'DownloadDialog') as MockDialog:
                            dlg_inst = MockDialog.return_value
                            dlg_inst.exec.return_value = dlg_inst.DialogCode.Accepted
                            dlg_inst.values.return_value = opts

                            check_server_calls = [0]
                            def mock_check_server(settings, timeout=3):
                                check_server_calls[0] += 1
                                if check_server_calls[0] == 1:
                                    return False, 'Offline'
                                return True, 'Running'

                            with patch.object(action_mod.flaresolverr, 'check_server', side_effect=mock_check_server):
                                with patch.object(action_mod.flaresolverr, 'fetch_through', return_value=(True, 'Page loaded')):
                                    with patch.object(action_mod.flaresolverr.launcher, 'start', return_value=(True, 'Started')) as mock_start:
                                        with patch.object(action_mod, 'FlareSolverrSetupWizard') as MockWizard:
                                            action.download()

                                            # Verify launcher.start was called
                                            mock_start.assert_called_once()

                                            # Verify wizard was NOT called (zero manual wizard prompt)
                                            MockWizard.assert_not_called()

                                            # Verify personal INI settings updated to enabled
                                            self.assertTrue(fs.is_enabled(opts['ini']))

    def test_http_403_triggers_relaunch_and_retry(self):
        """HTTP 403 challenges trigger one process re-launch and download retry before error dialogs show."""
        prefs['flaresolverr_path'] = str(self.dummy_exe)
        opts = {'lines': ['https://www.royalroad.com/fiction/12345'], 'allow_adult': False, 'extra_adult': [], 'skip': False, 'combine': False, 'ini': '[www.royalroad.com]\nuse_flaresolverr_proxy:true\n'}

        action = action_mod.FanficAction()
        action.gui = MagicMock()

        accepted = [{'domain': 'royalroad.com', 'url': 'https://www.royalroad.com/fiction/12345', 'begin': None, 'end': None}]

        download_calls = [0]
        def mock_download_story(info, ini, allow_adult):
            download_calls[0] += 1
            if download_calls[0] == 1:
                raise Exception('HTTP Error 403: Cloudflare bot challenge')
            return {'title': 'Retried Story', 'authors': ['Author'], 'site': 'Royal Road', 'tags': [], 'description': '', 'series': None, 'series_index': None, 'language': 'en', 'published': None, 'url': info['url'], 'chapter_errors': [], 'epub': b'dummy'}

        def dummy_run_task(gui, label, func):
            task = MagicMock()
            task.cancelled.return_value = False
            task.error = None
            task.status = ''
            try:
                task.result = func(task)
            except Exception as e:
                task.error = e
                task.result = None
            return task

        with patch.object(action_mod.engine, 'check_urls', return_value=(accepted, [])):
            with patch.object(action_mod.engine, 'download_story', side_effect=mock_download_story):
                with patch.object(action_mod.libkit, 'add_prebuilt', return_value=(1, [])):
                    with patch.object(action_mod, 'run_task', side_effect=dummy_run_task):
                        with patch.object(action_mod, 'DownloadDialog') as MockDialog:
                            dlg_inst = MockDialog.return_value
                            dlg_inst.exec.return_value = dlg_inst.DialogCode.Accepted
                            dlg_inst.values.return_value = opts

                            with patch.object(action_mod.flaresolverr, 'check_server', return_value=(True, 'Running')):
                                with patch.object(action_mod.flaresolverr, 'fetch_through', return_value=(True, 'Page loaded')):
                                    with patch.object(action_mod.flaresolverr.launcher, 'running', return_value=True):
                                        with patch.object(action_mod.flaresolverr.launcher, 'stop') as mock_stop:
                                            with patch.object(action_mod.flaresolverr.launcher, 'start', return_value=(True, 'Restarted')) as mock_start:
                                                with patch.object(action_mod, 'question_dialog') as mock_question:
                                                    action.download()

                                                    # Stop and start should be called for relaunch
                                                    mock_stop.assert_called_once()
                                                    mock_start.assert_called_once()

                                                    # Story download was called twice (initial + retry)
                                                    self.assertEqual(download_calls[0], 2)

                                                    # Question dialog for 403 recovery was NOT shown because auto-retry succeeded
                                                    for call in mock_question.call_args_list:
                                                        self.assertNotIn('Cloudflare 403 Challenge', call.args)

    def test_non_cloudflare_domains_do_not_trigger_autospawn(self):
        """Non-Cloudflare story downloads do not trigger process startup or port checks."""
        prefs['flaresolverr_path'] = str(self.dummy_exe)
        opts = {'lines': ['https://archiveofourown.org/works/12345'], 'allow_adult': False, 'extra_adult': [], 'skip': False, 'combine': False, 'ini': ''}

        action = action_mod.FanficAction()
        action.gui = MagicMock()

        accepted = [{'domain': 'archiveofourown.org', 'url': 'https://archiveofourown.org/works/12345', 'begin': None, 'end': None}]

        def dummy_run_task(gui, label, func):
            task = MagicMock()
            task.cancelled.return_value = False
            task.error = None
            task.status = ''
            task.result = func(task)
            return task

        with patch.object(action_mod.engine, 'check_urls', return_value=(accepted, [])):
            with patch.object(action_mod.engine, 'download_story', return_value={'title': 'AO3 Story', 'authors': ['Author'], 'site': 'AO3', 'tags': [], 'description': '', 'series': None, 'series_index': None, 'language': 'en', 'published': None, 'url': accepted[0]['url'], 'chapter_errors': [], 'epub': b'dummy'}):
                with patch.object(action_mod.libkit, 'add_prebuilt', return_value=(1, [])):
                    with patch.object(action_mod, 'run_task', side_effect=dummy_run_task):
                        with patch.object(action_mod, 'DownloadDialog') as MockDialog:
                            dlg_inst = MockDialog.return_value
                            dlg_inst.exec.return_value = dlg_inst.DialogCode.Accepted
                            dlg_inst.values.return_value = opts

                            with patch.object(action_mod.flaresolverr, 'check_server') as mock_check_server:
                                with patch.object(action_mod.flaresolverr.launcher, 'start') as mock_start:
                                    action.download()

                                    # Neither check_server nor launcher.start were called
                                    mock_check_server.assert_not_called()
                                    mock_start.assert_not_called()

    def test_fallback_to_wizard_when_no_valid_executable_path(self):
        """When no valid executable path exists, pre-flight opens guided setup wizard if proxy is not enabled."""
        prefs['flaresolverr_path'] = ''
        opts = {'lines': ['https://www.royalroad.com/fiction/12345'], 'allow_adult': False, 'extra_adult': [], 'skip': False, 'combine': False, 'ini': ''}

        action = action_mod.FanficAction()
        action.gui = MagicMock()

        accepted = [{'domain': 'royalroad.com', 'url': 'https://www.royalroad.com/fiction/12345', 'begin': None, 'end': None}]

        with patch.object(action_mod.engine, 'check_urls', return_value=(accepted, [])):
            with patch.object(action_mod, 'DownloadDialog') as MockDialog:
                dlg_inst = MockDialog.return_value
                dlg_inst.exec.return_value = dlg_inst.DialogCode.Accepted
                dlg_inst.values.return_value = opts

                with patch.object(action_mod, 'FlareSolverrSetupWizard') as MockWizard:
                    wiz_inst = MockWizard.return_value
                    wiz_inst.exec.return_value = wiz_inst.DialogCode.Rejected
                    with patch.object(action_mod, 'question_dialog', return_value=False):
                        action.download()

                        # Setup wizard WAS called because no valid executable path exists
                        MockWizard.assert_called_once()


if __name__ == '__main__':
    unittest.main()
