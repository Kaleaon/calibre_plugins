"""Import recovered formats through Calibre's database and metadata readers."""

import hashlib
from pathlib import Path

CHUNK_SIZE = 64 * 1024  # 64 KB


def _hash_source(source, chunk_size=CHUNK_SIZE):
    """Calculate SHA-256 digest in 64 KB chunks without loading whole files into RAM."""
    hasher = hashlib.sha256()
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
    elif isinstance(source, (bytes, bytearray)):
        view = memoryview(source)
        for i in range(0, len(view), chunk_size):
            hasher.update(view[i : i + chunk_size])
    elif hasattr(source, "read"):
        while chunk := source.read(chunk_size):
            hasher.update(chunk)
    else:
        raise ValueError(f"Unsupported source type for hashing: {type(source)}")
    return hasher.hexdigest()


def _get_existing_format_info(db, book_id, fmt_upper, candidate_size=None):
    """Return (size, path_or_stream_or_bytes) for format fmt_upper of book_id, or (None, None)."""
    size = None
    if hasattr(db, "format_metadata"):
        try:
            meta = db.format_metadata(book_id, fmt_upper)
            if isinstance(meta, dict) and meta.get("size") is not None:
                size = int(meta["size"])
        except Exception:
            pass

    fmt_path = None
    if hasattr(db, "format_abspath"):
        try:
            abspath = db.format_abspath(book_id, fmt_upper)
            if abspath:
                p = Path(abspath)
                if p.is_file():
                    fmt_path = p
                    if size is None:
                        size = p.stat().st_size
        except Exception:
            pass

    if size is not None and candidate_size is not None and size != candidate_size:
        return size, None

    if fmt_path is not None:
        return size, fmt_path

    data = None
    try:
        data = db.format(book_id, fmt_upper)
    except Exception:
        pass

    if data is None:
        return None, None

    if isinstance(data, (bytes, bytearray)):
        if size is None:
            size = len(data)
    elif hasattr(data, "read"):
        if size is None:
            if hasattr(data, "seek") and hasattr(data, "tell"):
                try:
                    data.seek(0, 2)
                    size = data.tell()
                    data.seek(0)
                except Exception:
                    pass

    return size, data


def repair_library_books(gui, book_ids, comics=False):
    """Repair formats already stored in the current Calibre library."""
    from tempfile import TemporaryDirectory

    from calibre.ebooks.metadata.meta import get_metadata
    from calibre_plugins.takeout_book_fixer.core import detect, image_pdf_to_cbz

    db = gui.current_db.new_api
    repaired, skipped, errors = [], [], []
    with TemporaryDirectory(prefix="takeout-book-fixer-"):
        for book_id in book_ids:
            try:
                for old_fmt in list(db.formats(book_id) or ()):
                    old_fmt = old_fmt.lower()
                    data = db.format(book_id, old_fmt.upper())
                    if not data:
                        continue
                    kind = detect(data)
                    if kind == "cbr" and old_fmt != "cbr":
                        kind = None
                    if comics and kind == "pdf":
                        converted = image_pdf_to_cbz(data)
                        if converted is not None:
                            data, kind = converted, "cbz"
                    if kind is None or kind == old_fmt:
                        skipped.append(book_id)
                        continue
                    new_fmt = kind.upper()
                    from io import BytesIO

                    if not db.add_format(book_id, new_fmt, BytesIO(data), replace=False):
                        raise ValueError("Calibre could not save corrected format")
                    try:
                        mi = get_metadata(__import__("io").BytesIO(data), stream_type=kind)
                        if mi.title:
                            db.set_metadata(book_id, mi)
                    except Exception:
                        pass
                    db.remove_formats({book_id: {old_fmt.upper()}})
                    repaired.append((book_id, old_fmt, kind))
            except Exception as exc:
                errors.append(f"{book_id}: {exc}")
    if repaired:
        gui.library_view.model().refresh()
        gui.tags_view.recount()
    return repaired, skipped, errors


