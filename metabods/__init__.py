from calibre.customize import InterfaceActionBase

class MetabodsDownloader(InterfaceActionBase):
    name = 'Metabods Downloader'
    description = 'Download Metabods stories as EPUB, search by tag, and combine multi-part stories'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.metabods_downloader.action:MetabodsAction'
