from calibre.utils.config import JSONConfig

try:
    from calibre_plugins.plugin_pack_manager.manifest import DEFAULT_BASE_URL
except ImportError:
    try:
        from .manifest import DEFAULT_BASE_URL
    except ImportError:
        from manifest import DEFAULT_BASE_URL

prefs = JSONConfig('plugins/plugin_pack_manager')
prefs.defaults['base_url'] = DEFAULT_BASE_URL
prefs.defaults['include_sources'] = True
prefs.defaults['include_bridge'] = True
prefs.defaults['verify_hashes'] = True
