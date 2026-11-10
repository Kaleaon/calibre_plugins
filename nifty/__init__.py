from calibre.customize import InterfaceActionBase

class NiftyDownloader(InterfaceActionBase):
    name = 'Nifty Downloader'
    description = 'Download Nifty archive stories as EPUB, browse by category, and join chapter files into one book'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 1, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.nifty_downloader.action:NiftyAction'
