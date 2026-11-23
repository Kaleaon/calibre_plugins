from calibre.customize import InterfaceActionBase


class MuseLibraryFixer(InterfaceActionBase):
    name = "Muse Library Fixer"
    description = "Asks Muse (Meta AI) to suggest cleaner titles, authors, series, tags and ISBNs, optionally by reading cover pictures (which renames the book files), with a preview and undo. No API key: batches are handed to Muse as request files in a .muse-fixer folder in the library, which Muse answers through Calibre Link."
    supported_platforms = ["windows", "osx", "linux"]
    author = "Bookfixer"
    version = (1, 0, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = "calibre_plugins.muse_library_fixer.action:MuseFixerAction"
