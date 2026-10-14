"""Decides which MusicBrainz recording or Open Library book a library record is, and what to change because of it. Pure standard
library so it can be tested outside Calibre.

Nothing here changes a library: plan_matches() returns suggestions and the caller applies only the ones the user ticks.
Every suggestion carries a score from 0 to 1 built from how closely the title, artist, album and length agree; the preview
shows it, and weak matches are not offered at all.
"""

import difflib
import re
import unicodedata
from datetime import datetime, timezone

from . import sources
from . import tags as audio

KINDS = ("music", "audiobook", "book")
KIND_TAGS = {"music": "Music", "audiobook": "Audiobook"}
FIELDS = ("title", "authors", "album", "date", "tags", "subjects", "identifiers", "cover")
DEFAULT_FIELDS = ("title", "authors", "album", "date", "tags", "identifiers", "cover")  # subjects are noisy, so opt-in
PER_BOOK_STATUS = (400, 404, 422)
MIN_FINGERPRINT = 0.5  # AcoustID results scoring less than this are ignored
MIN_SIMILAR_TITLE = 0.75  # a new title less alike than this is flagged as a big change and starts unticked
MIN_TITLE_SIM = (
    0.5  # a candidate whose title is less alike than this is never offered (unless a fingerprint vouches for it)
)
MIN_SCORE = 0.6  # below this a match is not offered
TICK_SCORE = 0.85  # at or above this the suggestion starts ticked
MAX_BOOK_TAGS = 5

NOISE = re.compile(
    r"[\(\[][^\)\]]*\b(remaster(ed)?|mono|stereo|version|edit|deluxe|bonus|explicit|unabridged|abridged|audiobook|"
    r"audio book|feat\.?|featuring|ft\.?|live|demo|single|album)\b[^\)\]]*[\)\]]",
    re.I,
)
DASH_NOISE = re.compile(
    r"\s*[-:\u2013]\s+(\d{4}\s+)?(remaster(ed)?|mono|stereo|single version|album version|radio edit|unabridged|abridged|"
    r"audiobook|audio book)\b.*$",
    re.I,
)


def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("&", " and ")
    text = re.sub(r"^(.+?),\s*(the|a|an)$", r"\2 \1", text.strip())  # "Beatles, The" -> "the beatles"
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text)).strip()


def strip_noise(text):
    return DASH_NOISE.sub("", NOISE.sub("", text or "")).strip()


def similarity(a, b):
    """0..1 closeness of two names, ignoring case, accents, punctuation, 'The' and edition noise such as '(Remastered)'."""
    best = 0.0
    for x in {a or "", strip_noise(a)}:
        for y in {b or "", strip_noise(b)}:
            nx, ny = _drop_article(norm(x)), _drop_article(norm(y))
            if not nx or not ny:
                continue
            if nx == ny:
                return 1.0
            ratio = difflib.SequenceMatcher(None, nx, ny).ratio()
            tx, ty = set(nx.split()), set(ny.split())
            overlap = len(tx & ty) / max(len(tx | ty), 1)
            best = max(best, ratio * 0.6 + overlap * 0.4 if ratio < 0.999 else 1.0)
    return best


def _drop_article(text):
    return re.sub(r"^(the|a|an) ", "", text)


def classify(book, forced="auto"):
    """'music', 'audiobook' or 'book' for a library record dict (formats, tags). A forced kind wins."""
    if forced in KINDS:
        return forced
    formats = {f.upper() for f in book.get("formats", [])}
    tags = {t.lower() for t in book.get("tags", [])}
    if not formats & audio.AUDIO_FORMATS:
        return "book"
    if "audiobook" in tags or "M4B" in formats or book.get("audiobook_flag"):
        return "audiobook"
    return "music"


# -- MusicBrainz candidates ----------------------------------------------------------------------------------------------
def credit_names(credit):
    return [c.get("name") or (c.get("artist") or {}).get("name", "") for c in credit or [] if isinstance(c, dict)]


