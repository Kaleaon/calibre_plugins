"""FanFicFare Sites metadata source — companion to the Fanfic Site Downloader plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside FanficDownloader.zip. All searching lives in the downloader's
metadata_source module; this class only adapts it to Calibre's Source API
and feeds it the downloader's saved settings.
"""

from calibre.ebooks.metadata.sources.base import Source


class FanficFareSource(Source):
    name = "FanFicFare Sites"
    description = (
        "Fetches story metadata from Royal Road, Archive of Our Own and 100+ other FanFicFare sites, by story URL"
    )
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 1, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({"identify", "cover"})
    touched_fields = frozenset(
        {"title", "authors", "tags", "comments", "series", "language", "pubdate", "identifier:url"}
    )
    has_html_comments = True
    cached_cover_url_is_reliable = False

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import config, metadata_source, metakit
        settings = {key: config.prefs[key] for key in ("allow_adult", "extra_adult", "advanced_ini")}
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

    def get_book_url(self, identifiers):
        with self:
            from . import metadata_source
        return metadata_source.book_url(identifiers)

    def id_from_url(self, url):
        with self:
            from . import metadata_source
        return metadata_source.id_from_url(url)
