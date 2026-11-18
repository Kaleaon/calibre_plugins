from calibre.customize import InterfaceActionBase


class PluginPackManager(InterfaceActionBase):
    name = 'Plugin Pack Manager'
    description = 'Install or update the whole Kaleaon calibre plugin pack (all plugins and metadata sources) in one go'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.plugin_pack_manager.action:PackManagerAction'
