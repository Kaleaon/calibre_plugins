"""Metabods parsing and download logic. Pure standard library so it can be tested outside Calibre."""

import html
import re
from urllib.parse import parse_qs, urlparse

try:  # inside Calibre the shared module ships in the plugin package
    from .storykit import Cancelled, Fetcher, clean_fragment, decode, epub_builder, strip_tags  # noqa: F401
except ImportError:  # tests load this file directly with common/ on sys.path
    from storykit import Cancelled, Fetcher, clean_fragment, decode, epub_builder, strip_tags  # noqa: F401

SITE = "https://metabods.com/mbxy/site/"
PUBLISHER = "Metabods"

build_epub = epub_builder(PUBLISHER)


# ---------------------------------------------------------------- input parsing

SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$")


def story_url(story_id):
    return f"{SITE}story.php?id={story_id}"


def print_url(story_id):
    return f"{SITE}story_print.php?id={story_id}"


def tag_url(tag_id):
    return f"{SITE}archive.php?list=tag&id={int(tag_id)}"


def classify(line):
    """Return ('story', id), ('list', url) or None for one line of user input."""
    line = line.strip()
    if not line:
        return None
    if "://" not in line and SLUG.match(line):
        return "story", line
    if "://" not in line and line.startswith(("metabods.com", "www.metabods.com")):
        line = "https://" + line
    parsed = urlparse(line)
    if not parsed.netloc.lower().endswith("metabods.com"):
        return None
    query = parse_qs(parsed.query)
    path = parsed.path.rsplit("/", 1)[-1]
    if path in ("story.php", "story_print.php") and query.get("id") and SLUG.match(query["id"][0]):
        return "story", query["id"][0]
    if path == "archive.php" and query.get("list"):
        return "list", f"{SITE}archive.php?" + parsed.query
    return None


# ---------------------------------------------------------------- list pages

_ROW = re.compile(
    r'<a\s[^>]*href="[^"]*story\.php\?id=([^"&]+)"[^>]*>(.*?)</a>(?:\s|<wbr>|&nbsp;)*'
    r"(?:by\s*<a\s[^>]*list=author&(?:amp;)?id=(\d+)[^>]*>(.*?)</a>)?",
    re.S | re.I,
)


def parse_story_rows(page):
    """Stories listed on an archive/author/tag/category page, in page order, de-duplicated."""
    rows = {}
    for m in _ROW.finditer(page):
        title = strip_tags(m.group(2))
        if not title:
            continue
        sid = html.unescape(m.group(1))
        author = strip_tags(m.group(4) or "")
        if sid not in rows:
            rows[sid] = {"id": sid, "title": title, "author": author}
        elif author and not rows[sid]["author"]:
            rows[sid]["author"] = author
    return list(rows.values())


def parse_page_heading(page):
    m = re.search(r"<title>(.*?)</title>", page, re.S)
    return re.sub(r"\s*-\s*Metabods.*$", "", strip_tags(m.group(1))) if m else ""


def parse_tag_index(page):
    tags = {}
    for m in re.finditer(r'<a\s[^>]*href="[^"]*list=tag&(?:amp;)?id=(\d+)"[^>]*>(.*?)</a>', page, re.S):
        name = strip_tags(m.group(2))
        if name and name.lower() != "all tags":
            tags.setdefault(int(m.group(1)), name)
    return sorted(tags.items(), key=lambda t: t[1].casefold())


def search_tags(fetcher, tag_ids):
    """Stories carrying each tag; returns {tag_id: [rows]}."""
    return {tid: parse_story_rows(fetcher.get(tag_url(tid))) for tid in tag_ids}


def combine_tag_results(results, match_all):
    """Union or intersection of per-tag story lists, ordered by title."""
    sets = [{r["id"] for r in rows} for rows in results.values()]
    if not sets:
        return []
    keep = set.intersection(*sets) if match_all else set.union(*sets)
    merged = {}
    for rows in results.values():
        for r in rows:
            if r["id"] in keep:
                merged.setdefault(r["id"], r)
    return sorted(merged.values(), key=lambda r: r["title"].casefold())


