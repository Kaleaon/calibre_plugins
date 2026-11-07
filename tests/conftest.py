"""Pytest configuration and test harness environment setup."""

import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

test_config_dir = os.path.join(tempfile.gettempdir(), "calibre_plugins_test_config")
os.makedirs(test_config_dir, exist_ok=True)

# Keep global QApplication alive across all tests
_GLOBAL_QT_APP = None

# Set up qt.core mapping if running in standalone test environment
if "qt.core" not in sys.modules:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets

        _GLOBAL_QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        qt = types.ModuleType("qt")
        qtcore = types.ModuleType("qt.core")
        for mod in (QtCore, QtGui, QtWidgets):
            for k, v in mod.__dict__.items():
                if not k.startswith("__"):
                    setattr(qtcore, k, v)
        qtcore._ = lambda s: s
        qt.core = qtcore
        sys.modules["qt"] = qt
        sys.modules["qt.core"] = qtcore
    except ImportError:
        qt = types.ModuleType("qt")
        qt.core = MagicMock()
        sys.modules["qt"] = qt
        sys.modules["qt.core"] = qt.core
else:
    try:
        from PyQt6 import QtWidgets

        if not QtWidgets.QApplication.instance():
            _GLOBAL_QT_APP = QtWidgets.QApplication([])
        else:
            _GLOBAL_QT_APP = QtWidgets.QApplication.instance()
    except Exception:
        pass


class StubInterfaceAction:
    def __init__(self, gui=None, name=None):
        self.gui = gui
        self.name = name


class StubJSONConfig(dict):
    def __init__(self, name="", *args, **kwargs):
        super().__init__()
        self.name = name
        self.defaults = {}

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            return self.defaults.get(key, "")

    def get(self, key, default=None):
        if key in self:
            return self[key]
        if key in self.defaults:
            return self.defaults[key]
        return default


class StubMetadata:
    def __init__(self, title="", authors=None):
        self.title = title
        self.authors = authors or []
        self.identifiers = {}

    def set_identifier(self, k, v):
        self.identifiers[k] = v


# Ensure calibre base modules are stubbed if running outside Calibre
for mod_name in (
    "calibre",
    "calibre.constants",
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
):
    if mod_name not in sys.modules:
        m = MagicMock()
        m.__path__ = []
        sys.modules[mod_name] = m

sys.modules["calibre.constants"].config_dir = test_config_dir
sys.modules["calibre.utils.config"].config_dir = test_config_dir
sys.modules["calibre.utils.config"].JSONConfig = StubJSONConfig
sys.modules["calibre.gui2.actions"].InterfaceAction = StubInterfaceAction
sys.modules["calibre.customize"].InterfaceActionBase = StubInterfaceAction
sys.modules["calibre.ebooks.metadata.book.base"].Metadata = StubMetadata
sys.modules["calibre.ebooks.metadata.book"].base = sys.modules[
    "calibre.ebooks.metadata.book.base"
]
