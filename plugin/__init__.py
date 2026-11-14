from calibre.customize import InterfaceActionBase

class TakeoutBookFixer(InterfaceActionBase):
    name = 'Takeout Book Fixer'
    description = 'Recover correctly typed books from Google Takeout folders and ZIP files'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Bookfixer'
    version = (1, 1, 1)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = 'calibre_plugins.takeout_book_fixer.action:TakeoutAction'
