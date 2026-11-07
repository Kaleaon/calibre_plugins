"""Pytest configuration and test harness environment setup."""

import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

test_config_dir = os.path.join(tempfile.gettempdir(), "calibre_plugins_test_config")
os.makedirs(test_config_dir, exist_ok=True)

# Set up qt.core mapping if running in standalone test environment
if "qt.core" not in sys.modules:
    try:
        from PyQt6 import QtCore, QtGui, QtWidgets

        qt = types.ModuleType("qt")
        qtcore = types.ModuleType("qt.core")
        for mod in (QtCore, QtGui, QtWidgets):
            for k, v in mod.__dict__.items():
                setattr(qtcore, k, v)
        sys.modules["qt"] = qt
        sys.modules["qt.core"] = qtcore
    except ImportError:
        qt = types.ModuleType("qt")
        qt.core = MagicMock()
        sys.modules["qt"] = qt
        sys.modules["qt.core"] = qt.core

# Ensure calibre base modules are stubbed if running outside Calibre
for mod_name in (
    "calibre",
    "calibre.constants",
    "calibre.customize",
    "calibre.gui2",
    "calibre.gui2.actions",
    "calibre.utils",
    "calibre.utils.config",
    "calibre.ebooks",
    "calibre.ebooks.metadata",
    "calibre.ebooks.metadata.book",
):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

sys.modules["calibre.constants"].config_dir = test_config_dir
sys.modules["calibre.utils.config"].config_dir = test_config_dir
