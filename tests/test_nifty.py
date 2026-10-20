import importlib.util
import io
import sys
import unittest
import xml.dom.minidom
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
spec = importlib.util.spec_from_file_location("ncore", Path(__file__).resolve().parents[1] / "nifty/core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

TABLE = (
    "<table><tr><th>Size</th><th>Date</th><th>Filename</th></tr>"
    '<tr><td>44K</td><td>Oct 4 15:53</td><td><a href="saga-10">saga-10</a></td></tr>'
    '<tr><td>3K</td><td>Oct 3 1999</td><td><a href="saga-2">saga-2</a></td></tr>'
    '<tr><td>3K</td><td>Oct 2 1999</td><td><a href="saga-1">saga-1</a></td></tr>'
    '<tr><td>7K</td><td>Sep 1 2020</td><td><a href="lone-story">lone-story</a></td></tr>'
    '<tr><td>Dir</td><td>Sep 1 2020</td><td><a href="more-saga/">more-saga/</a></td></tr></table>'
)
DIVS = (
    '<div class="ftr" role="row"><div>Dir</div><div>Oct 7 07:25</div><div><a href="campus-rivals/">campus-rivals/</a></div></div>'
    '<div class="ftr" role="row"><div>7K</div><div>Oct 4 16:39</div><div><a href="my-story">my-story</a></div></div>'
    '<div class="ftr"><div>Dir</div><div>Oct 4</div><div><a href="/nifty/gay/">parent</a></div></div>'
)

CHAPTER = (
    "Date: Thu, 1 Oct 2026 11:00:00 +0000\nFrom: The Writer <writer@example.com>\nSubject: Saga: Chapter {n} (Gay/Adult/College)\n\n"
    "Opening line that is wrapped\nacross two lines & more.\n\n_______\n\nRoses\nViolets\nSugar\n\nEnd.\n"
)


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.requested = pages, []

    def get(self, url):
        self.requested.append(url)
        if url not in self.pages:
            raise OSError("missing " + url)
        return self.pages[url]


class AddressTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(
            core.classify("https://www.nifty.org/nifty/gay/college/saga/saga-3"), "gay/college/saga/saga-3"
        )
        self.assertEqual(core.classify("nifty.org/nifty/gay/college/"), "gay/college/")
        self.assertIsNone(core.classify("https://www.nifty.org/nifty/new.html"))
        self.assertIsNone(core.classify("https://example.com/nifty/gay/"))
        self.assertIsNone(core.classify("https://www.nifty.org/other/gay/"))
        self.assertIsNone(core.classify("https://www.nifty.org/nifty/gay/../x"))


class ListingTests(unittest.TestCase):
    def test_both_layouts(self):
        rows = core.parse_listing(TABLE)
        self.assertEqual([r["name"] for r in rows], ["saga-10", "saga-2", "saga-1", "lone-story", "more-saga"])
        self.assertTrue(rows[-1]["is_dir"])
        divs = core.parse_listing(DIVS)
        self.assertEqual([r["name"] for r in divs], ["campus-rivals", "my-story"])  # parent link skipped

    def test_grouping_sorts_numerically(self):
        groups = dict(core.group_files(core.parse_listing(TABLE)))
        self.assertEqual(groups["saga"], ["saga-1", "saga-2", "saga-10"])
        self.assertEqual(groups["lone-story"], ["lone-story"])

    def test_categories(self):
        page = '<a href="/nifty/gay/college/">c</a><a href="/nifty/gay/college/">c</a><a href="/nifty/gay/camping/">x</a><a href="/nifty/lesbian/x/">y</a>'
        self.assertEqual(core.parse_categories(page, "gay"), ["college", "camping"])

    def test_expand_dir_and_targets(self):
        f = FakeFetcher({core.SITE + "gay/college/": TABLE})
        refs = {r["id"]: r for r in core.expand_dir(f, "gay/college")}
        self.assertEqual(
            refs["gay/college/saga"]["files"], ["gay/college/saga-1", "gay/college/saga-2", "gay/college/saga-10"]
        )
        self.assertIsNone(refs["gay/college/more-saga"]["files"])
        found, errors = core.expand_targets(
            f, ["https://www.nifty.org/nifty/gay/college/saga-2", "https://www.nifty.org/nifty/gay/", "junk"]
        )
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]["files"]), 3)  # a chapter link means the whole series
        self.assertEqual(len(errors), 2)


