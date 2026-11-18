"""Headless test of the REAL adapter's fs list/move confinement logic
against a stub calibre db and a scratch library folder.
Run: python3 tests/test_fs.py"""

import base64 as _b64
import hashlib as _hl
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.expanduser("~/workspace/calibre-plugin/src"))
from adapter import CalibreAdapter

HOME = os.path.expanduser("~")
LIB = os.path.join(HOME, "workspace/tools/test-tmp/lib-fs/Calibre Library")
shutil.rmtree(os.path.dirname(LIB), ignore_errors=True)
os.makedirs(LIB)


class StubAPI:
    def __init__(self, db):
        self._db = db

    def all_book_ids(self):
        return sorted(self._db.books)


class StubDB:
    library_path = LIB

    def __init__(self):
        self.books = {1: "Author A/Title One (1)", 2: "Author B/Title Two (2)"}
        self.new_api = StubAPI(self)

    def path(self, book_id, index_is_id=True):
        return self.books[book_id]


def put(rel, data=b"x"):
    p = os.path.join(LIB, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


put("Author A/Title One (1)/book.epub", b"live")
put("Author B/Title Two (2)/book.epub", b"live")
put("Orphan Folder (999)/lost.epub", b"orphan")
put("stray.epub", b"stray")
# a REAL (tiny) sqlite file as metadata.db, so adapter.backup() works
_con = sqlite3.connect(os.path.join(LIB, "metadata.db"))
_con.execute("create table books (id integer)")
_con.commit()
_con.close()

ad = CalibreAdapter(lambda: StubDB())

# -- list -------------------------------------------------------------
r = ad.fs_list("")
names = {e["name"]: e for e in r["entries"]}
assert names["Author A"]["type"] == "dir", r
assert names["stray.epub"]["size"] == 5, r
r = ad.fs_list("Author A")
assert r["entries"][0]["name"] == "Title One (1)"
assert r["entries"][0]["is_book_folder"] is True, r
r = ad.fs_list("")
names = {e["name"]: e for e in r["entries"]}
assert names["Orphan Folder (999)"]["is_book_folder"] is False, r

# -- list pagination (v1.12) ---------------------------------------------
r_all = ad.fs_list("")
assert r_all["total"] == len(r_all["entries"]) and not r_all["truncated"], r_all
p1 = ad.fs_list("", offset=0, limit=2)
p2 = ad.fs_list("", offset=2, limit=2)
p3 = ad.fs_list("", offset=4, limit=2)
assert p1["total"] == r_all["total"] and p1["truncated"], p1
assert not p3["truncated"], p3
paged = [e["name"] for e in p1["entries"] + p2["entries"] + p3["entries"]]
assert paged == [e["name"] for e in r_all["entries"]], (paged, r_all)
big = ad.fs_list("", offset=0, limit=99999)
assert len(big["entries"]) == big["total"], big  # limit clamped, no crash

# -- happy-path moves ---------------------------------------------------
r = ad.fs_move("stray.epub", "Orphan Folder (999)/stray.epub")
assert r["ok"] and r["kind"] == "file", r
assert os.path.exists(os.path.join(LIB, "Orphan Folder (999)", "stray.epub"))
r = ad.fs_move("Orphan Folder (999)", "_quarantine/Orphan Folder (999)")
assert r["ok"] and r["kind"] == "dir", r  # missing parent auto-created
assert os.path.exists(os.path.join(LIB, "_quarantine", "Orphan Folder (999)", "lost.epub"))

# -- uploads --------------------------------------------------------------
r = ad.fs_upload("incoming", "book.epub", b"EPUBBYTES")
assert r["ok"] and r["path"] == "incoming/book.epub" and r["size"] == 9, r
with open(os.path.join(LIB, "incoming", "book.epub"), "rb") as f:
    assert f.read() == b"EPUBBYTES"


def upload_refused(rel_dir, filename, data, why, overwrite=False):
    try:
        ad.fs_upload(rel_dir, filename, data, overwrite=overwrite)
    except ValueError as e:
        print("upload refused (%s): %s" % (why, e))
        return
    raise AssertionError("upload %r/%r should have been refused (%s)" % (rel_dir, filename, why))


upload_refused("incoming", "book.epub", b"again", "destination exists")
r = ad.fs_upload("incoming", "book.epub", b"v2", overwrite=True)
assert r["ok"], r
upload_refused("Author A/Title One (1)", "x.epub", b"x", "inside a live book folder")
upload_refused("", "metadata.db", b"x", "protected name", overwrite=True)
upload_refused("incoming", "sub/evil.epub", b"x", "filename not bare")
upload_refused("../out", "x.epub", b"x", "escape via ..")


# -- refusals -----------------------------------------------------------
def refused(src, dst, why):
    try:
        ad.fs_move(src, dst)
    except ValueError as e:
        print("refused (%s): %s" % (why, e))
        return
    raise AssertionError("move %r -> %r should have been refused (%s)" % (src, dst, why))


refused("Author A/Title One (1)", "elsewhere", "live book folder")
refused("Author A", "elsewhere", "ancestor of a live book folder")
refused("Author A/Title One (1)/book.epub", "elsewhere.epub", "file inside a live book folder")
refused("_quarantine", "Author B/Title Two (2)/sub", "destination inside a live book folder")
refused("metadata.db", "metadata-copy.db", "protected file")
refused("../outside", "x", "escape via ..")
refused("/etc/passwd", "x", "absolute path")
refused("C:/Windows/x", "y", "drive-absolute path")
refused("_quarantine", "_quarantine/inner", "folder into itself")
refused("nope.epub", "x.epub", "missing source")
refused("_quarantine/Orphan Folder (999)/lost.epub", "_quarantine/Orphan Folder (999)/stray.epub", "destination exists")

# refused moves changed nothing
assert os.path.exists(os.path.join(LIB, "metadata.db"))
assert os.path.exists(os.path.join(LIB, "Author A", "Title One (1)", "book.epub"))


# -- swap title/authors ---------------------------------------------------
class MI:
    def __init__(self, title, authors):
        self.title = title
        self.authors = authors
        self.author_sort = "STALE"
        self.tags = []
        self.series = None
        self.series_index = None
        self.publisher = None
        self.pubdate = None
        self.rating = None
        self.languages = []
        self.comments = None
        self.isbn = None
        self.has_cover = False

    def get_identifiers(self):
        return {}

    def set_identifiers(self, d):
        pass


class StubAPI2:
    def __init__(self, db):
        self._db = db

    def all_book_ids(self):
        return sorted(self._db.store)

    def formats(self, book_id):
        return ["EPUB"]

    def format(self, book_id, fmt):
        return b"BOOKBYTES"

    def format_abspath(self, book_id, fmt):
        raise RuntimeError("no path in stub")


class MetaDB:
    library_path = LIB

    def __init__(self):
        self.store = {
            7: MI("Smith, John", ["The Big Book"]),
            8: MI("Jane Doe", ["Another Title"]),
            9: MI("", ["No Title Here"]),
        }
        self.new_api = StubAPI2(self)

    def get_metadata(self, book_id, index_is_id=True):
        return self.store[book_id]

    def set_metadata(self, book_id, mi, force_changes=True):
        self.store[book_id] = mi

    def has_cover(self, book_id, index_is_id=True):
        return False

    def path(self, book_id, index_is_id=True):
        return "A/B (%d)" % book_id

    def formats(self, book_id, index_is_id=True):
        return ""


mdb = MetaDB()
mad = CalibreAdapter(lambda: mdb)
r = mad.swap_title_authors(7)
assert r["ok"] and r["backup"], r
mi = mdb.store[7]
assert mi.title == "The Big Book", mi.title
assert mi.authors == ["John Smith"], mi.authors  # "Last, First" inverted
assert mi.author_sort == "Smith, John", mi.author_sort
r = mad.swap_title_authors(8)
mi = mdb.store[8]
assert mi.title == "Another Title" and mi.authors == ["Jane Doe"], vars(mi)
assert mi.author_sort == "Doe, Jane", mi.author_sort  # fallback heuristic
r = mad.swap_title_authors(9)
assert not r["ok"], r  # empty title side cannot swap
assert mdb.store[9].authors == ["No Title Here"]

# -- downloads: book_file / fs_download -----------------------------------
r = mad.book_file(8)
assert r["ok"] and _b64.b64decode(r["data_base64"]) == b"BOOKBYTES", r
assert r["filename"] == "Another Title - Jane Doe.epub", r["filename"]
r = mad.book_file(8, "PDF")
assert not r["ok"], r  # format the book does not have
r = ad.fs_download("incoming/book.epub")
assert r["ok"] and _b64.b64decode(r["data_base64"]) == b"v2", r


def download_refused(rel, why):
    try:
        ad.fs_download(rel)
    except ValueError as e:
        print("download refused (%s): %s" % (why, e))
        return
    raise AssertionError("download %r should have been refused (%s)" % (rel, why))


download_refused("metadata.db", "protected — use /backup")
download_refused("incoming", "a folder, not a file")
download_refused("ghost.epub", "missing file")
download_refused("../x", "escape via ..")

# -- chunked, resumable uploads ---------------------------------------------
blob = bytes(range(256)) * 40  # 10240 bytes
tid = "test-transfer-0001"
r = ad.upload_begin("big", "bigfile.bin", len(blob), tid, sha256=_hl.sha256(blob).hexdigest())
assert r["ok"] and r["received"] == 0 and not r["resumed"], r
r = ad.upload_chunk(tid, 0, blob[:4000])
assert r["received"] == 4000, r
try:
    ad.upload_chunk(tid, 9000, b"x")
    raise AssertionError("gap chunk accepted")
except ValueError:
    pass
r = ad.upload_complete(tid)
assert not r["ok"], r  # incomplete: refuses, keeps the part file
# "restart": a brand-new adapter over the same library resumes,
# because transfer state lives on disk, not in memory
ad2 = CalibreAdapter(lambda: StubDB())
r = ad2.upload_status(tid)
assert r["received"] == 4000, r
r = ad2.upload_begin("big", "bigfile.bin", len(blob), tid, sha256=_hl.sha256(blob).hexdigest())
assert r["resumed"] and r["received"] == 4000, r
# lost-response retry: resending the tail truncates and rewrites it
r = ad2.upload_chunk(tid, 3000, blob[3000:4000])
assert r["received"] == 4000, r
r = ad2.upload_chunk(tid, 4000, blob[4000:])
assert r["received"] == len(blob), r
r = ad2.upload_complete(tid)
assert r["ok"] and r["path"] == "big/bigfile.bin", r
with open(os.path.join(LIB, "big", "bigfile.bin"), "rb") as f:
    assert f.read() == blob
r = ad2.upload_status(tid)
assert not r["ok"], r  # completed transfers are gone
# abort discards a transfer
ad.upload_begin("big", "junk.bin", 10, "test-transfer-0002")
ad.upload_chunk("test-transfer-0002", 0, b"12345")
r = ad.upload_abort("test-transfer-0002")
assert r["ok"], r
r = ad.upload_status("test-transfer-0002")
assert not r["ok"], r
# begin into a live book folder is refused, as is a path-y transfer id
for bad in (("Author A/Title One (1)", "x.bin", "test-transfer-0003"), ("big", "x.bin", "../evil")):
    try:
        ad.upload_begin(bad[0], bad[1], 1, bad[2])
        raise AssertionError("begin accepted: %r" % (bad,))
    except ValueError:
        pass

# -- ranged downloads ---------------------------------------------------------
r1 = ad.fs_download("big/bigfile.bin", offset=0, length=4000)
r2 = ad.fs_download("big/bigfile.bin", offset=4000, length=4000)
r3 = ad.fs_download("big/bigfile.bin", offset=8000)
assert r1["size"] == len(blob) and r1["chunk_size"] == 4000 and not r1["eof"], r1
assert r3["eof"] and r3["chunk_size"] == len(blob) - 8000, r3
joined = _b64.b64decode(r1["data_base64"]) + _b64.b64decode(r2["data_base64"]) + _b64.b64decode(r3["data_base64"])
assert joined == blob
r = mad.book_file(8, offset=0, length=4)
assert r["chunk_size"] == 4 and not r["eof"] and r["size"] == 9, r
r = mad.book_file(8, offset=4)
assert r["eof"] and _b64.b64decode(r["data_base64"]) == b"BYTES", r

# -- transfer progress registry + pause/resume (v1.13) --------------------
ad.upload_begin("big", "prog.bin", 10000, "test-transfer-0009")
ad.upload_chunk("test-transfer-0009", 0, b"x" * 2500)
tl = ad.transfers_list()
up = [t for t in tl["transfers"] if t["key"] == "test-transfer-0009"]
assert up and up[0]["kind"] == "upload" and up[0]["percent"] == 25, tl
down = [t for t in tl["transfers"] if t["kind"] == "download" and t["key"] == "big/bigfile.bin"]
assert down and down[0]["percent"] == 100, tl  # fully served above
r = ad.transfer_pause("test-transfer-0009")
assert r["ok"] and "test-transfer-0009" in r["paused"], r
r = ad.upload_chunk("test-transfer-0009", 2500, b"y" * 100)
assert not r["ok"] and r.get("paused"), r  # chunks refused while paused
tl = ad.transfers_list()
up = [t for t in tl["transfers"] if t["key"] == "test-transfer-0009"]
assert up[0]["paused"] and up[0]["state"] == "paused", tl
r = ad.transfer_pause("big/bigfile.bin")
assert r["ok"], r
try:
    ad.fs_download("big/bigfile.bin", offset=0, length=10)
    raise AssertionError("download served while paused")
except ValueError as e:
    assert "paused" in str(e), e
r = ad.transfer_resume("*")
assert r["ok"] and r["paused"] == [], r
r = ad.upload_chunk("test-transfer-0009", 2500, b"y" * 100)
assert r["ok"] and r["received"] == 2600, r  # resumes after unpause
ad.upload_abort("test-transfer-0009")

# -- chat channel (v1.14) ---------------------------------------------------
r = ad.chat_post("joe", "please fix the Wallis books")
assert r["ok"] and r["count"] == 1, r
r = ad.chat_post("tinker", "on it")
assert r["ok"] and r["count"] == 2, r
r = ad.chat_post("system", "not a stored sender")
assert not r["ok"], r
r = ad.chat_post("joe", "   ")
assert not r["ok"], r
cl = ad.chat_list()
assert cl["count"] == 2 and cl["messages"][0]["from"] == "joe", cl
cl = ad.chat_list(since=1)
assert cl["count"] == 2 and len(cl["messages"]) == 1 and cl["messages"][0]["from"] == "tinker", cl
print("ALL FS TESTS PASSED")
