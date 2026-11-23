"""Claude Metadata source — companion to the Claude Library Fixer plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside ClaudeLibraryFixer.zip. All searching lives in the fixer's
metadata_source module; this class only adapts it to Calibre's Source API
and feeds it the fixer's saved settings.
"""

from calibre.ebooks.metadata.sources.base import Source


class ClaudeSource(Source):
    name = "Claude Metadata"
    description = "Cleans up a book's title, authors, series, tags, language and ISBN with the Claude Library Fixer's engine and provider lookups"
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({"identify"})
    touched_fields = frozenset({"title", "authors", "tags", "series", "language", "identifier:isbn"})
    has_html_comments = True

    def _settings(self):
        with self:
            from . import config
        return {key: config.prefs[key] for key in ("api_key", "model", "fields", "min_confidence", "provider_metadata")}

    def is_configured(self):
        with self:
            from . import metadata_source
        return metadata_source.is_configured(self._settings())

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import metadata_source, metakit
        settings = self._settings()
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
