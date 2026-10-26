# -*- coding: utf-8 -*-
"""Calibre-facing adapter for the Calibre Link plugin.

Talks to the GUI's OWN database object (gui.current_db, the legacy
LibraryDatabase). That is the whole point of running inside calibre:

- The standalone bridge shelled out to calibredb, which must not be
  used against a library the GUI currently holds. Writes here go
  through the GUI's database, so calibre's cache, notifications and
  on-screen refresh all stay consistent — no separate process ever
  touches metadata.db for writing.
- Reads likewise come from the live cache, so Muse always sees the
  library exactly as calibre shows it.

All calibre imports are lazy (inside methods) so this module can be
imported headless for testing; only actually calling it needs calibre.

Backups are the one exception to "never touch the file": they use
SQLite's online backup API from a separate read-only connection,
which is designed to take a consistent snapshot of a live database.
"""
import os
import re
import shutil
import sqlite3
from datetime import datetime

SUPPORTED_FIELDS = ("title", "authors", "author_sort", "tags", "isbn",
                    "series", "series_index", "publisher", "pubdate",
                    "rating", "comments", "language")


import threading as _threading
_OPLOG = []
_OPLOCK = _threading.Lock()
_WRITELOCK = _threading.Lock()


def oplog(msg):
    import time as _t
    _OPLOG.append("%s %s" % (_t.strftime("%H:%M:%S"), msg))
    del _OPLOG[:-200]


