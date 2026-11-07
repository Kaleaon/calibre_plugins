"""Inkbunny and SoFurry parsing and download logic. Pure standard library so it can be tested outside Calibre.

Two very different sites behind one interface:

- Inkbunny (inkbunny.net) has an official JSON API. A session id comes from api_login.php (the "guest" account
  works for public, general-rated writing; a real account with API access enabled sees everything that account
  can see). api_search.php finds submission ids by author, pool or keyword; api_submissions.php returns the
  metadata and file list for a batch of ids. Writing submissions carry their text as uploaded files (.txt/.html).
- SoFurry (sofurry.com, the current site) is a Remix app. A story page's data is at <story url>.data in
  turbo-stream encoding; its content files (txt/html) are fetched from short-lived display URLs. Folders and
  galleries are listed by the JSON endpoint /api/profile.

A "work" here is one story. Works that arrive from a collection (an Inkbunny pool, a SoFurry folder) carry a
group id; the action compiles each group into a single EPUB, which is how a pool or folder becomes one book.
"""

import html
import json
import re
from urllib.parse import parse_qs, urlencode, urlparse

try:  # inside Calibre the shared module ships in the plugin package
    from .storykit import (  # noqa: F401
        Cancelled,
        Fetcher,
        RateLimited,
        clean_fragment,
        escape,
        parse_chapter_range,
        strip_tags,
    )
    from .storykit import build_epub as _build_epub
except ImportError:  # tests load this file directly with common/ on sys.path
    from storykit import (  # noqa: F401
        Cancelled,
        Fetcher,
        RateLimited,
        clean_fragment,
        escape,
        parse_chapter_range,
        strip_tags,
    )
    from storykit import build_epub as _build_epub

INKBUNNY = "inkbunny"
SOFURRY = "sofurry"
PUBLISHERS = {INKBUNNY: "Inkbunny", SOFURRY: "SoFurry"}
IB_BASE = "https://inkbunny.net"
SF_BASE = "https://sofurry.com"
WRITING_TYPE_ID = (
    "12"  # the API's submission type id for Writing - Document (poetry uses it too)
)


def build_epub(
    stories,
    title=None,
    author=None,
    publisher=None,
    tags=None,
    summary=None,
    preserve_story_order=False,
):
    """storykit build_epub with the publisher taken from the stories when they all come from one site."""
    if publisher is None:
        sites = {s.get("site") for s in stories}
        publisher = (
            PUBLISHERS.get(sites.pop(), "Inkbunny / SoFurry")
            if len(sites) == 1
            else "Inkbunny / SoFurry"
        )
    return _build_epub(
        stories,
        title=title,
        author=author,
        publisher=publisher,
        tags=tags,
        summary=summary,
        preserve_story_order=preserve_story_order,
    )


# ---------------------------------------------------------------- shared text handling


def looks_like_html(text):
    return bool(re.search(r"<\s*(?:html|body|p|br|div)\b", (text or "")[:4000], re.I))


def text_to_html(text):
    """Unwrap hard-wrapped plain text into paragraphs; keep verse-like blocks and scene-break rules."""
    out = []
    rule = re.compile(r"^\s*(?:[_\-=~#*]\s*){3,}$")
    for block in re.split(r"\n\s*\n", (text or "").replace("\t", "    ").strip("\n")):
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if all(rule.match(ln) for ln in lines):
            out.append("<hr/>")
        elif len(lines) >= 3 and sum(len(ln.strip()) for ln in lines) / len(lines) < 45:
            out.append(
                "<p>" + "<br/>".join(escape(ln.strip()) for ln in lines) + "</p>"
            )
        else:
            out.append("<p>" + escape(" ".join(ln.strip() for ln in lines)) + "</p>")
    return "".join(out)


def file_to_html(raw):
    """One uploaded story file (plain text or HTML) as a cleaned XHTML fragment."""
    if looks_like_html(raw):
        m = re.search(r"<body[^>]*>(.*)</body>", raw, re.S | re.I)
        return clean_fragment(m.group(1) if m else raw)
    return text_to_html(raw)


