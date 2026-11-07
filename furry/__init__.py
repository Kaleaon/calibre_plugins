from calibre.customize import InterfaceActionBase


class FurryDownloader(InterfaceActionBase):
    name = 'Inkbunny & SoFurry Downloader'
    description = 'Download writing from Inkbunny and SoFurry as EPUB; pools and folders compile into one book'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 0, 1)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.inkbunny_sofurry_downloader.action:FurryAction'
