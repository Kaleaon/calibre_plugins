from calibre.utils.config import JSONConfig

prefs = JSONConfig('plugins/story_collection_tagger')
prefs.defaults['add_source'] = True
prefs.defaults['source_prefix'] = 'Source.'
prefs.defaults['series_prefix'] = ''
prefs.defaults['scan_comments'] = False
prefs.defaults['rules'] = ''
prefs.defaults['sites'] = ''
