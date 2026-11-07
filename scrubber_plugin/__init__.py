from calibre.customize import InterfaceActionBase


class EpubPromoScrubber(InterfaceActionBase):
    name = "EPUB Promo Scrubber"
    description = "Remove promotional watermarks, banner pages and links that sites add to EPUB books"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = "calibre_plugins.epub_promo_scrubber.action:ScrubAction"
