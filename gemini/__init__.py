from calibre.customize import InterfaceActionBase

class GeminiLibraryFixer(InterfaceActionBase):
    name = 'Gemini Library Fixer'
    description = 'Uses the Google Gemini API to suggest cleaner titles, authors, series, tags and ISBNs, optionally by reading cover pictures (which renames the book files), with a preview and undo'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 2, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.gemini_library_fixer.action:GeminiFixerAction'
