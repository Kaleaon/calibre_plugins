"""Simulate calibre's plugin loader: extract the built zip as a
calibre_plugins.calibre_link package, stub the calibre modules the
plugin imports at load time, and verify the wiring."""

import os
import shutil
import sqlite3
import sys
import tempfile
import types
import zipfile

ROOT = os.path.expanduser("~/workspace/tools/test-tmp/plugin-import")
shutil.rmtree(ROOT, ignore_errors=True)
pkg = os.path.join(ROOT, "calibre_plugins", "calibre_link")
os.makedirs(pkg)
with zipfile.ZipFile(os.path.expanduser("~/workspace/calibre-plugin/CalibreLink.zip")) as z:
    names = z.namelist()
    assert "tailscale/calibre-tsnet-windows-amd64.exe" in names, names
    assert "tailscale/calibre-tsnet-linux-amd64" in names, names
    z.extractall(pkg)
sys.path.insert(0, ROOT)

calibre = types.ModuleType("calibre")
sys.modules["calibre"] = calibre
customize = types.ModuleType("calibre.customize")


class InterfaceActionBase:
    pass


customize.InterfaceActionBase = InterfaceActionBase
sys.modules["calibre.customize"] = customize
utils = types.ModuleType("calibre.utils")
sys.modules["calibre.utils"] = utils
cconfig = types.ModuleType("calibre.utils.config")


class JSONConfig:
    def __init__(self, path):
        self._d = {}

    @property
    def defaults(self):
        return self._d

    @defaults.setter
    def defaults(self, v):
        self._d.update(v)

    def get(self, k, default=None):
        v = self._d.get(k, default)
        return default if v in ("", None) and default is not None else v

    def __getitem__(self, k):
        return self._d[k]

    def __setitem__(self, k, v):
        self._d[k] = v


cconfig.JSONConfig = JSONConfig
cconfig.config_dir = ROOT
sys.modules["calibre.utils.config"] = cconfig
gui2 = types.ModuleType("calibre.gui2")
sys.modules["calibre.gui2"] = gui2
actions = types.ModuleType("calibre.gui2.actions")


class InterfaceAction:
    pass


actions.InterfaceAction = InterfaceAction
sys.modules["calibre.gui2.actions"] = actions
constants = types.ModuleType("calibre.constants")
constants.iswindows = False
constants.islinux = True
constants.isosx = False
sys.modules["calibre.constants"] = constants

import calibre_plugins.calibre_link as plugin_pkg  # noqa: E402

P = plugin_pkg.CalibreLinkPlugin
print("plugin name:", P.name, "| version:", P.version)
assert P.name == "Calibre Link"
assert P.version == (1, 14, 0)
assert P.actual_plugin == "calibre_plugins.calibre_link.ui:CalibreLinkAction"

import calibre_plugins.calibre_link.adapter as adapter  # noqa: E402
import calibre_plugins.calibre_link.config as config  # noqa: E402
import calibre_plugins.calibre_link.server as server  # noqa: E402

print("server VERSION:", server.VERSION)
print("ts prefs present:", all(k in config.prefs.defaults for k in ("ts_enabled", "ts_hostname", "ts_authkey")))
assert all(k in config.prefs.defaults for k in ("ts_enabled", "ts_hostname", "ts_authkey"))
print("config token generated:", bool(config.get_token()))
import calibre_plugins.calibre_link.ui as ui  # noqa: E402

A = ui.CalibreLinkAction
for hook in (
    "genesis",
    "initialization_complete",
    "library_changed",
    "shutting_down",
    "start_sidecar",
    "stop_sidecar",
    "show_status",
    "config_widget",
    "save_settings",
):
    assert hasattr(A, hook), hook
print(
    "ui hooks present: genesis/initialization_complete/library_changed/"
    "shutting_down/start_sidecar/stop_sidecar/show_status/config/save"
)
print("sidecar resource for linux:", ui._sidecar_resource())
assert ui._sidecar_resource() == "tailscale/calibre-tsnet-linux-amd64"
print("ALL IMPORT-STRUCTURE TESTS PASSED")

# --- v1.6.1 regression: real adapter merge path reaches the backup ---
_tmp = tempfile.mkdtemp()
sqlite3.connect(os.path.join(_tmp, "metadata.db")).close()


class _FakeDB:
    library_path = _tmp
    new_api = None


_ad = adapter.CalibreAdapter(lambda: _FakeDB())
_r = _ad.merge_books(1, [2])
assert _r["ok"] is False and "cache API unavailable" in _r["error"], _r
assert _r["backup"].startswith("metadata-"), _r
assert os.path.exists(os.path.join(_tmp, "backups", _r["backup"]))
print("merge smoke (real adapter, backup step): OK")
