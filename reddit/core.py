"""Following stories on Reddit: sources, feed and API parsing, a chapter cache, and building the book.

Pure standard library so it can be tested outside Calibre. Reddit is reached in one of two ways: its official API with the
user's own credentials (recommended), or its public Atom feeds read the way a feed reader would (see README for the
trade-offs). Requests are infrequent, one page at a time, and a rate limit (HTTP 429) ends the run instead of being retried.
"""

import base64
import csv
import html
import io
import json
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timezone
from urllib.parse import parse_qs, quote, urlencode, urlparse

try:  # inside Calibre the shared module ships in the plugin package
    from .storykit import (
        Cancelled,
        Fetcher,
        RateLimited,
        clean_fragment,
        escape,
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
        strip_tags,
    )
    from storykit import build_epub as _build_epub

WWW = "https://www.reddit.com"
OAUTH = "https://oauth.reddit.com"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
FEED_USER_AGENT = "CalibreRedditFollower/1.0 (personal feed reader; polls rarely)"
PUBLISHER = "Reddit"
MIN_CHECK_HOURS = 1.0
MIN_FEED_INTERVAL = 7.0  # seconds between requests without credentials (Reddit has allowed roughly 10 a minute)
MIN_API_INTERVAL = 2.0  # seconds between requests through the official API
FIRST_RUN_PAGES = 30  # newest 3000 posts at most per backfill pass; a longer history is finished by the next check
NOTE_BATCH = 300  # author comments fetched per check (one request each); the rest follow on the next checks
EACH_FIRST_RUN = 25  # 'each post is a book' follows add only the newest few on the first check, not a whole backlog
EACH_FIRST_PAGES = 5
EACH_PER_RUN = 50  # books added to the library per check
NOTES_GAP_HOURS = 0.25  # how soon a check that still owes author comments may run again
NAV_WORDS = r"(?:first|previous|prev|next|latest|last|index|wiki|series|home|start|final|chapter|part|start here|table of contents|toc)"


class SourceError(ValueError):
    """The address the user gave is not something we can follow."""


# ---------------------------------------------------------------- what to follow

_NAME = r"[A-Za-z0-9_\-]{2,32}"


def parse_source(text):
    """Turn a pasted address or shorthand into {'kind', 'subreddit', 'user', 'query'}.

    Accepts r/HFY, /r/hfy/new, https://www.reddit.com/r/HFY/, u/name, /user/name/submitted, and search addresses such as
    https://www.reddit.com/r/HFY/search/?q=Out+of+Cruel+Space&restrict_sr=1."""
    raw = (text or "").strip()
    if not raw:
        raise SourceError("Enter a subreddit (r/HFY), a user (u/name) or a Reddit search address.")
    if "://" not in raw and not raw.startswith("/"):
        raw = ("/" + raw) if re.match(r"(?i)^(r|u|user)/", raw) else raw
    if "://" in raw or re.match(r"(?i)^(www\.|old\.|new\.)?reddit\.com", raw):
        if "://" not in raw:
            raw = "https://" + raw
        parsed = urlparse(raw)
        if not (parsed.netloc.lower().endswith("reddit.com") or parsed.netloc.lower() == "redd.it"):
            raise SourceError("That is not a Reddit address.")
        path, query = parsed.path, parse_qs(parsed.query)
    else:
        path, query = raw, {}
    path = re.sub(r"\.(rss|json)$", "", path.rstrip("/"))
    m_sub = re.match(r"(?i)^/r/(" + _NAME + r")(/.*)?$", path)
    m_user = re.match(r"(?i)^/(?:u|user)/(" + _NAME + r")(/.*)?$", path)
    if m_sub and (m_sub.group(2) or "").lower().startswith("/search") or (m_sub and query.get("q")):
        q = (query.get("q") or [""])[0].strip()
        if not q:
            raise SourceError("That search address has no search words (q=...).")
        return {"kind": "search", "subreddit": m_sub.group(1), "user": "", "query": q}
    if m_sub:
        if (m_sub.group(2) or "").lower().startswith("/comments"):
            raise SourceError("That is a single post. Follow the subreddit, the author, or a search instead.")
        return {"kind": "subreddit", "subreddit": m_sub.group(1), "user": "", "query": ""}
    if m_user:
        return {"kind": "user", "subreddit": "", "user": m_user.group(1), "query": ""}
    if path.lower().startswith("/search") and query.get("q"):
        return {"kind": "search", "subreddit": "", "user": "", "query": query["q"][0].strip()}
    raise SourceError("Could not tell what to follow. Use r/NAME, u/NAME, or a Reddit search address.")


def describe_source(src):
    if src["kind"] == "subreddit":
        return f"r/{src['subreddit']}"
    if src["kind"] == "user":
        return f"u/{src['user']}"
    return f"search “{src['query']}”" + (f" in r/{src['subreddit']}" if src["subreddit"] else "")


def describe_filters(follow):
    """The follow's filters in words for the list of followed stories (the stored title filter may be a regular expression that
    this plugin wrote, which people should not have to read)."""
    parts = []
    title = follow.get("title_filter", "")
    if title:
        if title.lower().startswith("re:"):
            body = title[3:]
            starts = body.startswith("^\\s*")
            for fragment in ("^\\s*", _LEAD_TAGS, "(?![A-Za-z0-9])", "\\b", ".*\\d"):
                body = body.replace(fragment, "")
            body = re.sub(r"\\(.)", r"\1", body)
            parts.append(f"title {'starts with' if starts else 'matches'} \u201c{body}\u201d")
        else:
            parts.append(f"title has \u201c{title}\u201d")
    if follow.get("author_filter"):
        parts.append(f"by {follow['author_filter']}")
    if follow.get("flair_filter"):
        parts.append(f"flair \u201c{follow['flair_filter']}\u201d")
    if follow.get("layout") == "each":
        parts.append("each post as its own book")
    return "  " + ", ".join(parts) if parts else ""


# Ready-made follows for series the user asked for. Authors and title patterns were read from the series' own first post.
PRESETS = [
    {
        "label": "Out of Cruel Space (r/HFY, by KyleKKent)",
        "name": "Out of Cruel Space",
        "source": "u/KyleKKent",
        # the author renamed the series part-way: "Out of Cruel Space, Part N" became "OOCS, Into A Wider Galaxy, Part N"
        "title_filter": "re:^\\s*(Out of Cruel Space|OOCS)\\b.*\\d",
        "author_filter": "KyleKKent",
        # a fan-kept public chapter list (date, author, chapter, storyline) that names every chapter and fixes mistitled posts
        "index_url": "https://docs.google.com/spreadsheets/d/1IipEkkuMzpfhVlQh0BhOcrmHlguHBZhSKSLTwRhYDf8/edit?gid=1375133682",
        "title_from_body": True,
    },  # each post opens with its chapter title, e.g. 'The Pirates & The Bounty Hunters'
    # many authors, one story per post, each with a flair; every post becomes its own book (newest few on the first check)
    {
        "label": "r/gayincest_stories (each post as its own book)",
        "name": "r/gayincest_stories",
        "source": "r/gayincest_stories",
        "title_filter": "",
        "author_filter": "",
        "flair_filter": "",
        "layout": "each",
    },
]