class CalibreAdapter:
    """Adapter interface used by server.BridgeServer.

    get_db: zero-arg callable returning the current legacy
    LibraryDatabase (in the plugin: lambda: gui.current_db). Called on
    every request, so switching libraries in calibre just works.
    """

    def __init__(self, get_db):
        self._get_db = get_db

    # -- db access helpers ----------------------------------------------
    def _db(self):
        db = self._get_db()
        if db is None:
            raise RuntimeError("no library is open in calibre")
        return db

    def library_path(self):
        db = self._db()
        path = getattr(db, "library_path", None)
        if not path:
            raise RuntimeError("could not determine the library path")
        return path

    def _all_ids(self, db):
        # New API first, then progressively older fallbacks.
        try:
            return sorted(db.new_api.all_book_ids())
        except Exception:
            pass
        try:
            return sorted(db.data.iterallids())
        except Exception:
            pass
        try:
            return sorted(db.new_api.search(""))
        except Exception as e:
            raise RuntimeError("could not enumerate books: %s" % e)

    def _metadata(self, db, book_id):
        return db.get_metadata(book_id, index_is_id=True)

    def _has_cover(self, db, book_id, mi):
        try:
            return bool(db.has_cover(book_id, index_is_id=True))
        except Exception:
            return bool(getattr(mi, "has_cover", False))

    def _row(self, db, book_id, mi):
        isbn = None
        try:
            isbn = mi.get_identifiers().get("isbn") or mi.isbn
        except Exception:
            isbn = getattr(mi, "isbn", None)
        path = None
        try:
            path = db.path(book_id, index_is_id=True)
        except Exception:
            path = None
        return {
            "id": book_id,
            "title": mi.title,
            "author_sort": getattr(mi, "author_sort", None),
            "isbn": isbn,
            "has_cover": self._has_cover(db, book_id, mi),
            "path": path,
            "authors": " & ".join(mi.authors or []),
        }

    # -- adapter interface ------------------------------------------------
    def health(self):
        db = self._db()
        return {"library": self.library_path(),
                "book_count": len(self._all_ids(db))}

    def stats(self):
        db = self._db()
        ids = self._all_ids(db)
        out = {"total_books": len(ids), "missing_isbn": 0,
               "missing_cover": 0, "missing_author": 0, "missing_tags": 0}
        titles = {}
        for book_id in ids:
            mi = self._metadata(db, book_id)
            isbn = None
            try:
                isbn = mi.get_identifiers().get("isbn") or mi.isbn
            except Exception:
                isbn = getattr(mi, "isbn", None)
            if not isbn:
                out["missing_isbn"] += 1
            if not self._has_cover(db, book_id, mi):
                out["missing_cover"] += 1
            if not (mi.authors or []):
                out["missing_author"] += 1
            if not (mi.tags or []):
                out["missing_tags"] += 1
            key = (mi.title or "").lower()
            titles[key] = titles.get(key, 0) + 1
        out["duplicate_title_author"] = [
            {"t": t, "n": n} for t, n in
            sorted(titles.items(), key=lambda kv: -kv[1]) if n > 1][:50]
        return out

    def list_books(self, q="", limit=50, offset=0):
        db = self._db()
        ids = None
        if q:
            try:
                ids = sorted(db.new_api.search(q))
            except Exception:
                ids = None  # fall back to substring filtering below
        if ids is None:
            ids = self._all_ids(db)
            rows_all = []
            needle = (q or "").lower()
            for book_id in ids:
                mi = self._metadata(db, book_id)
                if needle and needle not in (mi.title or "").lower() and \
                        needle not in " & ".join(mi.authors or []).lower():
                    continue
                rows_all.append((mi.title or "", book_id, mi))
            rows_all.sort(key=lambda r: r[0].lower())
            ids = [r[1] for r in rows_all]
            by_id = {r[1]: r[2] for r in rows_all}
            return [self._row(db, b, by_id[b])
                    for b in ids[offset:offset + int(limit)]]
        rows = []
        for book_id in ids[offset:offset + int(limit)]:
            rows.append(self._row(db, book_id, self._metadata(db, book_id)))
        rows.sort(key=lambda r: (r["title"] or "").lower())
        return rows

    def get_book(self, book_id):
        db = self._db()
        try:
            mi = self._metadata(db, book_id)
        except Exception:
            return None
        if mi is None or mi.title is None and book_id not in self._all_ids(db):
            return None
        row = self._row(db, book_id, mi)
        identifiers = {}
        try:
            identifiers = dict(mi.get_identifiers())
        except Exception:
            pass
        formats = []
        try:
            fmts = db.formats(book_id, index_is_id=True)
            if fmts:
                formats = [f.strip() for f in fmts.split(",") if f.strip()]
        except Exception:
            pass
        row.update({
            "tags": list(mi.tags or []),
            "series": getattr(mi, "series", None),
            "series_index": getattr(mi, "series_index", None),
            "publisher": getattr(mi, "publisher", None),
            "pubdate": str(mi.pubdate) if getattr(mi, "pubdate", None) else None,
            "rating": getattr(mi, "rating", None),
            "languages": list(getattr(mi, "languages", None) or []),
            "comments": getattr(mi, "comments", None),
            "identifiers": identifiers,
            "formats": formats,
        })
        return row

    def backup(self):
        lib = self.library_path()
        src_path = os.path.join(lib, "metadata.db")
        if not os.path.exists(src_path):
            raise RuntimeError("metadata.db not found in %s" % lib)
        dst_dir = os.path.join(lib, "backups")
        os.makedirs(dst_dir, exist_ok=True)
        name = "metadata-%s.db" % datetime.now().strftime("%Y%m%d-%H%M%S")
        dst_path = os.path.join(dst_dir, name)
        # SQLite online backup: consistent snapshot of the live db.
        src = sqlite3.connect("file:" + src_path.replace(os.sep, "/")
                              + "?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(dst_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return name

    def ops_log(self):
        return {"lines": list(_OPLOG[-60:]),
                "write_lock_held": _WRITELOCK.locked()}

    # -- calibre plugin / metadata-source inventory ----------------------
    def plugins_inventory(self):
        """What metadata machinery does THIS calibre have?

        Deliberately defensive: calibre's enumeration APIs differ
        across versions, so try several routes and report diagnostics
        for each. Joe's install may carry extra source plugins (e.g.
        Metadata++); whatever is here is what fetch_metadata uses.
        """
        out = {"plugins": [], "metadata_sources": [], "diagnostics": {}}
        try:
            import calibre.customize.ui as cui
            plugins = None
            for attr in ("initialized_plugins", "get_plugins",
                         "available_plugins"):
                fn = getattr(cui, attr, None)
                if callable(fn):
                    try:
                        plugins = fn()
                        out["diagnostics"]["customize.ui." + attr] = "ok"
                        break
                    except Exception as e:
                        out["diagnostics"]["customize.ui." + attr] = str(e)
            if plugins:
                for p in plugins:
                    try:
                        out["plugins"].append({
                            "name": getattr(p, "name", str(p)),
                            "version": ".".join(map(str, getattr(
                                p, "version", ()) or ())),
                            "type": type(p).__name__})
                    except Exception:
                        pass
            else:
                out["diagnostics"]["plugins"] = "no enumeration route worked"
        except Exception as e:
            out["diagnostics"]["plugins"] = str(e)
        # metadata sources, route 1: enabled-sources helper
        try:
            from calibre.ebooks.metadata.sources.base import get_enabled_sources
            srcs = get_enabled_sources()
            out["metadata_sources"] = [
                {"name": getattr(s, "name", str(s)),
                 "id": getattr(s, "ID", None)} for s in srcs]
            out["diagnostics"]["get_enabled_sources"] = "ok"
        except Exception as e:
            out["diagnostics"]["get_enabled_sources"] = str(e)
        # route 2: the identify entry point itself
        try:
            from calibre.ebooks.metadata.sources.identify import identify
            out["diagnostics"]["identify_import"] = "ok"
        except Exception as e:
            out["diagnostics"]["identify_import"] = str(e)
        return out

    # -- metadata fetch via calibre's own sources --------------------------
    def fetch_metadata(self, book_id, apply=False, fields=None):
        """Run calibre's identify() (all enabled metadata sources —
        Google Books, Amazon, plus whatever plugins Joe has installed
        such as Metadata++) for one book and return the candidates.

        apply=True + fields=[...] copies those fields from the best
        candidate onto the book, with a backup taken first.
        """
        db = self._db()
        mi = self._metadata(db, book_id)
        identifiers = {}
        try:
            identifiers = dict(mi.get_identifiers())
        except Exception:
            pass
        result = {"ok": True, "book_id": book_id, "candidates": []}
        try:
            import threading
            from calibre.ebooks.metadata.sources.identify import identify
            log = _ListLog()
            results = identify(log, threading.Event(),
                               title=mi.title, authors=list(mi.authors or []),
                               identifiers=identifiers)
            result["log_tail"] = log.lines[-5:]
        except Exception as e:
            result.update({"ok": False,
                           "error": "identify failed: %s" % e})
            return result
        for r in (results or [])[:5]:
            cand = {
                "title": getattr(r, "title", None),
                "authors": list(getattr(r, "authors", None) or []),
                "series": getattr(r, "series", None),
                "series_index": getattr(r, "series_index", None),
                "publisher": getattr(r, "publisher", None),
                "pubdate": str(r.pubdate) if getattr(r, "pubdate", None) else None,
                "tags": list(getattr(r, "tags", None) or [])[:12],
                "identifiers": {},
                "source": getattr(r, "identify_plugin", None) or
                          getattr(r, "source", None),
                "has_cached_cover": bool(getattr(r, "cover_url", None) or
                                         getattr(r, "cached_cover_url", None)),
            }
            try:
                cand["identifiers"] = dict(r.get_identifiers())
            except Exception:
                pass
            result["candidates"].append(cand)
        if apply and result["candidates"]:
            wanted = fields or ["title", "authors", "isbn", "series",
                                "series_index", "publisher", "pubdate", "tags"]
            top = (results or [])[0]
            updates = {}
            if "title" in wanted and top.title:
                updates["title"] = top.title
            if "authors" in wanted and top.authors:
                updates["authors"] = list(top.authors)
            if "isbn" in wanted:
                try:
                    isbn = top.get_identifiers().get("isbn")
                except Exception:
                    isbn = None
                if isbn:
                    updates["isbn"] = isbn
            if "series" in wanted and getattr(top, "series", None):
                updates["series"] = top.series
            if "series_index" in wanted and getattr(top, "series_index", None):
                updates["series_index"] = top.series_index
            if "publisher" in wanted and getattr(top, "publisher", None):
                updates["publisher"] = top.publisher
            if "pubdate" in wanted and getattr(top, "pubdate", None):
                updates["pubdate"] = str(top.pubdate)
            if "tags" in wanted and getattr(top, "tags", None):
                updates["tags"] = list(top.tags)
            if updates:
                result["backup"] = self.backup()
                result["applied"] = updates
                result["book"] = self.set_metadata(book_id, updates)
        return result

    # -- add a downloaded book --------------------------------------------
    def add_book(self, filename, data, metadata=None):
        """Add one EPUB (bytes) to the library through the Cache API.

        Metadata is read from the file itself first (calibre's own
        reader), then caller-supplied fields override — the fetcher
        that downloaded the story usually knows its title/author
        better than an embedded OPF scraped from a story page.
        Duplicates are refused (create_book_entry, add_duplicates=
        False) and reported, never silently doubled.
        """
        metadata = metadata or {}
        if not _WRITELOCK.acquire(timeout=120):
            return {"ok": False, "error": "another write is in progress"}
        try:
            return self._add_book_locked(filename, data, metadata)
        finally:
            _WRITELOCK.release()

    def _add_book_locked(self, filename, data, metadata):
        oplog("add_book: start %s (%d bytes)" % (filename, len(data)))
        db = self._db()
        new_api = getattr(db, "new_api", None)
        if new_api is None:
            return {"ok": False, "error": "cache API unavailable"}
        import io
        mi = None
        try:
            from calibre.ebooks.metadata.meta import get_metadata
            mi = get_metadata(io.BytesIO(data), "epub")
            oplog("add_book: file metadata read: %r" % (mi.title,))
        except Exception as e:
            oplog("add_book: get_metadata failed: %s" % e)
            mi = None
        if mi is None:
            from calibre.ebooks.metadata.book.base import Metadata
            mi = Metadata(metadata.get("title") or "Unknown", [])
        if metadata.get("title"):
            mi.title = metadata["title"]
        if metadata.get("authors"):
            mi.authors = list(metadata["authors"])
        if metadata.get("series"):
            mi.series = metadata["series"]
        if metadata.get("series_index") is not None:
            try:
                mi.series_index = float(metadata["series_index"])
            except (TypeError, ValueError):
                pass
        if metadata.get("tags"):
            mi.tags = list(metadata["tags"])
        if metadata.get("isbn"):
            try:
                mi.set_identifiers({"isbn": metadata["isbn"]})
            except Exception:
                pass
        if metadata.get("comments"):
            mi.comments = metadata["comments"]
        if metadata.get("language"):
            try:
                mi.languages = [metadata["language"]]
            except Exception:
                pass
        oplog("add_book: create_book_entry...")
        book_id = new_api.create_book_entry(mi, add_duplicates=False)
        oplog("add_book: created id=%s" % book_id)
        if book_id is None:
            found = []
            try:
                found = list(new_api.search(
                    'title:"=%s"' % (mi.title or "").replace('"', " ")))
            except Exception:
                pass
            return {"ok": False, "error": "duplicate", "title": mi.title,
                    "existing_ids": found[:5]}
        new_api.add_format(book_id, "EPUB", io.BytesIO(data),
                           replace=False, run_hooks=False)
        oplog("add_book: format attached id=%s" % book_id)
        return {"ok": True, "book_id": book_id,
                "book": self._row(db, book_id, self._metadata(db, book_id))}

    # -- first-pages text (content-vs-metadata checks, stamp hunt) --------
    def book_text(self, book_id, max_chars=8000):
        """Extract head/tail text from the book's own file.

        The ground truth for mangled records: title pages and
        copyright pages carry the real title/author, and stamped
        uploader credits ('(ebook by Undead)') show up here even
        when they also polluted the metadata fields.
        Supports EPUB and TXT; other formats report supported=False.
        """
        db = self._db()
        new_api = getattr(db, "new_api", None)
        formats = []
        if new_api is not None:
            try:
                formats = [str(f) for f in new_api.formats(book_id)]
            except Exception:
                formats = []
        if not formats:
            try:
                raw = db.formats(book_id, index_is_id=True)
                formats = [f.strip() for f in (raw or "").split(",") if f.strip()]
            except Exception:
                formats = []
        if not formats:
            return {"ok": False, "book_id": book_id,
                    "error": "book has no formats"}
        fmt = None
        for want in ("EPUB", "TXT", "FB2"):
            if want in [f.upper() for f in formats]:
                fmt = want
                break
        if fmt is None:
            return {"ok": True, "book_id": book_id, "supported": False,
                    "formats": formats,
                    "note": "text extraction supports EPUB/TXT/FB2"}
        path = None
        if new_api is not None:
            try:
                path = new_api.format_abspath(book_id, fmt)
            except Exception:
                path = None
        if not path:
            path = db.format_abspath(book_id, fmt, index_is_id=True)
        if fmt == "EPUB":
            got = extract_text_from_epub(path, max_chars=max_chars)
        else:
            try:
                with open(path, "rb") as f:
                    data = f.read(400000)
                text = data.decode("utf-8", "replace")
                if fmt == "FB2":
                    text = _strip_html(text)
                text = re.sub(r"\s+", " ", text).strip()
                got = {"supported": True, "docs": 1,
                       "head": text[:max_chars], "tail": text[-2000:]}
            except Exception as e:
                got = {"supported": False, "error": str(e)}
        got.update({"ok": True, "book_id": book_id, "format": fmt})
        if got.get("supported"):
            got["stamps"] = find_stamps(got.get("head", "") + "\n" +
                                         got.get("tail", ""))
        return got

    def set_metadata(self, book_id, fields):
        db = self._db()
        unknown = [k for k in fields if k not in SUPPORTED_FIELDS]
        if unknown:
            raise ValueError("unsupported field(s): %s (supported: %s)"
                             % (", ".join(sorted(unknown)),
                                ", ".join(SUPPORTED_FIELDS)))
        mi = self._metadata(db, book_id)
        for key, value in fields.items():
            if key == "title":
                mi.title = value
            elif key == "authors":
                if isinstance(value, (list, tuple)):
                    mi.authors = [str(v) for v in value]
                else:
                    mi.authors = [a.strip() for a in
                                  str(value).replace(" & ", ",").split(",")
                                  if a.strip()]
            elif key == "author_sort":
                mi.author_sort = value
            elif key == "tags":
                if isinstance(value, (list, tuple)):
                    mi.tags = [str(v) for v in value]
                else:
                    mi.tags = [t.strip() for t in str(value).split(",")
                               if t.strip()]
            elif key == "isbn":
                ids = dict(mi.get_identifiers())
                if value:
                    ids["isbn"] = str(value)
                else:
                    ids.pop("isbn", None)
                mi.set_identifiers(ids)
                try:
                    mi.isbn = str(value) if value else None
                except Exception:
                    pass
            elif key == "series":
                mi.series = value or None
            elif key == "series_index":
                mi.series_index = float(value)
            elif key == "publisher":
                mi.publisher = value
            elif key == "pubdate":
                mi.pubdate = _parse_date(value)
            elif key == "rating":
                mi.rating = float(value)
            elif key == "comments":
                mi.comments = value
            elif key == "language":
                mi.languages = [str(value)] if value else []
        try:
            db.set_metadata(book_id, mi, force_changes=True)
        except TypeError:
            db.set_metadata(book_id, mi)
        return self.get_book(book_id)


class _ListLog:
    """Minimal duck-typed log for calibre's identify() machinery."""

    def __init__(self):
        self.lines = []

    def _add(self, level, *args):
        try:
            msg = " ".join(str(a) for a in args)
        except Exception:
            msg = "<unprintable>"
        self.lines.append("%s: %s" % (level, msg[:300]))

    def __call__(self, *a):
        # calibre's Log is callable; some sources call log(...) directly
        self._add("info", *a)

    def prints(self, *a):
        self._add("info", *a)

    def debug(self, *a):
        self._add("debug", *a)

    def info(self, *a):
        self._add("info", *a)

    def warn(self, *a):
        self._add("warn", *a)

    warning = warn

    def error(self, *a):
        self._add("error", *a)

    def exception(self, *a):
        self._add("exception", *a)


STAMP_PATTERNS = [
    r"e-?book by [^\n<]{1,50}",
    r"scanned by [^\n<]{1,50}",
    r"proofed by [^\n<]{1,50}",
    r"converted by [^\n<]{1,50}",
    r"brought to you by [^\n<]{1,50}",
    r"downloaded from [^\n<]{1,60}",
    r"this e-?book was (?:generated|produced|created|made) by [^\n<]{1,50}",
    r"\(\s*ebook by [^)]{1,60}\)",
]


def find_stamps(text):
    """Uploader/credit stamps in book text -- the '(ebook by Undead)'
    class of junk that also pollutes metadata fields."""
    out = []
    for pat in STAMP_PATTERNS:
        for m in re.finditer(pat, text or "", re.I):
            snippet = re.sub(r"\s+", " ", m.group(0)).strip()
            if ")" in snippet:
                snippet = snippet[:snippet.index(")") + 1]
            if ". " in snippet[12:]:
                snippet = snippet[:snippet.index(". ", 12)]
            snippet = snippet[:60].strip()
            if snippet and snippet not in out:
                out.append(snippet)
    return out[:10]


def _strip_html(raw):
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    import html as _html
    return re.sub(r"\s+", " ", _html.unescape(raw)).strip()


def extract_text_from_epub(path, max_chars=8000):
    """Head + tail text of an epub, spine order, no calibre imports.

    Returns {"supported": True, "head": str, "tail": str, "docs": int}
    or {"supported": False, "error": str}.
    """
    import posixpath
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        return {"supported": False, "error": "not a zip/epub: %s" % e}
    try:
        container = ET.fromstring(zf.read("META-INF/container.xml"))
        rootfile = None
        for el in container.iter():
            if el.tag.rsplit("}", 1)[-1] == "rootfile":
                rootfile = el.get("full-path")
                break
        if not rootfile:
            return {"supported": False, "error": "no rootfile in container"}
        opf = ET.fromstring(zf.read(rootfile))
        opf_dir = posixpath.dirname(rootfile)
        manifest = {}
        spine = []
        for el in opf.iter():
            tag = el.tag.rsplit("}", 1)[-1]
            if tag == "item":
                manifest[el.get("id")] = el.get("href")
            elif tag == "itemref":
                spine.append(el.get("idref"))
        docs = []
        for idref in spine:
            href = manifest.get(idref)
            if href:
                full = posixpath.normpath(posixpath.join(opf_dir, href)) \
                    if opf_dir else href
                docs.append(full)
        head_parts, total = [], 0
        for name in docs:
            if total >= max_chars:
                break
            try:
                text = _strip_html(zf.read(name).decode("utf-8", "replace"))
            except KeyError:
                continue
            head_parts.append(text)
            total += len(text)
        tail = ""
        if docs:
            try:
                tail_text = _strip_html(
                    zf.read(docs[-1]).decode("utf-8", "replace"))
                tail = tail_text[-2000:]
            except KeyError:
                pass
        return {"supported": True, "docs": len(docs),
                "head": " ".join(head_parts)[:max_chars], "tail": tail}
    except KeyError as e:
        return {"supported": False, "error": "missing member: %s" % e}
    except Exception as e:
        return {"supported": False, "error": str(e)}


def _parse_date(value):
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        from calibre.utils.date import parse_date
        return parse_date(text, assume_utc=True)
    except Exception:
        pass
    from datetime import timezone
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d", "%Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    raise ValueError("could not parse date: %r" % value)


def write_connection_file(adapter, host, port, token, extra_lines=None):
    """Write CalibreLink-connection.txt into the library folder.

    Joe can open this file and read the token/address to Tinker —
    the same flow as the flash-drive kit's CONNECTION.txt.
    extra_lines: optional list appended before the timestamp (the
    plugin uses it for the integrated-Tailscale section).
    Returns the path written, or None if no library is open.
    """
    try:
        lib = adapter.library_path()
    except Exception:
        return None
    addresses = _local_addresses()
    lines = [
        "Calibre Link is running inside calibre on this PC.",
        "",
        "Token (X-Token header): %s" % token,
        "Port: %d" % port,
        "",
        "Reachable addresses for this PC:",
    ]
    for addr in addresses:
        lines.append("  http://%s:%d" % (addr, port))
    lines += [
        "",
        "The 100.x address (if listed) is this PC's Tailscale address —",
        "that is the one Tinker uses. /health needs no token; everything",
        "else needs the token above.",
    ]
    if extra_lines:
        lines.append("")
        lines += list(extra_lines)
    lines += [
        "",
        "Written: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ]
    path = os.path.join(lib, "CalibreLink-connection.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _local_addresses():
    import socket
    out = ["127.0.0.1"]
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ip = info[4][0]
            if ip not in out:
                out.append(ip)
    except Exception:
        pass
    # Tailnet addresses sort first after localhost: they are what
    # Tinker connects to, so make them easy to spot in the file.
    tailnet = [a for a in out if a.startswith("100.")]
    rest = [a for a in out if not a.startswith("100.") and a != "127.0.0.1"]
    return ["127.0.0.1"] + tailnet + rest
