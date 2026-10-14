from calibre.utils.config import JSONConfig

prefs = JSONConfig("plugins/nifty_downloader")
prefs.defaults["favorite_categories"] = []  # ['gay/college', ...]
prefs.defaults["skip_existing"] = True