def source_text(src):
    """The source as an address parse_source() reads back, for filling in the edit box."""
    if src["kind"] == "subreddit":
        return f"r/{src['subreddit']}"
    if src["kind"] == "user":
        return f"u/{src['user']}"
    base = f"{WWW}/r/{src['subreddit']}/search/" if src["subreddit"] else f"{WWW}/search/"
    params = {"q": src["query"]}
    if src["subreddit"]:
        params["restrict_sr"] = "1"
    return f"{base}?{urlencode(params, quote_via=quote)}"


def _listing_path(src):
    if src["kind"] == "subreddit":
        sort = src.get("sort", "new")  # discovery browses the top of a subreddit; following always reads the newest
        return f"/r/{src['subreddit']}/{sort}", ({"t": src.get("t", "all")} if sort == "top" else {})
    if src["kind"] == "user":
        return f"/user/{src['user']}/submitted", {"sort": "new"}
    base = f"/r/{src['subreddit']}/search" if src["subreddit"] else "/search"
    params = {"q": src["query"], "sort": src.get("sort", "new")}
    if src.get("t") and params["sort"] in ("top", "relevance"):
        params["t"] = src["t"]
    if src["subreddit"]:
        params["restrict_sr"] = "on"
    return base, params


def feed_url(src, limit=100, after=None):
    path, params = _listing_path(src)
    params = dict(params, limit=limit)
    if after:
        params["after"] = after
    return f"{WWW}{path}.rss?{urlencode(params, quote_via=quote)}"


def api_path(src, limit=100, after=None):
    path, params = _listing_path(src)
    params = dict(params, limit=limit, raw_json=1)
    if src["kind"] == "search":
        params["type"] = "link"
    if after:
        params["after"] = after
    return f"{path}?{urlencode(params, quote_via=quote)}"


# ---------------------------------------------------------------- cleaning post HTML

_MD = re.compile(r'<div class="md">(.*?)</div>\s*<!--\s*SC_ON\s*-->', re.DOTALL)
_MD_LOOSE = re.compile(r'<div class="md">(.*)</div>', re.DOTALL)
_NAV_ONLY = re.compile(r"(?i)^[\s|/•·\-–—>»«\[\]()←→,.:]*(?:" + NAV_WORDS + r"[\s|/•·\-–—>»«\[\]()←→,.:\d]*)+$")


def main_html(content):
    """The post body from a feed entry or an API selftext_html: drops Reddit's wrapper and the 'submitted by ...' footer."""
    content = html.unescape(content) if "&lt;div" in content and "<div" not in content else content
    m = _MD.search(content) or _MD_LOOSE.search(content)
    body = m.group(1) if m else re.split(r"(?i)(?:&#32;|\s)*submitted\s+by\b", content)[0]
    cleaned = strip_navigation(clean_fragment(body))
    return cleaned if strip_tags(cleaned).strip() else ""  # image and link posts leave only empty markup


_BLOCK = re.compile(
    r"<p(?: [^>]*)?>.*?</p>|<hr/>|<(?P<tag>div|blockquote|ul|ol|pre|h[1-6])(?: [^>]*)?>.*?</(?P=tag)>", re.DOTALL
)


def strip_navigation(xhtml):
    """Remove 'First | Previous | Next' style link lines from the start and end of a post, and any rule beside them.

    Works on whole top-level blocks. If the text does not split cleanly into blocks it is returned unchanged."""
    blocks, position = [], 0
    for m in _BLOCK.finditer(xhtml):
        if xhtml[position : m.start()].strip():
            return xhtml.strip()  # loose text between blocks: not something we can split safely
        blocks.append(m.group(0))
        position = m.end()
    if xhtml[position:].strip():
        return xhtml.strip()

    def removable(block):
        if block == "<hr/>":
            return True
        text = strip_tags(block)
        return block.startswith("<p") and bool(text) and bool(_NAV_ONLY.match(text))

    while blocks and removable(blocks[-1]):
        blocks.pop()
    while blocks and removable(blocks[0]):
        blocks.pop(0)
    return "".join(blocks)


# ---------------------------------------------------------------- reading feeds and API listings


def _epoch(text):
    text = (text or "").strip().replace("Z", "+00:00")
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).timestamp()


def parse_atom(xml_text):
    """Entries of a Reddit Atom feed. Returns (entries, after) where after is the id to pass for the next, older page."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"The feed was not readable XML ({exc}). Reddit may have refused the request.") from exc
    entries = []
    for e in root.findall("a:entry", ns):
        raw_id = (e.findtext("a:id", namespaces=ns) or "").strip()
        if not re.match(r"^t3_\w+$", raw_id):
            continue  # comments and other kinds
        link = e.find("a:link", ns)
        author = (e.findtext("a:author/a:name", namespaces=ns) or "").strip()
        content = e.findtext("a:content", namespaces=ns) or ""
        entries.append(
            {
                "id": raw_id,
                "title": html.unescape((e.findtext("a:title", namespaces=ns) or "").strip()),
                "author": re.sub(r"^/?u/", "", author),
                "created": _epoch(e.findtext("a:published", namespaces=ns) or e.findtext("a:updated", namespaces=ns)),
                "link": link.get("href") if link is not None else "",
                "html": main_html(content) if '<div class="md">' in content or "submitted by" in content else "",
                "subreddit": (e.find("a:category", ns).get("term") if e.find("a:category", ns) is not None else ""),
            }
        )
    return entries, (entries[-1]["id"] if entries else None)


def parse_listing(json_text):
    """Entries of an API listing. Returns (entries, after) where after is the listing's own 'after' token."""
    data = json.loads(json_text)
    listing = data.get("data", {}) if isinstance(data, dict) else {}
    entries = []
    for child in listing.get("children", []):
        d = child.get("data", {}) if child.get("kind") == "t3" else {}
        if not d.get("name"):
            continue
        entries.append(
            {
                "id": d["name"],
                "title": html.unescape(d.get("title", "")),
                "author": d.get("author", ""),
                "created": float(d.get("created_utc") or 0),
                "link": "https://www.reddit.com" + d.get("permalink", ""),
                "html": main_html(d["selftext_html"]) if d.get("selftext_html") else "",
                "subreddit": d.get("subreddit", ""),
                "flair": (d.get("link_flair_text") or "").strip(),
                "score": int(d.get("score") or 0),
            }
        )
    return entries, listing.get("after")