def _filter_range(sections, chapter_range):
    if chapter_range is None:
        return sections
    indices = (
        parse_chapter_range(chapter_range, len(sections))
        if isinstance(chapter_range, str)
        else set(chapter_range)
    )
    kept = [s for i, s in enumerate(sections) if i in indices]
    if not kept:
        raise ValueError(
            f'No chapters match range "{chapter_range}" (story has {len(sections)} chapter(s))'
        )
    return kept


def _date_only(value):
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------- addresses


def classify(line):
    """One pasted line -> {'site', 'kind', 'key', ...} or None.

    kinds: 'story' (key = submission id), 'pool' (Inkbunny, key = pool id), 'author' (key = username/handle),
    'folder' (SoFurry, key = folder id, plus 'handle'), 'legacy' (an old www.sofurry.com/view/<id> address)."""
    line = (line or "").strip()
    if not line:
        return None
    if "://" not in line and re.match(
        r"^[\w.-]*(?:inkbunny\.net|sofurry\.com)(?:[/:?#]|$)", line
    ):
        line = "https://" + line
    parsed = urlparse(line)
    host = parsed.netloc.lower().split(":")[0]
    path = parsed.path
    query = parse_qs(parsed.query)

    if host == "inkbunny.net" or host.endswith(".inkbunny.net"):
        m = re.match(r"^/s/(\d+)", path)
        if m:
            return {"site": INKBUNNY, "kind": "story", "key": m.group(1)}
        if path.rsplit("/", 1)[-1] in (
            "submissionview.php",
            "submissionviewdetail.php",
        ) and query.get("id"):
            return {"site": INKBUNNY, "kind": "story", "key": query["id"][0]}
        last = path.rsplit("/", 1)[-1]
        if query.get("pool_id") and (
            last in ("poolview_process.php", "pools.php")
            or (last == "submissionsviewall.php" and query.get("mode") == ["pool"])
        ):
            # poolview_process.php is the current form (verified live Nov 3, 2026; it resolves to
            # submissionsviewall.php?mode=pool&pool_id=…); pools.php is the dead older spelling, still accepted
            # so old bookmarks paste fine — the id goes to the API either way.
            return {"site": INKBUNNY, "kind": "pool", "key": query["pool_id"][0]}
        m = re.match(r"^/(?:gallery|scraps)/([^/]+)/?$", path)
        if m:
            return {"site": INKBUNNY, "kind": "author", "key": m.group(1)}
        m = re.match(r"^/([^/]+)/?$", path)
        if m and m.group(1) not in (
            "login.php",
            "index.php",
            "account.php",
            "search.php",
            "submissions.php",
            "pools.php",
            "poolview_process.php",
            "submissionsviewall.php",
            "poolslist.php",
            "journals.php",
            "streaming.php",
            "forum",
        ):
            return {"site": INKBUNNY, "kind": "author", "key": m.group(1)}
        return None

    if host == "sofurry.com" or host.endswith(".sofurry.com"):
        m = re.match(r"^/s/([A-Za-z0-9_-]+)$", path.rstrip("/"))
        if m:
            return {"site": SOFURRY, "kind": "story", "key": m.group(1)}
        m = re.match(r"^/u/([^/]+)/gallery/?$", path)
        if m and query.get("folder"):
            return {
                "site": SOFURRY,
                "kind": "folder",
                "key": query["folder"][0],
                "handle": m.group(1),
            }
        m = re.match(r"^/u/([^/]+)(?:/.*)?$", path)
        if m:
            return {"site": SOFURRY, "kind": "author", "key": m.group(1)}
        m = re.match(r"^/view/(\d+)", path)
        if m:
            return {"site": SOFURRY, "kind": "legacy", "key": m.group(1)}
        if (
            host != "sofurry.com" and host != "www.sofurry.com"
        ):  # <handle>.sofurry.com is a member's page
            return {"site": SOFURRY, "kind": "author", "key": host.split(".")[0]}
        return None
    return None


def story_url(site, key):
    return f"{IB_BASE}/s/{key}" if site == INKBUNNY else f"{SF_BASE}/s/{key}"


