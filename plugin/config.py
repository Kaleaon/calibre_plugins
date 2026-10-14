try:
    from calibre.utils.config import JSONConfig
except ImportError:
    class JSONConfig(dict):
        def __init__(self, name):
            super().__init__()
            self.defaults = {}

        def __getitem__(self, key):
            try:
                return super().__getitem__(key)
            except KeyError:
                return self.defaults[key]

        def get(self, key, default=None):
            if key in self:
                return self[key]
            if key in self.defaults:
                return self.defaults[key]
            return default

prefs = JSONConfig('plugins/takeout_book_fixer')
prefs.defaults['watch_dir'] = ''
prefs.defaults['output_dir'] = ''
prefs.defaults['processed_dir'] = ''
prefs.defaults['auto_watch'] = False
prefs.defaults['convert_comics'] = False
prefs.defaults['auto_import'] = True
prefs.defaults['quiet_period'] = 3