class TextTests(unittest.TestCase):
    def test_headers_and_author(self):
        headers, body = core.split_headers(CHAPTER.format(n=1))
        self.assertEqual(headers["subject"], "Saga: Chapter 1 (Gay/Adult/College)")
        self.assertTrue(body.startswith("Opening line"))
        self.assertEqual(core.split_headers("Just a story\nwith no headers")[0], {})
        self.assertEqual(core.author_name("The Writer <writer@example.com>"), "The Writer")
        self.assertEqual(core.author_name("writer@example.com (Real Name)"), "Real Name")

    def test_titles(self):
        self.assertEqual(core.clean_title("Saga: Chapter 12 (Gay/Adult/College)", "x"), "Saga")
        self.assertEqual(core.clean_title("Epic 3", "x"), "Epic")
        self.assertEqual(core.clean_title("Tale Pt.2", "x"), "Tale")
        self.assertEqual(core.clean_title("The Magic Box (part one) by Sasha", "x"), "The Magic Box")
        self.assertEqual(core.clean_title("MY KINKY STORIES - CHAPTER ONE", "x"), "MY KINKY STORIES")
        self.assertEqual(core.clean_title("Tara (Lesbian/Domination, oral", "x"), "Tara")
        self.assertEqual(core.clean_title("Book Store (bisexual, interracial)", "x"), "Book Store")
        self.assertEqual(core.clean_title("", "Fallback"), "Fallback")
        self.assertEqual(core.clean_title("Chapter 1", "Fallback"), "Fallback")

    def test_html_chapters_and_generic_names(self):
        self.assertEqual(core.split_chapter("small-town-2.html"), ("small-town", 2))
        self.assertEqual(core.split_chapter("chapter05"), ("chapter", 5))
        self.assertEqual(core.book_title("chapter", "joe_bates_saga"), "Joe Bates Saga")
        self.assertEqual(core.book_title("saga", "college"), "Saga")
        raw = "<html><head><title>The Chance</title></head><body><p>One<p>Two <b>x</b></p><script>bad()</script></body></html>"
        self.assertTrue(core.looks_like_html(raw))
        self.assertFalse(core.looks_like_html("Plain story text with a < sign"))
        self.assertEqual(core.html_title(raw), "The Chance")
        out = core.html_body(raw)
        xml.dom.minidom.parseString(f"<r>{out}</r>")
        self.assertNotIn("bad", out)

    def test_scene_breaks(self):
        for rule in ("***", "* * *", "____", "-----", "=========="):
            self.assertEqual(core.text_to_html(f"One.\n\n{rule}\n\nTwo."), "<p>One.</p><hr/><p>Two.</p>", rule)
        self.assertEqual(core.text_to_html("One.\n\n...\n\nTwo."), "<p>One.</p><p>...</p><p>Two.</p>")

    def test_text_to_html(self):
        out = core.text_to_html("Wrapped\nline & more.\n\n____\n\nRoses\nViolets\nSugar")
        self.assertEqual(out, "<p>Wrapped line &amp; more.</p><hr/><p>Roses<br/>Violets<br/>Sugar</p>")


class DownloadTests(unittest.TestCase):
    def pages(self):
        pages = {core.SITE + "gay/college/": TABLE}
        for n in (1, 2, 10):
            pages[core.SITE + f"gay/college/saga-{n}"] = CHAPTER.format(n=n)
        pages[core.SITE + "gay/college/lone-story"] = (
            "Date: Mon, 5 Jan 2026 10:00:00 +0000\nFrom: a@b.org\nSubject: Lone\n\nOnly text.\n"
        )
        return pages

    def test_series_becomes_one_book_with_chapters(self):
        f = FakeFetcher(self.pages())
        ref = [r for r in core.expand_dir(f, "gay/college") if r["title"] == "Saga"][0]
        story = core.fetch_book(f, ref)
        self.assertEqual((story["title"], story["author"], story["pubdate"]), ("Saga", "The Writer", "2026-10-01"))
        self.assertEqual([h for h, _ in story["sections"]], ["Chapter 1", "Chapter 2", "Chapter 10"])
        self.assertEqual(story["tags"], ["Gay", "College"])
        data = core.build_epub([story])
        z = zipfile.ZipFile(io.BytesIO(data))
        for name in z.namelist():
            if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                xml.dom.minidom.parseString(z.read(name))
        self.assertIn("<dc:publisher>Nifty</dc:publisher>", z.read("OEBPS/content.opf").decode())

    def test_single_story_and_skip_ids(self):
        f = FakeFetcher(self.pages())
        ref = [r for r in core.expand_dir(f, "gay/college") if r["title"] == "Lone Story"][0]
        stories, skipped = core.download_ref(f, ref)
        self.assertEqual((len(stories), skipped, stories[0]["author"], stories[0]["sections"][0][0]), (1, 0, "a", ""))
        stories, skipped = core.download_ref(f, ref, skip_ids={"gay/college/lone-story"})
        self.assertEqual((len(stories), skipped), (0, 1))

    def test_chapter_range_filtering(self):
        f = FakeFetcher(self.pages())
        ref = [r for r in core.expand_dir(f, "gay/college") if r["title"] == "Saga"][0]
        stories, _ = core.download_ref(f, ref, chapter_range="1-2")
        self.assertEqual([h for h, _ in stories[0]["sections"]], ["Chapter 1", "Chapter 2"])
        self.assertNotIn(core.SITE + "gay/college/saga-10", f.requested)

        f_open = FakeFetcher(self.pages())
        stories, _ = core.download_ref(f_open, ref, chapter_range="3+")
        self.assertEqual([h for h, _ in stories[0]["sections"]], ["Chapter 10"])
        self.assertIn(core.SITE + "gay/college/saga-10", f_open.requested)
        self.assertNotIn(core.SITE + "gay/college/saga-1", f_open.requested)


