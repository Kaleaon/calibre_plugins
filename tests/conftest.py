"""Pytest global configuration and fixtures."""
import os
import sys
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_GLOBAL_QT_APP = None

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    _GLOBAL_QT_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    if 'qt.core' not in sys.modules:
        qtcore = types.ModuleType('qt.core')
        for module in (QtWidgets, QtGui, QtCore):
            for name in dir(module):
                setattr(qtcore, name, getattr(module, name))
        qtcore._ = lambda s: s
        sys.modules['qt'] = types.ModuleType('qt')
        sys.modules['qt.core'] = qtcore
except ImportError:
    pass