def ref_for(
    site,
    key,
    title="",
    author="",
    date="",
    detail="",
    group="",
    group_id="",
    group_index=None,
    tags=(),
):
    return {
        "site": site,
        "id": f"{site}:{key}",
        "key": key,
        "title": title or key,
        "author": author,
        "date": date,
        "detail": detail,
        "group": group,
        "group_id": group_id,
        "group_index": group_index,
        "tags": [str(t) for t in tags if t],
    }


# ---------------------------------------------------------------- Inkbunny API


class InkbunnyLoginError(IOError):
    pass


def ib_login(fetcher, username="", password=""):
    """A session id from the official API. No credentials = the guest account (public, general-rated only).
    A real account must have "Enable API access" ticked in its Inkbunny account settings."""
    user = username.strip() or "guest"
    params = {"username": user, "output_mode": "json"}
    if username.strip():  # a guest login sends no password at all
        params["password"] = password
    url = IB_BASE + "/api_login.php?" + urlencode(params)
    try:
        data = json.loads(fetcher.get(url))
    except ValueError as exc:
        raise InkbunnyLoginError(
            "Inkbunny did not answer the login request in a way we could read."
        ) from exc
    if not data.get("sid"):
        raise InkbunnyLoginError(
            f"Inkbunny refused the login for {user!r} "
            f"({data.get('error_message') or 'error ' + str(data.get('error_code', '?'))}). "
            "For your own account, check the password and that API access is enabled in "
            "your Inkbunny account settings."
        )
    return data["sid"]


def ib_session(fetcher, username="", password=""):
    """The session id cached on the fetcher, logging in on first use."""
    sid = getattr(fetcher, "ib_sid", None)
    if sid:
        return sid
    sid = ib_login(fetcher, username, password)
    try:
        fetcher.ib_sid = sid
    except AttributeError:
        pass
    return sid


def _ib_api(fetcher, sid, script, params):
    url = f"{IB_BASE}/{script}?" + urlencode(
        {**params, "sid": sid, "output_mode": "json"}
    )
    try:
        data = json.loads(fetcher.get(url))
    except ValueError as exc:
        raise OSError(
            f"Inkbunny answered {script} in a way we could not read."
        ) from exc
    if str(data.get("error_code", "0")) not in ("0", "None", ""):
        raise OSError(
            f"Inkbunny: {data.get('error_message') or 'error ' + str(data.get('error_code'))}"
        )
    return data


def ib_search(fetcher, sid, progress=lambda msg: None, **params):
    """All submission summary dicts matching a search, in the search's own order (pools keep pool order).

    The search response itself carries the summaries (title, author, date, type, rating, primary file);
    full text and file lists need ib_details."""
    out, page, seen_ids = [], 1, set()
    while True:
        data = _ib_api(
            fetcher,
            sid,
            "api_search.php",
            {
                **params,
                "page": page,
                "submissions_per_page": 100,
                "scraps": "both",
                "orderby": params.get("orderby", "create_datetime"),
            },
        )
        batch = [
            s for s in data.get("submissions") or [] if str(s.get("hidden")) != "t"
        ]
        out += batch
        pages = int(data.get("pages_count") or 1)
        progress(f"Inkbunny search: page {page} of {pages} ({len(out)} submissions)")
        if page >= pages or not batch:
            return out
        # Safety guards (v1.0.1): a misbehaving search must not loop forever and hang calibre —
        # stop on a page that adds no new submissions, and never read more than 200 pages.
        new_ids = {str(s.get("submission_id")) for s in batch} - seen_ids
        seen_ids |= {str(s.get("submission_id")) for s in batch}
        if not new_ids or page >= 200:
            return out
        page += 1


def ib_details(fetcher, sid, ids, progress=lambda msg: None):
    """Submission detail dicts for ids (batched 100 at a time), returned in the order the ids were given."""
    out = {}
    ids = list(dict.fromkeys(str(i) for i in ids))
    for start in range(0, len(ids), 100):
        batch = ids[start : start + 100]
        progress(
            f"Reading Inkbunny submission details: {start + len(batch)} of {len(ids)}"
        )
        data = _ib_api(
            fetcher,
            sid,
            "api_submissions.php",
            {
                "submission_ids": ",".join(batch),
                "show_description": "yes",
                "show_description_bbcode_parsed": "yes",
                "show_writing": "yes",
                "show_writing_bbcode_parsed": "yes",
                "show_pools": "yes",
            },
        )
        for sub in data.get("submissions") or []:
            out[str(sub.get("submission_id"))] = sub
    return [out[i] for i in ids if i in out]


