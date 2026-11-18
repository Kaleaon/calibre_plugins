"""Plugin Pack Manager action: one menu that checks, installs and updates the whole pack.

Downloads and hash checks run on a background task (guikit.run_task); the installs themselves run
on the GUI thread through calibre.customize.ui.add_plugin, which is Calibre's own validated
install path. Installing or updating any plugin only takes effect after a Calibre restart, which
the result log says plainly.
"""

import shutil
import tempfile
from pathlib import Path

from calibre.gui2 import info_dialog
from calibre.gui2.actions import InterfaceAction
from qt.core import QAction, QFileDialog, QMenu, QMessageBox

try:
    from calibre_plugins.plugin_pack_manager import core, manifest
    from calibre_plugins.plugin_pack_manager.config import prefs
    from calibre_plugins.plugin_pack_manager.guikit import run_task
    from calibre_plugins.plugin_pack_manager.ui import (
        LogDialog,
        SettingsDialog,
        StatusDialog,
    )
except ImportError:
    try:
        from . import core, manifest
        from .config import prefs
        from .guikit import run_task
        from .ui import LogDialog, SettingsDialog, StatusDialog
    except ImportError:
        import core
        import manifest
        from config import prefs
        from guikit import run_task
        from ui import LogDialog, SettingsDialog, StatusDialog


def plugins_dir():
    """The folder Calibre loads user plugins from."""
    try:
        from calibre.customize.ui import plugin_dir as found

        return Path(found() if callable(found) else found)
    except Exception:
        from calibre.utils.config import config_dir

        return Path(config_dir) / "plugins"


def calibre_add_plugin():
    try:
        from calibre.customize.ui import add_plugin

        return add_plugin
    except Exception:
        return None


