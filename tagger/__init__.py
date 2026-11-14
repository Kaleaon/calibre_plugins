from calibre.customize import InterfaceActionBase

class StoryCollectionTagger(InterfaceActionBase):
    name = 'Story Collection Tagger'
    description = 'Auto-tag books by source site (Metabods, Nifty, Royal Road...) and by your own keyword rules'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.story_collection_tagger.action:TaggerAction'
