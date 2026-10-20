import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness_utils import create_test_gui
from calibre.customize.ui import find_plugin
from calibre.library import db as calibre_db
root=Path('/workspace/bookfixer/takeout-validation/january')
rows=json.loads(next((root/'recovered').glob('recovery-report-*.json')).read_text())
plugin=find_plugin('Takeout Book Fixer')
with plugin:
 from calibre_plugins.takeout_book_fixer.core import recover
 from calibre_plugins.takeout_book_fixer.importer import import_books
 db=calibre_db(str(root/'comic-library'))
 gui=create_test_gui(db)
 for index,row in enumerate(r for r in rows if r.get('format')=='pdf'):
  files,result,report=recover(row['output'],root/('pdf-conversion-'+str(index)),comics=True)
  assert files,result
  ids,errors=import_books(gui,result)
  assert len(ids)==1 and not errors,(result,errors)
  expected='pdf' if index<2 else 'cbz'
  assert result[0]['format']==expected,result
  assert db.new_api.format(ids[0],expected.upper())
  print('PASS: real sample PDF',index+1,'recovered and imported as',expected,flush=True)
 db.close()