def credit_text(credit):
    return "".join(
        (c.get("name") or "") + (c.get("joinphrase") or "") for c in credit or [] if isinstance(c, dict)
    ).strip()


def _release_year(release):
    m = re.match(r"(\d{4})", release.get("date") or "")
    return int(m.group(1)) if m else None


def _track_in(release):
    """-> (disc, track number, tracks on that disc) of the recording inside this release (as the search result shows them)."""
    for medium in release.get("media", []):
        for track in medium.get("track", []):
            m = re.match(r"\d+", str(track.get("number", "")))
            return medium.get("position"), int(m.group(0)) if m else None, medium.get("track-count")
    return None, None, None


def release_rank(release, want):
    """How well this release of a recording fits what we know. Prefers the matching album, then official studio albums."""
    rank = similarity(want["album"], release.get("title", "")) if want.get("album") else 0.5
    if release.get("status") == "Official":
        rank += 0.15
    group = release.get("release-group") or {}
    if group.get("primary-type") == "Album" and not group.get("secondary-types"):
        rank += 0.1
    _disc, number, _total = _track_in(release)
    if want.get("track") and number == want["track"]:
        rank += 0.1
    if want.get("year") and _release_year(release) == want["year"]:
        rank += 0.1
    return rank


def score_recording(rec, want, acoustid_score=None):
    """Candidate dict for one MusicBrainz recording against what we know, or None if it is not a plausible match."""
    title, credit = rec.get("title", ""), rec.get("artist-credit")
    names = credit_names(credit)
    title_sim = similarity(want["title"], title)
    if title_sim < MIN_TITLE_SIM and acoustid_score is None:
        return None  # same artist and album but a different song is not a match, however well the rest agrees
    parts = [
        (0.1 if acoustid_score is not None else 0.4, title_sim)
    ]  # a fingerprint outweighs a library title that may be junk
    if want.get("artist"):
        artist_sim = max(
            [similarity(want["artist"], credit_text(credit))] + [similarity(want["artist"], n) for n in names]
        )
        parts.append((0.3, artist_sim))
    release = None
    releases = rec.get("releases") or []
    if releases:
        release = max(
            releases, key=lambda r: (round(release_rank(r, want), 3), -(_release_year(r) or 9999))
        )  # ties: the earliest release
    if want.get("album") and release:
        parts.append((0.2, similarity(want["album"], release.get("title", ""))))
    length = rec.get("length")
    if want.get("duration") and length:
        parts.append((0.15, max(0.0, 1 - abs(length / 1000 - want["duration"]) / 10)))
    if rec.get("score") is not None:
        parts.append((0.1, rec["score"] / 100))
    if acoustid_score is not None:
        parts.append((0.6, acoustid_score))
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    disc, number, total = _track_in(release) if release else (None, None, None)
    rank = release_rank(release, want) if release else 0.0
    group = (release or {}).get("release-group") or {}
    return {
        "source": "musicbrainz",
        "score": score,
        "title": title,
        "authors": names,
        "artist_credit": credit_text(credit),
        "artist_ids": [(c.get("artist") or {}).get("id", "") for c in credit or [] if isinstance(c, dict)],
        "album": (release or {}).get("title", ""),
        "track": number,
        "disc": disc,
        "track_total": total,
        "date": (release or {}).get("date") or rec.get("first-release-date") or "",
        "recording_id": rec.get("id", ""),
        "release_id": (release or {}).get("id", ""),
        "group_id": group.get("id", ""),
        "cover_urls": sources.cover_art_url((release or {}).get("id", ""), group.get("id", "")) if release else [],
        "length": length / 1000 if length else None,
        "rank": rank,
        "year": _release_year(release) if release else None,
    }


AMBIGUOUS_MARGIN = 0.03


