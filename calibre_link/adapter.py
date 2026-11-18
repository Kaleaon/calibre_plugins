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
        format_sizes = {}
        _na = getattr(db, "new_api", None)
        if _na is not None:
            for _f in formats:
                try:
                    _fm = _na.format_metadata(book_id, _f)
                    if _fm and _fm.get("size") is not None:
                        format_sizes[_f] = _fm["size"]
                except Exception:
                    pass
        row.update({
            "format_sizes": format_sizes,
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
    def merge_books(self, keep_id, remove_ids):
        """Merge certain-duplicate entries into the keeper: copy any
        formats the keeper lacks from the losers, union their tags,
        fill missing identifiers, then remove the losers into
        calibre's recycle bin (permanent=False). Backup first.
        The CALLER must have verified the group is a certain
        duplicate (same identifier, byte-exact format sizes)."""
        keep_id = int(keep_id)
        remove_ids = [int(x) for x in (remove_ids or [])
                      if int(x) != keep_id]
        if not remove_ids:
            return {"ok": False, "error": "nothing to remove"}
        if not _WRITELOCK.acquire(timeout=180):
            return {"ok": False, "error": "another write is in progress"}
        try:
            return self._merge_locked(keep_id, remove_ids)
        finally:
            _WRITELOCK.release()

    def _merge_locked(self, keep_id, remove_ids):
        import io
        oplog("merge: keep=%d remove=%s" % (keep_id, remove_ids))
        backup = self.backup()
        db = self._db()
        new_api = getattr(db, "new_api", None)
        if new_api is None:
            return {"ok": False, "error": "cache API unavailable",
                    "backup": backup}
        keep_mi = self._metadata(db, keep_id)
        if keep_mi is None:
            return {"ok": False, "error": "keeper not found",
                    "backup": backup}
        copied, tags_added = [], []
        keep_formats = {f.upper() for f in (new_api.formats(keep_id)
                                            or [])}
        keep_tags = list(keep_mi.tags or [])
        try:
            keep_ids = dict(keep_mi.get_identifiers())
        except Exception:
            keep_ids = {}
        for loser in remove_ids:
            lmi = self._metadata(db, loser)
            if lmi is None:
                return {"ok": False,
                        "error": "loser %d not found" % loser,
                        "backup": backup}
            for f in (new_api.formats(loser) or []):
                if f.upper() not in keep_formats:
                    data = new_api.format(loser, f)
                    if data is None:
                        return {"ok": False,
                                "error": "cannot read %s of %d"
                                         % (f, loser),
                                "backup": backup}
                    new_api.add_format(keep_id, f, io.BytesIO(data),
                                       replace=False, run_hooks=False)
                    keep_formats.add(f.upper())
                    copied.append("%d:%s" % (loser, f))
            for t in (lmi.tags or []):
                if t not in keep_tags:
                    keep_tags.append(t)
                    tags_added.append(t)
            try:
                for k, v in dict(lmi.get_identifiers()).items():
                    keep_ids.setdefault(k, v)
            except Exception:
                pass
        if tags_added:
            try:
                new_api.set_field("tags", {keep_id: keep_tags})
            except Exception as e:
                oplog("merge: tag union failed: %s" % e)
        try:
            cur_ids = dict(keep_mi.get_identifiers())
        except Exception:
            cur_ids = {}
        if keep_ids != cur_ids:
            try:
                new_api.set_field("identifiers", {keep_id: keep_ids})
            except Exception as e:
                oplog("merge: identifier fill failed: %s" % e)
        try:
            new_api.remove_books(remove_ids, permanent=False)
        except TypeError:
            new_api.remove_books(remove_ids)
        oplog("merge: removed %s (copied %s)" % (remove_ids, copied))
        return {"ok": True, "backup": backup, "keep": keep_id,
                "removed": remove_ids, "formats_copied": copied,
                "tags_added": tags_added}

    # -- recycle-bin (deleted books) ------------------------------------
    def _trash_dir(self):
        return os.path.join(self.library_path(), ".caltrash")

    def deleted_books(self):
        """List calibre's deleted (recycle-bin) books: ids the Cache
        reports, plus the on-disk trash entries as ground truth."""
        db = self._db()
        new_api = getattr(db, "new_api", None)
        ids, via = [], None
        if new_api is not None:
            for name in ("list_deleted_books", "deleted_books",
                         "get_deleted_books"):
                fn = getattr(new_api, name, None)
                if callable(fn):
                    try:
                        ids = [int(x) for x in (fn() or [])]
                        via = name
                        break
                    except Exception:
                        ids = []
        trash = self._trash_dir()
        entries = []
        if os.path.isdir(trash):
            for root, dirs, files in os.walk(trash):
                depth = root[len(trash):].count(os.sep)
                if depth > 3:
                    dirs[:] = []
                    continue
                for n in dirs + files:
                    entries.append(os.path.relpath(
                        os.path.join(root, n), trash))
            entries.sort()
        # attribute survey: where does this calibre keep its
        # delete service / restore entry points?
        survey = {}
        for label, obj in (("cache", new_api), ("db", db),
                           ("backend", getattr(db, "backend", None)),
                           ("cache.backend",
                            getattr(new_api, "backend", None))):
            if obj is None:
                continue
            try:
                survey[label] = [a for a in dir(obj)
                                 if any(k in a.lower() for k in
                                        ("delet", "restor", "trash"))]
            except Exception:
                pass
        return {"ids": ids, "via": via, "trash_dir": trash,
                "trash_entries": entries[:400], "survey": survey}

    def restore_deleted(self, ids):
        """Restore deleted books from calibre's recycle bin, trying
        the Cache/delete-service entry points this calibre exposes.
        Backup first; serialized with other writes."""
        ids = [int(x) for x in (ids or [])]
        if not ids:
            return {"ok": False, "error": "no ids"}
        if not _WRITELOCK.acquire(timeout=180):
            return {"ok": False, "error": "another write is in progress"}
        try:
            backup = self.backup()
            db = self._db()
            new_api = getattr(db, "new_api", None)
            results = {}
            holders = [("cache", new_api), ("db", db),
                       ("backend", getattr(db, "backend", None)),
                       ("cache.backend",
                        getattr(new_api, "backend", None))]
            services = []
            for hlabel, h in holders:
                if h is None:
                    continue
                for aname in ("delete_service", "_delete_service",
                              "trash", "deleted_books_service"):
                    svc = getattr(h, aname, None)
                    if svc is not None and not callable(svc):
                        services.append((hlabel + "." + aname, svc))
            for bid in ids:
                done, how, errs = False, None, []
                cands = []
                for hlabel, h in holders:
                    if h is None:
                        continue
                    for name in ("restore_deleted_book",
                                 "restore_deleted_books",
                                 "restore_books"):
                        fn = getattr(h, name, None)
                        if callable(fn):
                            cands.append((hlabel + "." + name, fn))
                for slabel, svc in services:
                    for name in ("restore_books", "restore_deleted",
                                 "restore_book", "restore"):
                        fn = getattr(svc, name, None)
                        if callable(fn):
                            cands.append((slabel + "." + name, fn))
                for name, fn in cands:
                    try:
                        try:
                            fn(bid)
                        except TypeError:
                            fn([bid])
                        done, how = True, name
                        break
                    except Exception as e:
                        errs.append("%s: %s" % (name, e))
                        oplog("restore %d via %s failed: %s"
                              % (bid, name, e))
                results[str(bid)] = {"restored": done, "via": how,
                                     "errors": errs[:6]}
                oplog("restore %d -> %s (%s)" % (bid, done, how))
            return {"ok": True, "backup": backup, "results": results}
        finally:
            _WRITELOCK.release()

    def trash_file(self, book_id):
        """Fallback if restore is unavailable: find a deleted book's
        format file in the trash dir and return its bytes."""
        import base64
        trash = self._trash_dir()
        if not os.path.isdir(trash):
            return {"ok": False, "error": "no trash dir"}
        needle = str(int(book_id))
        found = []
        for root, _dirs, files in os.walk(trash):
            if needle in os.path.basename(root):
                for f in files:
                    if f.rsplit(".", 1)[-1].lower() in (
                            "epub", "azw3", "mobi", "pdf"):
                        found.append(os.path.join(root, f))
        if not found:
            return {"ok": False,
                    "error": "no trashed file for %d" % book_id}
        found.sort(key=lambda p: -os.path.getsize(p))
        path = found[0]
        with open(path, "rb") as fh:
            data = fh.read()
        return {"ok": True, "filename": os.path.basename(path),
                "size": len(data),
                "data_base64": base64.b64encode(data).decode()}

    # -- filesystem: list / move inside the library folder --------------
    #
    # The library folder holds more than live book folders: the
    # recycle bin (.caltrash), backups/, and — after a database
    # restore — orphaned folders calibre no longer tracks. Cleanup
    # needs to MOVE those (quarantine, re-homing), and until now that
    # meant hands on the PC. These two methods expose list + move
    # over the bridge under strict confinement:
    #
    # - Paths are relative to the library root ("" = the root);
    #   absolute paths and ".." escapes are refused.
    # - metadata.db (and its journal/wal sidecars) and the
    #   connection file are never movable.
    # - A LIVE book folder is refused as source, as an ancestor of
    #   the source, and as a container of the destination: calibre
    #   owns those paths, and moving one behind its back desyncs
    #   the database. (To rename a book's folder, edit the book's
    #   metadata — calibre renames the folder itself.)
    # - The destination must not already exist (no silent
    #   overwrite); missing parent folders are created.
    # - Everything stays inside the one library folder, so a move
    #   is a same-filesystem rename, never a cross-drive copy.

    _FS_PROTECTED = ("metadata.db", "metadata.db-journal",
                     "metadata.db-wal", "metadata.db-shm",
                     "calibrelink-connection.txt")

    def _fs_resolve(self, rel):
        """(normalised relative path with '/' separators, abs path)
        for a library-relative path, or raise ValueError."""
        lib = self.library_path()
        rel = (rel or "").strip().replace("\\", "/")
        if rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
            raise ValueError("path must be relative to the library "
                             "folder: %r" % rel)
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise ValueError("path escapes the library folder: %r" % rel)
        norm = "/".join(parts)
        abspath = os.path.join(lib, *parts) if parts else lib
        # belt and braces: resolve symlinks/junctions, then re-check
        # containment against the resolved library root.
        real_lib = os.path.realpath(lib)
        real_abs = os.path.realpath(abspath)
        if real_abs != real_lib and \
                os.path.commonpath([real_abs, real_lib]) != real_lib:
            raise ValueError("path escapes the library folder: %r" % rel)
        return norm, abspath

    def _fs_book_paths(self, db):
        """Set of live book folder paths ('Author/Title (id)')."""
        out = set()
        for book_id in self._all_ids(db):
            try:
                p = db.path(book_id, index_is_id=True)
            except Exception:
                p = None
            if p:
                out.add(str(p).replace("\\", "/").strip("/"))
        return out

    def _fs_check_book_guard(self, db, norm, role):
        for bp in self._fs_book_paths(db):
            if norm == bp or bp.startswith(norm + "/") or \
                    norm.startswith(bp + "/"):
                raise ValueError(
                    "%s %r overlaps live book folder %r — calibre "
                    "owns book folders; change the book's metadata "
                    "instead and calibre renames it" % (role, norm, bp))

    def fs_list(self, rel="", offset=0, limit=1000):
        norm, abspath = self._fs_resolve(rel)
        if not os.path.isdir(abspath):
            raise ValueError("not a folder in the library: %r"
                             % (rel or "/"))
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            offset = 0
        try:
            limit = max(1, min(1000, int(limit)))
        except (TypeError, ValueError):
            limit = 1000
        books = self._fs_book_paths(self._db())
        names = sorted(os.listdir(abspath),
                       key=lambda n: (not os.path.isdir(
                           os.path.join(abspath, n)), n.lower()))
        total = len(names)
        entries = []
        for name in names[offset:offset + limit]:
            full = os.path.join(abspath, name)
            is_dir = os.path.isdir(full)
            entry = {"name": name, "type": "dir" if is_dir else "file"}
            if is_dir:
                child = (norm + "/" + name) if norm else name
                entry["is_book_folder"] = child in books
            else:
                try:
                    entry["size"] = os.path.getsize(full)
                except OSError:
                    entry["size"] = None
            entries.append(entry)
        return {"ok": True, "root": self.library_path(), "path": norm,
                "entries": entries, "total": total, "offset": offset,
                "truncated": offset + len(entries) < total}

    def fs_move(self, src, dst):
        src_norm, src_abs = self._fs_resolve(src)
        dst_norm, dst_abs = self._fs_resolve(dst)
        if not src_norm:
            raise ValueError("cannot move the library root")
        if not dst_norm:
            raise ValueError("destination must name a file or folder")
        if src_norm.lower() in self._FS_PROTECTED:
            raise ValueError("%s is protected and cannot be moved"
                             % src_norm)
        if dst_norm == src_norm or dst_norm.startswith(src_norm + "/"):
            raise ValueError("cannot move a folder into itself")
        if not os.path.lexists(src_abs):
            raise ValueError("no such file or folder: %r" % src)
        if os.path.lexists(dst_abs):
            raise ValueError("destination already exists: %r" % dst)
        if not _WRITELOCK.acquire(timeout=120):
            return {"ok": False, "error": "another write is in progress"}
        try:
            db = self._db()
            self._fs_check_book_guard(db, src_norm, "source")
            self._fs_check_book_guard(db, dst_norm, "destination")
            parent = os.path.dirname(dst_abs)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            kind = "dir" if os.path.isdir(src_abs) else "file"
            oplog("fs_move: %s -> %s" % (src_norm, dst_norm))
            shutil.move(src_abs, dst_abs)
            return {"ok": True, "src": src_norm, "dst": dst_norm,
                    "kind": kind}
        finally:
            _WRITELOCK.release()

    def fs_upload(self, rel_dir, filename, data, overwrite=False):
        """Write an uploaded file into a folder inside the library.

        The receiving end of a download pipeline: Tinker pulls a
        book file from wherever it lives (e.g. Google Drive) and
        lands it in a staging folder here, from which it can be
        added properly (/add-book) or moved with fs_move. Same
        confinement as fs_move: library-relative paths only, no
        writing into a live book folder, protected names refused,
        and no overwrite unless explicitly requested.
        """
        filename = (filename or "").strip()
        if not filename or filename in (".", "..") or \
                "/" in filename or "\\" in filename:
            raise ValueError("filename must be a bare file name: %r"
                             % filename)
        dir_norm, dir_abs = self._fs_resolve(rel_dir)
        target_norm = (dir_norm + "/" + filename) if dir_norm else filename
        if target_norm.lower() in self._FS_PROTECTED:
            raise ValueError("%s is protected and cannot be written"
                             % target_norm)
        target_abs = os.path.join(dir_abs, filename)
        if os.path.lexists(target_abs) and not overwrite:
            raise ValueError("destination already exists: %r"
                             % target_norm)
        if not _WRITELOCK.acquire(timeout=120):
            return {"ok": False, "error": "another write is in progress"}
        try:
            self._fs_check_book_guard(self._db(), target_norm,
                                      "destination")
            if not os.path.isdir(dir_abs):
                os.makedirs(dir_abs, exist_ok=True)
            with open(target_abs, "wb") as f:
                f.write(data)
            oplog("fs_upload: %s (%d bytes)" % (target_norm, len(data)))
            return {"ok": True, "path": target_norm, "size": len(data)}
        finally:
            _WRITELOCK.release()

    # -- chunked, resumable uploads ---------------------------------------
    #
    # For files too big for one request (the single-shot cap is
    # 64 MB) and for transfers that must survive a dropped
    # connection — or a calibre restart. State lives ON DISK, not
    # in memory: each transfer gets a part file plus a JSON sidecar
    # in <library>/.calibrelink-parts/, so begin/status/complete
    # work across bridge restarts. Pausing is simply not sending
    # more chunks; resuming is calling begin again with the same
    # transfer id and continuing from the reported offset. Chunks
    # must arrive in order; resending the tail (offset < received)
    # truncates and rewrites it, which makes a lost response safe
    # to retry. Complete verifies size (and sha256 when given),
    # re-runs every confinement check, then renames the part file
    # onto the target.

    _TRANSFER_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
    _CHUNK_CAP = 16 * 1024 * 1024

    def _parts_dir(self):
        d = os.path.join(self.library_path(), ".calibrelink-parts")
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        return d

    def _transfer_paths(self, transfer_id):
        if not self._TRANSFER_ID.match(transfer_id or ""):
            raise ValueError("bad transfer id: %r" % transfer_id)
        d = self._parts_dir()
        return (os.path.join(d, transfer_id + ".part"),
                os.path.join(d, transfer_id + ".json"))

    def _transfer_meta(self, transfer_id):
        import json
        _part, sidecar = self._transfer_paths(transfer_id)
        if not os.path.exists(sidecar):
            return None
        with open(sidecar, "r", encoding="utf-8") as f:
            return json.load(f)

    def _check_upload_target(self, dir_norm, filename, overwrite):
        target_norm = (dir_norm + "/" + filename) if dir_norm else filename
        if target_norm.lower() in self._FS_PROTECTED:
            raise ValueError("%s is protected and cannot be written"
                             % target_norm)
        _n, dir_abs = self._fs_resolve(dir_norm)
        if os.path.lexists(os.path.join(dir_abs, filename)) \
                and not overwrite:
            raise ValueError("destination already exists: %r"
                             % target_norm)
        self._fs_check_book_guard(self._db(), target_norm, "destination")
        return target_norm

    def upload_begin(self, rel_dir, filename, size, transfer_id,
                     sha256=None, overwrite=False):
        import json
        filename = (filename or "").strip()
        if not filename or filename in (".", "..") or \
                "/" in filename or "\\" in filename:
            raise ValueError("filename must be a bare file name: %r"
                             % filename)
        size = int(size)
        if size < 0:
            raise ValueError("bad size: %r" % size)
        dir_norm, _abs = self._fs_resolve(rel_dir)
        part, sidecar = self._transfer_paths(transfer_id)
        meta = self._transfer_meta(transfer_id)
        if meta is not None:
            if meta.get("dir") != dir_norm or \
                    meta.get("filename") != filename or \
                    int(meta.get("size", -1)) != size:
                raise ValueError(
                    "transfer %s already exists with different "
                    "parameters" % transfer_id)
            received = os.path.getsize(part) if os.path.exists(part) else 0
            return {"ok": True, "transfer_id": transfer_id,
                    "received": received, "size": size, "resumed": True}
        self._check_upload_target(dir_norm, filename, overwrite)
        with open(part, "wb"):
            pass
        meta = {"dir": dir_norm, "filename": filename, "size": size,
                "sha256": sha256 or None, "overwrite": bool(overwrite)}
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(meta, f)
        oplog("upload_begin: %s -> %s (%d bytes)"
              % (transfer_id, filename, size))
        return {"ok": True, "transfer_id": transfer_id, "received": 0,
                "size": size, "resumed": False}

    def upload_chunk(self, transfer_id, offset, data):
        meta = self._transfer_meta(transfer_id)
        if meta is None:
            return {"ok": False,
                    "error": "unknown transfer: %s" % transfer_id}
        if self._is_paused(transfer_id):
            return {"ok": False, "paused": True,
                    "error": "transfer paused: %s" % transfer_id}
        if len(data) > self._CHUNK_CAP:
            raise ValueError("chunk too large (%d bytes, cap %d)"
                             % (len(data), self._CHUNK_CAP))
        offset = int(offset)
        part, _sidecar = self._transfer_paths(transfer_id)
        if not _WRITELOCK.acquire(timeout=120):
            return {"ok": False, "error": "another write is in progress"}
        try:
            current = os.path.getsize(part) if os.path.exists(part) else 0
            if offset > current:
                raise ValueError("chunk gap: offset %d beyond received "
                                 "%d" % (offset, current))
            with open(part, "r+b") as f:
                if offset < current:
                    f.truncate(offset)
                f.seek(offset)
                f.write(data)
            received = os.path.getsize(part)
            return {"ok": True, "transfer_id": transfer_id,
                    "received": received, "size": meta["size"]}
        finally:
            _WRITELOCK.release()

    def upload_status(self, transfer_id):
        meta = self._transfer_meta(transfer_id)
        if meta is None:
            return {"ok": False,
                    "error": "unknown transfer: %s" % transfer_id}
        part, _sidecar = self._transfer_paths(transfer_id)
        received = os.path.getsize(part) if os.path.exists(part) else 0
        return {"ok": True, "transfer_id": transfer_id,
                "received": received, "size": meta["size"],
                "target": (meta["dir"] + "/" + meta["filename"])
                if meta["dir"] else meta["filename"]}

    def upload_complete(self, transfer_id):
        import hashlib
        meta = self._transfer_meta(transfer_id)
        if meta is None:
            return {"ok": False,
                    "error": "unknown transfer: %s" % transfer_id}
        part, sidecar = self._transfer_paths(transfer_id)
        received = os.path.getsize(part) if os.path.exists(part) else 0
        if received != int(meta["size"]):
            return {"ok": False,
                    "error": "incomplete transfer: %d of %d bytes "
                             "received" % (received, meta["size"])}
        if meta.get("sha256"):
            h = hashlib.sha256()
            with open(part, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
            if h.hexdigest().lower() != str(meta["sha256"]).lower():
                return {"ok": False,
                        "error": "sha256 mismatch — transfer is "
                                 "corrupt; abort and restart it"}
        if not _WRITELOCK.acquire(timeout=120):
            return {"ok": False, "error": "another write is in progress"}
        try:
            target_norm = self._check_upload_target(
                meta["dir"], meta["filename"], meta["overwrite"])
            _n, dir_abs = self._fs_resolve(meta["dir"])
            if not os.path.isdir(dir_abs):
                os.makedirs(dir_abs, exist_ok=True)
            target_abs = os.path.join(dir_abs, meta["filename"])
            shutil.move(part, target_abs)
            try:
                os.remove(sidecar)
            except OSError:
                pass
            oplog("upload_complete: %s (%d bytes)"
                  % (target_norm, received))
            return {"ok": True, "path": target_norm, "size": received}
        finally:
            _WRITELOCK.release()

    def upload_abort(self, transfer_id):
        part, sidecar = self._transfer_paths(transfer_id)
        for p in (part, sidecar):
            try:
                os.remove(p)
            except OSError:
                pass
        oplog("upload_abort: %s" % transfer_id)
        return {"ok": True, "transfer_id": transfer_id}

    # -- transfer progress: one registry for both directions -------------
    #
    # The status dialog's progress bars and the /transfers endpoint
    # both read from here. Uploads are disk-backed already (a sidecar
    # per transfer in .calibrelink-parts). Downloads are served
    # range-by-range to a remote client, so the bridge tracks them
    # in memory: per file, its size and the furthest byte served —
    # which is exactly the client's resume point. Pause flags live
    # on disk (paused.json in the parts dir) so a pause set in the
    # dialog is seen by the HTTP API and survives a calibre restart;
    # a paused upload refuses chunks and a paused download refuses
    # ranges until resumed. Keys: an upload's transfer_id, a
    # download's library-relative path, or "*" for everything.

    def _paused_file(self):
        return os.path.join(self._parts_dir(), "paused.json")

    def _paused_set(self):
        import json
        try:
            with open(self._paused_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data) if isinstance(data, list) else set()
        except Exception:
            return set()

    def _save_paused(self, keys):
        import json
        with open(self._paused_file(), "w", encoding="utf-8") as f:
            json.dump(sorted(keys), f)

    def _is_paused(self, key):
        paused = self._paused_set()
        return "*" in paused or key in paused

    def transfer_pause(self, key):
        key = (key or "*").strip() or "*"
        keys = self._paused_set()
        keys.add(key)
        self._save_paused(keys)
        oplog("transfer_pause: %s" % key)
        return {"ok": True, "paused": sorted(keys)}

    def transfer_resume(self, key):
        key = (key or "*").strip() or "*"
        keys = self._paused_set()
        if key == "*":
            keys.clear()
        else:
            keys.discard(key)
            keys.discard("*")
        self._save_paused(keys)
        oplog("transfer_resume: %s" % key)
        return {"ok": True, "paused": sorted(keys)}

    def _note_download(self, key, name, total, end):
        import time
        track = getattr(self, "_dl_track", None)
        if track is None:
            track = self._dl_track = {}
        rec = track.get(key)
        if rec is None or rec.get("size") != total:
            rec = {"name": name, "size": total, "served": 0,
                   "updated": 0.0}
            track[key] = rec
        rec["served"] = max(rec["served"], end)
        rec["updated"] = time.time()

    def transfers_list(self):
        import time
        paused = self._paused_set()
        pause_all = "*" in paused
        out = []
        parts = self._parts_dir()
        for name in sorted(os.listdir(parts)):
            if not name.endswith(".json") or name == "paused.json":
                continue
            tid = name[:-len(".json")]
            if not self._TRANSFER_ID.match(tid):
                continue
            meta = self._transfer_meta(tid)
            if meta is None:
                continue
            part, _s = self._transfer_paths(tid)
            received = os.path.getsize(part) \
                if os.path.exists(part) else 0
            size = int(meta.get("size", 0))
            is_paused = pause_all or tid in paused
            out.append({
                "kind": "upload", "key": tid,
                "name": meta.get("filename"),
                "target": (meta["dir"] + "/" + meta["filename"])
                if meta.get("dir") else meta.get("filename"),
                "size": size, "done": received,
                "percent": (100 * received // size) if size else 100,
                "paused": is_paused,
                "state": "paused" if is_paused else
                         ("ready" if received >= size else "active")})
        for key, rec in sorted(
                getattr(self, "_dl_track", {}).items()):
            size = int(rec["size"])
            done = int(rec["served"])
            is_paused = pause_all or key in paused
            out.append({
                "kind": "download", "key": key, "name": rec["name"],
                "size": size, "done": done,
                "percent": (100 * done // size) if size else 100,
                "paused": is_paused,
                "state": "paused" if is_paused else
                         ("done" if done >= size else "active"),
                "idle_seconds": int(time.time() - rec["updated"])})
        return {"ok": True, "transfers": out, "paused": sorted(paused)}

    # -- chat / command channel -------------------------------------------
    #
    # A message log stored in the library folder
    # (.calibrelink-chat.json), shared by three writers/readers: the
    # plugin's own console window (in-process, no token), the HTTP
    # API (Tinker, token-gated), and the MCP layer on top of it.
    # Senders: "joe" (typed in calibre), "tinker" (replies posted
    # through the API), "system" (the console's local command
    # results are shown in the window but NOT stored — only joe and
    # tinker messages persist, so the log stays a conversation, not
    # a console dump). Capped at the newest 500 messages.

    _CHAT_SENDERS = ("joe", "tinker")

    def _chat_file(self):
        return os.path.join(self.library_path(),
                            ".calibrelink-chat.json")

    def _chat_read(self):
        import json
        try:
            with open(self._chat_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            msgs = data.get("messages", [])
            return msgs if isinstance(msgs, list) else []
        except Exception:
            return []

    def chat_list(self, since=0):
        msgs = self._chat_read()
        since = max(0, int(since or 0))
        return {"ok": True, "messages": msgs[since:],
                "count": len(msgs)}

    def chat_post(self, sender, text):
        import json
        import time
        sender = (sender or "").strip().lower()
        if sender not in self._CHAT_SENDERS:
            return {"ok": False,
                    "error": "sender must be one of: %s"
                             % ", ".join(self._CHAT_SENDERS)}
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty message"}
        if len(text) > 4000:
            text = text[:4000]
        if not _WRITELOCK.acquire(timeout=120):
            return {"ok": False, "error": "another write is in progress"}
        try:
            msgs = self._chat_read()
            msg = {"ts": time.time(), "from": sender, "text": text}
            msgs.append(msg)
            msgs = msgs[-500:]
            tmp = self._chat_file() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"messages": msgs}, f)
            os.replace(tmp, self._chat_file())
            oplog("chat_post: %s (%d chars)" % (sender, len(text)))
            return {"ok": True, "message": msg, "count": len(msgs)}
        finally:
            _WRITELOCK.release()

    # -- downloads: library -> out (the Google Drive upload leg) ---------
    #
    # The other direction of the Drive pipeline: Tinker pulls a
    # book's format file (or any file staged in the library folder)
    # through the bridge, then uploads it to Google Drive with the
    # Drive connector. Reads only — no lock, no book-folder guard
    # (reading a book's file cannot desync anything). metadata.db
    # itself stays refused here: /backup is the consistent way to
    # copy the database. Whole-file responses cap at 64 MB; larger
    # files (and resumable transfers) use offset/length ranges —
    # a download pauses by simply stopping, and resumes by
    # re-requesting from the byte offset already saved.

    def _range(self, total, offset, length):
        """Validate/normalise a byte-range request. With no length,
        the whole remainder is served when it fits the 64 MB
        single-response cap; ranged requests are capped per chunk.
        Returns (offset, length)."""
        offset = max(0, int(offset or 0))
        if offset > total:
            raise ValueError("offset %d beyond end of file (%d bytes)"
                             % (offset, total))
        if length is None:
            if total - offset > 64 * 1024 * 1024:
                raise ValueError(
                    "too large for a single response (%d bytes) — "
                    "request it in offset/length chunks" % total)
            length = total - offset
        length = max(0, min(int(length), self._CHUNK_CAP,
                            total - offset))
        return offset, length

    def book_file(self, book_id, fmt=None, offset=0, length=None):
        import base64
        db = self._db()
        api = db.new_api
        formats = []
        try:
            formats = [str(f) for f in (api.formats(book_id) or [])]
        except Exception:
            formats = []
        if not formats:
            try:
                raw = db.formats(book_id, index_is_id=True) or ""
                formats = [f.strip() for f in str(raw).split(",")
                           if f.strip()]
            except Exception:
                formats = []
        if not formats:
            return {"ok": False, "error": "book has no format files"}
        want = (fmt or formats[0]).upper()
        match = [f for f in formats if f.upper() == want]
        if not match:
            return {"ok": False,
                    "error": "format %s not available (have: %s)"
                             % (want, ", ".join(formats))}
        fmt = match[0]
        data, name, total = None, None, None
        path = None
        try:
            path = api.format_abspath(book_id, fmt)
        except Exception:
            path = None
        if path and os.path.isfile(path):
            total = os.path.getsize(path)
            name = os.path.basename(path)
            dl_key = os.path.relpath(path, self.library_path()) \
                .replace(os.sep, "/")
            if self._is_paused(dl_key):
                return {"ok": False, "paused": True,
                        "error": "transfer paused: %s" % dl_key}
            offset, length = self._range(total, offset, length)
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read(length)
            self._note_download(dl_key, name, total,
                                offset + len(data))
        else:
            try:
                data = api.format(book_id, fmt)
            except Exception as e:
                return {"ok": False,
                        "error": "cannot read format file: %s" % e}
            if not data:
                return {"ok": False, "error": "format file is empty"}
            total = len(data)
            offset, length = self._range(total, offset, length)
            data = data[offset:offset + length]
        if not name:
            mi = self._metadata(db, book_id)
            title = ((mi.title if mi else "") or str(book_id)).strip()
            authors = ""
            if mi and mi.authors:
                authors = " & ".join(str(a) for a in mi.authors)
            base = ("%s - %s" % (title, authors)) if authors else title
            base = base.replace("/", "_").replace("\\", "_")
            name = "%s.%s" % (base, fmt.lower())
        return {"ok": True, "book_id": book_id, "format": fmt,
                "filename": name, "size": total, "offset": offset,
                "chunk_size": len(data),
                "eof": offset + len(data) >= total,
                "data_base64": base64.b64encode(data).decode()}

    def fs_download(self, rel, offset=0, length=None):
        import base64
        norm, abspath = self._fs_resolve(rel)
        if not norm:
            raise ValueError("path must name a file in the library")
        if norm.lower() in self._FS_PROTECTED:
            raise ValueError("%s is protected — use /backup for a "
                             "consistent copy of the database" % norm)
        if self._is_paused(norm):
            raise ValueError("transfer paused: %s" % norm)
        if not os.path.isfile(abspath):
            raise ValueError("not a file in the library: %r" % rel)
        total = os.path.getsize(abspath)
        offset, length = self._range(total, offset, length)
        with open(abspath, "rb") as f:
            f.seek(offset)
            data = f.read(length)
        self._note_download(norm, os.path.basename(abspath), total,
                            offset + len(data))
        return {"ok": True, "path": norm,
                "filename": os.path.basename(abspath), "size": total,
                "offset": offset, "chunk_size": len(data),
                "eof": offset + len(data) >= total,
                "data_base64": base64.b64encode(data).decode()}

    # -- metadata repair: swapped title / authors -------------------------
    def swap_title_authors(self, book_id):
        """Swap a book's title and author(s) — the classic mangled
        record where each field holds the other's value.

        The old title usually IS an author name, often in sort form
        ("Smith, John"): a single "Last, First" title is inverted
        back to display form for the authors field and kept as the
        author_sort. Backup first; serialized with other writes.
        Returns the repaired book row.
        """
        if not _WRITELOCK.acquire(timeout=120):
            return {"ok": False, "error": "another write is in progress"}
        try:
            db = self._db()
            mi = self._metadata(db, book_id)
            if mi is None:
                return {"ok": False, "error": "no such book"}
            old_title = (mi.title or "").strip()
            old_authors = [str(a).strip() for a in (mi.authors or [])
                           if str(a).strip()]
            if not old_title or not old_authors:
                return {"ok": False,
                        "error": "cannot swap: title and authors "
                                 "must both be non-empty"}
            backup = self.backup()
            new_title = " & ".join(old_authors)
            new_authors, new_sort = [], []
            raw = old_title
            if raw.count(",") == 1 and len(old_authors) == 1:
                last, first = [p.strip() for p in raw.split(",", 1)]
                if last and first:
                    new_authors = ["%s %s" % (first, last)]
                    new_sort = [raw]
            if not new_authors:
                new_authors = [raw]
                new_sort = [_author_sort(a) for a in new_authors]
            book = self.set_metadata(book_id, {
                "title": new_title,
                "authors": new_authors,
                "author_sort": " & ".join(new_sort)})
            oplog("swap_title_authors: #%d %r <-> %r"
                  % (book_id, old_title, old_authors))
            return {"ok": True, "backup": backup, "book": book,
                    "swapped": {"from_title": old_title,
                                "from_authors": old_authors}}
        finally:
            _WRITELOCK.release()

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


def _author_sort(name):
    """'First Last' -> 'Last, First' — calibre's own helper when
    running inside calibre, a plain inversion fallback headless."""
    try:
        from calibre.ebooks.metadata import author_to_author_sort
        return author_to_author_sort(name)
    except Exception:
        pass
    name = (name or "").strip()
    if "," in name or " " not in name:
        return name
    first, _, last = name.rpartition(" ")
    return "%s, %s" % (last, first)


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
