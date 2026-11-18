"""Story Source Tagger metadata source — companion to the Story Collection Tagger plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside StoryCollectionTagger.zip. All tagging lives in the tagger's
metadata_source module; this class only adapts it to Calibre's Source API
and feeds it the tagger's saved rules and settings.
"""
from calibre.ebooks.metadata.sources.base import Source


class StorySourceTaggerSource(Source):
    name = 'Story Source Tagger'
    description = 'Adds the tags the Story Collection Tagger would give a book (source site and keyword rules); touches tags only'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 1, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({'identify'})
    touched_fields = frozenset({'tags'})
    has_html_comments = False

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import config, metadata_source, metakit
        settings = {key: config.prefs[key] for key in
                    ('add_source', 'source_prefix', 'series_prefix', 'scan_comments', 'rules', 'sites')}
        metakit.run_identify(self, lambda **kw: metadata_source.search(settings=settings, **kw),
                             log, result_queue, abort,
                             title=title, authors=authors, identifiers=identifiers, timeout=timeout)