def best_music_candidate(recordings, want, acoustid_scores=None, exact_ids=()):
    """The best candidate of a search result, or None. acoustid_scores: {recording id: fingerprint score}. exact_ids:
    recordings whose id is in the file's own tags, which counts as a strong (not independent) match.

    The winner is marked 'ambiguous' when a different recording scores almost as well and nothing (the file's own ids, a
    fingerprint) singles it out: with only a title and artist, a popular song has hundreds of near-identical recordings."""
    scored = []
    for rec in recordings:
        cand = score_recording(rec, want, (acoustid_scores or {}).get(rec.get("id")))
        if cand is None:
            continue
        if rec.get("id") in exact_ids:
            cand["score"] = max(cand["score"], 0.95)
            cand["by_id"] = True
        scored.append(cand)
    if not scored:
        return None
    scored.sort(
        key=lambda c: (-round(c["score"], 3), -round(c["rank"], 2), c["year"] or 9999)
    )  # ties: the better-fitting, then the earliest, release
    best = scored[0]

    def outcome(c):
        return (norm(c["title"]), norm(c["artist_credit"]), norm(c["album"]))

    rivals = [
        c
        for c in scored[1:]
        if c["recording_id"] != best["recording_id"]
        and c["score"] >= best["score"] - AMBIGUOUS_MARGIN
        and outcome(c) != outcome(best)
    ]  # a rival that would change nothing (same song on the same album) is no doubt
    best["ambiguous"] = bool(rivals) and not best.get("by_id") and not (acoustid_scores or {}).get(best["recording_id"])
    best["rivals"] = len(rivals)
    return best


# -- Open Library candidates ---------------------------------------------------------------------------------------------
def score_book(doc, want):
    authors = doc.get("author_name") or []
    title_sim = similarity(want["title"], doc.get("title", ""))
    if title_sim < MIN_TITLE_SIM:
        return None
    parts = [(0.6, title_sim)]
    if want.get("artist"):
        parts.append((0.4, max([similarity(want["artist"], a) for a in authors] or [0.0])))
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    year = doc.get("first_publish_year")
    subjects = [s for s in (doc.get("subject") or []) if len(s) < 40][:MAX_BOOK_TAGS]
    return {
        "source": "openlibrary",
        "score": score,
        "title": doc.get("title", ""),
        "authors": list(authors[:4]),
        "date": str(year) if year else "",
        "olid": doc.get("cover_edition_key") or "",
        "work": (doc.get("key") or "").rsplit("/", 1)[-1],
        "subjects": subjects,
        "cover_urls": sources.ol_cover_urls(doc.get("cover_i")),
    }


def best_book_candidate(docs, want):
    scored = [c for c in (score_book(d, want) for d in docs) if c]
    return max(scored, key=lambda c: c["score"]) if scored else None


# -- turning a candidate into changes --------------------------------------------------------------------------------------
def parse_date(text):
    """'1965-08-06', '1965-08' or '1965' -> datetime at UTC midnight (month/day default to January 1st), or None."""
    m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", text or "")
    if not m or int(m.group(1)) < 1000:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1), tzinfo=timezone.utc)
    except ValueError:
        return None


def want_for(book, kind, file_tags=None):
    """What we know about a record, for searching. Music: title/artist/album/track from the record, length and ids from the file.
    Audiobooks: the book title is the album (chapter files) or the title; the author is the album artist or first author."""
    file_tags = file_tags or {}
    authors = book.get("authors") or []
    first = authors[0] if authors and authors[0].lower() != "unknown" else ""
    title = book.get("title", "")
    want = {
        "title": title,
        "artist": first,
        "album": book.get("series", ""),
        "track": None,
        "year": None,
        "duration": file_tags.get("duration"),
    }
    index = book.get("series_index")
    if index is not None and float(index).is_integer() and book.get("series"):
        want["track"] = int(index)
    if file_tags.get("year"):
        want["year"] = file_tags["year"]
    if kind == "music":
        want["ids"] = [
            i for i in (file_tags.get("mb_recording"), (book.get("identifiers") or {}).get("mb_recording")) if i
        ]
    else:
        # Chapter files carry the book's title as their album (the record's series); whole-book files carry it as the title.
        if kind == "audiobook" and book.get("series"):
            want["title"] = book["series"]
        want["title"] = strip_noise(
            re.sub(r"^\s*(chapter|part|track|disc)\s*\d+\s*[-:.]?\s*", "", want["title"], flags=re.I)
        )
        if file_tags.get("album_artist") and not first:
            want["artist"] = file_tags["album_artist"]
    return want