class FeedSource:
    """Reads pages of posts from public Atom feeds (no credentials)."""

    mode = "rss"

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def page(self, src, after=None, limit=100):
        text = self.fetcher.get(
            feed_url(src, limit, after),
            headers={"User-Agent": FEED_USER_AGENT, "Accept": "application/atom+xml, application/xml"},
        )
        return parse_atom(text)


class ApiSource:
    """Reads pages of posts through Reddit's official API with the user's own app.

    With a saved login (a refresh token from "Log in with Reddit") it acts as that account; otherwise with only a client id
    (an 'installed app') it uses the installed-client grant, and with a secret it uses client credentials.
    Written against Reddit's documented API and tested with a stand-in server; not verified against the live service."""

    mode = "api"

    def __init__(self, fetcher, client_id, client_secret="", username="", token_url=None, base=None, refresh_token=""):
        self.fetcher, self.client_id, self.client_secret = fetcher, client_id.strip(), (client_secret or "").strip()
        self.token_url, self.base = token_url or TOKEN_URL, base or OAUTH
        self.refresh_token = (refresh_token or "").strip()
        self.user_agent = (
            f"calibre:reddit-follower:1.0 (by /u/{(username or 'unknown').strip().lstrip('/').replace('u/', '')})"
        )
        self._token, self._expires = None, 0.0
        self._device = uuid.uuid4().hex

    def _authorize(self):
        if not self.client_id:
            raise SourceError("Enter your Reddit app's client id in the settings to use the official API.")
        basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        if self.refresh_token:
            body = urlencode({"grant_type": "refresh_token", "refresh_token": self.refresh_token})
        elif self.client_secret:
            body = urlencode({"grant_type": "client_credentials"})
        else:
            body = urlencode(
                {"grant_type": "https://oauth.reddit.com/grants/installed_client", "device_id": self._device}
            )
        reply = json.loads(
            self.fetcher.get(
                self.token_url,
                post=body.encode(),
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": self.user_agent,
                },
            )
        )
        if not reply.get("access_token"):
            if self.refresh_token:
                raise SourceError(
                    "Reddit no longer accepts the saved login. Open Settings and log in with Reddit again."
                )
            raise OSError(f"Reddit refused the credentials: {reply.get('error') or reply.get('message') or reply}")
        self._token = reply["access_token"]
        self._expires = time.monotonic() + float(reply.get("expires_in", 3600)) - 60

    def page(self, src, after=None, limit=100):
        if not self._token or time.monotonic() >= self._expires:
            self._authorize()
        try:
            text = self.fetcher.get(
                self.base + api_path(src, limit, after),
                headers={"Authorization": f"bearer {self._token}", "User-Agent": self.user_agent},
            )
        except OSError as exc:
            if "HTTP Error 403" in str(exc) and "insufficient_scope" in str(exc) and self.refresh_token:
                raise SourceError(
                    "Reddit says the saved login lacks permission for this page. Open Settings, Log out, then Log in with "
                    "Reddit again to grant it."
                ) from exc
            raise
        return parse_listing(text)

    def author_comment(self, entry):
        """The post author's own top-level comment as cleaned HTML ('' if there is none). One request."""
        if not self._token or time.monotonic() >= self._expires:
            self._authorize()
        post_id = entry["id"].split("_", 1)[-1]
        text = self.fetcher.get(
            f"{self.base}/comments/{post_id}?limit=100&depth=1&sort=top&raw_json=1",
            headers={"Authorization": f"bearer {self._token}", "User-Agent": self.user_agent},
        )
        return top_level_author_comment(text, entry.get("author", ""))


def top_level_author_comment(json_text, author):
    data = json.loads(json_text)
    if not (isinstance(data, list) and len(data) > 1):
        return ""
    for child in data[1].get("data", {}).get("children", []):
        d = child.get("data", {}) if child.get("kind") == "t1" else {}
        if author and d.get("author", "").casefold() == author.casefold() and d.get("body_html"):
            return main_html(d["body_html"])
    return ""


# ---------------------------------------------------------------- finding stories and whole series

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_PART_TAIL = re.compile(
    r"^(?P<stem>.*?\S)[\s,:;\-\u2013\u2014(\[#]*"
    r"(?i:(?:part|pt\.?|chapter|ch\.?|book|episode|ep\.?|volume|vol\.?)\s*)?"
    r"(?P<num>\d{1,4}|(?-i:[IVXLC]{1,6}))\s*[)\]]?\s*(?:[:\-\u2013\u2014].*)?$"
)


def _roman(text):
    total = 0
    for i, ch in enumerate(text):
        value = _ROMAN[ch]
        total += -value if i + 1 < len(text) and _ROMAN[text[i + 1]] > value else value
    return total


_LEAD_TAGS = r"(?:[\[(][^\])]{0,30}[\])]\s*[-\u2013\u2014:]*\s*)*"
_LEAD_TAGS_RE = re.compile(r"^\s*" + _LEAD_TAGS)


def series_parts(title):
    """('Why Humans Avoid War', 8) for 'Why Humans Avoid War VIII'; (title, None) when the title carries no part number.
    Reads trailing numbers (12, Part 12, Ch. 12, (12), #12) and capital Roman numerals, with an optional ': subtitle'."""
    title = title.strip()
    bare = _LEAD_TAGS_RE.sub("", title).strip()  # [OC] and [Universe] tags in front do not name the series...
    if bare != title:
        stem, number = _split_part(bare)
        if (
            re.search(r"[A-Za-z]{3}", stem)
            and not stem[0].isdigit()
            and not re.fullmatch(r"(?i)(?:part|pt|chapter|ch|book|episode|ep|volume|vol)\W*", stem)
        ):
            return stem, number
    return _split_part(title)  # ...unless nothing but a number is left once they are removed


def _split_part(title):
    m = _PART_TAIL.match(title)
    if not m:
        return title, None
    num = m.group("num")
    return m.group("stem").strip(" ,:;-\u2013\u2014"), (int(num) if num.isdigit() else _roman(num))


def _stem_key(stem):
    return re.sub(r"\W+", " ", stem.casefold()).strip()


