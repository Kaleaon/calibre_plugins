"""Dialogs for the Plugin Pack Manager: pack status table, run log, and settings."""

from qt.core import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

try:
    from calibre_plugins.plugin_pack_manager.core import format_version
    from calibre_plugins.plugin_pack_manager.manifest import DEFAULT_BASE_URL
except ImportError:
    try:
        from .core import format_version
        from .manifest import DEFAULT_BASE_URL
    except ImportError:
        from core import format_version
        from manifest import DEFAULT_BASE_URL

KIND_LABELS = {"plugin": "Plugin", "source": "Metadata source", "bridge": "Bridge", "manager": "Manager"}
ACTION_LABELS = {
    "install": "Not installed",
    "update": "Update available",
    "current": "Up to date",
    "check": "Installed (unverified)",
}


class StatusDialog(QDialog):
    """One row per pack entry: what it is, the installed version, and its plan/status."""

    def __init__(self, parent, rows, title="Plugin pack status"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["Plugin", "Kind", "Installed", "Status"])
        for row_idx, row in enumerate(rows):
            entry = row["entry"]
            installed = row.get("installed")
            values = [
                entry["name"],
                KIND_LABELS.get(entry["kind"], entry["kind"]),
                format_version(installed["version"]) if installed else "—",
                row.get("status") or ACTION_LABELS.get(row.get("action", ""), row.get("action", "")),
            ]
            for col_idx, value in enumerate(values):
                table.setItem(row_idx, col_idx, QTableWidgetItem(value))
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class LogDialog(QDialog):
    """The plain-text result of an install/update run."""

    def __init__(self, parent, title, lines):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 480)
        layout = QVBoxLayout(self)
        text = QPlainTextEdit("\n".join(lines))
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class SettingsDialog(QDialog):
    """Where the pack is fetched from, and which parts of it are included."""

    def __init__(self, parent, prefs):
        super().__init__(parent)
        self.setWindowTitle("Plugin Pack Manager settings")
        self.prefs = prefs
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Download base URL (the folder holding the plugin ZIPs and SHA256SUMS.txt):"))
        self.base_url = QLineEdit(prefs.get("base_url") or DEFAULT_BASE_URL)
        layout.addWidget(self.base_url)
        self.include_sources = QCheckBox("Include the metadata sources")
        self.include_sources.setChecked(bool(prefs.get("include_sources", True)))
        layout.addWidget(self.include_sources)
        self.include_bridge = QCheckBox("Include Calibre Link (the ~17 MB bridge plugin)")
        self.include_bridge.setChecked(bool(prefs.get("include_bridge", True)))
        layout.addWidget(self.include_bridge)
        self.verify_hashes = QCheckBox("Verify downloads against the published SHA-256 hashes")
        self.verify_hashes.setChecked(bool(prefs.get("verify_hashes", True)))
        layout.addWidget(self.verify_hashes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self):
        self.prefs["base_url"] = self.base_url.text().strip() or DEFAULT_BASE_URL
        self.prefs["include_sources"] = self.include_sources.isChecked()
        self.prefs["include_bridge"] = self.include_bridge.isChecked()
        self.prefs["verify_hashes"] = self.verify_hashes.isChecked()
        self.accept()