class ChapterRangeTests(unittest.TestCase):
    def test_parse_chapter_range(self):
        self.assertEqual(core.parse_chapter_range("1-5", 10), {0, 1, 2, 3, 4})
        self.assertEqual(core.parse_chapter_range("5+", 10), {4, 5, 6, 7, 8, 9})
        self.assertEqual(core.parse_chapter_range("5-", 10), {4, 5, 6, 7, 8, 9})
        self.assertEqual(core.parse_chapter_range("-3", 10), {0, 1, 2})
        self.assertEqual(core.parse_chapter_range("1-2, 8, 10+", 10), {0, 1, 7, 9})
        self.assertEqual(core.parse_chapter_range("", 10), set(range(10)))
        self.assertEqual(core.parse_chapter_range(None, 10), set(range(10)))
        self.assertEqual(core.parse_chapter_range("invalid text", 10), set(range(10)))
        self.assertEqual(core.parse_chapter_range("50", 10), set())
        self.assertEqual(core.parse_chapter_range("1-2, 50", 10), {0, 1})


class PreviewGroupTests(unittest.TestCase):
    def test_build_preview_groups(self):
        refs = [
            {
                "id": "gay/college/saga",
                "title": "Saga",
                "files": ["gay/college/saga-1", "gay/college/saga-2", "gay/college/saga-10"],
            },
            {"id": "gay/college/lone-story", "title": "Lone Story", "files": ["gay/college/lone-story"]},
        ]
        groups = core.build_preview_groups(refs, combine=False)
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["title"], "Saga")
        self.assertEqual(len(groups[0]["chapters"]), 3)
        self.assertEqual(groups[1]["title"], "Lone Story")
        self.assertEqual(len(groups[1]["chapters"]), 1)

        combined = core.build_preview_groups(refs, combine=True, title="College Stories Omnibus")
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["title"], "College Stories Omnibus")
        self.assertEqual(len(combined[0]["chapters"]), 4)

    def test_fetch_group(self):
        pages = {
            core.SITE + "gay/college/saga-1": CHAPTER.format(n=1),
            core.SITE + "gay/college/saga-2": CHAPTER.format(n=2),
        }
        f = FakeFetcher(pages)
        group = {
            "id": "gay/college/saga",
            "title": "Saga Custom Title",
            "chapters": [
                {"file": "gay/college/saga-1", "title": "Chapter 1", "enabled": True},
                {"file": "gay/college/saga-2", "title": "Chapter 2", "enabled": True},
            ],
        }
        story = core.fetch_group(f, group)
        self.assertIsNotNone(story)
        self.assertEqual(story["title"], "Saga Custom Title")
        self.assertEqual(len(story["sections"]), 2)


if __name__ == "__main__":
    unittest.main()


class RateLimitTests(unittest.TestCase):
    def test_429_raises_immediately_with_retry_after_and_is_not_retried(self):
        import urllib.error

        calls = []

        class Headers(dict):
            def get(self, k, d=None):
                return dict.get(self, k, d)

        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", Headers({"Retry-After": "120"}), None)

        fetcher = core.Fetcher(min_interval=0, opener=opener)
        with self.assertRaises(core.RateLimited) as caught:
            fetcher.get("https://example.org/x")
        self.assertEqual(caught.exception.retry_after, 120)
        self.assertEqual(len(calls), 1, "a rate limit must not be hammered with retries")
        self.assertIsInstance(caught.exception, IOError, "callers that catch IOError keep working")