def _ib_is_writing(sub):
    return str(sub.get("submission_type_id") or "") == WRITING_TYPE_ID or (
        sub.get("type_name") or ""
    ).startswith("Writing")


def _ib_ref(sub, group="", group_id="", group_index=None):
    return ref_for(
        INKBUNNY,
        sub.get("submission_id"),
        title=html.unescape(sub.get("title") or ""),
        author=sub.get("username") or "",
        date=_date_only(sub.get("create_datetime")),
        detail=sub.get("type_name") or "",
        group=group,
        group_id=group_id,
        group_index=group_index,
    )


def ib_expand_author(fetcher, sid, username, progress):
    subs = ib_search(fetcher, sid, progress, username=username, type=WRITING_TYPE_ID)
    refs = [_ib_ref(sub) for sub in subs if _ib_is_writing(sub)]
    refs.sort(key=lambda r: (r["date"], r["title"].casefold()))
    return refs


def ib_expand_pool(fetcher, sid, pool_id, progress):
    subs = [
        s
        for s in ib_search(
            fetcher, sid, progress, pool_id=pool_id, orderby="pool_order"
        )
        if _ib_is_writing(s)
    ]
    name = ""
    if subs:  # the pool's name is only in the details (show_pools), so read the first member's
        for sub in ib_details(fetcher, sid, [subs[0].get("submission_id")], progress):
            for pool in sub.get("pools") or []:
                if str(pool.get("pool_id")) == str(pool_id):
                    name = pool.get("name") or name
    group = name or f"Inkbunny pool {pool_id}"
    return [
        _ib_ref(sub, group=group, group_id=f"pool/{pool_id}", group_index=n)
        for n, sub in enumerate(subs, 1)
    ]


def ib_expand_search(fetcher, sid, text, progress):
    subs = ib_search(
        fetcher,
        sid,
        progress,
        text=text,
        keywords="yes",
        title="yes",
        description="yes",
        type=WRITING_TYPE_ID,
    )
    return [_ib_ref(sub) for sub in subs if _ib_is_writing(sub)]


_TEXT_MIMES = {"text/plain", "text/html", "text/markdown"}
_TEXT_EXTS = (".txt", ".html", ".htm", ".md")


def _bbcode_text(text):
    """BBCode roughly to plain text: formatting tags dropped, link targets kept as their link text."""
    text = re.sub(r"\[url[^\]]*\](.*?)\[/url\]", r"\1", text or "", flags=re.S)
    text = re.sub(r"\[.*?\]", "", text)
    return html.unescape(text)


def _bbcode_to_html(text):
    return text_to_html(_bbcode_text(text)) if (text or "").strip() else ""


def _ib_description_html(sub):
    """The submission description as HTML: the API's parsed form when present, else BBCode roughly converted."""
    parsed = sub.get("description_bbcode_parsed") or ""
    if parsed and "<" in parsed:
        return clean_fragment(parsed)
    return _bbcode_to_html(sub.get("description") or "")


