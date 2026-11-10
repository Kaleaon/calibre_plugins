"""Unit tests for Calibre Link UI automated Tailscale auth modal."""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parents[1]


def _setup_shims():
    """Ensure qt.core and calibre mocks exist for headless test execution."""
    if "qt.core" not in sys.modules:
        qtcore = types.ModuleType("qt.core")
        for mod in (QtWidgets, QtGui, QtCore):
            for name in dir(mod):
                setattr(qtcore, name, getattr(mod, name))
        qtcore._ = lambda s: s
        qt = sys.modules.get("qt") or types.ModuleType("qt")
        qt.core = qtcore
        sys.modules["qt"] = qt
        sys.modules["qt.core"] = qtcore
    else:
        qt = sys.modules.get("qt") or types.ModuleType("qt")
        if not hasattr(qt, "core"):
            qt.core = sys.modules["qt.core"]
        sys.modules["qt"] = qt

    for name in (
        "calibre",
        "calibre.gui2",
        "calibre.customize",
        "calibre.gui2.actions",
        "calibre.constants",
        "calibre.utils",
        "calibre.utils.config",
    ):
        if name not in sys.modules:
            m = types.ModuleType(name)
            m.__path__ = []
            sys.modules[name] = m
        if "." in name:
            parent_name, child_name = name.rsplit(".", 1)
            parent_mod = sys.modules.get(parent_name)
            if parent_mod is not None:
                setattr(parent_mod, child_name, sys.modules[name])

    gui2 = sys.modules["calibre.gui2"]
    for fn in ("error_dialog", "info_dialog", "question_dialog", "warning_dialog"):
        if not hasattr(gui2, fn):
            setattr(gui2, fn, MagicMock())

    class StubPrefs(dict):
        def __init__(self):
            super().__init__()
            self.defaults = {}

        def __getitem__(self, k):
            try:
                return super().__getitem__(k)
            except KeyError:
                return self.defaults[k]

        def get(self, k, default=None):
            return super().get(k, self.defaults.get(k, default))

    class StubAction:
        def __init__(self, gui=None, name=None):
            self.gui = gui
            self.name = name
            self.qaction = MagicMock()
            self.actual_plugin = MagicMock()
            self.interface_action_base_plugin = None

    sys.modules["calibre.gui2.actions"].InterfaceAction = StubAction
    sys.modules["calibre.customize"].InterfaceActionBase = object
    sys.modules["calibre.constants"].iswindows = False
    sys.modules["calibre.constants"].islinux = True
    sys.modules["calibre.utils.config"].JSONConfig = lambda name: StubPrefs()
    sys.modules["calibre.utils.config"].config_dir = "/tmp"

    if "calibre_plugins" not in sys.modules:
        sys.modules["calibre_plugins"] = types.ModuleType("calibre_plugins")
    if not getattr(sys.modules["calibre_plugins"], "__path__", None):
        sys.modules["calibre_plugins"].__path__ = [str(ROOT)]
    if "calibre_plugins.calibre_link" not in sys.modules:
        import calibre_link

        sys.modules["calibre_plugins.calibre_link"] = calibre_link


_setup_shims()

from calibre_link.ui import CalibreLinkAction  # noqa: E402


class CalibreLinkUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.action = CalibreLinkAction(None, "Calibre Link")
        self.action.genesis()
        # Suppress real file writing during unit tests
        self.action._write_connection_file = MagicMock()

    def test_genesis_initializes_auth_prompted_and_suppress_dialogs(self):
        self.assertFalse(self.action._auth_prompted)
        self.assertFalse(self.action.suppress_dialogs)
        self.assertEqual(self.action.ts_status["state"], "off")

    def test_sidecar_line_auth_url_marker_schedules_prompt(self):
        timer_calls = []
        test_url = "https://login.tailscale.com/a/test001"
        with patch(
            "qt.core.QTimer.singleShot",
            side_effect=lambda delay, cb: timer_calls.append((delay, cb)),
        ):
            self.action._sidecar_line(f"CALIBRE_TSNET auth_url={test_url}")

        self.assertEqual(self.action.ts_status["state"], "auth")
        self.assertEqual(self.action.ts_status["auth_url"], test_url)
        self.assertTrue(self.action._auth_prompted)
        self.assertEqual(len(timer_calls), 1)
        delay, callback = timer_calls[0]
        self.assertEqual(delay, 0)

        # Invoking the callback should trigger _prompt_tailscale_auth
        with patch.object(self.action, "_prompt_tailscale_auth") as mock_prompt:
            callback()
            mock_prompt.assert_called_once_with(test_url)

    def test_sidecar_line_tsnet_stdout_marker_schedules_prompt(self):
        timer_calls = []
        test_url = "https://login.tailscale.com/a/test002"
        with patch(
            "qt.core.QTimer.singleShot",
            side_effect=lambda delay, cb: timer_calls.append((delay, cb)),
        ):
            self.action._sidecar_line(
                f"tsnet: To authenticate, visit: go to: {test_url}"
            )

        self.assertEqual(self.action.ts_status["state"], "auth")
        self.assertEqual(self.action.ts_status["auth_url"], test_url)
        self.assertTrue(self.action._auth_prompted)
        self.assertEqual(len(timer_calls), 1)
        self.assertEqual(timer_calls[0][0], 0)

    def test_sidecar_line_auth_url_deduplication(self):
        timer_calls = []
        test_url = "https://login.tailscale.com/a/test003"
        with patch(
            "qt.core.QTimer.singleShot",
            side_effect=lambda delay, cb: timer_calls.append((delay, cb)),
        ):
            self.action._sidecar_line(f"CALIBRE_TSNET auth_url={test_url}")
            self.action._sidecar_line(f"CALIBRE_TSNET auth_url={test_url}")
            self.action._sidecar_line(f"tsnet: go to: {test_url}")

        self.assertEqual(len(timer_calls), 1)

    def test_auth_prompted_resets_on_connected_state(self):
        self.action._auth_prompted = True
        self.action._sidecar_line(
            "CALIBRE_TSNET ip=100.64.0.1 dns=calibre-link.tailnet"
        )
        self.assertEqual(self.action.ts_status["state"], "up")
        self.assertFalse(self.action._auth_prompted)

    def test_auth_prompted_resets_on_start_sidecar(self):
        self.action._auth_prompted = True
        self.action._sidecar_path = MagicMock(return_value=None)
        self.action.start_sidecar()
        self.assertFalse(self.action._auth_prompted)

    def test_prompt_tailscale_auth_modal_dialog_construction(self):
        test_url = "https://login.tailscale.com/a/test004"
        with patch.object(QtWidgets.QDialog, "exec", return_value=0) as mock_exec:
            dlg = self.action._prompt_tailscale_auth(test_url)

        self.assertIsNotNone(dlg)
        self.assertTrue(mock_exec.called)
        self.assertTrue(dlg.isModal())
        self.assertEqual(dlg.windowTitle(), "Tailscale Authentication")
        self.assertEqual(dlg.auth_url, test_url)
        self.assertEqual(dlg.open_button.text(), "Open Browser")
        self.assertEqual(dlg.copy_button.text(), "Copy Link")
        self.assertEqual(dlg.dismiss_button.text(), "Dismiss")

    def test_prompt_tailscale_auth_open_browser_action(self):
        test_url = "https://login.tailscale.com/a/test005"
        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg = self.action._prompt_tailscale_auth(test_url)

        accepted_called = []
        dlg.accepted.connect(lambda: accepted_called.append(True))
        with patch("qt.core.QDesktopServices.openUrl") as mock_open:
            dlg.open_button.click()
            self.assertTrue(mock_open.called)
            self.assertEqual(mock_open.call_args[0][0].toString(), test_url)
            self.assertEqual(len(accepted_called), 1)

    def test_prompt_tailscale_auth_copy_link_action(self):
        test_url = "https://login.tailscale.com/a/test006"
        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg = self.action._prompt_tailscale_auth(test_url)

        with patch.object(
            QtWidgets.QApplication.clipboard(), "setText"
        ) as mock_set_text:
            dlg.copy_button.click()
            mock_set_text.assert_called_once_with(test_url)

    def test_prompt_tailscale_auth_dismiss_action(self):
        test_url = "https://login.tailscale.com/a/test007"
        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg = self.action._prompt_tailscale_auth(test_url)

        rejected_called = []
        dlg.rejected.connect(lambda: rejected_called.append(True))
        dlg.dismiss_button.click()
        self.assertEqual(len(rejected_called), 1)

    def test_prompt_tailscale_auth_suppression_attribute(self):
        test_url = "https://login.tailscale.com/a/test008"
        self.action.suppress_dialogs = True
        with patch.object(QtWidgets.QDialog, "exec") as mock_exec:
            res = self.action._prompt_tailscale_auth(test_url)
            self.assertIsNone(res)
            mock_exec.assert_not_called()

        self.action.suppress_dialogs = False
        self.action._suppress_auth_dialog = True
        with patch.object(QtWidgets.QDialog, "exec") as mock_exec:
            res = self.action._prompt_tailscale_auth(test_url)
            self.assertIsNone(res)
            mock_exec.assert_not_called()

    def test_prompt_tailscale_auth_suppression_env_var(self):
        test_url = "https://login.tailscale.com/a/test009"
        with patch.dict(os.environ, {"CALIBRE_LINK_SUPPRESS_AUTH_DIALOG": "1"}):
            with patch.object(QtWidgets.QDialog, "exec") as mock_exec:
                res = self.action._prompt_tailscale_auth(test_url)
                self.assertIsNone(res)
                mock_exec.assert_not_called()

    def test_prompt_tailscale_auth_empty_url(self):
        with patch.object(QtWidgets.QDialog, "exec") as mock_exec:
            self.assertIsNone(self.action._prompt_tailscale_auth(""))
            self.assertIsNone(self.action._prompt_tailscale_auth(None))
            mock_exec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