def group_series(entries):
    """Group posts into likely series by author and title stem. Returns groups, biggest first: {'name', 'author', 'posts',
    'numbers', 'flairs', 'is_series'}. A group is a series when two different part numbers appear, or the posts carry a
    'series' flair; everything else is a single story. Posts without story text (links, images) are left out."""
    groups = {}
    for e in entries:
        if not e.get("html") or not e.get("title"):
            continue
        stem, number = series_parts(e["title"])
        g = groups.setdefault(
            (e.get("author", "").casefold(), _stem_key(stem)),
            {"name": stem, "author": e.get("author", ""), "posts": {}, "numbers": set(), "flairs": set(), "score": 0},
        )
        g["posts"][e["id"]] = e
        g["score"] = g.get("score", 0) + int(
            e.get("score") or 0
        )  # public feeds carry no score, so this stays 0 without the API
        if number is not None:
            g["numbers"].add(number)
        if e.get("flair"):
            g["flairs"].add(e["flair"])
    out = []
    for g in groups.values():
        g["posts"] = sorted(g["posts"].values(), key=lambda p: p["created"])
        g["is_series"] = (
            len(g["numbers"]) >= 2 or any(re.search(r"(?i)series", f) for f in g["flairs"]) and len(g["posts"]) >= 2
        )
        out.append(g)
    return sorted(out, key=lambda g: (-g["is_series"], -len(g["posts"]), g["name"].casefold()))


def rank_groups(groups, by="score"):
    """Series first, then by total rating of the posts found ('score') or by how many parts were found ('posts')."""
    key = (lambda g: g.get("score", 0)) if by == "score" else (lambda g: len(g["posts"]))
    return sorted(groups, key=lambda g: (-g["is_series"], -key(g), -len(g["posts"]), g["name"].casefold()))


def followed_keys(follows):
    """(author, name) of series already followed, to avoid adding the same one twice."""
    keys = set()
    for f in follows:
        if f.get("author_filter"):
            keys.add((f["author_filter"].casefold(), _stem_key(f["name"])))
    return keys


def top_series(groups, count=5, min_parts=3, follows=()):
    """The highest rated series among discovered groups: real series with at least `min_parts` parts found, not already followed."""
    taken = followed_keys(follows)
    chosen = [
        g
        for g in rank_groups(groups, "score")
        if g["is_series"]
        and len(g["posts"]) >= min_parts
        and (g["author"].casefold(), _stem_key(g["name"])) not in taken
    ]
    return chosen[:count]


BROWSE = [
    ("relevance", "Best match for the search words"),
    ("top-all", "Top of all time"),
    ("top-year", "Top this year"),
    ("top-month", "Top this month"),
    ("new", "Newest"),
]


def discover_source(subreddit, words="", browse="relevance"):
    """The listing to look through: a search (with words) or a browse of the subreddit's top or newest posts."""
    subreddit = subreddit.strip().lstrip("/").replace("r/", "", 1).strip()
    sort, _, period = browse.partition("-")
    if not re.fullmatch(_NAME, subreddit or "-"):
        raise SourceError("Enter a subreddit name such as HFY.")
    if words.strip():
        src = {
            "kind": "search",
            "subreddit": subreddit,
            "user": "",
            "query": words.strip(),
            "sort": sort if sort in ("relevance", "top", "new") else "relevance",
        }
    else:
        src = {
            "kind": "subreddit",
            "subreddit": subreddit,
            "user": "",
            "query": "",
            "sort": sort if sort in ("top", "new") else "top",
        }
    if period or src["sort"] == "top":
        src["t"] = period or "all"
    return src


def discover(source, src, flair="", pages=3, cancelled=lambda: False, progress=lambda msg: None):
    """Look through a few pages of a listing and group what is there into series and single stories."""
    entries, after = [], None
    for number in range(pages):
        if cancelled():
            raise Cancelled()
        progress(f"Reading page {number + 1}")
        found, after = source.page(src, after)
        entries += [e for e in found if _text_matches(flair, e.get("flair", ""))]
        if not found or not after:
            break
    return group_series(entries)


def series_follow(group, author_note=False):
    """A follow for a discovered series: the author's posts whose title starts with the series name."""
    stem = group["name"]
    name = re.escape(stem).replace("\\ ", " ")  # spaces need no escaping here, and the pattern is shown to people
    return new_follow(
        stem,
        f"u/{group['author']}",
        "re:^\\s*" + _LEAD_TAGS + name + r"(?![A-Za-z0-9])",
        group["author"],
        author_note=author_note,
    )


def preview_series(source, follow, pages=FIRST_RUN_PAGES, cancelled=lambda: False, progress=lambda msg: None):
    """Everything the author has under this series name, so the whole series can be judged before following it.
    Returns {'count', 'first', 'last', 'titles'} (titles: first and last few)."""
    posts, after = {}, None
    for number in range(pages):
        if cancelled():
            raise Cancelled()
        progress(f"Looking through {follow['source']['user']}'s posts, page {number + 1}")
        found, after = source.page(follow["source"], after)
        for e in found:
            if matches(e, follow):
                posts[e["id"]] = e
        if not found or not after:
            break
    ordered = sorted(posts.values(), key=lambda p: p["created"])
    day = lambda p: datetime.fromtimestamp(p["created"], UTC).date().isoformat()
    return {
        "count": len(ordered),
        "first": day(ordered[0]) if ordered else "",
        "last": day(ordered[-1]) if ordered else "",
        "titles": [p["title"] for p in ordered[:3]]
        + (["\u2026"] if len(ordered) > 6 else [])
        + [p["title"] for p in ordered[-3:]]
        if len(ordered) > 3
        else [p["title"] for p in ordered],
    }


# ---------------------------------------------------------------- chapter index (a public spreadsheet)

INDEX_REFRESH_HOURS = 24
_SHEET = re.compile(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_\-]+)")
_CHAPTER_LABEL = re.compile(r"(?i)\bchapter\s+(\d+)")


def sheet_csv_url(url):
    """The CSV export address for a public Google Sheets link (the tab in the link, if it names one)."""
    m = _SHEET.search(url or "")
    if not m:
        raise SourceError("That is not a Google Sheets address (https://docs.google.com/spreadsheets/d/...).")
    gid = re.search(r"[#&?]gid=(\d+)", url)
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (
        f"&gid={gid.group(1)}" if gid else ""
    )


