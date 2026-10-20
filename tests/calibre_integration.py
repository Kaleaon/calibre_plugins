"""Run with calibre-debug -e tests/calibre_integration.py."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import zipfile
from calibre.customize.ui import find_plugin
from calibre.library import db as calibre_db
from qt.core import QApplication, QMainWindow, QAction, QDialog
app = QApplication([])
plugin = find_plugin('Takeout Book Fixer')
assert plugin is not None, 'Plugin was not installed'
with plugin:
    from calibre_plugins.takeout_book_fixer.core import recover
    from calibre_plugins.takeout_book_fixer.importer import import_books
    from calibre_plugins.takeout_book_fixer.action import TakeoutAction
    print('Plugin modules and Qt action loaded')
    sample = Path('/workspace/bookfixer/calibre-test/sample.epub').read_bytes()
    with tempfile.TemporaryDirectory(prefix='calibre-integration-', dir='/workspace') as tmp:
        root = Path(tmp)
        source = root / 'takeout.zip'
        with zipfile.ZipFile(source, 'w') as z:
            z.writestr('Takeout/Google Play Books/misnamed.pdf', sample)
            z.writestr('Takeout/Google Play Books/misnamed.json', json.dumps({'volumeInfo': {'title': 'Sidecar Sample Title', 'authors': ['Sidecar Author'], 'publisher': 'Sample Press', 'industryIdentifiers': [{'type': 'ISBN_13', 'identifier': '9780306406157'}], 'language': 'en', 'description': 'Recovered description', 'categories': ['Sample'], 'publishedDate': '2024-01-01'}}))
        files, rows, report = recover(source, root / 'out')
        assert len(files) == 1
        assert Path(files[0]).suffix == '.epub'
        db = calibre_db(str(root / 'library'))
        gui = SimpleNamespace(current_db=db, library_view=SimpleNamespace(model=lambda: SimpleNamespace(refresh=lambda: None)), tags_view=SimpleNamespace(recount=lambda: None))
        ids, errors = import_books(gui, rows)
        assert not errors, errors
        assert len(ids) == 1, rows
        mi = db.new_api.get_metadata(ids[0])
        assert mi.title == 'Sidecar Sample Title', mi.title
        assert mi.authors == ['Sidecar Author'], mi.authors
        assert mi.publisher == 'Sample Press'
        assert mi.isbn == '9780306406157'
        assert mi.tags == ['Sample']
        assert db.new_api.format(ids[0], 'EPUB') is not None
        assert db.new_api.cover(ids[0]) is not None, 'Embedded cover missing'
        print('PASS: misnamed EPUB recovered and imported with sidecar metadata and embedded cover')
        ids2, errors = import_books(gui, rows)
        assert not errors and not ids2, (ids2, errors)
        assert len(db.new_api.all_book_ids()) == 1
        print('PASS: repeated import skips identical book')
        files, rows, _ = recover(root / 'out' / Path(files[0]).name, root / 'individual')
        ids3, errors = import_books(gui, rows)
        assert not errors and len(ids3) == 1, (ids3, errors)
        assert db.new_api.get_metadata(ids3[0]).title == 'Embedded Sample Title'
        print('PASS: individual book import reads embedded title and author')
        # Execute the complete GUI action with deterministic file dialogs.
        from unittest.mock import patch
        from calibre_plugins.takeout_book_fixer import action as action_module
        window = QMainWindow()
        window.current_db = db
        window.library_view = gui.library_view
        window.tags_view = gui.tags_view
        action = TakeoutAction(window, plugin)
        action.qaction = QAction(window)
        action.genesis()
        assert len(action.qaction.menu().actions()) == 2
        with patch.object(action_module.QFileDialog, 'getOpenFileName', return_value=(str(source), '')), patch.object(action_module.QFileDialog, 'getExistingDirectory', return_value=str(root / 'gui-output')), patch.object(action_module.QDialog, 'exec', return_value=QDialog.DialogCode.Accepted), patch.object(action_module, 'info_dialog') as success, patch.object(action_module, 'error_dialog') as failure:
            action.run_recovery(True)
            assert not failure.called, failure.call_args
            assert success.called
            reports = list((root / 'gui-output').glob('recovery-report-*.json'))
            saved = json.loads(reports[0].read_text())
            assert any(row.get('import_status') == 'duplicate skipped' for row in saved)
        print('PASS: complete GUI recovery/import action and saved import report')
        comic = Path('/workspace/bookfixer/calibre-test/image.cbz')
        _, comic_rows, _ = recover(comic, root / 'comics')
        comic_ids, errors = import_books(gui, comic_rows)
        assert not errors and len(comic_ids) == 1, errors
        assert db.new_api.format(comic_ids[0], 'CBZ') is not None
        print('PASS: converted comic imported as CBZ into Calibre')
        db.close()
print('All Calibre integration checks passed')
