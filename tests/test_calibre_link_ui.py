"""Unit tests for Calibre Link UI status dialog and Tailscale auth actions."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

calibre_mod = types.ModuleType("calibre")
calibre_mod.__path__ = []
sys.modules["calibre"] = calibre_mod
sys.modules["calibre.customize"] = MagicMock()
sys.modules["calibre.constants"] = MagicMock(islinux=True, iswindows=False)
sys.modules["calibre.gui2"] = MagicMock()
sys.modules["calibre.gui2.actions"] = MagicMock()
actions = types.ModuleType("calibre.gui2.actions")
actions.InterfaceAction = type(
    "InterfaceAction",
    (object,),
    {
        "__init__": lambda self, *args, **kwargs: setattr(
            self, "gui", args[0] if args else kwargs.get("gui")
        )
    },
)
sys.modules["calibre.gui2.actions"] = actions
sys.modules["calibre.constants"] = types.ModuleType("calibre.constants")
sys.modules["calibre.constants"].islinux = True
sys.modules["calibre.constants"].iswindows = False
sys.modules["calibre.constants"].isosx = False

sys.modules["calibre.constants"].config_dir = tempfile.gettempdir()
sys.modules["calibre.utils"] = MagicMock()
sys.modules["calibre.utils.config"] = MagicMock()

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: E402

qtcore = types.ModuleType("qt.core")
for module in (QtWidgets, QtGui, QtCore):
    for attr in dir(module):
        setattr(qtcore, attr, getattr(module, attr))
sys.modules["qt"] = types.ModuleType("qt")
sys.modules["qt.core"] = qtcore

import calibre_link  # noqa: E402

cp = types.ModuleType("calibre_plugins")
cp.__path__ = [str(ROOT)]
sys.modules["calibre_plugins"] = cp
sys.modules["calibre_plugins.calibre_link"] = calibre_link

from calibre_link.ui import CalibreLinkAction  # noqa: E402


class CalibreLinkStatusDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.action = CalibreLinkAction(None, "Calibre Link")
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
            self.assertTrue(
                mock_open.called, "Open Auth Link button did not call openUrl"
            )
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

        buttons_empty = {
            b.text(): b for b in dlg_empty.findChildren(QtWidgets.QPushButton)
        }
        self.assertNotIn("Open Auth Link", buttons_empty)
        self.assertNotIn("Copy Auth Link", buttons_empty)

        # None value
        self.action.ts_status["auth_url"] = None
        with patch.object(QtWidgets.QDialog, "exec", return_value=0):
            dlg_none = self.action.show_status()

        buttons_none = {
            b.text(): b for b in dlg_none.findChildren(QtWidgets.QPushButton)
        }
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


if __name__ == "__main__":
    unittest.main()