def import_books(gui, rows):
    from calibre.ebooks.metadata.book.base import Metadata
    from calibre.ebooks.metadata.meta import get_metadata
    from calibre.utils.date import parse_date

    db = gui.current_db.new_api
    imported, errors = [], []
    seen = set()

    # Pre-build title index upfront to avoid repeated title queries
    title_to_ids = {}
    try:
        all_ids = list(db.all_book_ids() or ())
    except Exception:
        all_ids = []

    titles_map = None
    if all_ids:
        try:
            titles_map = db.all_field_for("title", all_ids)
        except Exception:
            try:
                titles_map = db.all_field_for("title", all_ids, default="")
            except Exception:
                titles_map = None

    if isinstance(titles_map, dict):
        for b_id, title in titles_map.items():
            if title:
                title_to_ids.setdefault(str(title).casefold(), []).append(b_id)
    else:
        for b_id in all_ids:
            try:
                title = db.field_for("title", b_id, "")
            except Exception:
                title = ""
            if title:
                title_to_ids.setdefault(str(title).casefold(), []).append(b_id)

    for row in rows:
        filename = row.get("output")
        if not filename or filename in seen:
            continue
        seen.add(filename)
        book_id = None
        try:
            path = Path(filename)
            fmt = row["format"]
            fallback = Path(row["source"].replace("\\", "/")).stem
            try:
                with path.open("rb") as stream:
                    mi = get_metadata(stream, stream_type=fmt)
            except Exception:
                mi = Metadata(fallback, ["Unknown"])
            if not mi.title or mi.title.lower() in ("unknown", "untitled"):
                mi.title = fallback
            sidecar = row.get("metadata") or {}
            title = sidecar.get("title")
            if isinstance(title, str) and title.strip():
                mi.title = title.strip()
            authors = sidecar.get("authors", sidecar.get("author"))
            if isinstance(authors, str):
                authors = [authors]
            if isinstance(authors, list) and all(isinstance(x, str) for x in authors) and authors:
                mi.authors = authors
            for key, field in [("publisher", "publisher"), ("description", "comments")]:
                if isinstance(sidecar.get(key), str):
                    setattr(mi, field, sidecar[key])
            if isinstance(sidecar.get("language"), str):
                mi.languages = [sidecar["language"]]
            categories = sidecar.get("categories")
            if isinstance(categories, list) and all(isinstance(x, str) for x in categories):
                mi.tags = categories
            if isinstance(sidecar.get("publishedDate"), str):
                try:
                    mi.pubdate = parse_date(sidecar["publishedDate"], assume_utc=True)
                except Exception:
                    pass
            if isinstance(sidecar.get("isbn"), str):
                mi.set_identifier("isbn", sidecar["isbn"])
            identifiers = sidecar.get("industryIdentifiers", [])
            if isinstance(identifiers, list):
                for identifier in identifiers:
                    if (
                        isinstance(identifier, dict)
                        and str(identifier.get("type", "")).startswith("ISBN")
                        and isinstance(identifier.get("identifier"), str)
                    ):
                        mi.set_identifier("isbn", identifier["identifier"])

            # Multi-tiered duplicate checking: title index -> size check -> 64 KB chunked SHA-256
            duplicate = False
            title_key = mi.title.casefold()
            candidate_ids = title_to_ids.get(title_key, [])
            if candidate_ids:
                candidate_size = path.stat().st_size
                candidate_sha256 = None
                for existing_id in candidate_ids:
                    existing_size, existing_source = _get_existing_format_info(
                        db, existing_id, fmt.upper(), candidate_size=candidate_size
                    )
                    if existing_size is None or existing_size != candidate_size or existing_source is None:
                        continue
                    if candidate_sha256 is None:
                        candidate_sha256 = _hash_source(path)
                    existing_sha256 = _hash_source(existing_source)
                    if candidate_sha256 == existing_sha256:
                        duplicate = True
                        break

            if duplicate:
                row["import_status"] = "duplicate skipped"
                continue
            book_id = db.create_book_entry(mi)
            if book_id is None:
                raise ValueError("Calibre did not create a library entry")
            with path.open("rb") as stream:
                if not db.add_format(book_id, fmt.upper(), stream):
                    raise ValueError("Calibre did not save the book format")
            cover = getattr(mi, "cover_data", None)
            if cover and cover[1]:
                db.set_cover({book_id: cover[1]})
            row.update(import_status="imported", calibre_id=book_id)
            imported.append(book_id)
            title_to_ids.setdefault(title_key, []).append(book_id)
        except Exception as e:
            # Remove only the incomplete new record created by this import.
            if book_id is not None:
                try:
                    db.remove_books({book_id})
                except Exception as cleanup:
                    row["cleanup_error"] = str(cleanup)
            row.update(import_status="error", import_error=str(e))
            errors.append(f"{row['source']}: {e}")
    if imported:
        gui.library_view.model().refresh()
        gui.tags_view.recount()
    return imported, errors
