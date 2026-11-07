import sys
import tempfile
import types
from unittest.mock import MagicMock


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

    if not hasattr(sys.modules["calibre.utils.config"], "config_dir"):
        sys.modules["calibre.utils.config"].config_dir = tempfile.gettempdir()


ensure_stubs()


def pytest_runtest_setup(item):
    ensure_stubs()
