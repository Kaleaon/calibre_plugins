"""Inkbunny & SoFurry metadata source — companion to the Inkbunny & SoFurry Downloader plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside InkbunnySoFurryDownloader.zip. All searching lives in the
downloader's metadata_source module; this class only adapts it to Calibre's
Source API and feeds it the downloader's saved logins.
"""

from calibre.ebooks.metadata.sources.base import Source


class InkbunnySoFurrySource(Source):
    name = "Inkbunny & SoFurry"
    description = "Fetches writing metadata from Inkbunny (API search and submission ids) and SoFurry (submission ids and author galleries)"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 2, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({"identify"})
    touched_fields = frozenset(
        {"title", "authors", "tags", "comments", "pubdate", "publisher", "identifier:inkbunny", "identifier:sofurry"}
    )
    has_html_comments = True

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import config, metadata_source, metakit
        settings = {key: config.prefs[key] for key in ("inkbunny_username", "inkbunny_password", "sofurry_token")}
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
