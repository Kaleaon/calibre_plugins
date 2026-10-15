from pathlib import Path
import zipfile
root = Path(__file__).resolve().parent
SHARED = ['storykit.py', 'guikit.py', 'libkit.py', 'error_handler.py']
PLUGINS = [('plugin', 'TakeoutBookFixer', 'takeout_book_fixer'),
           ('scrubber_plugin', 'EpubPromoScrubber', 'epub_promo_scrubber')]
for folder, zip_name, import_name in PLUGINS:
    with zipfile.ZipFile(root / f'dist/{zip_name}.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted((root / folder).glob('*.py')):
            z.write(path, path.name)
        for name in SHARED:
            if (root / 'common' / name).exists():
                z.write(root / 'common' / name, name)
        z.writestr(f'plugin-import-name-{import_name}.txt', '')
    print(root / f'dist/{zip_name}.zip')
