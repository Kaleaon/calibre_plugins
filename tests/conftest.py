import sys
import types
from unittest.mock import MagicMock

# Base stubs for Calibre and Qt when running in headless/CI test environments without full Calibre installed.
for mod_name in (
    "calibre",
    "calibre.customize",
    "calibre.gui2",
    "calibre.gui2.actions",
    "calibre.utils",
    "calibre.utils.config",
    "calibre.utils.date",
    "calibre.ebooks",
    "calibre.ebooks.metadata",
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


sys.modules["calibre.utils.config"].JSONConfig = _get_config