class PrefetchTests(unittest.TestCase):
    def test_extract_directory_urls(self):
        lines = [
            "https://www.nifty.org/nifty/gay/college/saga-1",
            "https://www.nifty.org/nifty/gay/college/saga-2",
            "https://www.nifty.org/nifty/gay/sports/jock-1",
            "https://www.nifty.org/nifty/gay/college/lone-story",
            "https://www.nifty.org/nifty/lesbian/school/",
            "https://www.nifty.org/nifty/gay/",
            "not a valid url",
        ]
        urls = core.extract_directory_urls(lines)
        expected = [
            "https://www.nifty.org/nifty/gay/college/",
            "https://www.nifty.org/nifty/gay/sports/",
            "https://www.nifty.org/nifty/lesbian/school/",
        ]
        self.assertEqual(urls, expected)

    def test_bounded_worker_pool_concurrency(self):
        import threading
        import time

        lock = threading.Lock()
        active = 0
        peak_active = 0

        pages = {}
        for i in range(8):
            pages[core.SITE + f"gay/folder{i}/"] = TABLE

        class SlowFetcher:
            def __init__(self, pages):
                self.pages = pages
                self.requested = []

            def cancelled(self):
                return False

            def get(self, url):
                nonlocal active, peak_active
                with lock:
                    active += 1
                    peak_active = max(peak_active, active)
                time.sleep(0.05)
                with lock:
                    active -= 1
                    self.requested.append(url)
                return self.pages.get(url, TABLE)

        fetcher = SlowFetcher(pages)
        lines = [f"https://www.nifty.org/nifty/gay/folder{i}/story-1" for i in range(8)]
        found, errors = core.expand_targets(fetcher, lines)
        self.assertEqual(len(found), 8)
        self.assertEqual(len(errors), 0)
        self.assertLessEqual(peak_active, 4, f"Peak active workers was {peak_active}, expected <= 4")

    def test_prefetch_cancellation_speed(self):
        import time

        class CancellingFetcher:
            def __init__(self):
                self.cancel_flag = False

            def cancelled(self):
                return self.cancel_flag

            def get(self, url):
                time.sleep(0.5)
                return TABLE

        fetcher = CancellingFetcher()
        fetcher.cancel_flag = True

        start = time.monotonic()
        lines = [f"https://www.nifty.org/nifty/gay/folder{i}/story-1" for i in range(5)]
        with self.assertRaises(core.Cancelled):
            core.expand_targets(fetcher, lines)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.1, f"Cancellation took {elapsed:.3f}s, expected < 100ms")

    def test_multi_folder_prefetch_performance(self):
        import time

        pages = {}
        for i in range(5):
            pages[core.SITE + f"gay/folder{i}/"] = TABLE

        class NetworkFetcher:
            def __init__(self, pages):
                self.pages = pages

            def cancelled(self):
                return False

            def get(self, url):
                time.sleep(0.1)  # Simulate 100ms network latency per fetch
                return self.pages[url]

        fetcher = NetworkFetcher(pages)
        lines = [f"https://www.nifty.org/nifty/gay/folder{i}/story-1" for i in range(5)]
        start = time.monotonic()
        found, errors = core.expand_targets(fetcher, lines)
        elapsed = time.monotonic() - start

        self.assertEqual(len(found), 5)
        self.assertEqual(len(errors), 0)
        self.assertLess(elapsed, 2.0, f"Target expansion took {elapsed:.3f}s, expected < 2.0s")

    def test_target_expansion_equivalence(self):
        pages = {core.SITE + "gay/college/": TABLE, core.SITE + "gay/highschool/": TABLE}
        f = FakeFetcher(pages)
        lines = [
            "https://www.nifty.org/nifty/gay/college/saga-2",
            "https://www.nifty.org/nifty/gay/highschool/saga-1",
            "https://www.nifty.org/nifty/gay/college/lone-story",
            "https://www.nifty.org/nifty/gay/",
            "invalid line",
        ]
        found, errors = core.expand_targets(f, lines)
        self.assertEqual(len(found), 3)
        titles = {r["title"] for r in found}
        self.assertIn("Saga", titles)
        self.assertIn("Lone Story", titles)
        self.assertEqual(len(errors), 2)

    def test_worker_exception_handling(self):
        pages = {core.SITE + "gay/college/": TABLE}

        class ExceptionFetcher:
            def __init__(self, pages):
                self.pages = pages

            def cancelled(self):
                return False

            def get(self, url):
                if "badfolder" in url:
                    raise OSError("HTTP 404: Not Found")
                return self.pages[url]

        f = ExceptionFetcher(pages)
        lines = ["https://www.nifty.org/nifty/gay/college/saga-1", "https://www.nifty.org/nifty/gay/badfolder/story-1"]
        found, errors = core.expand_targets(f, lines)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("HTTP 404: Not Found", errors[0])
