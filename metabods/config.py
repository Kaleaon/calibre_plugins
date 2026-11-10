from calibre.utils.config import JSONConfig

prefs = JSONConfig('plugins/metabods_downloader')
prefs.defaults['favorite_tags'] = []  # [[tag id, name], ...]
prefs.defaults['tag_cache'] = []      # [[tag id, name], ...] from the site's tag index
prefs.defaults['skip_existing'] = True
prefs.defaults['match_all'] = False
