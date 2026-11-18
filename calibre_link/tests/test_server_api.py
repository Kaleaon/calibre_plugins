"""Headless end-to-end test of the plugin's HTTP API against a fake
adapter backed by a COPY of the test library (sqlite, like the old
standalone bridge). Run: python3 tests/test_server_api.py"""

import json
import os
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.expanduser("~/workspace/calibre-plugin/src"))
from server import BridgeServer

HOME = os.path.expanduser("~")
SRC_LIB = os.path.join(HOME, "workspace/calibre-bridge/test-library/Calibre Library")
LIB = os.path.join(HOME, "workspace/tools/test-tmp/lib-plugin/Calibre Library")
shutil.rmtree(os.path.dirname(LIB), ignore_errors=True)
os.makedirs(LIB)
shutil.copy2(os.path.join(SRC_LIB, "metadata.db"), os.path.join(LIB, "metadata.db"))


def ro():
    con = sqlite3.connect("file:" + LIB.replace(os.sep, "/") + "/metadata.db?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


class FakeAdapter:
    def health(self):
        n = ro().execute("select count(*) c from books").fetchone()["c"]
        return {"library": LIB, "book_count": n}

    def stats(self):
        con = ro()
        return {
            "total_books": con.execute("select count(*) c from books").fetchone()["c"],
            "missing_isbn": con.execute("select count(*) c from books where isbn is null or isbn=''").fetchone()["c"],
        }

    def list_books(self, q="", limit=50, offset=0):
        con = ro()
        rows = [
            dict(r)
            for r in con.execute(
                "select b.id, b.title, b.author_sort, b.isbn, b.has_cover, b.path, group_concat(a.name,' & ') authors "
                "from books b left join books_authors_link l on l.book=b.id left join authors a on a.id=l.author "
                "group by b.id order by b.title limit ? offset ?",
                (int(limit), int(offset)),
            )
        ]
        if q:
            rows = [r for r in rows if q.lower() in (r["title"] or "").lower()]
        return rows

    def get_book(self, book_id):
        for r in self.list_books("", 10000, 0):
            if r["id"] == book_id:
                return r
        return None

    def backup(self):
        os.makedirs(os.path.join(LIB, "backups"), exist_ok=True)
        name = "metadata-test-backup.db"
        src = sqlite3.connect("file:" + LIB.replace(os.sep, "/") + "/metadata.db?mode=ro", uri=True)
        dst = sqlite3.connect(os.path.join(LIB, "backups", name))
        src.backup(dst)
        dst.close()
        src.close()
        return name

    def deleted_books(self):
        return {"ids": [2], "via": "fake", "trash_dir": "/tmp/x", "trash_entries": [], "survey": {}}

    def restore_deleted(self, ids):
        return {
            "ok": True,
            "backup": "metadata-test.db",
            "results": {str(i): {"restored": True, "via": "fake"} for i in ids},
        }

    def trash_file(self, book_id):
        return {"ok": False, "error": "no trash dir"}

    def merge_books(self, keep_id, remove_ids):
        return {
            "ok": True,
            "keep": keep_id,
            "removed": remove_ids,
            "backup": "metadata-test.db",
            "formats_copied": [],
            "tags_added": [],
        }

    def ops_log(self):
        return {"lines": ["12:00:00 test"], "write_lock_held": False}

    def add_book(self, filename, data, metadata=None):
        return {"ok": True, "book_id": 4242, "received": len(data), "metadata": metadata or {}}

    def book_text(self, book_id, max_chars=8000):
        return {
            "ok": True,
            "book_id": book_id,
            "supported": True,
            "format": "EPUB",
            "head": "Title page text",
            "tail": "",
            "stamps": ["(ebook by Undead)"],
        }

    def plugins_inventory(self):
        return {
            "plugins": [{"name": "Metadata++", "version": "9.9", "type": "Source"}],
            "metadata_sources": [{"name": "Google Books", "id": None}],
            "diagnostics": {"identify_import": "ok"},
        }

    def fetch_metadata(self, book_id, apply=False, fields=None):
        return {
            "ok": True,
            "book_id": book_id,
            "candidates": [
                {"title": "Fetched Title", "authors": ["A U Thor"], "identifiers": {"isbn": "9780000000000"}}
            ],
        }

    def _real_fs(self):
        # fs endpoints run the REAL adapter logic over a stub db
        # whose book paths come from the copied test library.
        from adapter import CalibreAdapter

        paths = {r["id"]: r["path"] for r in ro().execute("select id, path from books")}

        class _StubAPI:
            def all_book_ids(self):
                return sorted(paths)

        class _StubDB:
            library_path = LIB
            new_api = _StubAPI()

            def path(self, book_id, index_is_id=True):
                return paths[book_id]

        return CalibreAdapter(lambda: _StubDB())

    def fs_list(self, rel="", offset=0, limit=1000):
        return self._real_fs().fs_list(rel, offset=offset, limit=limit)

    def fs_move(self, src, dst):
        return self._real_fs().fs_move(src, dst)

    def fs_upload(self, rel_dir, filename, data, overwrite=False):
        return self._real_fs().fs_upload(rel_dir, filename, data, overwrite=overwrite)

    def fs_download(self, rel, offset=0, length=None):
        return self._real_fs().fs_download(rel, offset=offset, length=length)

    def book_file(self, book_id, fmt=None, offset=0, length=None):
        data = b"ABC"
        chunk = data[offset:] if length is None else data[offset : offset + length]
        return {
            "ok": True,
            "book_id": book_id,
            "format": (fmt or "EPUB").upper(),
            "filename": "x.epub",
            "size": 3,
            "offset": offset,
            "chunk_size": len(chunk),
            "eof": offset + len(chunk) >= 3,
            "data_base64": _b64.b64encode(chunk).decode(),
        }

    def upload_begin(self, rel_dir, filename, size, transfer_id, sha256=None, overwrite=False):
        return self._real_fs().upload_begin(rel_dir, filename, size, transfer_id, sha256=sha256, overwrite=overwrite)

    def upload_chunk(self, transfer_id, offset, data):
        return self._real_fs().upload_chunk(transfer_id, offset, data)

    def upload_status(self, transfer_id):
        return self._real_fs().upload_status(transfer_id)

    def upload_complete(self, transfer_id):
        return self._real_fs().upload_complete(transfer_id)

    def upload_abort(self, transfer_id):
        return self._real_fs().upload_abort(transfer_id)

    def transfers_list(self):
        return self._real_fs().transfers_list()

    def transfer_pause(self, key):
        return self._real_fs().transfer_pause(key)

    def transfer_resume(self, key):
        return self._real_fs().transfer_resume(key)

    def chat_list(self, since=0):
        return self._real_fs().chat_list(since=since)

    def chat_post(self, sender, text):
        return self._real_fs().chat_post(sender, text)

    def swap_title_authors(self, book_id):
        return {
            "ok": True,
            "backup": "metadata-test.db",
            "swapped": {"from_title": "Doe, Jane", "from_authors": ["Some Title"]},
            "book": self.get_book(book_id),
        }

    def set_metadata(self, book_id, fields):
        con = sqlite3.connect(os.path.join(LIB, "metadata.db"))
        if "title" in fields:
            con.execute("update books set title=? where id=?", (fields["title"], book_id))
        con.commit()
        con.close()
        return self.get_book(book_id)


TOKEN = "test-token-123"
srv = BridgeServer("127.0.0.1", 18765, TOKEN, FakeAdapter())
assert srv.start(), srv.last_error


def call(method, path, payload=None, token=TOKEN):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:18765" + path,
        data=data,
        method=method,
        headers={"X-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


s, h = call("GET", "/health", token="")
assert s == 200 and h["ok"] and h["mode"] == "plugin", h
s, r = call("GET", "/stats", token="wrong")
assert s == 401
s, r = call("GET", "/stats")
assert s == 200 and r["ok"]
s, r = call("GET", "/books?limit=5")
assert s == 200
books = r["books"]
bid = books[0]["id"]
s, r = call("GET", "/book/%d" % bid)
assert s == 200
s, r = call("POST", "/backup", {})
assert s == 200
s, r = call("POST", "/set-metadata/%d" % bid, {"fields": {"title": "Plugin Test Title"}})
assert r["book"]["title"] == "Plugin Test Title"
s, r = call("POST", "/calibredb", {"args": ["show_metadata", str(bid)]})
assert r["ok"] and r["via"] == "plugin"
s, r = call("POST", "/calibredb", {"args": ["remove", str(bid)]})
assert not r["ok"]
s, r = call("GET", "/nope")
assert s == 404
import base64 as _b64  # noqa: E402

s, r = call(
    "POST",
    "/add-book",
    {"filename": "x.epub", "data_base64": _b64.b64encode(b"fake-epub").decode(), "metadata": {"title": "T"}},
)
assert s == 200 and r["book_id"] == 4242 and r["received"] == 9, r
s, r = call("POST", "/add-book", {"filename": "x.pdf", "data_base64": "AA=="})
assert s == 400, r
s, r = call("GET", "/deleted-books")
assert s == 200 and r["ids"] == [2], r
s, r = call("POST", "/restore-deleted", {"ids": [2]})
assert s == 200 and r["results"]["2"]["restored"], r
s, r = call("POST", "/merge-books", {"keep": 1, "remove": [2]})
assert s == 200 and r["ok"] and r["removed"] == [2], r
s, r = call("GET", "/log")
assert s == 200 and r["ops"]["lines"], r
s, r = call("GET", "/book-text/%d" % bid)
assert s == 200 and r["stamps"] == ["(ebook by Undead)"], r
s, r = call("GET", "/plugins")
assert s == 200 and r["plugins"]["metadata_sources"][0]["name"] == "Google Books", r
s, r = call("POST", "/fetch-metadata/%d" % bid, {})
assert s == 200 and r["candidates"][0]["identifiers"]["isbn"] == "9780000000000", r
s, r = call(
    "POST",
    "/set-metadata-batch",
    {"items": [{"id": bid, "fields": {"title": "Batch Title"}}, {"id": 999999, "fields": {"title": "Ghost"}}]},
)
assert s == 200 and r["backup"] and len(r["results"]) == 2 and r["results"][0]["ok"], r
print("batch results:", [(x["id"], x["ok"]) for x in r["results"]])
s, r = call("GET", "/fs-list", token="wrong")
assert s == 401
s, r = call("GET", "/fs-list")
assert s == 200 and any(e["name"] == "metadata.db" for e in r["entries"]), r
assert r["total"] == len(r["entries"]) and r["offset"] == 0, r
s, r = call("GET", "/fs-list?offset=1&limit=1")
assert s == 200 and len(r["entries"]) == 1 and r["offset"] == 1 and r["total"] > 1, r
with open(os.path.join(LIB, "stray.txt"), "w") as f:
    f.write("x")
s, r = call("POST", "/fs-move", {"src": "stray.txt", "dst": "moved/stray.txt"})
assert s == 200 and r["ok"] and r["kind"] == "file", r
assert os.path.exists(os.path.join(LIB, "moved", "stray.txt"))
s, r = call("POST", "/fs-move", {"src": "../x", "dst": "y"})
assert s == 500 and not r["ok"], r
s, r = call("POST", "/fs-move", {"src": "metadata.db", "dst": "z.db"})
assert s == 500 and not r["ok"], r
s, r = call(
    "POST",
    "/fs-upload",
    {"path": "incoming", "filename": "b.epub", "data_base64": _b64.b64encode(b"epub-bytes").decode()},
)
assert s == 200 and r["ok"] and r["path"] == "incoming/b.epub", r
with open(os.path.join(LIB, "incoming", "b.epub"), "rb") as f:
    assert f.read() == b"epub-bytes"
s, r = call(
    "POST", "/fs-upload", {"path": "incoming", "filename": "b.epub", "data_base64": _b64.b64encode(b"again").decode()}
)
assert s == 500 and not r["ok"], r
s, r = call("POST", "/swap-title-authors/%d" % bid, {})
assert s == 200 and r["ok"] and r["swapped"]["from_title"] == "Doe, Jane", r
s, r = call("GET", "/fs-download?path=incoming/b.epub")
assert s == 200 and r["ok"] and _b64.b64decode(r["data_base64"]) == b"epub-bytes", r
s, r = call("GET", "/fs-download?path=metadata.db")
assert s == 500 and not r["ok"], r
s, r = call("GET", "/book-file/%d" % bid)
assert s == 200 and r["ok"] and r["data_base64"] == "QUJD", r
s, r = call("GET", "/fs-download?path=incoming/b.epub&offset=4&length=4")
assert s == 200 and _b64.b64decode(r["data_base64"]) == b"-byt" and not r["eof"], r
# chunked upload over HTTP: begin -> chunk -> status -> chunk -> complete
import hashlib as _hl  # noqa: E402

blob = b"Z" * 10000
tid = "http-transfer-01"
s, r = call(
    "POST",
    "/fs-upload-begin",
    {"transfer_id": tid, "path": "big", "filename": "z.bin", "size": len(blob), "sha256": _hl.sha256(blob).hexdigest()},
)
assert s == 200 and r["ok"] and r["received"] == 0, r
s, r = call(
    "POST", "/fs-upload-chunk", {"transfer_id": tid, "offset": 0, "data_base64": _b64.b64encode(blob[:6000]).decode()}
)
assert s == 200 and r["received"] == 6000, r
s, r = call("GET", "/fs-upload-status?transfer_id=" + tid)
assert s == 200 and r["received"] == 6000, r
s, r = call(
    "POST",
    "/fs-upload-chunk",
    {"transfer_id": tid, "offset": 6000, "data_base64": _b64.b64encode(blob[6000:]).decode()},
)
assert s == 200 and r["received"] == len(blob), r
s, r = call("POST", "/fs-upload-complete", {"transfer_id": tid})
assert s == 200 and r["ok"] and r["path"] == "big/z.bin", r
with open(os.path.join(LIB, "big", "z.bin"), "rb") as f:
    assert f.read() == blob
# -- transfer progress + pause/resume routes (v1.13) ----------------------
s, r = call(
    "POST", "/fs-upload-begin", {"transfer_id": "http-transfer-02", "path": "big", "filename": "p.bin", "size": 100}
)
assert s == 200 and r["ok"], r
s, r = call(
    "POST",
    "/fs-upload-chunk",
    {"transfer_id": "http-transfer-02", "offset": 0, "data_base64": _b64.b64encode(b"Q" * 40).decode()},
)
assert s == 200 and r["received"] == 40, r
s, r = call("GET", "/transfers")
assert s == 200 and r["ok"], r
mine = [t for t in r["transfers"] if t["key"] == "http-transfer-02"]
assert mine and mine[0]["percent"] == 40 and mine[0]["kind"] == "upload", r
s, r = call("POST", "/transfers-pause", {"key": "http-transfer-02"})
assert s == 200 and "http-transfer-02" in r["paused"], r
s, r = call(
    "POST",
    "/fs-upload-chunk",
    {"transfer_id": "http-transfer-02", "offset": 40, "data_base64": _b64.b64encode(b"Q").decode()},
)
assert s == 200 and not r["ok"] and r.get("paused"), r
s, r = call("POST", "/transfers-resume", {"key": "*"})
assert s == 200 and r["paused"] == [], r
s, r = call("POST", "/fs-upload-abort", {"transfer_id": "http-transfer-02"})
assert s == 200 and r["ok"], r
# -- chat routes (v1.14) ----------------------------------------------------
s, r = call("POST", "/chat", {"from": "joe", "text": "hello bridge"})
assert s == 200 and r["ok"] and r["count"] == 1, r
s, r = call("POST", "/chat", {"from": "mallory", "text": "intruder"})
assert s == 200 and not r["ok"], r
s, r = call("GET", "/chat")
assert s == 200 and r["count"] == 1 and r["messages"][0]["text"] == "hello bridge", r
s, r = call("GET", "/chat?since=1")
assert s == 200 and r["messages"] == [], r
srv.stop()
print("ALL SERVER API TESTS PASSED")
