"""Pytest global configuration and fixtures."""
import os
import sys
import types
from unittest.mock import MagicMock

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

for mod_name in (
    'calibre',
    'calibre.customize',
    'calibre.gui2',
    'calibre.gui2.actions',
    'calibre.utils',
    'calibre.utils.config',
    'calibre.utils.date',
    'calibre.utils.shared_file',
    'calibre.ebooks',
    'calibre.ebooks.metadata',
    'calibre.ebooks.metadata.book',
    'calibre.ebooks.metadata.book.base',
    'calibre.library',
    'calibre.library.db',
):
    if mod_name not in sys.modules:
        m = types.ModuleType(mod_name)
        m.__path__ = []
        sys.modules[mod_name] = m

if not hasattr(sys.modules['calibre.utils.shared_file'], 'share_open'):
    sys.modules['calibre.utils.shared_file'].share_open = lambda p, *a, **kw: open(p, *a, **kw)

if not hasattr(sys.modules['calibre.customize'], 'InterfaceActionBase'):
    sys.modules['calibre.customize'].InterfaceActionBase = type(
        'InterfaceActionBase', (object,), {}
    )

if not hasattr(sys.modules['calibre.gui2.actions'], 'InterfaceAction'):
    sys.modules['calibre.gui2.actions'].InterfaceAction = type(
        'InterfaceAction',
        (object,),
        {'__init__': lambda self, gui=None, name=None: setattr(self, 'gui', gui)},
    )

if not hasattr(sys.modules['calibre.gui2'], 'info_dialog'):
    sys.modules['calibre.gui2'].info_dialog = MagicMock()
if not hasattr(sys.modules['calibre.gui2'], 'error_dialog'):
    sys.modules['calibre.gui2'].error_dialog = MagicMock()
if not hasattr(sys.modules['calibre.gui2'], 'question_dialog'):
    sys.modules['calibre.gui2'].question_dialog = MagicMock()


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


_shared_configs = {}


def _get_config(name):
    if name not in _shared_configs:
        _shared_configs[name] = StubPrefs()
    return _shared_configs[name]


if not hasattr(sys.modules['calibre.utils.config'], 'JSONConfig') or isinstance(sys.modules['calibre.utils.config'].JSONConfig, MagicMock):
    sys.modules['calibre.utils.config'].JSONConfig = _get_config


class Metadata:
    def __init__(self, title='Test', authors=None):
        self.title = title
        self.authors = authors or ['Author']
        self.ids = {}

    def set_identifier(self, k, v):
        self.ids[k] = v


for mod in ('calibre.ebooks.metadata.meta',):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)
sys.modules['calibre.ebooks.metadata.meta'].get_metadata = lambda stream, stream_type: Metadata('Test', ['Author'])
sys.modules['calibre.ebooks.metadata.book.base'].Metadata = Metadata
if 'calibre.utils.date' not in sys.modules:
    sys.modules['calibre.utils.date'] = types.ModuleType('calibre.utils.date')
sys.modules['calibre.utils.date'].parse_date = lambda d, assume_utc=True: None

import tempfile

_orig_cleanup = tempfile.TemporaryDirectory.cleanup


def _safe_cleanup(self):
    try:
        _orig_cleanup(self)
    except OSError:
        pass


tempfile.TemporaryDirectory.cleanup = _safe_cleanup


