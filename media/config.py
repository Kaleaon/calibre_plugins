from calibre.utils.config import JSONConfig

prefs = JSONConfig('plugins/media_matcher')
prefs.defaults['kind'] = 'auto'
prefs.defaults['fields'] = ['title', 'authors', 'album', 'date', 'tags', 'identifiers', 'cover']
prefs.defaults['min_score'] = 0.6
prefs.defaults['contact'] = ''
prefs.defaults['acoustid_key'] = ''
prefs.defaults['fpcalc'] = ''
prefs.defaults['fingerprint'] = False
prefs.defaults['add_kind'] = 'auto'
prefs.defaults['skip_existing'] = True
prefs.defaults['lookup_after_add'] = False
prefs.defaults['undo'] = {}
