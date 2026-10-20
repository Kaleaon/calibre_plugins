"""Validate a real Takeout ZIP via installed plugin and a separate Calibre library."""

import collections
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from calibre.customize.ui import find_plugin
from calibre.db.legacy import LibraryDatabase

source = Path(sys.argv[1])
target = Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=True)
plugin = find_plugin("Takeout Book Fixer")
with plugin:
    from calibre_plugins.takeout_book_fixer.core import recover
    from calibre_plugins.takeout_book_fixer.importer import import_books

    files, rows, report = recover(source, target / "recovered")
    print("Recovery:", dict(collections.Counter(r["status"] for r in rows)), flush=True)
    print(
        "Formats:",
        dict(collections.Counter(r.get("format") for r in rows if "output" in r)),
        flush=True,
    )
    print(
        "Extension corrections:",
        sum(
            Path(r["source"]).suffix.lower() != "." + r["format"]
            for r in rows
            if "output" in r
        ),
        flush=True,
    )
    db = LibraryDatabase(str(target / "library"))
    gui = SimpleNamespace(
        current_db=db,
        library_view=SimpleNamespace(
            model=lambda: SimpleNamespace(refresh=lambda: None)
        ),
        tags_view=SimpleNamespace(recount=lambda: None),
    )
    ids, errors = import_books(gui, rows)
    validated = 0
    known_authors = 0
    covers = 0
    for book_id in ids:
        mi = db.new_api.get_metadata(book_id)
        assert mi.title
        if mi.authors and mi.authors != ["Unknown"]:
            known_authors += 1
        if db.new_api.cover(book_id):
            covers += 1
        assert db.new_api.formats(book_id)
        validated += 1
    print(
        "Imported:",
        len(ids),
        "errors:",
        len(errors),
        "verified library records:",
        validated,
        "known authors:",
        known_authors,
        "covers:",
        covers,
        flush=True,
    )
    for row in rows:
        if row.get("status") == "error" or row.get("import_status") == "error":
            print("ERROR:", row.get("reason") or row.get("import_error"), flush=True)
    with open(report, "w") as f:
        json.dump(rows, f, indent=2)
    db.close()
    print("Report:", report, flush=True)
