"""Nifty archive parsing and download logic. Pure standard library so it can be tested outside Calibre."""

import html
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

try:  # inside Calibre the shared module ships in the plugin package
    from .storykit import (  # noqa: F401
        Cancelled,
        Fetcher,
        RateLimited,
        clean_fragment,
        epub_builder,
        escape,
        sort_by_posting_order,
        strip_tags,
    )
except ImportError:  # tests load this file directly with common/ on sys.path
    from storykit import (  # noqa: F401
        Cancelled,
        Fetcher,
        RateLimited,
        clean_fragment,
        epub_builder,
        escape,
        sort_by_posting_order,
        strip_tags,
    )

SITE = "https://www.nifty.org/nifty/"
PUBLISHER = "Nifty"
SECTIONS = ["gay", "lesbian", "bisexual", "transgender"]
MAX_DEPTH = 6

build_epub = epub_builder(PUBLISHER)


# ---------------------------------------------------------------- addresses


def humanize(slug):
    return re.sub(r"[-_]+", " ", slug).strip().title()


def classify(line):
    """Return the archive path (relative to /nifty/, trailing slash = directory) or None."""
    line = line.strip()
    if not line:
        return None
    if "://" not in line and line.startswith(("nifty.org", "www.nifty.org")):
        line = "https://" + line
    parsed = urlparse(line)
    host = parsed.netloc.lower()
    if not (host == "nifty.org" or host.endswith(".nifty.org")) or not parsed.path.startswith("/nifty/"):
        return None
    path = parsed.path[len("/nifty/") :]
    if not path or ".." in path.split("/"):
        return None
    if path.endswith((".html", ".xml", ".txt")) and "/" not in path:
        return None  # site pages such as new.html or authorslist.html
    return path


def url_for(path):
    return SITE + path


# ---------------------------------------------------------------- listings

_ROW = re.compile(
    r"<(?:td|div)[^>]*>\s*(Dir|[\d.]+[KMG]?)\s*</(?:td|div)>\s*<(?:td|div)[^>]*>\s*([^<]*?)\s*</(?:td|div)>\s*"
    r'<(?:td|div)[^>]*>\s*<a\s+href="([^"#?/][^"]*)"',
    re.I,
)


def parse_listing(page):
    """Entries of a directory page as dicts: name, is_dir, size, date. Newest first, as the site orders them."""
    entries = []
    for m in _ROW.finditer(page):
        name = html.unescape(m.group(3))
        if name.startswith((".", "..")):
            continue
        is_dir = m.group(1).lower() == "dir" or name.endswith("/")
        entries.append({"name": name.rstrip("/"), "is_dir": is_dir, "size": m.group(1), "date": m.group(2)})
    return entries


def parse_categories(page, section):
    """Category directories linked from a section page, in page order."""
    seen = []
    for m in re.finditer(rf'href="/nifty/{re.escape(section)}/([^/"#?]+)/"', page):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


GENERIC_PREFIXES = {"chapter", "chap", "ch", "part", "pt", "story", "book", "episode", "ep", "p", "c"}


def strip_ext(name):
    return re.sub(r"(?i)\.(?:html?|txt)$", "", name)


def book_title(prefix, folder):
    """Display name for a file group; generic names such as 'chapter' borrow the folder's name."""
    return humanize(folder if prefix.lower() in GENERIC_PREFIXES and folder else prefix)


def split_chapter(name):
    m = re.match(r"^(.*?)[-_]?(\d+)$", strip_ext(name))
    if m and m.group(1):
        return m.group(1), int(m.group(2))
    return strip_ext(name), None


def group_files(entries):
    """Group chapter files (slug-1, slug-2, ...) into books. Returns [(prefix, [names in reading order])]."""
    groups = {}
    for e in entries:
        if e["is_dir"]:
            continue
        prefix, number = split_chapter(e["name"])
        groups.setdefault(prefix, []).append((-1 if number is None else number, e["name"]))
    return [(prefix, [n for _, n in sorted(items)]) for prefix, items in groups.items()]


