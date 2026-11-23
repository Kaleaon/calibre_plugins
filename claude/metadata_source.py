"""Metadata-source side of the Claude Library Fixer: answers Calibre's
identify requests with the fixer's own machinery (engine.py, providers.py).

Two passes, mirroring what the fixer does to a selected book:

- provider enrichment (when enabled in the fixer's settings): if the book's
  identifiers point at Metabods, Nifty, a FanFicFare site or Open Library,
  that provider's own metadata is returned directly;
- a Claude suggestion (when an API key is saved in the fixer's settings):
  the query book is run through engine.plan_fixes and the validated changes
  are applied on top of the query, so the result is the cleaned-up book —
  tidier title/authors, series and volume, tags, language and ISBN.

Without a key and without a recognisable provider identifier there is
nothing honest to return, so the search simply finds nothing.

The companion ZIP (claude_source/) wraps this module in a Calibre Source.
"""

try:
    from . import engine, providers
except ImportError:  # tests load this file with the folder and common/ on sys.path
    import engine
    import providers

SOURCE_NAME = "Claude Metadata"
CAPABILITIES = frozenset({"identify"})
TOUCHED_FIELDS = frozenset({"title", "authors", "tags", "series", "language", "identifier:isbn"})


def _from_provider(meta, identifiers):
    if not meta or not (meta.get("title") or meta.get("author") or meta.get("authors")):
        return None
    authors = list(meta.get("authors") or ([meta["author"]] if meta.get("author") else []))
    result = {
        "title": meta.get("title") or "",
        "authors": authors,
        "publisher": meta.get("publisher") or "",
        "tags": list(meta.get("tags") or []) + list(meta.get("categories") or []),
        "summary": meta.get("summary") or meta.get("description") or "",
        "identifiers": dict(identifiers or {}),
        "url": meta.get("url") or "",
    }
    if meta.get("isbn"):
        result["identifiers"]["isbn"] = meta["isbn"]
    if meta.get("id") and meta.get("provider"):
        result["identifiers"].setdefault(meta["provider"], str(meta["id"]))
    return result


def _apply_changes(title, authors, identifiers, changes):
    result = {
        "title": title or "",
        "authors": list(authors or []),
        "identifiers": dict(identifiers or {}),
    }
    if "title" in changes:
        result["title"] = changes["title"][1]
    if "authors" in changes:
        result["authors"] = list(changes["authors"][1])
    if "series" in changes:
        name, index = changes["series"][1]
        result["series"] = name
        if index is not None:
            result["series_index"] = index
    if "tags" in changes:
        result["tags"] = list(changes["tags"][1])
    if "language" in changes:
        result["language"] = changes["language"][1][0]
    if "isbn" in changes:
        result["identifiers"]["isbn"] = changes["isbn"][1]
    return result


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, settings=None):
    settings = settings or {}
    identifiers = dict(identifiers or {})
    book = {
        "id": 0,
        "title": title or "",
        "authors": list(authors or []),
        "series": "",
        "series_index": None,
        "tags": [],
        "languages": [],
        "publisher": "",
        "filenames": [],
        "identifiers": identifiers,
        "comments": "",
    }
    results = []
    if settings.get("provider_metadata", True):
        try:
            found = _from_provider(providers.get_provider_metadata(book), identifiers)
        except Exception:
            found = None
        if found and found.get("title"):
            results.append(found)
    key = settings.get("api_key", "")
    if key:
        fields = settings.get("fields") or engine.FIELDS
        suggestions, _skipped = engine.plan_fixes(
            [book],
            key,
            model=settings.get("model") or engine.DEFAULT_MODEL,
            fields=fields,
            min_confidence=settings.get("min_confidence", "medium"),
            batch_size=1,
            min_interval=0,
            cancelled=abort.is_set if abort is not None else lambda: False,
        )
        for suggestion in suggestions:
            if suggestion.get("changes"):
                results.append(_apply_changes(title, authors, identifiers, suggestion["changes"]))
    return results


def is_configured(settings=None):
    """Usable when a Claude key is saved or provider enrichment is enabled."""
    settings = settings or {}
    return bool(settings.get("api_key")) or bool(settings.get("provider_metadata", True))
