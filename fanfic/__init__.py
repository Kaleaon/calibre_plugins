from calibre.customize import InterfaceActionBase

class FanficDownloader(InterfaceActionBase):
    name = 'Fanfic Site Downloader'
    description = 'Download stories from Royal Road, Archive of Our Own and 100+ other sites as EPUB, using FanFicFare\'s adapters'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.fanfic_downloader.action:FanficAction'

    def load_actual_plugin(self, gui):
        # The bundled third-party libraries (cloudscraper, requests_file, ...) are importable by their plain names
        # only while the plugin is on sys.path; FanFicFare itself lives in this package as a sub-package.
        with self:
            try:
                # FanFicFare imports those libraries when it loads, so load it now, while they can be found.
                from calibre_plugins.fanfic_downloader import engine
                engine.load()
            except Exception:
                pass  # reported with the real reason when the user first tries to download
            return InterfaceActionBase.load_actual_plugin(self, gui)