def parse_index(csv_text, author=""):
    """Chapters listed by an index sheet with columns Date, Author, Chapter and (optionally) Note. Returns
    [{'n', 'label', 'note', 'ts'}] for the author's rows, where ts is the listed date read as UTC+2 (the sheet's CEST) in epoch
    seconds. Rows whose Chapter cell has no 'Chapter N' (side stories, notes) are ignored."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    head = next(
        (
            i
            for i, r in enumerate(rows)
            if len(r) > 2 and r[0].strip().lower().startswith("date") and "chapter" in " ".join(r).lower()
        ),
        None,
    )
    if head is None:
        raise ValueError("That sheet has no Date / Author / Chapter columns.")
    cols = {c.strip().lower(): i for i, c in enumerate(rows[head]) if c.strip()}
    col_date, col_author, col_chapter = (
        cols.get("date (cest)", cols.get("date", 0)),
        cols.get("author", 1),
        cols.get("chapter", 2),
    )
    col_note = cols.get("note")
    out = []
    for r in rows[head + 1 :]:
        if len(r) <= col_chapter or not r[col_date].strip():
            continue
        if author and r[col_author].strip().casefold() != author.casefold():
            continue
        m = _CHAPTER_LABEL.search(r[col_chapter])
        if not m or not re.match(r"\d{4}/\d\d/\d\d", r[col_date].strip()):
            continue
        try:
            when = datetime.strptime(r[col_date].strip(), "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp() - 2 * 3600
        except ValueError:
            continue
        note = r[col_note].strip() if col_note is not None and len(r) > col_note else ""
        out.append(
            {
                "n": int(m.group(1)),
                "label": re.sub(r"\s+", " ", r[col_chapter]).strip(),
                "note": re.sub(r"\s+", " ", note),
                "ts": when,
            }
        )
    return out


def match_index(posts, rows):
    """Pair posts with index rows. First by chapter number and date (within three days, so Chapter 5 of two eras do not mix up),
    then leftovers one-to-one by posting time (within 36 hours), which catches mistitled posts. Returns
    ({post_id: row}, unmatched rows, unmatched posts)."""
    posts = sorted(posts, key=lambda p: p["created"])
    free = list(rows)
    matched, left = {}, []
    for p in posts:
        numbers = {int(n) for n in re.findall(r"\d+", p["title"])}
        near = [r for r in free if r["n"] in numbers and abs(r["ts"] - p["created"]) <= 3 * 86400]
        if near:
            best = min(near, key=lambda r: abs(r["ts"] - p["created"]))
            matched[p["id"]] = best
            free.remove(best)
        else:
            left.append(p)
    still = []
    for p in left:
        near = [r for r in free if abs(r["ts"] - p["created"]) <= 36 * 3600]
        if near:
            best = min(near, key=lambda r: abs(r["ts"] - p["created"]))
            matched[p["id"]] = best
            free.remove(best)
        else:
            still.append(p)
    return matched, free, still


def index_title(row, fallback, with_note=True):
    return row["label"] + (f" \u2013 {row['note']}" if row["note"] and with_note else "") if row else fallback


def refresh_index(source, follow, cache, now=time.time):
    """Fetch the follow's chapter index at most once a day. A failure keeps the older copy and is remembered, not fatal."""
    url = follow.get("index_url")
    if not url:
        return
    data = cache.data.get("index") or {}
    if data.get("url") == url and now() - data.get("fetched", 0) < INDEX_REFRESH_HOURS * 3600 and data.get("rows"):
        return
    try:
        rows = parse_index(source.fetcher.get(sheet_csv_url(url)), follow.get("author_filter", ""))
        cache.data["index"] = {"url": url, "fetched": now(), "rows": rows, "error": ""}
    except (OSError, ValueError, SourceError) as exc:
        cache.data["index"] = dict(
            data, url=url, fetched=now() - INDEX_REFRESH_HOURS * 3600 + 3600, error=str(exc)[:200]
        )
    cache.save()


def index_report(follow, cache):
    """What the index says about the collected chapters: {'rows', 'matched', 'missing' (labels), 'unlisted' (post titles)}."""
    data = cache.data.get("index") or {}
    rows = data.get("rows") or []
    matched, free, unlisted = match_index(list(cache.posts.values()), rows) if rows else ({}, [], [])
    return {
        "rows": len(rows),
        "matched": len(matched),
        "missing": [r["label"] for r in free],
        "unlisted": [p["title"] for p in unlisted],
        "error": data.get("error", ""),
    }


def make_source(mode, client_id="", client_secret="", username="", cancelled=lambda: False, refresh_token=""):
    """The reader for the chosen mode, paced so as to stay within Reddit's limits for that mode."""
    if mode == "api":
        return ApiSource(
            Fetcher(min_interval=MIN_API_INTERVAL, cancelled=cancelled),
            client_id,
            client_secret,
            username,
            refresh_token=refresh_token,
        )
    return FeedSource(Fetcher(min_interval=MIN_FEED_INTERVAL, cancelled=cancelled))


def probe(source, src, follow=None, limit=25):
    """Make ONE request and say what came back. Returns (ok, message); never raises for network or format problems."""
    try:
        entries, _ = source.page(src, None, limit)
    except RateLimited as exc:
        wait = f" It asked for about {max(1, round(exc.retry_after / 60))} minute(s)." if exc.retry_after else ""
        return False, (
            "Reddit asked us to slow down (HTTP 429)."
            + wait
            + " Shared and cloud networks hit this far more often than a home "
            "connection. Wait a while and try again; the plugin itself will back off the same way."
        )
    except SourceError as exc:
        return False, str(exc)
    except (OSError, ValueError) as exc:
        return False, f"Reddit did not give a usable answer: {exc}"
    if not entries:
        return True, f"Reddit answered, but {describe_source(src)} returned no posts."
    with_text = [e for e in entries if e.get("html")]
    parts = [
        f"Reddit answered through the {'official API' if source.mode == 'api' else 'public feed'}: {len(entries)} post(s), "
        f"{len(with_text)} with story text"
    ]
    if follow is not None:
        mine = [e for e in entries if matches(e, follow)]
        parts.append(
            f"{len(mine)} match your filters"
            + (
                f", newest \u201c{max(mine, key=lambda e: e['created'])['title']}\u201d"
                if mine
                else " (none on this first page; the first check reads further back)"
            )
        )
    return True, "; ".join(parts) + "."


# ---------------------------------------------------------------- follows, filters, and the chapter cache


def new_follow(
    name,
    source_input="",
    title_filter="",
    author_filter="",
    author_note=False,
    layout="series",
    flair_filter="",
    index_url="",
    title_from_body=False,
    sources=None,
):
    if sources is None:
        src = (
            parse_source(source_input)
            if source_input
            else {"kind": "subreddit", "subreddit": "", "user": "", "query": ""}
        )
        sources = [
            {
                "kind": src["kind"],
                "subreddit": src.get("subreddit", ""),
                "user": src.get("user", ""),
                "query": src.get("query", ""),
                "title_filter": title_filter.strip(),
                "author_filter": author_filter.strip().lstrip("/").replace("u/", "", 1),
                "flair_filter": flair_filter.strip(),
            }
        ]
    else:
        src = sources[0] if sources else {"kind": "subreddit", "subreddit": "", "user": "", "query": ""}

    first_src_text = source_input or (source_text(src) if src.get("kind") else "")
    return {
        "id": uuid.uuid4().hex[:10],
        "name": name.strip() or describe_source(src),
        "source": src,
        "sources": sources,
        "title_filter": title_filter.strip() if title_filter else sources[0].get("title_filter", ""),
        "author_filter": author_filter.strip().lstrip("/").replace("u/", "", 1)
        if author_filter
        else sources[0].get("author_filter", ""),
        "author_note": bool(author_note),
        "layout": "each" if layout == "each" else "series",
        "flair_filter": flair_filter.strip() if flair_filter else sources[0].get("flair_filter", ""),
        "index_url": index_url.strip(),
        "title_from_body": bool(title_from_body),
        "last_checked": 0.0,
        "last_status": "",
    }


