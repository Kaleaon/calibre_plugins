"""Media Matcher Lookup metadata source — companion to the Media Matcher plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside MediaMatcher.zip. All searching lives in the plugin's
metadata_source module; this class only adapts it to Calibre's Source API
and feeds it the plugin's saved settings.
"""

from calibre.ebooks.metadata.sources.base import Source


class MediaMatcherSource(Source):
    name = "Media Matcher Lookup"
    description = "Looks up music on MusicBrainz (with Cover Art Archive covers) and books/audiobooks on Open Library, using Media Matcher's scoring"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 1, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({"identify", "cover"})
    touched_fields = frozenset(
        {
            "title",
            "authors",
            "tags",
            "series",
            "pubdate",
            "identifier:isbn",
            "identifier:musicbrainz",
            "identifier:openlibrary",
        }
    )
    has_html_comments = False
    cached_cover_url_is_reliable = True

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import config, metadata_source, metakit
        settings = {key: config.prefs[key] for key in ("kind", "contact", "acoustid_key", "fpcalc", "fingerprint")}
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
