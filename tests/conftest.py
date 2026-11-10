import os
import sys
from pathlib import Path

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
    import types
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

import pytest
from unittest.mock import MagicMock


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
