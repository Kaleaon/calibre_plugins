from calibre.customize import InterfaceActionBase

class MediaMatcher(InterfaceActionBase):
    name = 'Media Matcher'
    description = ('Adds music files and audiobooks to your library, reading their tags, and matches music, audiobooks and books '
                   'against the free MusicBrainz, Cover Art Archive, AcoustID and Open Library databases, with a preview and undo')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.media_matcher.action:MediaMatcherAction'