def matches_source(entry, src, layout="series"):
    """Whether a post matches a specific feed source rule."""
    if not entry.get("html") or not entry.get("title"):
        return False  # link, image and empty posts have no story text
    flt = src.get("title_filter", "")
    if flt:
        if flt.lower().startswith("re:"):
            try:
                if not re.search(flt[3:], entry["title"], re.IGNORECASE):
                    return False
            except re.error:
                return False
        elif flt.casefold() not in entry["title"].casefold():
            return False
    if not _text_matches(src.get("flair_filter", ""), entry.get("flair", "")):
        return False
    if layout == "each" and stated_minor_age(entry["title"]):
        return False
    author = src.get("author_filter", "")
    return not author or entry.get("author", "").casefold() == author.casefold()


def matches(entry, follow):
    """Whether a post belongs to the followed series across any of its feed sources."""
    sources = follow.get("sources")
    layout = follow.get("layout", "series")
    if sources:
        return any(
            matches_source(
                entry,
                {
                    "kind": s.get("kind", ""),
                    "subreddit": s.get("subreddit", ""),
                    "user": s.get("user", ""),
                    "query": s.get("query", ""),
                    "title_filter": s.get("title_filter") or follow.get("title_filter", ""),
                    "author_filter": s.get("author_filter") or follow.get("author_filter", ""),
                    "flair_filter": s.get("flair_filter") or follow.get("flair_filter", ""),
                },
                layout,
            )
            for s in sources
        )
    src = follow.get("source", {})
    rule = {
        "kind": src.get("kind", ""),
        "subreddit": src.get("subreddit", ""),
        "user": src.get("user", ""),
        "query": src.get("query", ""),
        "title_filter": follow.get("title_filter", ""),
        "author_filter": follow.get("author_filter", ""),
        "flair_filter": follow.get("flair_filter", ""),
    }
    return matches_source(entry, rule, layout)


def _text_matches(flt, text):
    if not flt:
        return True
    if flt.lower().startswith("re:"):
        try:
            return bool(re.search(flt[3:], text, re.IGNORECASE))
        except re.error:
            return False
    return flt.casefold() in text.casefold()


_AGE_TAGS = re.compile(r"[\[(]\s*[mfMF]?\s*(\d{1,2})\s*[mfMF]?\s*[\])]")
_AGE_WORDS = re.compile(r"(?i)\b(\d{1,2})\s*(?:yo|y/o|y\.o\.|years?[ -]old)\b")
_AGE_LETTER = re.compile(r"(?i)\b(\d{1,2})\s?[mf]\b")


def stated_minor_age(title):
    """True if the title states an age under 18 (such as [16M], (17), 15 yo)."""
    for pattern in (_AGE_TAGS, _AGE_WORDS, _AGE_LETTER):
        for m in pattern.finditer(title):
            if 1 <= int(m.group(1)) < 18:
                return True
    return False


class ChapterCache:
    """Posts already fetched for one followed series, backed by SQLite SeriesRegistry."""

    def __init__(self, folder, follow_id, registry=None):
        self.folder = folder
        self.follow_id = follow_id
        self.path = os.path.join(folder, f"{follow_id}.json")
        db_path = os.path.join(folder, "series_registry.db")
        if registry:
            self.registry = registry
        else:
            try:
                from .registry import SeriesRegistry
            except ImportError:
                try:
                    from registry import SeriesRegistry
                except ImportError:
                    import os as _os
                    import sys as _sys

                    _pkg_dir = _os.path.dirname(_os.path.abspath(__file__))
                    if _pkg_dir not in _sys.path:
                        _sys.path.insert(0, _pkg_dir)
                    from registry import SeriesRegistry
            self.registry = SeriesRegistry(db_path)

        self._series = self.registry.get_series(self.follow_id) or {}
        idx_data = {}
        if self._series.get("index_data"):
            try:
                idx_data = json.loads(self._series["index_data"])
            except Exception:
                pass

        sig = self._series.get("signature")
        if isinstance(sig, str) and sig:
            try:
                sig = json.loads(sig)
            except Exception:
                pass

        cached_chapters = {c["id"]: c for c in self.registry.get_chapters(self.follow_id)}
        self.data = {
            "posts": cached_chapters,
            "newest_created": float(self._series.get("newest_created") or 0.0),
            "newest_id": self._series.get("newest_id") or "",
            "complete": bool(self._series.get("complete", False)),
            "signature": sig,
            "index": idx_data,
            "added": list(self.registry.get_added_post_ids(self.follow_id)),
        }

    @property
    def posts(self):
        return self.data["posts"]

    def save(self):
        s_dict = self.registry.get_series(self.follow_id) or {"id": self.follow_id, "name": self.follow_id}
        s_dict["complete"] = self.data.get("complete", False)
        s_dict["newest_created"] = self.data.get("newest_created", 0.0)
        s_dict["newest_id"] = self.data.get("newest_id", "")
        if "signature" in self.data:
            s_dict["signature"] = self.data["signature"]
        if "index" in self.data:
            s_dict["index_data"] = json.dumps(self.data["index"])
        self.registry.save_series(s_dict)

        added_ids = set(self.data.get("added", []))
        for pid, post in self.posts.items():
            p_copy = dict(post)
            if pid in added_ids:
                p_copy["added"] = True
            self.registry.save_chapter_and_manifest(self.follow_id, p_copy)

        # Mirror JSON for legacy tool compatibility if path is used
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            json.dump(self.data, stream)
        os.replace(tmp, self.path)

    def ordered(self):
        manifest = self.registry.get_manifest(self.follow_id)
        if manifest:
            p_map = self.posts
            out = []
            for m in manifest:
                pid = m["post_id"]
                if pid in p_map:
                    out.append(p_map[pid])
            if out:
                return out
        return sorted(self.posts.values(), key=lambda p: (p["created"], p["title"]))

    def delete(self):
        self.registry.delete_series(self.follow_id)
        try:
            os.remove(self.path)
        except OSError:
            pass


