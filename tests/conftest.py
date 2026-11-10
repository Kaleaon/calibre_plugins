import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure ROOT and common directories are in sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "common") not in sys.path:
    sys.path.insert(0, str(ROOT / "common"))

# Ensure Qt runs in headless / offscreen mode during tests
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Keep a module-level reference to QApplication so it isn't garbage collected
_GLOBAL_TEST_APP = None
_QT_MOD = None
_QT_CORE = None

try:
    from PyQt6 import QtCore, QtGui, QtWidgets

    _GLOBAL_TEST_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    _QT_CORE = types.ModuleType("qt.core")
    for module in (QtWidgets, QtGui, QtCore):
        for name in dir(module):
            setattr(_QT_CORE, name, getattr(module, name))
    _QT_MOD = types.ModuleType("qt")
    _QT_MOD.core = _QT_CORE
    sys.modules.setdefault("qt", _QT_MOD)
    sys.modules.setdefault("qt.core", _QT_CORE)
except Exception:
    pass

try:
    import lxml
    import lxml.etree

    _REAL_LXML = lxml
    _REAL_LXML_ETREE = lxml.etree
except Exception:
    _REAL_LXML = None
    _REAL_LXML_ETREE = None


class StubMetadata:
    def __init__(self, title="", authors=None):
        self.title = title
        self.authors = authors or []
        self.ids = {}
        self.comments = None
        self.tags = []

    def set_identifier(self, key, value):
        self.ids[key] = value


class StubPrefs(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.defaults = {}

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            return self.defaults.get(key, None)

    def get(self, key, default=None):
        if key in self:
            return self[key]
        if key in self.defaults:
            return self.defaults[key]
        return default


def _get_config(name):
    return StubPrefs()


def ensure_stubs():
    # Base stubs for Calibre and Qt when running in headless/CI test environments without full Calibre installed.
    for mod_name in (
        "calibre",
        "calibre.customize",
        "calibre.gui2",
        "calibre.gui2.actions",
        "calibre.utils",
        "calibre.utils.config",
        "calibre.utils.date",
        "calibre.constants",
        "calibre.ebooks",
        "calibre.ebooks.metadata",
        "calibre.ebooks.metadata.meta",
        "calibre.ebooks.metadata.book",
        "calibre.ebooks.metadata.book.base",
        "calibre.library",
        "calibre.library.db",
    ):
        if mod_name not in sys.modules:
            m = types.ModuleType(mod_name)
            m.__path__ = []
            sys.modules[mod_name] = m

    if not hasattr(sys.modules["calibre.customize"], "InterfaceActionBase"):
        sys.modules["calibre.customize"].InterfaceActionBase = type(
            "InterfaceActionBase", (object,), {}
        )

    if not hasattr(sys.modules["calibre.gui2.actions"], "InterfaceAction"):
        sys.modules["calibre.gui2.actions"].InterfaceAction = type(
            "InterfaceAction",
            (object,),
            {"__init__": lambda self, gui=None, name=None: setattr(self, "gui", gui)},
        )

    if not hasattr(sys.modules["calibre.gui2"], "info_dialog"):
        sys.modules["calibre.gui2"].info_dialog = MagicMock()
    if not hasattr(sys.modules["calibre.gui2"], "error_dialog"):
        sys.modules["calibre.gui2"].error_dialog = MagicMock()
    if not hasattr(sys.modules["calibre.gui2"], "question_dialog"):
        sys.modules["calibre.gui2"].question_dialog = MagicMock()

    if not hasattr(sys.modules["calibre.ebooks.metadata.book.base"], "Metadata"):
        sys.modules["calibre.ebooks.metadata.book.base"].Metadata = StubMetadata

    if not hasattr(sys.modules["calibre.ebooks.metadata.meta"], "get_metadata"):
        sys.modules["calibre.ebooks.metadata.meta"].get_metadata = (
            lambda stream, stream_type="epub": StubMetadata("Test", ["Author"])
        )

    if not hasattr(sys.modules["calibre.utils.date"], "parse_date"):
        sys.modules["calibre.utils.date"].parse_date = lambda d, assume_utc=True: None

    if not hasattr(sys.modules["calibre.utils.config"], "JSONConfig"):
        sys.modules["calibre.utils.config"].JSONConfig = _get_config

    if not hasattr(sys.modules["calibre.utils.config"], "config_dir") or not isinstance(
        getattr(sys.modules["calibre.utils.config"], "config_dir", None), str
    ):
        sys.modules["calibre.utils.config"].config_dir = tempfile.gettempdir()

    if "calibre.constants" in sys.modules:
        c_mod = sys.modules["calibre.constants"]
        if not hasattr(c_mod, "config_dir") or not isinstance(
            getattr(c_mod, "config_dir", None), str
        ):
            c_mod.config_dir = tempfile.gettempdir()
        if not hasattr(c_mod, "islinux"):
            c_mod.islinux = sys.platform.startswith("linux")
        if not hasattr(c_mod, "iswindows"):
            c_mod.iswindows = sys.platform.startswith("win")
        if not hasattr(c_mod, "isosx"):
            c_mod.isosx = sys.platform == "darwin"


ensure_stubs()


@pytest.fixture(autouse=True)
def restore_real_modules():
    yield
    if _REAL_LXML is not None and isinstance(sys.modules.get("lxml"), MagicMock):
        sys.modules["lxml"] = _REAL_LXML
    if _REAL_LXML_ETREE is not None and isinstance(sys.modules.get("lxml.etree"), MagicMock):
        sys.modules["lxml.etree"] = _REAL_LXML_ETREE
    if _QT_MOD is not None and "qt" not in sys.modules:
        sys.modules["qt"] = _QT_MOD
    if _QT_CORE is not None and "qt.core" not in sys.modules:
        sys.modules["qt.core"] = _QT_CORE
    ensure_stubs()


def pytest_runtest_setup(item):
    ensure_stubs()


def pytest_runtest_teardown(item, nextitem=None):
    ensure_stubs()
