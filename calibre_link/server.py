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

Auth: every endpoint except /health needs header  X-Token: <token>.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.5.0"
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
