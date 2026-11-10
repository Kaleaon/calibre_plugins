import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import MagicMock

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
    _QT_CORE._ = lambda s: s
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

# Set up Calibre stubs if Calibre is not installed
if "calibre" not in sys.modules:
    for mod_name in [
        "calibre",
        "calibre.customize",
        "calibre.gui2",
        "calibre.gui2.actions",
        "calibre.utils",
        "calibre.utils.config",
        "calibre.utils.date",
        "calibre.ebooks",
        "calibre.ebooks.metadata",
        "calibre.ebooks.metadata.meta",
        "calibre.ebooks.metadata.book",
        "calibre.ebooks.metadata.book.base",
    ]:
        if mod_name not in sys.modules:
            sys.modules[mod_name] = MagicMock()

    class StubMetadata:
        def __init__(self, title="", authors=None):
            self.title = title
            self.authors = authors or []
            self.ids = {}
            self.comments = None
            self.tags = []
            self.publisher = None

        def set_identifier(self, key, value):
            self.ids[key] = value

    sys.modules["calibre.ebooks.metadata.book.base"].Metadata = StubMetadata
    sys.modules["calibre.gui2.actions"].InterfaceAction = type(
        "InterfaceAction",
        (object,),
        {"__init__": lambda self, gui=None, name=None: setattr(self, "gui", gui)},
    )
    sys.modules["calibre.customize"].InterfaceActionBase = type(
        "InterfaceActionBase",
        (object,),
        {},
    )

    class StubPrefs(dict):
        def __init__(self):
            super().__init__()
            self.defaults = {}

        def __getitem__(self, key):
            try:
                return dict.__getitem__(self, key)
            except KeyError:
                return self.defaults[key]

        def get(self, key, default=None):
            if key in self:
                return dict.__getitem__(self, key)
            if key in self.defaults:
                return self.defaults[key]
            return default

    sys.modules["calibre.utils.config"].JSONConfig = lambda name: StubPrefs()
    sys.modules["calibre.utils.config"].config_dir = tempfile.gettempdir()

import pytest


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
