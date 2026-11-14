# -*- coding: utf-8 -*-
"""Calibre Link preferences + config widget.

Settings live in calibre's own plugin config store
(plugins/calibre_link.json in the calibre config directory), so they
survive restarts and travel with the calibre install.
"""
import secrets

from calibre.utils.config import JSONConfig

prefs = JSONConfig("plugins/calibre_link")
prefs.defaults = {
    # 0.0.0.0 = reachable from this PC's other addresses, including its
    # Tailscale address. The token below is always required, and
    # Windows Firewall will still ask before the port is reachable.
    "host": "0.0.0.0",
    "port": 8765,
    "token": "",
    # --- integrated Tailscale (tsnet sidecar; see ui.py) ---
    # The plugin launches a small embedded Tailscale node that joins
    # the tailnet as its own device and proxies to the bridge above.
    # No system Tailscale install needed; the node runs only while
    # calibre runs.
    "ts_enabled": True,
    "ts_hostname": "calibre-link",
    # Optional. Leave empty to approve the device once through the
    # login link the plugin shows on first run (recommended).
    "ts_authkey": "",
}


def get_token():
    """Return the bridge token, generating and saving one on first run."""
    token = prefs.get("token") or ""
    if not token:
        token = secrets.token_urlsafe(24)
        prefs["token"] = token
    return token


# ----------------------------------------------------------------------
# Config widget (Preferences -> Plugins -> Calibre Link -> Customize)
# ----------------------------------------------------------------------
try:
    from qt.core import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                         QLineEdit, QSpinBox, QPushButton, QCheckBox)

    class ConfigWidget(QWidget):
        def __init__(self):
            QWidget.__init__(self)
            layout = QVBoxLayout(self)

            row = QHBoxLayout()
            row.addWidget(QLabel("Listen on address:"))
            self.host_edit = QLineEdit(prefs.get("host", "0.0.0.0"))
            self.host_edit.setToolTip(
                "0.0.0.0 listens on all of this PC's addresses "
                "(including Tailscale). 127.0.0.1 is this PC only.")
            row.addWidget(self.host_edit)
            layout.addLayout(row)

            row = QHBoxLayout()
            row.addWidget(QLabel("Port:"))
            self.port_spin = QSpinBox()
            self.port_spin.setRange(1, 65535)
            self.port_spin.setValue(int(prefs.get("port", 8765)))
            row.addWidget(self.port_spin)
            layout.addLayout(row)

            row = QHBoxLayout()
            row.addWidget(QLabel("Token:"))
            self.token_edit = QLineEdit(get_token())
            self.token_edit.setToolTip(
                "Sent as the X-Token header on every request except "
                "/health. Anyone with this token can edit metadata — "
                "keep it private.")
            row.addWidget(self.token_edit)
            regen = QPushButton("Regenerate")
            regen.clicked.connect(
                lambda: self.token_edit.setText(secrets.token_urlsafe(24)))
            row.addWidget(regen)
            layout.addLayout(row)

            note = QLabel(
                "Changes take effect when the bridge restarts — use the\n"
                "Calibre Link toolbar button and choose Restart, or just\n"
                "restart calibre. The same token and address list are\n"
                "written to CalibreLink-connection.txt in your library\n"
                "folder every time the bridge starts.")
            layout.addWidget(note)

            layout.addWidget(QLabel("<b>Tailscale (integrated)</b>"))
            self.ts_enabled = QCheckBox(
                "Join my tailnet while calibre is running")
            self.ts_enabled.setChecked(bool(prefs.get("ts_enabled", True)))
            layout.addWidget(self.ts_enabled)

            row = QHBoxLayout()
            row.addWidget(QLabel("Device name on the tailnet:"))
            self.ts_hostname = QLineEdit(
                prefs.get("ts_hostname", "calibre-link"))
            row.addWidget(self.ts_hostname)
            layout.addLayout(row)

            row = QHBoxLayout()
            row.addWidget(QLabel("Auth key (optional):"))
            self.ts_authkey = QLineEdit(prefs.get("ts_authkey", ""))
            self.ts_authkey.setToolTip(
                "Leave empty: on first run the plugin shows a one-time "
                "login link; approve it once and the device stays "
                "registered. An auth key (tskey-auth-...) skips that "
                "step.")
            row.addWidget(self.ts_authkey)
            layout.addLayout(row)

            layout.addStretch(1)

        def save_settings(self):
            prefs["host"] = self.host_edit.text().strip() or "0.0.0.0"
            prefs["port"] = int(self.port_spin.value())
            prefs["token"] = self.token_edit.text().strip()
            prefs["ts_enabled"] = bool(self.ts_enabled.isChecked())
            prefs["ts_hostname"] = (self.ts_hostname.text().strip()
                                    or "calibre-link")
            prefs["ts_authkey"] = self.ts_authkey.text().strip()

except Exception:  # headless import (tests) — the real plugin always has Qt
    ConfigWidget = None
