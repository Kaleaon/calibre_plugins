"""The plugin pack: every ZIP this repo ships, in install order.

Single hand-maintained list, guarded by tests/test_pack_manager.py, which checks it against
build_plugins.PLUGINS / build_plugins.SOURCES and against the name in each plugin's __init__.py.
Kinds: 'plugin' (InterfaceAction), 'source' (metadata source), 'bridge' (Calibre Link, prebuilt
ZIP committed to dist/), 'manager' (this plugin itself, so the pack can update its own updater).
"""

DEFAULT_BASE_URL = 'https://github.com/Kaleaon/calibre_plugins/raw/refs/heads/main/dist'
SUMS_FILE = 'SHA256SUMS.txt'

PACK = [
    {'zip': 'TakeoutBookFixer.zip', 'name': 'Takeout Book Fixer', 'import_name': 'takeout_book_fixer', 'kind': 'plugin'},
    {'zip': 'EpubPromoScrubber.zip', 'name': 'EPUB Promo Scrubber', 'import_name': 'epub_promo_scrubber', 'kind': 'plugin'},
    {'zip': 'MetabodsDownloader.zip', 'name': 'Metabods Downloader', 'import_name': 'metabods_downloader', 'kind': 'plugin'},
    {'zip': 'NiftyDownloader.zip', 'name': 'Nifty Downloader', 'import_name': 'nifty_downloader', 'kind': 'plugin'},
    {'zip': 'StoryCollectionTagger.zip', 'name': 'Story Collection Tagger', 'import_name': 'story_collection_tagger', 'kind': 'plugin'},
    {'zip': 'FanficDownloader.zip', 'name': 'Fanfic Site Downloader', 'import_name': 'fanfic_downloader', 'kind': 'plugin'},
    {'zip': 'RedditStoryFollower.zip', 'name': 'Reddit Story Follower', 'import_name': 'reddit_follower', 'kind': 'plugin'},
    {'zip': 'GeminiLibraryFixer.zip', 'name': 'Gemini Library Fixer', 'import_name': 'gemini_library_fixer', 'kind': 'plugin'},
    {'zip': 'MediaMatcher.zip', 'name': 'Media Matcher', 'import_name': 'media_matcher', 'kind': 'plugin'},
    {'zip': 'InkbunnySoFurryDownloader.zip', 'name': 'Inkbunny & SoFurry Downloader', 'import_name': 'inkbunny_sofurry_downloader', 'kind': 'plugin'},
    {'zip': 'TakeoutGoogleBooksSource.zip', 'name': 'Takeout Google Books', 'import_name': 'takeout_google_books_source', 'kind': 'source'},
    {'zip': 'EpubPromoScrubberSource.zip', 'name': 'Promo Scrubber', 'import_name': 'epub_promo_scrubber_source', 'kind': 'source'},
    {'zip': 'MetabodsMetadataSource.zip', 'name': 'Metabods', 'import_name': 'metabods_metadata_source', 'kind': 'source'},
    {'zip': 'NiftyMetadataSource.zip', 'name': 'Nifty', 'import_name': 'nifty_metadata_source', 'kind': 'source'},
    {'zip': 'StoryCollectionTaggerSource.zip', 'name': 'Story Source Tagger', 'import_name': 'story_collection_tagger_source', 'kind': 'source'},
    {'zip': 'FanficMetadataSource.zip', 'name': 'FanFicFare Sites', 'import_name': 'fanfic_metadata_source', 'kind': 'source'},
    {'zip': 'RedditMetadataSource.zip', 'name': 'Reddit Stories', 'import_name': 'reddit_metadata_source', 'kind': 'source'},
    {'zip': 'GeminiMetadataSource.zip', 'name': 'Gemini Metadata', 'import_name': 'gemini_metadata_source', 'kind': 'source'},
    {'zip': 'MediaMatcherMetadataSource.zip', 'name': 'Media Matcher Lookup', 'import_name': 'media_matcher_source', 'kind': 'source'},
    {'zip': 'InkbunnySoFurryMetadataSource.zip', 'name': 'Inkbunny & SoFurry', 'import_name': 'inkbunny_sofurry_metadata_source', 'kind': 'source'},
    {'zip': 'CalibreLink.zip', 'name': 'Calibre Link', 'import_name': 'calibre_link', 'kind': 'bridge'},
    {'zip': 'PluginPackManager.zip', 'name': 'Plugin Pack Manager', 'import_name': 'plugin_pack_manager', 'kind': 'manager'},
]

BY_ZIP = {entry['zip']: entry for entry in PACK}
BY_IMPORT = {entry['import_name']: entry for entry in PACK}


def select_entries(include_sources=True, include_bridge=True, include_manager=True, only=None, exclude=None):
    """Filter PACK by kind switches and by name/zip/import-name substrings (only, then exclude)."""
    entries = []
    for entry in PACK:
        if entry['kind'] == 'source' and not include_sources:
            continue
        if entry['kind'] == 'bridge' and not include_bridge:
            continue
        if entry['kind'] == 'manager' and not include_manager:
            continue
        entries.append(entry)
    if only:
        wanted = [term.lower() for term in only]
        entries = [e for e in entries
                   if any(term in e['zip'].lower() or term in e['import_name'].lower() or term in e['name'].lower()
                          for term in wanted)]
    if exclude:
        banned = [term.lower() for term in exclude]
        entries = [e for e in entries
                   if not any(term in e['zip'].lower() or term in e['import_name'].lower() or term in e['name'].lower()
                              for term in banned)]
    return entries
