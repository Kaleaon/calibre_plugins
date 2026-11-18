"""Reddit Stories metadata source — companion to the Reddit Story Follower plugin.

Calibre registers one plugin per ZIP, so this source ships as its own ZIP
alongside RedditStoryFollower.zip. All searching lives in the follower's
metadata_source module; this class only adapts it to Calibre's Source API
and feeds it the follower's saved mode and credentials.
"""
from calibre.ebooks.metadata.sources.base import Source


class RedditSource(Source):
    name = 'Reddit Stories'
    description = 'Finds stories and series on Reddit by title or author, using the same feeds/API as the Reddit Story Follower'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 8, 0)
    minimum_calibre_version = (6, 0, 0)

    capabilities = frozenset({'identify'})
    touched_fields = frozenset({'title', 'authors', 'tags', 'series', 'pubdate', 'publisher', 'identifier:url'})
    has_html_comments = True

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):
        with self:
            from . import config, metadata_source, metakit
        settings = {key: config.prefs[key] for key in
                    ('mode', 'client_id', 'client_secret', 'username', 'refresh_token')}
        metakit.run_identify(self, lambda **kw: metadata_source.search(settings=settings, **kw),
                             log, result_queue, abort,
                             title=title, authors=authors, identifiers=identifiers, timeout=timeout)

    def get_book_url(self, identifiers):
        with self:
            from . import metadata_source
        return metadata_source.book_url(identifiers)

    def id_from_url(self, url):
        with self:
            from . import metadata_source
        return metadata_source.id_from_url(url)
