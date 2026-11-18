"""Promo Scrubber metadata source — companion to the EPUB Promo Scrubber plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside EpubPromoScrubber.zip. All cleaning lives in the scrubber's
metadata_source module; this class only adapts it to Calibre's Source API
and feeds it the scrubber's saved patterns. The scrubber keeps its
preferences in the same JSONConfig its action uses, so they are read here
directly (the scrubber plugin has no config module).
"""

from calibre.ebooks.metadata.sources.base import Source
from calibre.utils.config import JSONConfig

prefs = JSONConfig("plugins/epub_promo_scrubber")
prefs.defaults["extra_patterns"] = ""
prefs.defaults["heuristic"] = True


class PromoScrubberSource(Source):
    name = "Promo Scrubber"
    description = (
        "Returns the book's title with promo-site marks (OceanofPDF, PDFDrive, ...) removed; touches the title only"
    )
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 1, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({"identify"})
    touched_fields = frozenset({"title"})
    has_html_comments = False

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import metadata_source, metakit
        settings = {"extra_patterns": prefs["extra_patterns"], "heuristic": prefs["heuristic"]}
        metakit.run_identify(
            self,
            lambda **kw: metadata_source.search(settings=settings, **kw),
            log,
            result_queue,
            abort,
            title=title,
            authors=authors,
            identifiers=identifiers,
            timeout=timeout,
        )