# ---------------------------------------------------------------- story pages


def parse_story_page(page):
    """Metadata from the normal story page (the print page lacks tags and summary)."""
    info = {"tags": [], "categories": [], "summary": ""}
    m = re.search(r"<h5>by\s*<a[^>]*>(.*?)</a>", page, re.S)
    if m:
        info["author"] = strip_tags(m.group(1))
    m = re.search(r"<h5>.*?</h5>\s*<p[^>]*>(.*?)</p>", page, re.S)
    if m:
        text = strip_tags(m.group(1))
        info["summary"] = text
    # The page repeats tags in a per-part popup and a duplicate header; only the first block is the story's own.
    start = page.find("fa-tags me-2")
    if start >= 0:
        end = page.find("fa-tags me-2", start + 12)
        block = page[start : end if end > 0 else start + 60000]
        seen = set()
        for kind, key in (("tag", "tags"), ("category", "categories")):
            for m in re.finditer(
                rf'<a\s[^>]*href="/mbxy/site/archive\.php\?list={kind}&(?:amp;)?id=\d+"[^>]*>(.*?)</a>', block, re.S
            ):
                name = strip_tags(m.group(1)).lstrip("\u2022").strip()
                if name and (kind, name) not in seen:
                    seen.add((kind, name))
                    info[key].append(name)
    return info


def parse_print_page(page):
    """Title, author and the ordered (heading, html) sections of story_print.php."""
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.S)
    if not m:
        raise ValueError("Print page has no story title")
    title = strip_tags(m.group(1))
    m = re.search(r"<strong>\s*by\s+(.*?)</strong>", page, re.S)
    author = strip_tags(m.group(1)) if m else "Unknown"
    m = re.search(r"story copyright\s*&copy;\s*by\s*(.*?)<br", page, re.S)
    copyright_holder = strip_tags(m.group(1)) if m else author
    sections = []
    for chunk in page.split('<div class="xyp_section">')[1:]:
        start = re.search(r"<div[^>]*xyp_section_cols[^>]*>", chunk)
        if not start:
            continue  # title block or footer
        h = re.search(r"<h3[^>]*>(.*?)</h3>", chunk[: start.start()], re.S)
        body = re.sub(r"</div>\s*</div>\s*$", "", chunk[start.end() :].rstrip())
        sections.append((strip_tags(h.group(1)) if h else "", body))
    if not sections:
        raise ValueError("Print page has no story text")
    return {"title": title, "author": author, "copyright": copyright_holder, "sections": sections}


def fetch_story(fetcher, story_id):
    """Download one story (all parts) and merge print-page text with story-page metadata."""
    story = parse_print_page(fetcher.get(print_url(story_id)))
    story["id"] = story_id
    story["url"] = story_url(story_id)
    try:
        info = parse_story_page(fetcher.get(story["url"]))
    except Cancelled:
        raise
    except OSError:
        info = {"tags": [], "categories": [], "summary": ""}
    story.update(tags=info["tags"], categories=info["categories"], summary=info["summary"])
    return story


def expand_targets(fetcher, lines, progress=lambda msg: None):
    """Resolve pasted story ids/URLs and list pages (author, tag, category, archive) into story rows."""
    rows, errors = {}, []
    for line in lines:
        kind = classify(line)
        if kind is None:
            if line.strip():
                errors.append(f"Not a Metabods story or list address: {line.strip()}")
            continue
        if kind[0] == "story":
            rows.setdefault(kind[1], {"id": kind[1], "title": kind[1], "author": ""})
            continue
        progress(f"Reading list {kind[1]}")
        try:
            found = parse_story_rows(fetcher.get(kind[1]))
        except OSError as exc:
            errors.append(str(exc))
            continue
        if not found:
            errors.append(f"No stories found at {kind[1]}")
        for row in found:
            rows.setdefault(row["id"], row)
    return list(rows.values()), errors