def check_follow(source, follow, cache, max_pages=None, cancelled=lambda: False, progress=lambda msg: None):
    """Fetch new posts for one follow across all its configured feed sources into the cache."""
    if hasattr(cache, "registry") and cache.registry and follow.get("id"):
        cache.registry.save_series(follow)
    sources = follow.get("sources")
    if not sources:
        src = follow.get("source", {})
        sources = [
            {
                "kind": src.get("kind", "subreddit"),
                "subreddit": src.get("subreddit", ""),
                "user": src.get("user", ""),
                "query": src.get("query", ""),
                "title_filter": follow.get("title_filter", ""),
                "author_filter": follow.get("author_filter", ""),
                "flair_filter": follow.get("flair_filter", ""),
            }
        ]

    signature = [
        follow.get("title_filter", ""),
        follow.get("author_filter", ""),
        source_text(follow.get("source", {})),
        follow.get("flair_filter", ""),
        follow.get("layout", "series"),
        str(sources),
    ]
    if cache.data.get("signature") != signature:
        cache.data["complete"] = False
        cache.data["signature"] = signature

    first_run = not cache.data.get("complete")
    each = follow.get("layout") == "each"
    max_pages = max_pages or ((EACH_FIRST_PAGES if each else FIRST_RUN_PAGES) if first_run else 3)
    known_newest, known_id = cache.data["newest_created"], cache.data["newest_id"]
    new, changed, newest = [], [], (0.0, "")
    total_pages = 0

    try:
        for src_rule in sources:
            if cancelled():
                raise Cancelled()
            after, pages = None, 0
            while pages < max_pages:
                if cancelled():
                    raise Cancelled()
                progress(f"{follow['name']}: reading {describe_source(src_rule)} page {pages + 1}")
                entries, after = source.page(src_rule, after)
                pages += 1
                total_pages += 1
                reached_known = False
                for e in entries:
                    newest = max(newest, (e["created"], e["id"]))
                    if not first_run and (e["id"] == known_id or (known_newest and e["created"] < known_newest - 1)):
                        reached_known = True
                    if (
                        not first_run
                        and known_newest
                        and e["created"] < known_newest - 1
                        and e["id"] not in cache.posts
                    ):
                        continue
                    if not matches_source(e, src_rule, follow.get("layout", "series")):
                        continue
                    old = cache.posts.get(e["id"])
                    if old is None:
                        new.append(e["title"])
                    elif old["html"] != e["html"] or old["title"] != e["title"]:
                        changed.append(e["title"])
                        if e["id"] in cache.data.get("added", []):
                            cache.data["added"].remove(e["id"])
                    else:
                        continue
                    cache.posts[e["id"]] = {
                        k: e.get(k, "") for k in ("id", "title", "author", "created", "link", "html", "flair")
                    }
                    if old is not None and "note" in old:
                        cache.posts[e["id"]]["note"] = old["note"]
                if not entries or not after or reached_known:
                    if first_run and (not entries or not after):
                        cache.data["complete"] = True
                    break

        if each and first_run:
            cache.data["complete"] = True
            keep = {
                p["id"] for p in sorted(cache.posts.values(), key=lambda p: p["created"], reverse=True)[:EACH_FIRST_RUN]
            }
            for post_id in [i for i in cache.posts if i not in keep]:
                del cache.posts[post_id]
            new = [t for t in new if any(p["title"] == t for p in cache.posts.values())]
    finally:
        if newest[0]:
            cache.data["newest_created"], cache.data["newest_id"] = (
                max(known_newest, newest[0]),
                newest[1] if newest[0] >= known_newest else known_id,
            )
        cache.save()

    pending = fetch_notes(source, follow, cache, cancelled, progress)
    refresh_index(source, follow, cache)
    return {"new": new, "changed": changed, "pages": total_pages, "notes_pending": pending}


def fetch_notes(source, follow, cache, cancelled=lambda: False, progress=lambda msg: None, batch=None):
    """Fetch the author's own comment for cached chapters that lack one."""
    if not follow.get("author_note") or not hasattr(source, "author_comment"):
        return 0
    todo = [p for p in sorted(cache.posts.values(), key=lambda p: p["created"], reverse=True) if "note" not in p]
    done = 0
    try:
        for post in todo[: batch or NOTE_BATCH]:
            if cancelled():
                raise Cancelled()
            progress(f"{follow['name']}: author comment {done + 1} of {min(len(todo), batch or NOTE_BATCH)}")
            post["note"] = source.author_comment(post)
            done += 1
    finally:
        if done:
            cache.save()
    return len(todo) - done


_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
_BLANK_PARA = re.compile(r"<p>(?:\s|&nbsp;|&#8203;|&#x200[bB];|[" + _ZERO_WIDTH + r"])*</p>")
_PARA = re.compile(r"<p>(.*?)</p>", re.DOTALL)
_MARKER = re.compile(r"(?i)^[~*\[(]*\s*(?:first|second|third|early|1st)\s*[~*\])]*[.!]*$")


def _plain(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip(" " + _ZERO_WIDTH)


def _looks_like_title(text):
    if len(text) > 80 or "(" in text or text.startswith(('"', "\u201c", "\u2018")):
        return False
    if text.endswith("!"):
        words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\u2019]+", text) if len(w) > 3]
        return bool(words) and all(w[0].isupper() for w in words)
    return not text.endswith((".", "?", ",", ";", ":", '"', "\u201d", "\u2026"))


def split_opening(body_html):
    """Look at how a post opens. Returns (title, notes, body)."""
    body_html = _BLANK_PARA.sub("", body_html)
    title, notes, cut, end = "", [], [], 0
    for number, match in enumerate(_PARA.finditer(body_html)):
        if number >= 4:
            break
        text = _plain(match.group(1))
        if _MARKER.match(text):
            cut.append(match.span())
        elif (
            text.startswith("(")
            and text.endswith(")")
            or (text.startswith("[") and text.endswith("]") and len(text) > 40)
        ):
            inner = match.group(1).strip()
            if inner[:1] in "([" and inner[-1:] in ")]":
                inner = inner[1:-1].strip()
            notes.append(inner)
            cut.append(match.span())
        elif not title and _looks_like_title(text):
            title = text
            cut.append(match.span())
        else:
            break
    if not title and not notes and not cut:
        return "", [], body_html
    for start, stop in reversed(cut):
        body_html = body_html[:start] + body_html[stop:]
    return title, notes, body_html


def split_body_title(body_html):
    """(title, body with the title, markers and notes taken out); see split_opening."""
    title, _, body = split_opening(body_html)
    return title, body


def author_aside(label, parts):
    return (
        f'<div class="author-note"><p class="author-note-label">{label}</p>'
        + "".join(part if part.lstrip().startswith("<") else f"<p>{part}</p>" for part in parts)
        + "</div>"
    )


def chapter_html(post, title_from_body=False):
    body, notes = _BLANK_PARA.sub("", post["html"] or ""), []
    if title_from_body:
        _, notes, body = split_opening(body)
    body = body or "<p>(empty)</p>"
    if notes:
        body = author_aside("Author's note", notes) + body
    if post.get("note"):
        body += author_aside("Author's comment", [post["note"]])
    return body