class PackManagerAction(InterfaceAction):
    name = "Plugin Pack Manager"
    action_spec = ("Plugin Pack Manager", None, "Install or update the whole plugin pack at once", None)

    def genesis(self):
        menu = QMenu(self.gui)
        self.qaction.setMenu(menu)
        for title, slot in [
            ("Install / update all plugins…", self.install_update_all),
            ("Check for updates…", self.check_updates),
            ("Install from a local folder…", self.install_from_folder),
            ("Pack status…", self.pack_status),
            ("Settings…", self.settings),
        ]:
            action = QAction(title, self.gui)
            action.triggered.connect(lambda checked=False, slot=slot: slot())
            menu.addAction(action)
        self.qaction.triggered.connect(self.install_update_all)

    # -- helpers ---------------------------------------------------------
    def entries(self):
        return manifest.select_entries(
            include_sources=bool(prefs.get("include_sources", True)),
            include_bridge=bool(prefs.get("include_bridge", True)),
            include_manager=True,
        )

    def base_url(self):
        return (prefs.get("base_url") or manifest.DEFAULT_BASE_URL).rstrip("/")

    def installed(self):
        return core.scan_plugins_dir(plugins_dir())

    def install_downloaded(self, results):
        """Install every successfully downloaded ZIP whose bytes differ from the installed copy.
        Returns the log lines for the run."""
        installed = self.installed()
        add_plugin = calibre_add_plugin()
        lines, changed = [], 0
        for result in results:
            entry = result["entry"]
            if not result["ok"]:
                lines.append(f"✗ {entry['name']}: {result['error']}")
                continue
            current = installed.get(entry["import_name"])
            if current and current["sha256"] == result["sha256"]:
                lines.append(f"• {entry['name']}: already up to date, skipped")
                continue
            new_version = core.zip_info(result["path"])["version"]
            if current and core.is_downgrade(current["version"], new_version):
                lines.append(
                    f"• {entry['name']}: installed {core.format_version(current['version'])} is newer than "
                    f"the published {core.format_version(new_version)} — skipped, not downgraded"
                )
                continue
            try:
                core.install_zip(result["path"], plugins_dir=plugins_dir(), add_plugin=add_plugin)
            except Exception as exc:
                lines.append(f"✗ {entry['name']}: install failed — {exc}")
                continue
            verb = "updated" if current else "installed"
            lines.append(f"✓ {entry['name']}: {verb} ({core.format_version(new_version)})")
            changed += 1
        if changed:
            try:
                from calibre.customize.ui import reread_metadata_plugins

                reread_metadata_plugins()
            except Exception:
                pass
            lines.append("")
            lines.append(f"{changed} plugin(s) changed. Restart Calibre to load the new versions.")
        return lines

    # -- menu actions ------------------------------------------------------
    def check_updates(self):
        base_url = self.base_url()

        def work(task):
            task.status = "Fetching the published hash list…"
            return core.fetch_sums(base_url)

        task = run_task(self.gui, "Checking for pack updates…", work)
        if task.error:
            info_dialog(self.gui, "Plugin Pack Manager", f"Could not check for updates:\n{task.error}", show=True)
            return
        if task.result is None:
            info_dialog(
                self.gui,
                "Plugin Pack Manager",
                "The hash list (SHA256SUMS.txt) could not be fetched from:\n"
                + base_url
                + "\n\nCheck the base URL in Settings, and your network connection.",
                show=True,
            )
            return
        rows = core.plan_pack(self.entries(), self.installed(), remote_sums=task.result)
        StatusDialog(self.gui, rows, title="Plugin pack — update check").exec()

    def install_update_all(self):
        entries = self.entries()
        sure = QMessageBox.question(
            self.gui,
            "Install / update the plugin pack?",
            f"Download and install/update {len(entries)} pack plugins from:\n{self.base_url()}\n\n"
            "Plugins that are already current are skipped. Calibre needs a restart afterwards.",
        )
        if sure != QMessageBox.StandardButton.Yes:
            return
        base_url = self.base_url()
        verify = bool(prefs.get("verify_hashes", True))
        tmp = tempfile.mkdtemp(prefix="plugin-pack-")

        def work(task):
            sums = core.fetch_sums(base_url) if verify else None
            return core.download_pack(
                entries,
                tmp,
                base_url,
                sums=sums,
                verify=verify,
                log=lambda line: setattr(task, "status", line),
                should_cancel=task.cancelled,
            )

        task = run_task(self.gui, "Downloading the plugin pack…", work)
        if task.error:
            shutil.rmtree(tmp, ignore_errors=True)
            info_dialog(self.gui, "Plugin Pack Manager", f"The download run failed:\n{task.error}", show=True)
            return
        lines = self.install_downloaded(task.result or [])
        shutil.rmtree(tmp, ignore_errors=True)
        LogDialog(self.gui, "Plugin pack — install / update", lines).exec()

    def install_from_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self.gui, "Folder holding the pack ZIPs (e.g. an extracted CalibrePluginsPack)"
        )
        if not folder:
            return
        found, missing = core.folder_pack(folder, self.entries())
        if not found:
            info_dialog(self.gui, "Plugin Pack Manager", "No pack ZIPs were found in that folder.", show=True)
            return
        add_plugin = calibre_add_plugin()
        installed = self.installed()
        lines, changed = [], 0
        for entry in found:
            path = Path(folder) / entry["zip"]
            current = installed.get(entry["import_name"])
            if current and current["sha256"] == core.sha256_file(path):
                lines.append(f"• {entry['name']}: already up to date, skipped")
                continue
            new_version = core.zip_info(path)["version"]
            if current and core.is_downgrade(current["version"], new_version):
                lines.append(
                    f"• {entry['name']}: installed {core.format_version(current['version'])} is newer than "
                    f"the folder's {core.format_version(new_version)} — skipped, not downgraded"
                )
                continue
            try:
                core.install_zip(path, plugins_dir=plugins_dir(), add_plugin=add_plugin)
            except Exception as exc:
                lines.append(f"✗ {entry['name']}: install failed — {exc}")
                continue
            verb = "updated" if current else "installed"
            lines.append(f"✓ {entry['name']}: {verb} ({core.format_version(new_version)})")
            changed += 1
        for entry in missing:
            lines.append(f"• {entry['name']}: {entry['zip']} not in the folder, skipped")
        if changed:
            lines.append("")
            lines.append(f"{changed} plugin(s) changed. Restart Calibre to load the new versions.")
        LogDialog(self.gui, "Plugin pack — install from folder", lines).exec()

    def pack_status(self):
        rows = core.plan_pack(self.entries(), self.installed(), remote_sums=None)
        StatusDialog(self.gui, rows).exec()

    def settings(self):
        SettingsDialog(self.gui, prefs).exec()