def plan_changes(book, kind, cand, fields):
    """{field: (old, new)} for the fields that differ. Values are JSON-safe so they can be stored for undo.

    title: str; authors: [str]; album: [series, series_index]; date: ISO string or None; tags: full new tag list;
    identifiers: full new dict; cover: (None, [candidate image URLs])."""
    changes = {}
    whole_book = kind == "book" or (
        kind == "audiobook" and not book.get("series")
    )  # a chapter file's title is not the book's
    if "title" in fields and cand["title"] and (kind == "music" or whole_book) and cand["title"] != book.get("title"):
        changes["title"] = (book.get("title", ""), cand["title"])
    if (
        "authors" in fields
        and cand["authors"]
        and sorted(map(norm, cand["authors"])) != sorted(map(norm, book.get("authors") or []))
    ):
        changes["authors"] = (list(book.get("authors") or []), list(cand["authors"]))
    if "album" in fields and kind == "music" and cand.get("album"):
        old = [book.get("series") or "", book.get("series_index")]
        new = [cand["album"], float(cand["track"]) if cand.get("track") else book.get("series_index")]
        if old != new and (old[0] != new[0] or (new[1] is not None and old[1] != new[1])):
            changes["album"] = (old, new)
    new_date = parse_date(cand.get("date")) if "date" in fields else None
    if new_date:
        old_date = book.get("pubdate")  # ISO string or None
        year_only = len(cand["date"]) < 10
        same = bool(old_date) and (
            old_date[:4] == str(new_date.year) if year_only else old_date[:10] == new_date.isoformat()[:10]
        )
        if not same:  # a year-only date never replaces a full date from the same year
            changes["date"] = (old_date, new_date.isoformat())
    wanted = [KIND_TAGS[kind]] if "tags" in fields and kind in KIND_TAGS else []
    if "subjects" in fields:
        wanted += list(cand.get("subjects") or [])
    have = list(book.get("tags") or [])
    add = [t for t in dict.fromkeys(wanted) if t.lower() not in {h.lower() for h in have}]
    if add:
        changes["tags"] = (have, have + add)
    if "identifiers" in fields:
        ids = dict(book.get("identifiers") or {})
        new = dict(ids)
        if cand["source"] == "musicbrainz":
            for key, value in (("mb_recording", cand.get("recording_id")), ("mb_release", cand.get("release_id"))):
                if value:
                    new[key] = value
        else:
            if (
                cand.get("olid") and "olid" not in ids
            ):  # the edition whose cover is offered; no ISBN: a search cannot tell which edition you own
                new["olid"] = cand["olid"]
        if new != ids:
            changes["identifiers"] = (ids, new)
    if (
        "cover" in fields and cand.get("cover_urls") and not book.get("has_cover")
    ):  # an existing cover is never replaced
        changes["cover"] = (None, cand["cover_urls"])
    return changes


# -- the whole run ----------------------------------------------------------------------------------------------------------
def describe(book):
    authors = ", ".join(book.get("authors") or []) or "unknown"
    album = f" / {book['series']}" if book.get("series") else ""
    return f"{book.get('title') or '(no title)'} — {authors}{album}"