def expand_dir(fetcher, path):
    """Story references found in a directory: file groups, plus unresolved sub-folders."""
    path = path.rstrip("/")
    entries = parse_listing(fetcher.get(url_for(path + "/")))
    refs = []
    for e in entries:
        if e["is_dir"]:
            refs.append(
                {
                    "id": f"{path}/{e['name']}",
                    "title": humanize(e["name"]),
                    "author": "",
                    "date": e["date"],
                    "dir": f"{path}/{e['name']}",
                    "files": None,
                    "detail": "series folder",
                }
            )
    dates = {e["name"]: e["date"] for e in entries}
    for prefix, names in group_files(entries):
        refs.append(
            {
                "id": f"{path}/{prefix}",
                "title": book_title(prefix, path.rsplit("/", 1)[-1]),
                "author": "",
                "date": dates[names[-1]],
                "dir": None,
                "files": [f"{path}/{n}" for n in names],
                "detail": f"{len(names)} chapter(s)" if len(names) > 1 else "single story",
            }
        )
    return refs


def resolve_ref(fetcher, ref, depth=0):
    """Turn an unresolved folder reference into file-group references (one level of sub-folders deep per call)."""
    if ref["files"] is not None:
        return [ref]
    if depth >= MAX_DEPTH:
        return []
    out = []
    for sub in expand_dir(fetcher, ref["dir"]):
        out.extend(resolve_ref(fetcher, sub, depth + 1))
    return out


def expand_targets(fetcher, lines, progress=lambda msg: None):
    """Resolve pasted Nifty addresses into story references (files, series folders or category contents)."""
    refs, errors = {}, []
    for line in lines:
        path = classify(line)
        if path is None:
            if line.strip():
                errors.append(f"Not a Nifty story or folder address: {line.strip()}")
            continue
        parts = path.strip("/").split("/")
        try:
            if len(parts) == 1:
                errors.append(f"{line.strip()}: a whole section is too broad; pick a category or story.")
            elif len(parts) == 2:
                progress(f"Reading category {path}")
                found = expand_dir(fetcher, path)
                if not found:
                    errors.append(f"No stories found at {line.strip()}")
                for r in found:
                    refs.setdefault(r["id"], r)
            elif path.endswith("/"):
                refs.setdefault(
                    path.rstrip("/"),
                    {
                        "id": path.rstrip("/"),
                        "title": humanize(parts[-1]),
                        "author": "",
                        "date": "",
                        "dir": path.rstrip("/"),
                        "files": None,
                        "detail": "series folder",
                    },
                )
            else:
                folder, name = path.rsplit("/", 1)
                prefix, number = split_chapter(name)
                if number is None:
                    r = {
                        "id": path,
                        "title": humanize(prefix),
                        "author": "",
                        "date": "",
                        "dir": None,
                        "files": [path],
                        "detail": "single story",
                    }
                else:  # a chapter link means the whole series
                    progress(f"Reading series {folder}")
                    names = dict(group_files(parse_listing(fetcher.get(url_for(folder + "/"))))).get(prefix) or [name]
                    r = {
                        "id": f"{folder}/{prefix}",
                        "title": book_title(prefix, folder.rsplit("/", 1)[-1]),
                        "author": "",
                        "date": "",
                        "dir": None,
                        "files": [f"{folder}/{n}" for n in names],
                        "detail": f"{len(names)} chapter(s)",
                    }
                refs.setdefault(r["id"], r)
        except OSError as exc:
            errors.append(str(exc))
    return list(refs.values()), errors


# ---------------------------------------------------------------- chapter text

HEADER = re.compile(r"^([A-Za-z][A-Za-z\-]*):[ \t]*(.*)$")
RULE = re.compile(r"^\s*(?:[_\-=~#*]\s*){3,}$")  # scene breaks: ____, ***, * * *, ---
EMAIL = re.compile(r"<[^<>@\s]+@[^<>\s]+>|\([^()@\s]+@[^()\s]+\)|\S+@\S+")