def ib_fetch_story(fetcher, sid, ref, progress=lambda msg: None, chapter_range=None):
    subs = ib_details(fetcher, sid, [ref["key"]], progress)
    if not subs:
        raise OSError(
            f"Inkbunny has no submission {ref['key']} (deleted, or not visible to this login)"
        )
    sub = subs[0]
    if not _ib_is_writing(sub):
        raise ValueError(
            f"Not a writing submission on Inkbunny (it is {sub.get('type_name') or 'an unknown type'}); "
            "only Writing - Document submissions carry story text"
        )
    # The story text, in order of preference. The API's writing field is auto-filled from the first .doc/.txt
    # file, so for a single-file submission it IS the story (already BBCode-parsed when requested). A multi-file
    # submission is a multi-part work: each text file is a chapter, in the site's file order.
    files = [
        f
        for f in sub.get("files") or []
        if (f.get("mimetype") or "") in _TEXT_MIMES
        or (f.get("file_name") or "").lower().endswith(_TEXT_EXTS)
    ]
    files.sort(key=lambda f: int(f.get("submission_file_order") or 0))
    sections = []
    parsed_writing = sub.get("writing_bbcode_parsed") or ""
    if len(files) >= 2:
        for n, f in enumerate(files, 1):
            url = f.get("file_url_full") or f.get("file_url_screen")
            if not url:
                continue
            progress(f"{sub.get('title')}: file {n} of {len(files)}")
            heading = re.sub(r"\.[^.]+$", "", f.get("file_name") or f"Part {n}")
            sections.append((heading, file_to_html(fetcher.get(url))))
    elif parsed_writing and len(strip_tags(parsed_writing)) >= 50:
        sections = [("", clean_fragment(parsed_writing))]
    elif files:
        url = files[0].get("file_url_full") or files[0].get("file_url_screen")
        if url:
            sections = [("", file_to_html(fetcher.get(url)))]
    if not sections and (sub.get("writing") or "").strip():
        body = _bbcode_to_html(sub["writing"])
        if len(strip_tags(body)) >= 50:
            sections = [("", body)]
    if not sections:
        body = _ib_description_html(sub)
        if (
            len(strip_tags(body)) >= 100
        ):  # some writing is posted in the description itself (poetry especially)
            sections = [("", body)]
    if not sections:
        raise ValueError(
            "This Inkbunny submission has no readable story text and no text file (.txt/.html); "
            "its file may be a .doc/.rtf/.pdf the API could not extract text from"
        )
    sections = _filter_range(sections, chapter_range)
    keywords = [
        html.unescape(k.get("keyword_name") or "") for k in sub.get("keywords") or []
    ]
    story = {
        "id": ref["id"],
        "site": INKBUNNY,
        "key": ref["key"],
        "url": story_url(INKBUNNY, ref["key"]),
        "title": html.unescape(sub.get("title") or ref["title"]),
        "author": sub.get("username") or ref["author"],
        "sections": sections,
        "tags": [k for k in keywords if k],
        "categories": [c for c in (sub.get("type_name"), sub.get("rating_name")) if c],
        "summary": strip_tags(sub.get("description") or ""),
        "publisher": PUBLISHERS[INKBUNNY],
        "pubdate": _date_only(sub.get("create_datetime")),
    }
    if ref.get("group"):
        story["series"], story["series_index"] = (
            ref["group"],
            float(ref.get("group_index") or 1),
        )
        story["group_id"] = ref.get("group_id") or ""
    return story


# ---------------------------------------------------------------- SoFurry: turbo-stream data


def decode_turbo(payload):
    """Decode a Remix turbo-stream body (the format of sofurry.com's .data endpoints) into plain Python data.

    The body is a JSON table: slot 0 is the root value, every other value lives in its own slot, objects are
    {"_<key slot>": <value slot>}, arrays are lists of slots, and a list whose first item is a string is a
    tagged value (date, URL, set, map, ...). Negative slots are fixed literals (null, undefined, NaN, ...)."""
    table = json.loads(payload.split("\n", 1)[0])
    sentinels = {
        -1: None,
        -2: float("nan"),
        -3: float("-inf"),
        -4: -0.0,
        -5: None,
        -6: float("inf"),
        -7: None,
    }
    done = {}

    def resolve(slot):
        if slot in sentinels:
            return sentinels[slot]
        if slot in done:
            return done[slot]
        entry = table[slot]
        if not isinstance(entry, (list, dict)):
            done[slot] = entry
            return entry
        if isinstance(entry, dict):
            obj = done[slot] = {}
            for key_ref, value_ref in entry.items():
                obj[resolve(int(key_ref[1:]))] = resolve(value_ref)
            return obj
        if entry and isinstance(entry[0], str):
            return resolve_tagged(slot, entry)
        array = done[slot] = []
        array.extend(resolve(item) for item in entry)
        return array

    def resolve_tagged(slot, entry):
        tag, args = entry[0], entry[1:]
        if tag in ("D", "U", "Y"):  # date / URL / symbol: kept as their string
            done[slot] = args[0]
        elif tag == "B":
            done[slot] = int(args[0])
        elif tag == "R":
            done[slot] = {"pattern": args[0], "flags": args[1] if len(args) > 1 else ""}
        elif tag == "S":
            out = done[slot] = []
            out.extend(resolve(ref) for ref in args)
        elif tag == "M":
            out = done[slot] = {}
            for i in range(0, len(args) - 1, 2):
                out[resolve(args[i])] = resolve(args[i + 1])
        elif tag == "N":
            out = done[slot] = {}
            for key_ref, value_ref in args[0].items():
                out[resolve(int(key_ref[1:]))] = resolve(value_ref)
        elif tag == "Z":
            done[slot] = resolve(args[0])
        else:
            raise ValueError(f"unknown turbo-stream tag: {tag!r}")
        return done[slot]

    return resolve(0)