def build_story(follow, cache):
    """A story dict for storykit.build_epub from everything cached: multi-author attribution and sequence ordering."""
    posts = cache.ordered()
    if not posts:
        raise ValueError("Nothing has been collected for this series yet.")
    titles, from_body = {}, follow.get("title_from_body")
    if (cache.data.get("index") or {}).get("rows"):
        matched, _, _ = match_index(posts, cache.data["index"]["rows"])
        titles = {pid: index_title(row, None, with_note=not from_body) for pid, row in matched.items()}

    # Collect distinct contributing authors
    authors_list = []
    for p in posts:
        a = (p.get("author") or "Unknown").strip()
        if a and a not in authors_list:
            authors_list.append(a)

    is_multi_author = len(authors_list) > 1

    def section(p):
        name = titles.get(p["id"]) or p["title"]
        chapter = split_body_title(p["html"] or "")[0] if from_body else ""
        sec_title = f"{name} \u2013 {chapter}" if chapter else name
        if is_multi_author and p.get("author"):
            sec_title += f" (by {p['author']})"
        return sec_title, chapter_html(p, from_body)

    main_author = ", ".join(authors_list) if authors_list else "Unknown"
    last = datetime.fromtimestamp(posts[-1]["created"], UTC).date().isoformat()
    first = datetime.fromtimestamp(posts[0]["created"], UTC).date().isoformat()

    subreddits = set()
    for src in follow.get("sources", [follow.get("source", {})]):
        if src.get("subreddit"):
            subreddits.add(f"r/{src['subreddit']}")
    tags = ["Reddit"] + sorted(list(subreddits))

    source_desc = (
        f"{len(authors_list)} author(s) ({', '.join(authors_list)})"
        if is_multi_author
        else describe_source(follow.get("source", {}))
    )
    return {
        "id": "reddit-" + follow["id"],
        "url": posts[-1]["link"] or WWW,
        "title": follow["name"],
        "author": main_author,
        "sections": [section(p) for p in posts],
        "tags": tags,
        "categories": [],
        "summary": f"{len(posts)} chapters collected from Reddit ({source_desc}), {first} to {last}.",
        "publisher": PUBLISHER,
        "pubdate": first,
    }


_PART = re.compile(r"(?i)^(?P<stem>.*?)[\s,:;\-\u2013\u2014(\[]*\b(?:part|pt\.?|chapter|ch\.?)\s*(?P<n>\d+)\b")


def build_each(follow, post):
    """One post as its own story dict: flair becomes tags ('TRUE STORY - Uncle' -> 'TRUE STORY', 'Uncle'), and a title like
    'Name, Part 3' joins the Calibre series 'Name' (index 3)."""
    m = re.search(r"/r/([^/]+)/", post.get("link", ""))
    tags = ["Reddit"] + ([f"r/{m.group(1)}"] if m else [])
    for piece in re.split(r"\s+[-\u2013\u2014]\s+", post.get("flair", "")):
        if piece.strip():
            tags.append(piece.strip())
    story = {
        "id": "reddit-" + post["id"],
        "url": post.get("link") or WWW,
        "title": post["title"],
        "author": post.get("author") or "Unknown",
        "sections": [(post["title"], chapter_html(post))],
        "tags": list(dict.fromkeys(tags)),
        "categories": [],
        "summary": "",
        "publisher": PUBLISHER,
        "pubdate": datetime.fromtimestamp(post["created"], UTC).date().isoformat(),
    }
    part = _PART.match(post["title"])
    if part and len(re.sub(r"\W", "", part.group("stem"))) >= 4:
        story["series"], story["series_index"] = part.group("stem").strip(" ,:;-\u2013\u2014"), float(part.group("n"))
    return story


def pending_each(follow, cache, limit=None):
    """Cached posts not yet made into library books, oldest first (at most `limit`)."""
    added = set(cache.data.get("added", []))
    return [p for p in cache.ordered() if p["id"] not in added][: limit or EACH_PER_RUN]


def build_epub(story):
    return _build_epub([story], title=story["title"], publisher=PUBLISHER)


# ---------------------------------------------------------------- running checks for several follows


def due(follow, hours, now=None):
    """Whether a follow has not been checked within the interval. Never less often than MIN_CHECK_HOURS apart."""
    if not follow.get("last_checked"):
        return True  # never checked
    now = time.time() if now is None else now
    if follow.get("notes_pending"):
        hours = NOTES_GAP_HOURS  # still collecting author comments: carry on soon, a few pages of listing per check
        return now - follow["last_checked"] >= hours * 3600
    return now - follow["last_checked"] >= max(hours, MIN_CHECK_HOURS) * 3600


def run_follows(source, follows, cache_dir, cancelled=lambda: False, progress=lambda msg: None, now=time.time):
    """Check each follow in turn. Returns a list of {'follow', 'new', 'changed', 'error', 'rate_limited', 'retry_after'}.

    A rate limit ends the whole run (the remaining follows are reported as skipped) because the limit applies to
    every request we make. Whatever was read before the limit is kept in the cache."""
    results = []
    for index, follow in enumerate(follows):
        result = {
            "follow": follow,
            "new": [],
            "changed": [],
            "error": "",
            "rate_limited": False,
            "retry_after": None,
            "skipped": False,
        }
        cache = ChapterCache(cache_dir, follow["id"])
        try:
            outcome = check_follow(source, follow, cache, cancelled=cancelled, progress=progress)
            result["new"], result["changed"] = outcome["new"], outcome["changed"]
            follow["last_checked"] = now()
            follow["notes_pending"] = bool(outcome["notes_pending"])
            follow["last_status"] = f"{len(outcome['new'])} new chapter(s)" if outcome["new"] else "up to date"
            if outcome["notes_pending"]:
                follow["last_status"] += f"; {outcome['notes_pending']} author comment(s) still to fetch"
            elif follow.get("author_note") and not hasattr(source, "author_comment"):
                follow["last_status"] += "; author comments need the official API"
            if follow.get("index_url"):
                report = index_report(follow, cache)
                if report["error"]:
                    follow["last_status"] += f"; chapter index not read ({report['error'][:60]})"
                elif report["missing"]:
                    follow["last_status"] += f"; {len(report['missing'])} chapter(s) in the index have no Reddit post"
                elif report["rows"]:
                    follow["last_status"] += "; matches the chapter index"
        except RateLimited as exc:
            result.update(rate_limited=True, retry_after=exc.retry_after, error=str(exc))
            follow["last_status"] = "Reddit asked us to slow down; will try again later"
            results.append(result)
            results.extend(
                {
                    "follow": f,
                    "new": [],
                    "changed": [],
                    "error": "",
                    "rate_limited": False,
                    "retry_after": None,
                    "skipped": True,
                }
                for f in follows[index + 1 :]
            )
            return results
        except Cancelled:
            raise
        except (OSError, ValueError) as exc:
            result["error"] = str(exc)
            follow["last_status"] = f"error: {exc}"[:200]
        results.append(result)
    return results
