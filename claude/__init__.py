from calibre.customize import InterfaceActionBase

class ClaudeLibraryFixer(InterfaceActionBase):
    name = 'Claude Library Fixer'
    description = 'Uses the Anthropic Muse API to suggest cleaner titles, authors, series, tags and ISBNs, optionally by reading cover pictures (which renames the book files), with a preview and undo'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.claude_library_fixer.action:ClaudeFixerAction'
