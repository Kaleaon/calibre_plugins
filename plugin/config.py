try:
    from calibre.utils.config import JSONConfig
except ImportError:
    class JSONConfig(dict):
        def __init__(self, name):
            super().__init__()
            self.defaults = {}

        def __getitem__(self, key):
            return super().get(key, self.defaults.get(key))

        def __setitem__(self, key, value):
            super().__setitem__(key, value)

        def get(self, key, default=None):
            return super().get(key, self.defaults.get(key, default))

prefs = JSONConfig('plugins/takeout_book_fixer')
prefs.defaults['drop_folder'] = ''
prefs.defaults['default_output_dir'] = ''
prefs.defaults['auto_import'] = True
prefs.defaults['convert_comics'] = False
prefs.defaults['watch_interval_sec'] = 5
