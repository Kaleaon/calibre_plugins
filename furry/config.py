from calibre.utils.config import JSONConfig

prefs = JSONConfig('plugins/inkbunny_sofurry_downloader')
prefs.defaults['skip_existing'] = True
# The Inkbunny login is saved at Joe's request (Nov 3, 2026) so it does not have to be re-entered each time.
# Like every calibre plugin preference it is stored as plain text in calibre's own config folder
# (plugins/inkbunny_sofurry_downloader.json) on this machine only; it is never sent anywhere but Inkbunny's
# own login endpoint.
prefs.defaults['inkbunny_username'] = ''
prefs.defaults['inkbunny_password'] = ''