def _sf_data(fetcher, url):
    try:
        return decode_turbo(fetcher.get(url))
    except ValueError as exc:
        raise OSError(f"SoFurry answered {url} in a way we could not read.") from exc


def _sf_submission(fetcher, key):
    data = _sf_data(fetcher, f"{SF_BASE}/s/{key}.data")
    try:
        return data["routes/submission.$id"]["data"]["submission"]
    except (KeyError, TypeError) as exc:
        raise OSError(
            f"SoFurry has no readable story at {story_url(SOFURRY, key)} "
            "(deleted, or login-only and this plugin has no SoFurry login yet)"
        ) from exc


def _sf_rating_name(rating):
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return ""
    return "Adult" if rating >= 20 else "Mature" if rating >= 10 else "Clean"


def _sf_tags(sub):
    """Tag names from a .data submission. The endpoint gives tag *ids* (numbers) there, not names; those are
    skipped rather than turned into meaningless number tags. Names come from the profile listing instead,
    whose rows carry tags as strings, and arrive on the reference."""
    tags = []
    for tag in sub.get("tags") or []:
        name = (
            tag.get("name")
            if isinstance(tag, dict)
            else tag
            if isinstance(tag, str)
            else ""
        )
        if name:
            tags.append(str(name))
    return tags


def sf_fetch_story(fetcher, ref, progress=lambda msg: None, chapter_range=None):
    sub = _sf_submission(fetcher, ref["key"])
    chapters = [
        c
        for c in sub.get("content") or []
        if (c.get("extension") or "").lower() in ("txt", "html", "htm")
    ]
    if not chapters:
        kinds = sorted({(c.get("extension") or "?") for c in sub.get("content") or []})
        raise ValueError(
            "Not a readable story on SoFurry"
            + (
                f" (its files are: {', '.join(kinds)})"
                if kinds
                else " (it has no content files)"
            )
        )
    sections = []
    for n, chapter in enumerate(chapters, 1):
        progress(f"{sub.get('title')}: chapter {n} of {len(chapters)}")
        url = chapter.get("displayUrl")
        if not url:
            raise OSError(
                "SoFurry gave a chapter with no file address (the address may have expired; try again)"
            )
        heading = chapter.get("title") or "" if len(chapters) > 1 else ""
        sections.append((heading, file_to_html(fetcher.get(url))))
    sections = _filter_range(sections, chapter_range)
    author = sub.get("author") or {}
    story = {
        "id": ref["id"],
        "site": SOFURRY,
        "key": ref["key"],
        "url": story_url(SOFURRY, ref["key"]),
        "title": sub.get("title") or ref["title"],
        "author": author.get("username") or ref["author"] or "Unknown",
        "sections": sections,
        "tags": list(dict.fromkeys((ref.get("tags") or []) + _sf_tags(sub))),
        "categories": [c for c in (_sf_rating_name(sub.get("rating")),) if c],
        "summary": strip_tags(sub.get("description") or ""),
        "publisher": PUBLISHERS[SOFURRY],
        "pubdate": _date_only(sub.get("publishedAt")),
    }
    if ref.get("group"):
        story["series"], story["series_index"] = (
            ref["group"],
            float(ref.get("group_index") or 1),
        )
        story["group_id"] = ref.get("group_id") or ""
    return story