def split_headers(text):
    """Separate the mail-style header block (Date/From/Subject...) from the body."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    headers, i = {}, 0
    while i < len(lines) and lines[i].strip():
        m = HEADER.match(lines[i])
        if m:
            headers[m.group(1).lower()] = m.group(2).strip()
        elif not (lines[i][:1] in " \t" and headers):
            return {}, "\n".join(lines)  # not a header block
        i += 1
    if not headers.keys() & {"from", "subject", "date"}:
        return {}, "\n".join(lines)
    return headers, "\n".join(lines[i:]).strip("\n")


def author_name(value):
    name = EMAIL.sub("", value or "").strip(" \t\"'<>()")
    if name:
        return re.sub(r"\s+", " ", name)
    m = re.match(r"\s*<?([^@<\s]+)@", value or "")
    return m.group(1) if m else "Unknown"


def looks_like_html(text):
    return bool(re.search(r"<\s*(?:html|body|p|br|div)\b", text[:4000], re.I))


def html_body(text):
    m = re.search(r"<body[^>]*>(.*)</body>", text, re.S | re.I) or re.search(r"<body[^>]*>(.*)", text, re.S | re.I)
    return clean_fragment(m.group(1) if m else text)


def html_title(text):
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
    return strip_tags(m.group(1)) if m else ""


def text_to_html(text):
    """Unwrap hard-wrapped plain text into paragraphs; keep verse-like blocks and rules."""
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\t", "    ").strip("\n")):
        lines = [ln.rstrip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if all(RULE.match(ln) for ln in lines):
            out.append("<hr/>")
        elif len(lines) >= 3 and sum(len(ln.strip()) for ln in lines) / len(lines) < 45:
            out.append("<p>" + "<br/>".join(escape(ln.strip()) for ln in lines) + "</p>")  # poems, lists, signatures
        else:
            out.append("<p>" + escape(" ".join(ln.strip() for ln in lines)) + "</p>")
    return "".join(out)


NUMBER_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"


def clean_title(subject, fallback):
    """Strip chapter markers, category lists and truncated trailers from a Subject: line."""
    title = re.sub(
        r"(?i)[\s,:;\-\u2013(]*\b(?:chapter|chap|ch|part|pt|p|book|episode|ep)(?=[\s.\d])\.?\s*"
        rf"(?:\d+|[IVXLC]+\b|(?:{NUMBER_WORDS})\b).*$",
        "",
        subject or "",
    )
    title = re.sub(r"\s*\([^()]*$", "", title)  # truncated "(Lesbian/Domination, oral"
    title = re.sub(r"\s*\([^()]*[/,][^()]*\)\s*$", "", title)  # "(Gay/Adult/College)" category list
    title = re.sub(r"[\s#:;,\-]*\d+\s*$", "", title).strip(" \t:;,-")
    return title or fallback


def parse_chapter_range(range_str, total_chapters):
    """Convert range syntax (e.g. '1-5', '8', '10+', '5-') into 0-based chapter indices.

    Invalid or empty range strings default to including all chapters.
    Out-of-bound indices are safely ignored.
    """
    if total_chapters <= 0:
        return set()
    all_indices = set(range(total_chapters))
    if not range_str or not str(range_str).strip():
        return all_indices

    tokens = [t for t in re.split(r"[,;\s]+", str(range_str).strip()) if t]
    indices = set()
    has_valid_syntax = False

    for token in tokens:
        # Match '10+' or '10-'
        m = re.match(r"^(\d+)[+-]$", token)
        if m:
            start = int(m.group(1))
            has_valid_syntax = True
            for idx in range(start - 1, total_chapters):
                if 0 <= idx < total_chapters:
                    indices.add(idx)
            continue

        # Match '1-5'
        m = re.match(r"^(\d+)-(\d+)$", token)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            has_valid_syntax = True
            if start <= end:
                for idx in range(start - 1, end):
                    if 0 <= idx < total_chapters:
                        indices.add(idx)
            continue

        # Match '-5'
        m = re.match(r"^-(\d+)$", token)
        if m:
            end = int(m.group(1))
            has_valid_syntax = True
            for idx in range(0, end):
                if 0 <= idx < total_chapters:
                    indices.add(idx)
            continue

        # Match single chapter number '8'
        m = re.match(r"^(\d+)$", token)
        if m:
            num = int(m.group(1))
            has_valid_syntax = True
            idx = num - 1
            if 0 <= idx < total_chapters:
                indices.add(idx)
            continue

    if not has_valid_syntax:
        return all_indices

    return indices


def fetch_book(fetcher, ref, progress=lambda msg: None, chapter_range=None):
    """Download every chapter file of a resolved reference and return one story dict."""
    orig_files = ref["files"] or []
    total_chapters = len(orig_files)
    paths = orig_files
    if chapter_range is not None and paths:
        if isinstance(chapter_range, str):
            indices = parse_chapter_range(chapter_range, len(paths))
        else:
            indices = set(chapter_range)
        paths = [p for i, p in enumerate(paths) if i in indices]
    chapters, first_headers = [], {}
    for n, path in enumerate(paths, 1):
        progress(f"{ref['title']}: chapter {n} of {len(paths)}")
        raw = fetcher.get(url_for(path))
        if looks_like_html(raw):  # a few old files are HTML pages rather than mail-style text
            headers, converted = {"subject": html_title(raw)}, html_body(raw)
        else:
            headers, body = split_headers(raw)
            converted = text_to_html(body)
        if n == 1:
            first_headers = headers
        number = split_chapter(path.rsplit("/", 1)[-1])[1]
        label = "" if total_chapters == 1 else f"Chapter {number if number is not None else n}"
        chapters.append((label, converted or "<p>(empty)</p>"))
    folder_parts = ref["id"].split("/")
    tags = [humanize(p) for p in folder_parts[:-1]][:2]  # section and category
    pubdate = ""
    try:
        pubdate = parsedate_to_datetime(first_headers.get("date", "")).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    first_url = (
        url_for(paths[0])
        if paths and len(paths) == 1
        else (url_for(paths[0].rsplit("/", 1)[0] + "/") if paths else url_for(ref["id"] + "/"))
    )
    return {
        "id": ref["id"],
        "url": first_url,
        "title": clean_title(first_headers.get("subject", ""), ref["title"]),
        "author": author_name(first_headers.get("from", "")),
        "sections": chapters,
        "tags": tags,
        "categories": [],
        "summary": "",
        "publisher": PUBLISHER,
        "pubdate": pubdate,
    }


def download_ref(fetcher, ref, skip_ids=frozenset(), progress=lambda msg: None, chapter_range=None):
    """Resolve a reference and download its books. Returns (stories, skipped_count)."""
    stories, skipped = [], 0
    for resolved in resolve_ref(fetcher, ref):
        if resolved["id"] in skip_ids:
            skipped += 1
            continue
        stories.append(fetch_book(fetcher, resolved, progress, chapter_range=chapter_range))
    return stories, skipped


# ---------------------------------------------------------------- authors directory: stories spread over many folders

AUTHOR_PAGES = ("authors.html", "prolific.html")  # the regular and the "prolific authors" directories
_PANEL = re.compile(
    r'<div id="([^"]+)" class="panel panel-default">\s*<div class="panel-heading">\s*'
    r'<h4 class="panel-title">(.*?)</h4>.*?<ul>(.*?)</ul>',
    re.S,
)
_ENTRY = re.compile(r'<li><a href="(/nifty/[^"]+)">(.*?)</a>', re.S)


def norm_path(href):
    """Archive path without the /nifty/ prefix, '.html' suffix or trailing slash, so one story has one key."""
    path = href.split("#")[0].split("?")[0]
    if path.startswith("/nifty/"):
        path = path[len("/nifty/") :]
    return re.sub(r"(?i)\.html?$", "", path.strip("/"))


def parse_authors(page):
    """[{'id', 'name', 'stories': [{'title', 'path', 'dir'}]}] from an authors directory page.
    'dir' is true when the link names a folder (a multi-chapter story); duplicate links within an author are dropped."""
    authors = []
    for anchor, name, ul in _PANEL.findall(page):
        seen, stories = set(), []
        for href, title in _ENTRY.findall(ul):
            path = norm_path(href)
            title = html.unescape(strip_tags(title)).strip()
            if path and path not in seen and title:
                seen.add(path)
                stories.append({"title": title, "path": path, "dir": href.split("#")[0].endswith("/")})
        authors.append({"id": anchor, "name": html.unescape(strip_tags(name)).strip(), "stories": stories})
    return authors


def load_authors(fetcher, progress=lambda msg: None):
    """Both directory pages merged by author id. Returns a list sorted by name."""
    merged = {}
    for page_name in AUTHOR_PAGES:
        progress(f"Reading the authors directory ({page_name})")
        for author in parse_authors(fetcher.get(SITE + page_name)):
            existing = merged.setdefault(author["id"], {"id": author["id"], "name": author["name"], "stories": []})
            have = {s["path"] for s in existing["stories"]}
            existing["stories"] += [s for s in author["stories"] if s["path"] not in have]
    return sorted(merged.values(), key=lambda a: a["name"].casefold())


def author_sections(author):
    """Sections (gay, lesbian, ...) in which this author has stories."""
    return sorted({s["path"].split("/")[0] for s in author["stories"]})


def story_folder(story):
    parts = story["path"].split("/")
    return "/".join(parts[:2])


_GENERIC = {
    "the",
    "a",
    "an",
    "my",
    "his",
    "her",
    "our",
    "your",
    "of",
    "and",
    "in",
    "to",
    "on",
    "for",
    "with",
    "at",
    "story",
    "stories",
    "tale",
    "tales",
}
_NUMBERED = re.compile(
    r"[\s:,\-–(]*\b(?:(?:part|pt|chapter|ch|book|vol|volume|episode|ep)\.?\s*)?(?:\d+|[ivxlc]+)\)?\s*$", re.I
)
_VERSION = re.compile(
    r"\b(original|revised|revision|redux|rewrite|rewritten|edited|remaster(?:ed)?|new version|old version|"
    r"updated|alternate|reissue|v\d)\b",
    re.I,
)


def _norm_title(title):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", title.lower())).strip()


def _meaningful(base):
    return len(base) >= 6 and any(w not in _GENERIC for w in base.split())


def _split_title(title):
    """(series name, kind) where kind is 'colon' for 'Series: Episode', 'number' for 'Series 3', else 'plain'."""
    m = re.match(r"^(.{3,}?)\s*[:–—]\s+(.+)$", title)
    if m:
        return m.group(1).strip(), "colon"
    m = _NUMBERED.search(title)
    if m and m.start() > 2:
        return title[: m.start()].strip(" :-,"), "number"
    return title.strip(), "plain"


def suggest_series(stories):
    """Suggest which of one author's stories belong together, even across folders.

    Returns (series, rest). series is a list of {'name', 'reason', 'stories'} with 2+ stories each; rest is everything else.
    Deliberately conservative and only a suggestion: it matches 'Series: Episode' titles, 'Series 2' / 'Series III' numbering,
    and titles that are whole-word extensions of another of the author's titles ('Valley Boys' / 'Valley Boys Rugby Tour').
    It does not match a shared opening phrase, which on Nifty is usually an author's habit ('Night with Mark' / 'Night with Mike').
    Alternate versions of one story ('(Revised)', '[Original]', 'redux') are never treated as a series."""
    stories = list(stories)
    plain = {}
    groups = {}
    for s in stories:
        base, kind = _split_title(s["title"])
        key = _norm_title(base)
        if kind == "plain":
            plain.setdefault(key, s)
        elif _meaningful(key):
            groups.setdefault((key, kind), (base, []))[1].append(s)
    found = {}
    for (key, kind), (base, members) in groups.items():
        members = list(members)
        if key in plain and plain[key] not in members:
            members.insert(0, plain[key])  # the opening story is named exactly like the series
        if len(members) >= 2 and key not in found:
            found[key] = {
                "name": base,
                "reason": 'title with "Series: Episode"' if kind == "colon" else "numbered titles",
                "stories": members,
            }
    titles = [(_norm_title(s["title"]), s) for s in stories]
    for key, s in titles:
        if key in found or sum(1 for w in key.split() if w not in _GENERIC) < 2:
            continue
        extensions = [o for k, o in titles if k != key and k.startswith(key + " ") and not _VERSION.search(o["title"])]
        if extensions and not _VERSION.search(s["title"]):
            found[key] = {"name": s["title"], "reason": "titles that extend another title", "stories": [s] + extensions}
    series = []
    for entry in found.values():
        entry["stories"] = _order_series(entry["stories"])
        if len(entry["stories"]) >= 2 and not all(_VERSION.search(t["title"]) for t in entry["stories"][1:]):
            series.append(entry)
    used = {id(s) for entry in series for s in entry["stories"]}
    series.sort(key=lambda e: e["name"].casefold())
    return series, [s for s in stories if id(s) not in used]


def _order_series(members):
    def number(s):
        m = re.search(r"(\d+)\s*$", s["title"])
        return int(m.group(1)) if m else -1

    seen, ordered = set(), []
    for s in sorted(members, key=lambda s: (number(s), s["title"].casefold())):
        if id(s) not in seen:
            seen.add(id(s))
            ordered.append(s)
    return ordered


def story_address(story):
    """Address core.expand_targets understands for a directory-listed story."""
    return url_for(story["path"] + ("/" if story.get("dir") else ""))


def iter_selection(fetcher, selection, skip_ids=frozenset(), progress=lambda msg: None, stats=None, chapter_range=None):
    """Download each selection item in turn, yielding {'title', 'stories'} as soon as it is done, so a cancel keeps
    everything finished so far. stats collects 'skipped' (already in the library) and 'problems'.

    selection: [{'title': book title or None, 'addresses': [...]}]. An item with a title and 2+ resulting stories is meant
    to be one combined book; any other item gives one book per story."""
    stats = stats if stats is not None else {}
    stats.setdefault("skipped", 0)
    stats.setdefault("problems", [])
    for item in selection:
        stories = []
        try:
            refs, errors = expand_targets(fetcher, item["addresses"], progress)
            stats["problems"] += errors
            for ref in refs:
                got, skip = download_ref(fetcher, ref, skip_ids, progress, chapter_range=chapter_range)
                stories += got
                stats["skipped"] += skip
        except OSError as exc:
            stats["problems"].append(str(exc))
        if stories:
            stories = sort_by_posting_order(stories)
            if item.get("series") and len(stories) >= 2:
                for number, story in enumerate(stories, 1):  # order of the addresses = reading order
                    story["series"], story["series_index"] = item["series"], float(number)
            yield {"title": item.get("title"), "stories": stories}


def download_selection(fetcher, selection, skip_ids=frozenset(), progress=lambda msg: None):
    """All of iter_selection at once. Returns (groups, skipped, problems)."""
    stats = {}
    groups = list(iter_selection(fetcher, selection, skip_ids, progress, stats))
    return groups, stats["skipped"], stats["problems"]


def build_selection(series_checked, singles, combine_series=True, combine_all_title=None):
    """Turn what the user ticked into iter_selection() input.

    series_checked: [(series name, [stories])] with only the ticked stories; singles: ticked stories that are in no
    suggested series. combine_series: True = each set becomes one combined book, 'series' = separate books that share a
    Calibre series name and numbering, False = plain separate books. combine_all_title: if set, everything ticked becomes
    one book with that title."""
    everything = [s for _, members in series_checked for s in members] + list(singles)
    if combine_all_title and combine_all_title.strip():
        everything = sort_by_posting_order(everything)
        return (
            [{"title": combine_all_title.strip(), "addresses": [story_address(s) for s in everything]}]
            if everything
            else []
        )
    selection = []
    for name, members in series_checked:
        members = sort_by_posting_order(members)
        if combine_series == "series" and len(members) >= 2:
            selection.append({"title": None, "series": name, "addresses": [story_address(s) for s in members]})
        elif combine_series is True and len(members) >= 2:
            selection.append({"title": name, "addresses": [story_address(s) for s in members]})
        else:
            selection += [{"title": None, "addresses": [story_address(s)]} for s in members]
    selection += [{"title": None, "addresses": [story_address(s)]} for s in singles]
    return selection


# ---------------------------------------------------------------- series found from story contents

_WORDNUM = r"(?:\d+|[ivxlc]+|one|two|three|four|five|six|seven|eight|nine|ten)"
_REF = r"(?P<ref>[^\n\r.!?;]{3,90})"
_RELATIONS = [
    # (pattern, relation, True if this story comes AFTER the one it names)
    (re.compile(r"(?i)\bcontinu(?:ed|es|ing|ation)\s+(?:from|of)\s+" + _REF), "continued from", True),
    (re.compile(r"(?i)\b(?:sequel|follow[- ]?up)\s+(?:to|of)\s+" + _REF), "sequel to", True),
    (re.compile(r"(?i)\bprequel\s+(?:to|of)\s+" + _REF), "prequel to", False),
    (
        re.compile(r"(?i)\b(?:part|chapter|book|episode)\s+" + _WORDNUM + r"\s+(?:of|in)\s+(?:the\s+)?" + _REF),
        "part of",
        True,
    ),
    (
        re.compile(
            r"(?i)\b(?:previous|earlier|first)\s+(?:story|stories|installment|part|episode)s?\s*[:\-]\s*" + _REF
        ),
        "previous story",
        True,
    ),
    (
        re.compile(
            r"(?i)\b(?:read|see)\s+(?:the\s+)?(?:first|previous|earlier)\s+(?:story|part|installment)?\s*[:\-]?\s*"
            + _REF
        ),
        "read first",
        True,
    ),
]


def _clean_ref(text):
    text = re.sub(r"(?i)<[^>]+>", " ", text)
    return text.strip(" \t\"'“”‘’*_:-,")


def match_title(ref, stories, exclude_path=None):
    """The author's story a free-text reference most plausibly names, or None. Needs the whole title to appear in the
    reference as whole words (or the reference to be the title), so ordinary prose never matches."""
    ref_n = _norm_title(_clean_ref(ref))
    if not ref_n:
        return None
    best = None
    for s in stories:
        if s["path"] == exclude_path:
            continue
        title_n = _norm_title(s["title"])
        if len(title_n) < 5 or sum(1 for w in title_n.split() if w not in _GENERIC) < 1:
            continue
        if ref_n == title_n or re.search(r"(?<![a-z0-9])" + re.escape(title_n) + r"(?![a-z0-9])", ref_n):
            if best is None or len(title_n) > len(_norm_title(best["title"])):
                best = s
    return best


def content_links(texts, stories):
    """Links between one author's stories found in the sampled text of each story.

    texts: {path: text}. Returns [{'later', 'earlier', 'relation', 'quote'}] where both ends are stories of this author.
    A phrase such as 'continued from' only counts when what follows names another of the author's stories."""
    by_path = {s["path"]: s for s in stories}
    links, seen = [], set()
    for path, text in texts.items():
        if path not in by_path:
            continue
        flat = re.sub(r"<[^>]+>", " ", text)
        for pattern, relation, after in _RELATIONS:
            for m in pattern.finditer(flat):
                other = match_title(m.group("ref"), stories, exclude_path=path)
                if other is None:
                    continue
                later, earlier = (path, other["path"]) if after else (other["path"], path)
                key = (later, earlier)
                if key not in seen:
                    seen.add(key)
                    begin, end = max(0, m.start() - 30), min(len(flat), m.end() + 30)
                    quote = re.sub(r"\s+", " ", flat[begin:end]).strip()
                    if begin > 0:
                        quote = quote.split(" ", 1)[-1]  # drop a word cut in half
                    if end < len(flat):
                        quote = quote.rsplit(" ", 1)[0]
                    links.append({"later": later, "earlier": earlier, "relation": relation, "quote": quote})
    return links


