# -*- coding: utf-8 -*-
"""Calibre Link bridge server — stdlib only, no calibre imports.

This is the HTTP layer of the Calibre Link plugin. It is deliberately
kept free of calibre/Qt imports so it can be tested headless. The
plugin (ui.py) injects an `adapter` object that talks to calibre's
live database; tests can inject a fake adapter instead.

API (same shape as the standalone bridge, so calibre_client.py works
unchanged — only /calibredb became a compatibility shim):

  GET  /health            (no auth) status, library path, book count
  GET  /stats             audit counts (missing isbn/cover/author/tags, dup titles)
  GET  /books?q=&limit=&offset=
  GET  /book/<id>         full metadata for one book
  POST /backup            hot backup of metadata.db into <library>/backups/
  POST /set-metadata/<id> {"fields": {...}} — backup first, then write
  POST /set-metadata-batch {"items": [{id, fields}, ...]} — ONE backup
                          for the whole batch, then all writes
  GET  /plugins           inventory of calibre's installed plugins and
                          metadata sources (incl. Metadata++ if present)
  POST /add-book          {"filename", "data_base64", "metadata"} —
                          add a downloaded EPUB through the Cache API
                          (duplicates refused and reported)
  GET  /book-text/<id>?chars=N — head/tail text extracted from the
                          book's own file (EPUB/TXT/FB2) + uploader
                          stamp findings
  POST /fetch-metadata/<id> {"apply": false, "fields": [...]} — ask
                          calibre's own metadata sources (identify) for
                          this book; optionally apply chosen fields
                          (backup first)
  POST /calibredb         {"args": [...]} — read-only subcommands + set_metadata
                          translated onto the live db; other writes refused.
  GET  /fs-list?path=<rel>&offset=N&limit=N — list one folder inside
                          the library folder (paths are library-relative;
                          entries flag live book folders; offset/limit
                          page through folders with >1000 entries and the
                          reply carries the folder's total entry count)
  POST /fs-move           {"src": <rel>, "dst": <rel>} — move a file or
                          folder within the library folder. Confined:
                          no absolute paths, no escapes, no overwriting,
                          metadata.db protected, and live book folders
                          (and their parents/contents) are refused —
                          calibre owns those paths.
  POST /fs-upload         {"path": <rel dir>, "filename", "data_base64",
                          "overwrite": false} — write an uploaded file
                          into a library folder (the receiving end of a
                          download pipeline, e.g. books pulled from
                          Google Drive). Same confinement as /fs-move;
                          64 MB cap.
  POST /swap-title-authors/<id> — repair a record whose title and
                          author(s) hold each other's values (backup
                          first; a "Last, First" title is inverted back
                          to display form and kept as author_sort).
  GET  /book-file/<id>?format=EPUB — a book's format file as base64
                          (the library -> Google Drive upload leg;
                          64 MB cap)
  GET  /fs-download?path=<rel> — any file inside the library folder
                          as base64 (same cap; metadata.db refused —
                          use /backup for the database)
  Both downloads accept &offset=N&length=N for chunked, resumable
  transfers (response carries size/offset/eof); pause = stop
  asking, resume = re-request from the saved offset.
  Chunked upload (for files over the 64 MB single-shot cap, and
  resumable across dropped connections and calibre restarts —
  state is a part file + sidecar in <library>/.calibrelink-parts/):
  POST /fs-upload-begin   {"transfer_id", "path", "filename", "size",
                          "sha256"?, "overwrite"?} — idempotent;
                          re-beginning an existing id reports how
                          many bytes it already holds (resume point)
  POST /fs-upload-chunk   {"transfer_id", "offset", "data_base64"}
  GET  /fs-upload-status?transfer_id=<id>
  POST /fs-upload-complete {"transfer_id"} — verifies size/sha256,
                          re-checks confinement, renames into place
  POST /fs-upload-abort   {"transfer_id"} — discards the part file
  Transfer progress (v1.13 — feeds the status dialog's progress
  bars for BOTH directions, uploads and downloads alike):
  GET  /transfers         every known transfer with size, bytes
                          done, percent, state and paused flag
                          (uploads read from their sidecars;
                          downloads tracked from ranges served)
  POST /transfers-pause   {"key"} — key is an upload transfer_id,
                          a download's library-relative path, or
                          "*" for all; paused uploads refuse chunks
                          and paused downloads refuse ranges
  POST /transfers-resume  {"key"} — clears that pause ("*" = all)
  Chat / command channel (v1.14 — the console window in calibre):
  GET  /chat?since=<n>    messages after index n + total count;
                          the log lives in .calibrelink-chat.json
                          in the library folder (newest 500 kept)
  POST /chat              {"from": "joe"|"tinker", "text"} —
                          appends a message; Tinker reads and
                          replies through these two routes

Auth: every endpoint except /health needs header  X-Token: <token>.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.14.0"
READ_SUBCOMMANDS = {"list", "search", "show_metadata", "check_library"}


class BridgeServer:
    """Owns the HTTP server + thread. Adapter is supplied by the plugin."""

    def __init__(self, host, port, token, adapter):
        self.host = host
        self.port = int(port)
        self.token = token
        self.adapter = adapter
        self._httpd = None
        self._thread = None
        self.last_error = None

    # -- lifecycle ------------------------------------------------------
    def start(self):
        if self._httpd is not None:
            return True
        try:
            handler = _make_handler(self)
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
            self._thread = threading.Thread(
                target=self._httpd.serve_forever,
                name="calibre-link-server", daemon=True)
            self._thread.start()
            self.last_error = None
            return True
        except Exception as e:  # port in use, bad host, ...
            self.last_error = "%s: %s" % (type(e).__name__, e)
            self._httpd = None
            return False

    def stop(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
            self._thread = None

    @property
    def running(self):
        return self._httpd is not None


def _make_handler(bridge):
    adapter = bridge.adapter

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        # -- plumbing ---------------------------------------------------
        def send_json(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authed(self):
            return self.headers.get("X-Token", "") == bridge.token

        def read_payload(self):
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        # -- routes -----------------------------------------------------
        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if u.path == "/health":
                try:
                    info = adapter.health()
                    info.update({"ok": True, "mode": "plugin",
                                 "version": VERSION,
                                 "server_running": bridge.running})
                    return self.send_json(info)
                except Exception as e:
                    return self.send_json(
                        {"ok": False, "mode": "plugin", "version": VERSION,
                         "error": str(e)}, 500)
            if not self.authed():
                return self.send_json(
                    {"ok": False, "error": "bad or missing X-Token"}, 401)
            try:
                if u.path == "/stats":
                    return self.send_json({"ok": True, "stats": adapter.stats()})
                if u.path == "/books":
                    return self.send_json({"ok": True, "books": adapter.list_books(
                        qs.get("q", [""])[0],
                        int(qs.get("limit", ["50"])[0]),
                        int(qs.get("offset", ["0"])[0]))})
                if u.path.startswith("/book/"):
                    book_id = u.path.rsplit("/", 1)[-1]
                    if not book_id.isdigit():
                        return self.send_json(
                            {"ok": False, "error": "need numeric id"}, 400)
                    book = adapter.get_book(int(book_id))
                    if book is None:
                        return self.send_json(
                            {"ok": False, "error": "no such book"}, 404)
                    return self.send_json({"ok": True, "book": book})
                if u.path == "/fs-list":
                    return self.send_json(adapter.fs_list(
                        qs.get("path", [""])[0],
                        offset=_qint(qs, "offset", 0),
                        limit=_qint(qs, "limit", 1000)))
                if u.path == "/fs-download":
                    return self.send_json(adapter.fs_download(
                        qs.get("path", [""])[0],
                        offset=_qint(qs, "offset", 0),
                        length=_qint(qs, "length", None)))
                if u.path == "/fs-upload-status":
                    return self.send_json(adapter.upload_status(
                        qs.get("transfer_id", [""])[0]))
                if u.path == "/transfers":
                    return self.send_json(adapter.transfers_list())
                if u.path == "/chat":
                    return self.send_json(adapter.chat_list(
                        since=_qint(qs, "since", 0)))
                if u.path.startswith("/book-file/"):
                    book_id = u.path.rsplit("/", 1)[-1]
                    if not book_id.isdigit():
                        return self.send_json(
                            {"ok": False, "error": "need numeric id"}, 400)
                    return self.send_json(adapter.book_file(
                        int(book_id), qs.get("format", [None])[0],
                        offset=_qint(qs, "offset", 0),
                        length=_qint(qs, "length", None)))
                if u.path == "/deleted-books":
                    return self.send_json(
                        {"ok": True, **adapter.deleted_books()})
                if u.path == "/log":
                    return self.send_json(
                        {"ok": True, "ops": adapter.ops_log()})
                if u.path == "/plugins":
                    return self.send_json(
                        {"ok": True, "plugins": adapter.plugins_inventory()})
                if u.path.startswith("/book-text/"):
                    book_id = u.path.rsplit("/", 1)[-1]
                    if not book_id.isdigit():
                        return self.send_json(
                            {"ok": False, "error": "need numeric id"}, 400)
                    chars = 8000
                    qs = u.query or ""
                    for part in qs.split("&"):
                        if part.startswith("chars="):
                            try:
                                chars = max(500, min(50000,
                                                     int(part[6:])))
                            except ValueError:
                                pass
                    return self.send_json(
                        adapter.book_text(int(book_id), max_chars=chars))
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
            return self.send_json({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            if not self.authed():
                return self.send_json(
                    {"ok": False, "error": "bad or missing X-Token"}, 401)
            u = urlparse(self.path)
            payload = self.read_payload()
            try:
                if u.path == "/fs-move":
                    return self.send_json(adapter.fs_move(
                        payload.get("src") or "",
                        payload.get("dst") or ""))
                if u.path == "/fs-upload":
                    import base64
                    try:
                        data = base64.b64decode(
                            payload.get("data_base64") or "",
                            validate=True)
                    except Exception:
                        return self.send_json(
                            {"ok": False, "error": "bad base64"}, 400)
                    if not data:
                        return self.send_json(
                            {"ok": False, "error": "empty file"}, 400)
                    if len(data) > 64 * 1024 * 1024:
                        return self.send_json(
                            {"ok": False, "error": "file too large"}, 400)
                    return self.send_json(adapter.fs_upload(
                        payload.get("path") or "",
                        payload.get("filename") or "",
                        data,
                        overwrite=bool(payload.get("overwrite", False))))
                if u.path == "/fs-upload-begin":
                    return self.send_json(adapter.upload_begin(
                        payload.get("path") or "",
                        payload.get("filename") or "",
                        payload.get("size") or 0,
                        payload.get("transfer_id") or "",
                        sha256=payload.get("sha256"),
                        overwrite=bool(payload.get("overwrite", False))))
                if u.path == "/fs-upload-chunk":
                    import base64
                    try:
                        data = base64.b64decode(
                            payload.get("data_base64") or "",
                            validate=True)
                    except Exception:
                        return self.send_json(
                            {"ok": False, "error": "bad base64"}, 400)
                    return self.send_json(adapter.upload_chunk(
                        payload.get("transfer_id") or "",
                        payload.get("offset") or 0, data))
                if u.path == "/fs-upload-complete":
                    return self.send_json(adapter.upload_complete(
                        payload.get("transfer_id") or ""))
                if u.path == "/fs-upload-abort":
                    return self.send_json(adapter.upload_abort(
                        payload.get("transfer_id") or ""))
                if u.path == "/transfers-pause":
                    return self.send_json(adapter.transfer_pause(
                        payload.get("key") or "*"))
                if u.path == "/transfers-resume":
                    return self.send_json(adapter.transfer_resume(
                        payload.get("key") or "*"))
                if u.path == "/chat":
                    return self.send_json(adapter.chat_post(
                        payload.get("from") or "",
                        payload.get("text") or ""))
                if u.path.startswith("/swap-title-authors/"):
                    book_id = u.path.rsplit("/", 1)[-1]
                    if not book_id.isdigit():
                        return self.send_json(
                            {"ok": False, "error": "need numeric id"}, 400)
                    return self.send_json(adapter.swap_title_authors(
                        int(book_id)))
                if u.path == "/restore-deleted":
                    return self.send_json(adapter.restore_deleted(
                        payload.get("ids") or []))
                if u.path == "/trash-file":
                    return self.send_json(adapter.trash_file(
                        int(payload.get("id") or 0)))
                if u.path == "/merge-books":
                    keep = payload.get("keep")
                    if keep is None:
                        return self.send_json(
                            {"ok": False, "error": "keep required"}, 400)
                    return self.send_json(adapter.merge_books(
                        int(keep),
                        [int(x) for x in (payload.get("remove") or [])]))
                if u.path == "/backup":
                    return self.send_json(
                        {"ok": True, "backup": adapter.backup()})
                if u.path == "/add-book":
                    import base64
                    filename = payload.get("filename") or "book.epub"
                    if not filename.lower().endswith(".epub"):
                        return self.send_json(
                            {"ok": False, "error": "EPUB only"}, 400)
                    try:
                        data = base64.b64decode(
                            payload.get("data_base64") or "", validate=True)
                    except Exception:
                        return self.send_json(
                            {"ok": False, "error": "bad base64"}, 400)
                    if not data:
                        return self.send_json(
                            {"ok": False, "error": "empty file"}, 400)
                    if len(data) > 64 * 1024 * 1024:
                        return self.send_json(
                            {"ok": False, "error": "file too large"}, 400)
                    return self.send_json(adapter.add_book(
                        filename, data, payload.get("metadata") or {}))
                if u.path == "/set-metadata-batch":
                    items = payload.get("items", [])
                    if not items:
                        return self.send_json(
                            {"ok": False, "error": "need items"}, 400)
                    # ONE backup for the whole batch, then writes.
                    backup_name = adapter.backup()
                    results = []
                    for item in items:
                        bid = item.get("id")
                        fields = item.get("fields", {})
                        try:
                            book = adapter.set_metadata(int(bid), fields)
                            results.append({"id": bid, "ok": True,
                                            "book": book})
                        except Exception as e:
                            results.append({"id": bid, "ok": False,
                                            "error": str(e)})
                    return self.send_json({"ok": True, "backup": backup_name,
                                           "results": results})
                if u.path.startswith("/fetch-metadata/"):
                    book_id = u.path.rsplit("/", 1)[-1]
                    if not book_id.isdigit():
                        return self.send_json(
                            {"ok": False, "error": "need numeric id"}, 400)
                    found = adapter.fetch_metadata(
                        int(book_id),
                        apply=bool(payload.get("apply", False)),
                        fields=payload.get("fields"))
                    return self.send_json(found)
                if u.path.startswith("/set-metadata/"):
                    book_id = u.path.rsplit("/", 1)[-1]
                    fields = payload.get("fields", {})
                    if not book_id.isdigit() or not fields:
                        return self.send_json(
                            {"ok": False,
                             "error": "need numeric id and fields"}, 400)
                    backup_name = adapter.backup()
                    book = adapter.set_metadata(int(book_id), fields)
                    return self.send_json({"ok": True, "backup": backup_name,
                                           "book": book})
                if u.path == "/calibredb":
                    return self.send_json(
                        _calibredb_shim(adapter, payload.get("args", [])))
            except Exception as e:
                return self.send_json({"ok": False, "error": str(e)}, 500)
            return self.send_json({"ok": False, "error": "not found"}, 404)

    return H


def _qint(qs, name, default):
    try:
        return int(qs.get(name, [default])[0])
    except (TypeError, ValueError):
        return default


def _calibredb_shim(adapter, args):
    """Translate the safe subset of calibredb calls onto the live db.

    The plugin never shells out to calibredb: while the GUI holds the
    library, writes must go through the GUI's own database object.
    Read-only subcommands and set_metadata are supported; anything else
    gets a clear refusal instead of a surprise.
    """
    if not args:
        return {"ok": False, "error": "no subcommand given"}
    sub = args[0]
    rest = list(args[1:])
    if sub == "set_metadata":
        # form: set_metadata <id> --field k:v [--field k:v ...]
        if not rest or not rest[0].isdigit():
            return {"ok": False, "error": "set_metadata needs a numeric id"}
        book_id = int(rest[0])
        fields = {}
        i = 1
        while i < len(rest):
            if rest[i] == "--field" and i + 1 < len(rest) and ":" in rest[i + 1]:
                k, v = rest[i + 1].split(":", 1)
                fields[k] = v
                i += 2
            else:
                i += 1
        if not fields:
            return {"ok": False, "error": "set_metadata needs --field k:v"}
        backup_name = adapter.backup()
        book = adapter.set_metadata(book_id, fields)
        return {"ok": True, "returncode": 0, "via": "plugin",
                "backup": backup_name,
                "stdout": json.dumps(book, ensure_ascii=False),
                "stderr": ""}
    if sub in READ_SUBCOMMANDS:
        if sub == "show_metadata":
            ids = [a for a in rest if a.isdigit()]
            if not ids:
                return {"ok": False, "error": "show_metadata needs an id"}
            book = adapter.get_book(int(ids[0]))
            return {"ok": True, "returncode": 0, "via": "plugin",
                    "stdout": json.dumps(book, ensure_ascii=False),
                    "stderr": ""}
        q = " ".join(a for a in rest if not a.startswith("--"))
        books = adapter.list_books(q, 200, 0)
        return {"ok": True, "returncode": 0, "via": "plugin",
                "stdout": json.dumps(books, ensure_ascii=False), "stderr": ""}
    return {"ok": False, "via": "plugin", "error":
            "subcommand %r is not available inside the calibre GUI plugin; "
            "use /set-metadata/<id> for writes and /books, /book/<id>, "
            "/stats for reads" % sub}
