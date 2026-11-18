"""Metabods metadata source — companion to the Metabods Downloader plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside MetabodsDownloader.zip. All searching lives in the downloader's
metadata_source module; this class only adapts it to Calibre's Source API.
"""
from calibre.ebooks.metadata.sources.base import Source


class MetabodsSource(Source):
    name = 'Metabods'
    description = 'Fetches story metadata (author, tags, summary) from Metabods, by story id/URL or by author'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 1, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({'identify'})
    touched_fields = frozenset({'title', 'authors', 'tags', 'comments', 'publisher', 'identifier:metabods'})
    has_html_comments = True

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import metadata_source, metakit
        metakit.run_identify(self, metadata_source.search, log, result_queue, abort,
                             title=title, authors=authors, identifiers=identifiers, timeout=timeout)

    def get_book_url(self, identifiers):
        with self:
            from . import metadata_source
        return metadata_source.book_url(identifiers)

    def id_from_url(self, url):
        with self:
            from . import metadata_source
        return metadata_source.id_from_url(url)