def plan_matches(books, http, options, cancelled=lambda: False, on_event=lambda kind, **info: None):
    """books: record dicts (id, title, authors, series, series_index, tags, formats, identifiers, pubdate, publisher,
    has_cover, path). options: kind ('auto'|'music'|'audiobook'|'book'), fields, min_score, acoustid_key, fpcalc, fingerprint.

    Returns (suggestions, skipped). A suggestion: {id, title, kind, score, candidate, changes, big, ambiguous}. skipped: [text]."""
    fields = set(options.get("fields", DEFAULT_FIELDS))
    min_score = options.get("min_score", MIN_SCORE)
    suggestions, skipped = [], []
    for done, book in enumerate(books, 1):
        if cancelled():
            break
        name = describe(book)
        on_event("book", book=book, name=name, done=done, total=len(books))
        try:
            kind = classify(book, options.get("kind", "auto"))
            file_tags = {}
            if kind != "book" and book.get("path"):
                file_tags = audio.read_tags(book["path"])
            want = want_for(book, kind, file_tags)
            if kind == "music":
                cand = _match_music(want, book, http, options, on_event, name)
            else:
                cand = _match_book(want, http, on_event, name)
        except sources.Cancelled:
            break
        except sources.HttpStatus as exc:
            if exc.code in PER_BOOK_STATUS:  # this one lookup was refused; the rest of the run is fine
                skipped.append(f"{name}: {exc}")
                on_event("error", name=name, text=str(exc))
                continue
            skipped.append(f'Stopped at "{name}": {exc}')
            on_event("stopped", text=str(exc))
            break
        except sources.ServiceError as exc:  # no network, bad AcoustID key...: more requests would fail the same way
            skipped.append(f'Stopped at "{name}": {exc}')
            on_event("stopped", text=str(exc))
            break
        if cand is None or cand["score"] < min_score:
            on_event("nomatch", name=name, best=cand["score"] if cand else None)
            continue
        changes = plan_changes(book, kind, cand, fields)
        big = bool(changes.get("title")) and similarity(book.get("title", ""), cand["title"]) < MIN_SIMILAR_TITLE
        on_event("match", name=name, candidate=cand, changes=changes, media=kind)
        if changes:
            suggestions.append(
                {
                    "id": book["id"],
                    "title": book.get("title", ""),
                    "kind": kind,
                    "score": cand["score"],
                    "candidate": cand,
                    "changes": changes,
                    "big": big,
                    "ambiguous": bool(cand.get("ambiguous")),
                }
            )
    return suggestions, skipped


def _match_music(want, book, http, options, on_event, name):
    recs, acoustid_scores = {}, {}
    key, fpcalc = options.get("acoustid_key", ""), options.get("fpcalc", "")
    if options.get("fingerprint") and key and fpcalc and book.get("path"):
        try:
            on_event("fingerprint", name=name)
            duration, fp = sources.fingerprint(book["path"], fpcalc)
            want = dict(want, duration=want.get("duration") or duration)
            for score, ids in sources.acoustid_lookup(http, key, duration, fp)[:2]:
                for rid in ids[:3]:
                    if score >= MIN_FINGERPRINT:
                        acoustid_scores.setdefault(rid, score)
        except sources.FingerprintError as exc:
            on_event("notice", text=f"{name}: fingerprint not used ({exc})")
    exact = tuple(want.get("ids") or ())

    def add(found):
        for rec in found:
            recs.setdefault(rec.get("id"), rec)

    for rid in list(dict.fromkeys(list(exact) + list(acoustid_scores)))[:4]:
        add(sources.mb_search_recordings(http, recording_id=rid, limit=1))
    if want.get("title"):
        on_event("searching", name=name, query=f"{want['title']} / {want.get('artist', '')} / {want.get('album', '')}")
        add(sources.mb_search_recordings(http, want["title"], want.get("artist", ""), want.get("album", "")))
        cand = best_music_candidate(recs.values(), want, acoustid_scores, exact)
        if want.get("album") and (cand is None or cand["score"] < TICK_SCORE):
            # the album name may be spelled differently on MusicBrainz, or the track may be on other releases only
            add(sources.mb_search_recordings(http, want["title"], want.get("artist", "")))
    return best_music_candidate(recs.values(), want, acoustid_scores, exact)


def _match_book(want, http, on_event, name):
    if not want.get("title"):
        return None
    on_event("searching", name=name, query=f"{want['title']} / {want.get('artist', '')}")
    return best_book_candidate(sources.ol_search(http, want["title"], want.get("artist", "")), want)
