"""Takeout Google Books metadata source — companion to the Takeout Book Fixer plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside TakeoutBookFixer.zip. All searching lives in the fixer's
metadata_source module; this class only adapts it to Calibre's Source API.
"""

from calibre.ebooks.metadata.sources.base import Source


class TakeoutGoogleBooksSource(Source):
    name = "Takeout Google Books"
    description = "Fetches metadata and covers from Google Books — the same records Google Takeout sidecars carry, mapped the way the Takeout Book Fixer maps them"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 2, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({"identify", "cover"})
    touched_fields = frozenset(
        {
            "title",
            "authors",
            "tags",
            "comments",
            "publisher",
            "pubdate",
            "language",
            "identifier:isbn",
            "identifier:google",
        }
    )
    has_html_comments = True
    cached_cover_url_is_reliable = False

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import metadata_source, metakit
        metakit.run_identify(
            self,
            metadata_source.search,
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