def story_date(text):
    """Date from a story's mail-style header as 'YYYY-MM-DD', or ''."""
    m = re.search(r"(?mi)^Date:[ \t]*(.+)$", text[:1500])
    if m:
        try:
            return parsedate_to_datetime(m.group(1).strip()).date().isoformat()
        except (TypeError, ValueError, IndexError):
            pass
    return ""


def suggest_series_with_contents(stories, texts):
    """suggest_series() plus links found in the stories' text. Returns (series, rest) like suggest_series, where each
    entry also has 'evidence' (quotes from the stories) and members ordered by what they say about each other."""
    stories = list(stories)
    series, _ = suggest_series(stories)
    links = content_links(texts, stories)
    parent = {s["path"]: s["path"] for s in stories}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for entry in series:
        for other in entry["stories"][1:]:
            union(entry["stories"][0]["path"], other["path"])
    for link in links:
        union(link["later"], link["earlier"])
    components = {}
    for s in stories:
        components.setdefault(find(s["path"]), []).append(s)
    by_path = {s["path"]: s for s in stories}
    title_sets = {find(e["stories"][0]["path"]): e for e in series}
    result = []
    for root, members in components.items():
        if len(members) < 2:
            continue
        paths = {m["path"] for m in members}
        evidence = [
            f"{by_path[link['later']]['title']} says: “{link['quote']}”" for link in links if link["later"] in paths
        ]
        depth = {p: 0 for p in paths}
        for _ in range(len(paths)):  # longest chain of 'earlier -> later' links
            for link in links:
                if link["later"] in paths and depth[link["later"]] <= depth[link["earlier"]]:
                    depth[link["later"]] = depth[link["earlier"]] + 1

        def order(s):
            n = re.search(r"(\d+)\s*$", s["title"])
            return (
                depth[s["path"]],
                story_date(texts.get(s["path"], "")) or "9999",
                int(n.group(1)) if n else -1,
                s["title"].casefold(),
            )

        ordered = sorted(members, key=order)
        titled = title_sets.get(root)
        reasons = ([titled["reason"]] if titled else []) + (["the stories refer to each other"] if evidence else [])
        result.append(
            {
                "name": titled["name"] if titled else ordered[0]["title"],
                "reason": " and ".join(reasons) or "linked titles",
                "stories": ordered,
                "evidence": evidence,
            }
        )
    result.sort(key=lambda e: e["name"].casefold())
    used = {s["path"] for e in result for s in e["stories"]}
    return result, [s for s in stories if s["path"] not in used]


def sample_text(fetcher, story, head=3000, tail=1500):
    """The start and end of a story's text (first and last chapter for a multi-chapter story), using range requests."""
    path = story["path"]
    first = last = path
    if story.get("dir"):
        groups = group_files(parse_listing(fetcher.get(url_for(path + "/"))))
        if not groups:
            return ""
        names = max(groups, key=lambda g: len(g[1]))[1]
        first, last = f"{path}/{names[0]}", f"{path}/{names[-1]}"
    start = fetcher.get_range(url_for(first), head)
    end = fetcher.get_range(url_for(last), tail, tail=True) if tail else ""
    return start + "\n" + end


def scan_texts(fetcher, stories, texts, progress=lambda msg: None):
    """Fill texts[path] for every story not sampled yet. texts is updated as it goes, so a cancel keeps the progress."""
    todo = [s for s in stories if s["path"] not in texts]
    for n, story in enumerate(todo, 1):
        progress(f"Reading the start and end of stories: {n} of {len(todo)} ({story['title']})")
        try:
            texts[story["path"]] = sample_text(fetcher, story)
        except OSError:
            texts[story["path"]] = ""  # unreadable; do not retry in this session
    return texts