def _sf_profile(fetcher, handle, tab, folder_id=None, page=0):
    params = {"handle": handle, "tab": tab, "page": page, "per_page": 100}
    if folder_id:
        params["folder_id"] = folder_id
    try:
        return json.loads(fetcher.get(f"{SF_BASE}/api/profile?" + urlencode(params)))
    except ValueError as exc:
        raise OSError(
            "SoFurry answered its profile listing in a way we could not read."
        ) from exc


def _sf_listing(fetcher, handle, tab, folder_id, progress):
    """Every submission row of one profile listing, oldest first."""
    rows, page, seen_ids = [], 0, set()
    while True:
        data = _sf_profile(fetcher, handle, tab, folder_id, page)
        submissions = data.get("submissions") or {}
        batch = submissions.get("data") or []
        rows += batch
        progress(f"SoFurry listing: {len(rows)} submissions so far")
        if not submissions.get("hasNextPage") or not batch:
            break
        # Safety guards (v1.0.1): stop on a page that adds no new submissions, and cap the listing
        # at 200 pages, so a misbehaving listing cannot loop forever and hang calibre.
        new_ids = {str(r.get("id")) for r in batch} - seen_ids
        seen_ids |= {str(r.get("id")) for r in batch}
        if not new_ids or page >= 200:
            break
        page += 1
    rows.reverse()  # the site lists newest first; a compiled book reads oldest first
    return rows, data.get("folder") or {}


def sf_expand_folder(fetcher, handle, folder_id, progress):
    data = _sf_data(fetcher, f"{SF_BASE}/u/{handle}/gallery.data?folder={folder_id}")
    try:
        profile = data["profile"]["data"]["profile"]
        handle = profile.get("handle") or handle
    except (KeyError, TypeError):
        pass
    rows, folder = _sf_listing(fetcher, handle, "folder", folder_id, progress)
    group = folder.get("name") or f"SoFurry folder {folder_id}"
    refs = []
    for row in rows:
        if row.get("type") != "story":
            continue
        refs.append(
            ref_for(
                SOFURRY,
                row.get("id"),
                title=row.get("title") or "",
                date=_date_only(row.get("publishedAt")),
                detail="story",
                group=group,
                group_id=f"folder/{folder_id}",
                group_index=len(refs) + 1,
                tags=row.get("tags") or (),
            )
        )
    return refs


def sf_expand_author(fetcher, handle, progress):
    """All stories on a member's gallery. The listing tab name is probed because the site has used several."""
    last_error = None
    for tab in ("gallery", "submissions", "stories"):
        try:
            rows, _ = _sf_listing(fetcher, handle, tab, None, progress)
        except OSError as exc:
            last_error = exc
            continue
        if rows:
            return [
                ref_for(
                    SOFURRY,
                    row.get("id"),
                    title=row.get("title") or "",
                    date=_date_only(row.get("publishedAt")),
                    detail="story",
                    tags=row.get("tags") or (),
                )
                for row in rows
                if row.get("type") == "story"
            ]
    if last_error:
        raise last_error
    return []


def sf_resolve_legacy(fetcher, numeric_id):
    """An old www.sofurry.com/view/<number> address for a real submission redirects to its current /s/<id>
    address (verified live); the .data form of the same address lands on the story's data, whose submission
    carries the current id. Returns the current id, or None when the old id does not resolve."""
    try:
        data = _sf_data(fetcher, f"{SF_BASE}/view/{numeric_id}.data")
        short = data["routes/submission.$id"]["data"]["submission"].get("id")
    except Exception:
        return None
    return str(short) if short else None


# ---------------------------------------------------------------- dispatch


