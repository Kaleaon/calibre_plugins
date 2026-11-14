"""Unit tests for Calibre Link UI status dialog and automated Tailscale auth modal."""

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


class CalibreLinkStatusDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.action = CalibreLinkAction(None, "Calibre Link")
        self.action.genesis()
        self.action._write_connection_file = MagicMock()
        self.action.server = MagicMock(running=True, last_error=None)
        self.action.adapter = MagicMock()
        self.action.adapter.health.return_value = {
            "library": "/tmp/test_library",
            "book_count": 42,
        }
        self.action.start_server = MagicMock()
        self.action.start_sidecar = MagicMock()

    def test_auth_state_renders_anchor_link_and_action_buttons(self):
        auth_url = "https://login.tailscale.com/a/1234567890abcdef"
        self.action.ts_status = {
            "state": "auth",
            "auth_url": auth_url,
            "ip": None,
            "dns": None,
            "bridge": False,
            "error": None,
        }

        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg = self.action.show_status()

        # Requirement 1: QTextBrowser with setOpenExternalLinks(True)
        tb = dlg.findChild(QtWidgets.QTextBrowser)
        self.assertIsNotNone(tb, "Expected QTextBrowser widget in status dialog")
        self.assertTrue(
            tb.openExternalLinks(),
            "QTextBrowser must have setOpenExternalLinks(True)",
        )
        self.assertTrue(tb.isReadOnly(), "QTextBrowser should be read-only")

        # Requirement 2: Authorization URL rendered as an HTML anchor tag
        html_content = tb.toHtml()
        expected_anchor = f'<a href="{auth_url}">'
        self.assertIn(
            expected_anchor,
            html_content,
            f"Expected HTML anchor '{expected_anchor}' in status dialog HTML",
        )

        buttons = {b.text(): b for b in dlg.findChildren(QtWidgets.QPushButton)}

        # Requirement 3: Dedicated "Open Auth Link" button calling QDesktopServices.openUrl()
        self.assertIn("Open Auth Link", buttons)
        with patch.object(QtGui.QDesktopServices, "openUrl") as mock_open:
            buttons["Open Auth Link"].click()
            self.assertTrue(mock_open.called, "Open Auth Link button did not call openUrl")
            arg = mock_open.call_args[0][0]
            self.assertEqual(arg.toString(), auth_url)

        # Requirement 4: Dedicated "Copy Auth Link" button calling QApplication.clipboard().setText()
        self.assertIn("Copy Auth Link", buttons)
        QtWidgets.QApplication.clipboard().clear()
        buttons["Copy Auth Link"].click()
        self.assertEqual(
            QtWidgets.QApplication.clipboard().text(),
            auth_url,
            "Copy Auth Link button did not copy expected URL to clipboard",
        )

        # Backward compatibility: standard controls remain present
        self.assertIn("Restart bridge", buttons)
        self.assertIn("Restart Tailscale", buttons)
        self.assertIn("Close", buttons)

    def test_reverts_to_standard_actions_when_connected(self):
        self.action.ts_status = {
            "state": "up",
            "auth_url": None,
            "ip": "100.101.102.103",
            "dns": "calibre-link.tailnet.ts.net",
            "bridge": True,
            "error": None,
        }

        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg = self.action.show_status()

        buttons = {b.text(): b for b in dlg.findChildren(QtWidgets.QPushButton)}
        self.assertNotIn(
            "Open Auth Link",
            buttons,
            "Open Auth Link should not appear when Tailscale state is 'up'",
        )
        self.assertNotIn(
            "Copy Auth Link",
            buttons,
            "Copy Auth Link should not appear when Tailscale state is 'up'",
        )
        self.assertIn("Restart bridge", buttons)
        self.assertIn("Restart Tailscale", buttons)
        self.assertIn("Close", buttons)

        tb = dlg.findChild(QtWidgets.QTextBrowser)
        self.assertIsNotNone(tb)
        self.assertIn("CONNECTED as calibre-link.tailnet.ts.net", tb.toPlainText())
        self.assertIn("Tailnet IP: 100.101.102.103", tb.toPlainText())

    def test_auth_state_without_auth_url_does_not_add_action_buttons(self):
        # Empty string
        self.action.ts_status = {
            "state": "auth",
            "auth_url": "",
            "ip": None,
            "dns": None,
            "bridge": False,
            "error": None,
        }
        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg_empty = self.action.show_status()

        buttons_empty = {b.text(): b for b in dlg_empty.findChildren(QtWidgets.QPushButton)}
        self.assertNotIn("Open Auth Link", buttons_empty)
        self.assertNotIn("Copy Auth Link", buttons_empty)

        # None value
        self.action.ts_status["auth_url"] = None
        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg_none = self.action.show_status()

        buttons_none = {b.text(): b for b in dlg_none.findChildren(QtWidgets.QPushButton)}
        self.assertNotIn("Open Auth Link", buttons_none)
        self.assertNotIn("Copy Auth Link", buttons_none)

    def test_restart_buttons_functionality(self):
        self.action.ts_status = {
            "state": "starting",
            "auth_url": None,
            "ip": None,
            "dns": None,
            "bridge": False,
            "error": None,
        }
        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg = self.action.show_status()

        buttons = {b.text(): b for b in dlg.findChildren(QtWidgets.QPushButton)}

        with patch.object(dlg, "accept") as mock_accept:
            buttons["Restart bridge"].click()
            self.action.start_server.assert_called_once()
            mock_accept.assert_called_once()

        with patch.object(dlg, "accept") as mock_accept:
            buttons["Restart Tailscale"].click()
            self.action.start_sidecar.assert_called_once()
            mock_accept.assert_called_once()


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
            self.action._sidecar_line(f"tsnet: To authenticate, visit: go to: {test_url}")

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
        self.action._sidecar_line("CALIBRE_TSNET ip=100.64.0.1 dns=calibre-link.tailnet")
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

        with patch.object(QtWidgets.QApplication.clipboard(), "setText") as mock_set_text:
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
