"""Pytest configuration and environment fixtures.

Provides offline Calibre and Qt stubs so that tests importing plugin modules
can be collected and run cleanly without requiring a full Calibre installation.
"""

import os
import sys
import types
from unittest.mock import MagicMock

# Force offscreen Qt platform for headless test execution
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_GLOBAL_APP = None

# Set up Qt stubs or real PyQt6 QApplication if available
if "qt.core" not in sys.modules:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets

        _GLOBAL_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        qtcore = types.ModuleType("qt.core")
        for module in (QtWidgets, QtGui, QtCore):
            for name in dir(module):
                setattr(qtcore, name, getattr(module, name))
        qtcore._ = lambda s: s
        qt_mod = types.ModuleType("qt")
        qt_mod.core = qtcore
        sys.modules["qt"] = qt_mod
        sys.modules["qt.core"] = qtcore
    except ImportError:
        qt_mod = types.ModuleType("qt")
        qt_core = MagicMock()
        qt_mod.core = qt_core
        sys.modules["qt"] = qt_mod
        sys.modules["qt.core"] = qt_core

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

    import tempfile

    cfg_dir = os.path.join(tempfile.gettempdir(), "calibre_config")
    os.makedirs(cfg_dir, exist_ok=True)
    sys.modules["calibre.utils.config"].JSONConfig = lambda name: StubPrefs()
    sys.modules["calibre.utils.config"].config_dir = cfg_dir
    constants = types.ModuleType("calibre.constants")
    constants.config_dir = cfg_dir
    sys.modules["calibre.constants"] = constants
    if "calibre" in sys.modules:
        sys.modules["calibre"].constants = constants