def expand_targets(
    fetcher,
    lines,
    progress=lambda msg: None,
    inkbunny_username="",
    inkbunny_password="",
):
    """Resolve pasted addresses into story references. Returns (refs, errors).

    Inkbunny collections need an API session; the guest session is used unless credentials were given.
    SoFurry folder and author listings are public API reads."""
    refs, errors, seen = [], [], set()

    def add(new_refs):
        for r in new_refs:
            if r["id"] not in seen:
                seen.add(r["id"])
                refs.append(r)

    sid = None

    def session():
        nonlocal sid
        if sid is None:
            sid = ib_session(fetcher, inkbunny_username, inkbunny_password)
        return sid

    for line in lines:
        target = classify(line)
        if target is None:
            if line.strip():
                errors.append(
                    f"Not an Inkbunny or SoFurry story, pool, folder or author address: {line.strip()}"
                )
            continue
        try:
            if target["kind"] == "story":
                add([ref_for(target["site"], target["key"])])
            elif target["kind"] == "legacy":
                short = sf_resolve_legacy(fetcher, target["key"])
                if short:
                    add([ref_for(SOFURRY, short)])
                else:
                    errors.append(
                        f"{line.strip()}: this old www.sofurry.com/view/ address did not resolve to a current "
                        "story (the id may not be a real old submission); open it in a browser and paste "
                        "the sofurry.com/s/ address it lands on."
                    )
            elif target["site"] == INKBUNNY and target["kind"] == "pool":
                progress(f"Reading Inkbunny pool {target['key']}")
                add(ib_expand_pool(fetcher, session(), target["key"], progress))
            elif target["site"] == INKBUNNY and target["kind"] == "author":
                progress(f"Reading {target['key']}'s Inkbunny writing")
                add(ib_expand_author(fetcher, session(), target["key"], progress))
            elif target["site"] == SOFURRY and target["kind"] == "folder":
                progress(f"Reading SoFurry folder {target['key']}")
                add(
                    sf_expand_folder(fetcher, target["handle"], target["key"], progress)
                )
            elif target["site"] == SOFURRY and target["kind"] == "author":
                progress(f"Reading {target['key']}'s SoFurry stories")
                add(sf_expand_author(fetcher, target["key"], progress))
        except OSError as exc:
            errors.append(f"{line.strip()}: {exc}")
    return refs, errors


def fetch_story(
    fetcher,
    ref,
    progress=lambda msg: None,
    chapter_range=None,
    inkbunny_username="",
    inkbunny_password="",
):
    """Download one referenced work as a story dict for build_epub."""
    if ref["site"] == INKBUNNY:
        return ib_fetch_story(
            fetcher,
            ib_session(fetcher, inkbunny_username, inkbunny_password),
            ref,
            progress,
            chapter_range,
        )
    return sf_fetch_story(fetcher, ref, progress, chapter_range)


def download_refs(
    fetcher,
    refs,
    skip_ids=frozenset(),
    progress=lambda msg: None,
    chapter_range=None,
    inkbunny_username="",
    inkbunny_password="",
):
    """Download each reference in turn. Returns (stories, skipped, failures); a failure never stops the rest."""
    stories, failures, skipped = [], [], 0
    for n, ref in enumerate(refs, 1):
        if ref["id"] in skip_ids:
            skipped += 1
            continue
        progress(f"Downloading {n} of {len(refs)}: {ref['title']}")
        try:
            stories.append(
                fetch_story(
                    fetcher,
                    ref,
                    progress,
                    chapter_range,
                    inkbunny_username,
                    inkbunny_password,
                )
            )
        except Cancelled:
            raise
        except Exception as exc:
            failures.append(f"{ref['id']}: {exc}")
    return stories, skipped, failures


def compile_groups(stories):
    """Group downloaded stories into books: one book per collection (pool/folder), one per loose story.

    Returns [(group_id or '', [stories in reading order])]. Collection members keep their group order;
    loose stories keep download order. The Combine-everything option in the dialog bypasses this."""
    groups, order = {}, []
    for story in stories:
        key = story.get("group_id") or story["id"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(story)
    out = []
    for key in order:
        members = groups[key]
        if len(members) > 1 or key != members[0]["id"]:
            members = sorted(members, key=lambda s: s.get("series_index") or 0)
        out.append((key if key != members[0]["id"] else "", members))
    return out
