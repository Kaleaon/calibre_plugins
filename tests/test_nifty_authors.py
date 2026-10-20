import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
spec = importlib.util.spec_from_file_location(
    "ncore", Path(__file__).resolve().parents[1] / "nifty/core.py"
)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def panel(anchor, name, *entries):
    lis = "\n".join(f'<li><a href="{href}">{title}</a>' for href, title in entries)
    return (
        f'<div id="{anchor}" class="panel panel-default"><div class="panel-heading"><h4 class="panel-title">{name}</h4></div>'
        f'<div class="panel-body"><ul>\n{lis}\n</ul></div></div>'
    )


AUTHORS_PAGE = panel(
    "writer",
    "Writer &amp; Co",
    ("/nifty/gay/college/texas-tails/", "Texas Tails"),
    ("/nifty/gay/highschool/jeremiah", "Texas Tails: Jeremiah"),
    ("/nifty/gay/college/other-one.html", "Other &amp; One"),
    ("/nifty/gay/college/other-one", "Other &amp; One"),
    ("/nifty/lesbian/beginnings/elsewhere", "Elsewhere"),
) + panel("solo", "Solo Author", ("/nifty/gay/camping/lone-wolf", "Lone Wolf"))
PROLIFIC_PAGE = panel(
    "writer",
    "Writer &amp; Co",
    ("/nifty/gay/college/texas-tails/", "Texas Tails"),
    ("/nifty/gay/sf-fantasy/third-story", "A Third Story"),
)


def stories(*pairs):
    return [{"title": t, "path": p, "dir": False} for t, p in pairs]


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.requested = pages, []

    def get(self, url):
        self.requested.append(url)
        if url not in self.pages:
            raise OSError("missing " + url)
        return self.pages[url]


class AuthorsDirectoryTests(unittest.TestCase):
    def test_paths_are_normalised_and_duplicates_dropped(self):
        authors = core.parse_authors(AUTHORS_PAGE)
        self.assertEqual([a["name"] for a in authors], ["Writer & Co", "Solo Author"])
        writer = authors[0]["stories"]
        self.assertEqual(
            [s["path"] for s in writer],
            [
                "gay/college/texas-tails",
                "gay/highschool/jeremiah",
                "gay/college/other-one",
                "lesbian/beginnings/elsewhere",
            ],
        )
        self.assertTrue(writer[0]["dir"])
        self.assertFalse(writer[1]["dir"])
        self.assertEqual(writer[2]["title"], "Other & One")
        self.assertEqual(core.author_sections(authors[0]), ["gay", "lesbian"])

    def test_both_pages_merge_without_repeating_stories(self):
        f = FakeFetcher(
            {
                core.SITE + "authors.html": AUTHORS_PAGE,
                core.SITE + "prolific.html": PROLIFIC_PAGE,
            }
        )
        authors = core.load_authors(f)
        writer = next(a for a in authors if a["id"] == "writer")
        paths = [s["path"] for s in writer["stories"]]
        self.assertEqual(len(paths), len(set(paths)))
        self.assertIn("gay/sf-fantasy/third-story", paths)
        self.assertEqual(
            [a["name"] for a in authors],
            ["Solo Author", "Writer & Co"],
            "sorted by name",
        )

    def test_norm_path(self):
        for href in (
            "/nifty/gay/x/y",
            "/nifty/gay/x/y/",
            "/nifty/gay/x/y.html",
            "/nifty/gay/x/y.htm#top",
        ):
            self.assertEqual(core.norm_path(href), "gay/x/y")


class SeriesSuggestionTests(unittest.TestCase):
    def names(self, series):
        return {e["name"]: [s["title"] for s in e["stories"]] for e in series}

    def test_colon_numbered_and_extension_sets_across_folders(self):
        series, rest = core.suggest_series(
            stories(
                ("Texas Tails", "gay/young-friends/texas-tails"),
                ("Texas Tails: Roy and John", "gay/highschool/roy-and-john"),
                ("Texas Tails: Jeremiah", "gay/highschool/jeremiah"),
                ("The Beau Cycle 8", "gay/incest/the-beau-cycle-8"),
                ("The Beau Cycle 6", "gay/highschool/the-beau-cycle-6"),
                ("The Beau Cycle 7", "gay/highschool/the-beau-cycle-7"),
                ("Valley Boys", "gay/beginnings/valley-boys"),
                ("Valley Boys Rugby Tour", "gay/athletics/valley-boys-rugby-tour"),
                ("Pornman II", "gay/a/pornman-ii"),
                ("Pornman III", "gay/a/pornman-iii"),
                ("Pornman 4", "gay/a/pornman-4"),
                ("Unrelated Story", "gay/college/unrelated"),
            )
        )
        by_name = self.names(series)
        self.assertEqual(
            by_name["Texas Tails"],
            ["Texas Tails", "Texas Tails: Jeremiah", "Texas Tails: Roy and John"],
        )
        self.assertEqual(
            by_name["The Beau Cycle"],
            ["The Beau Cycle 6", "The Beau Cycle 7", "The Beau Cycle 8"],
            "ordered by number",
        )
        self.assertEqual(
            by_name["Valley Boys"], ["Valley Boys", "Valley Boys Rugby Tour"]
        )
        self.assertEqual(len(by_name["Pornman"]), 3)
        self.assertEqual([s["title"] for s in rest], ["Unrelated Story"])

    def test_author_habits_are_not_series(self):
        series, rest = core.suggest_series(
            stories(
                ("Night with Mark", "gay/a/night-with-mark"),
                ("Night with Mike", "gay/b/night-with-mike"),
                ("Sex with Corey", "gay/a/sex-with-corey"),
                ("Sex with My Uncle", "gay/b/sex-with-my-uncle"),
                ("Call Me Gangsta", "gay/a/call-me-gangsta"),
                ("Call Me Paxton", "gay/b/call-me-paxton"),
            )
        )
        self.assertEqual(series, [])
        self.assertEqual(len(rest), 6)

    def test_alternate_versions_are_not_a_series(self):
        series, _ = core.suggest_series(
            stories(
                ("Bed Buddies", "gay/a/bed-buddies"),
                ("Bed Buddies (Revised)", "gay/b/bed-buddies-revised"),
                ("The Only Boy On Calisto", "gay/a/calisto"),
                ("The Only Boy On Calisto [Original]", "gay/b/calisto-original"),
                ("Lost in Ireland's Rural Village", "gay/a/lost"),
                ("Lost in Ireland's Rural Village (redux)", "gay/b/lost-redux"),
            )
        )
        self.assertEqual(series, [])

    def test_short_or_generic_names_do_not_form_sets(self):
        series, _ = core.suggest_series(
            stories(
                ("Dad", "gay/a/dad"),
                ("Dad: Part 2", "gay/b/dad-2"),
                ("The Boys", "gay/a/boys"),
                ("The Boys Next Door", "gay/b/boys-next-door"),
            )
        )
        self.assertEqual(series, [])

    def test_empty_and_single(self):
        self.assertEqual(core.suggest_series([]), ([], []))
        series, rest = core.suggest_series(stories(("Only One", "gay/a/one")))
        self.assertEqual((series, len(rest)), ([], 1))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.a = {"title": "Series One", "path": "gay/a/series-one", "dir": True}
        self.b = {"title": "Series Two", "path": "gay/b/series-two", "dir": False}
        self.c = {"title": "Alone", "path": "gay/c/alone", "dir": False}

    def test_story_address(self):
        self.assertEqual(core.story_address(self.a), core.SITE + "gay/a/series-one/")
        self.assertEqual(core.story_address(self.b), core.SITE + "gay/b/series-two")

    def test_sets_combine_and_singles_stay_single(self):
        sel = core.build_selection([("Series", [self.a, self.b])], [self.c])
        self.assertEqual([i["title"] for i in sel], ["Series", None])
        self.assertEqual(len(sel[0]["addresses"]), 2)

    def test_one_ticked_story_of_a_set_is_just_a_story(self):
        sel = core.build_selection([("Series", [self.a])], [])
        self.assertEqual(
            sel, [{"title": None, "addresses": [core.story_address(self.a)]}]
        )

    def test_combining_can_be_switched_off(self):
        sel = core.build_selection(
            [("Series", [self.a, self.b])], [], combine_series=False
        )
        self.assertEqual([i["title"] for i in sel], [None, None])

    def test_combine_everything_into_one_titled_book(self):
        sel = core.build_selection(
            [("Series", [self.a, self.b])],
            [self.c],
            combine_all_title="  My Collection ",
        )
        self.assertEqual(len(sel), 1)
        self.assertEqual(
            (sel[0]["title"], len(sel[0]["addresses"])), ("My Collection", 3)
        )
        self.assertEqual(core.build_selection([], [], combine_all_title="Empty"), [])


CHAPTER = "Date: Thu, 1 Oct 2026 11:00:00 +0000\nFrom: Writer <w@example.com>\nSubject: {subject}\n\nSome text for {subject}.\n"


class DownloadSelectionTests(unittest.TestCase):
    def pages(self):
        return {
            core.SITE + "gay/college/texas-tails/": (
                '<table><tr><td>3K</td><td>Oct 3 1999</td><td><a href="texas-tails-1">texas-tails-1</a></td></tr>'
                '<tr><td>3K</td><td>Oct 4 1999</td><td><a href="texas-tails-2">texas-tails-2</a></td></tr></table>'
            ),
            core.SITE + "gay/college/texas-tails/texas-tails-1": CHAPTER.format(
                subject="Texas Tails Chapter 1"
            ),
            core.SITE + "gay/college/texas-tails/texas-tails-2": CHAPTER.format(
                subject="Texas Tails Chapter 2"
            ),
            core.SITE + "gay/highschool/jeremiah": CHAPTER.format(
                subject="Texas Tails: Jeremiah"
            ),
        }

    def selection(self):
        tt = {"title": "Texas Tails", "path": "gay/college/texas-tails", "dir": True}
        jer = {
            "title": "Texas Tails: Jeremiah",
            "path": "gay/highschool/jeremiah",
            "dir": False,
        }
        return core.build_selection([("Texas Tails", [tt, jer])], [])

    def test_a_set_from_two_folders_becomes_one_group_of_stories(self):
        groups, skipped, problems = core.download_selection(
            FakeFetcher(self.pages()), self.selection()
        )
        self.assertEqual((skipped, problems), (0, []))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["title"], "Texas Tails")
        self.assertEqual(
            [s["title"] for s in groups[0]["stories"]],
            ["Texas Tails", "Texas Tails: Jeremiah"],
        )
        self.assertEqual(
            len(groups[0]["stories"][0]["sections"]),
            2,
            "the multi-chapter story keeps its chapters",
        )
        data = core.build_epub(groups[0]["stories"], title=groups[0]["title"])
        self.assertTrue(data.startswith(b"PK"))

    def test_already_owned_stories_are_skipped(self):
        groups, skipped, _ = core.download_selection(
            FakeFetcher(self.pages()),
            self.selection(),
            skip_ids={"gay/highschool/jeremiah"},
        )
        self.assertEqual(skipped, 1)
        self.assertEqual([s["title"] for s in groups[0]["stories"]], ["Texas Tails"])

    def test_a_missing_page_is_reported_and_the_rest_continues(self):
        pages = self.pages()
        del pages[core.SITE + "gay/highschool/jeremiah"]
        groups, _, problems = core.download_selection(
            FakeFetcher(pages), self.selection()
        )
        self.assertEqual(len(groups[0]["stories"]), 1)
        self.assertTrue(any("jeremiah" in p for p in problems), problems)

    def test_finished_groups_survive_a_cancel(self):
        class Cancelling(FakeFetcher):
            def get(self, url):
                if url.endswith("/jeremiah"):
                    raise core.Cancelled()
                return super().get(url)

        done = []
        selection = core.build_selection(
            [],
            [
                {
                    "title": "Texas Tails",
                    "path": "gay/college/texas-tails",
                    "dir": True,
                },
                {
                    "title": "Texas Tails: Jeremiah",
                    "path": "gay/highschool/jeremiah",
                    "dir": False,
                },
            ],
        )
        with self.assertRaises(core.Cancelled):
            for group in core.iter_selection(Cancelling(self.pages()), selection):
                done.append(group)
        self.assertEqual(
            len(done), 1, "the first story finished before the cancel and must be kept"
        )


if __name__ == "__main__":
    unittest.main()


class ContentLinkTests(unittest.TestCase):
    def setUp(self):
        self.stories = stories(
            ("Farmer in My Bed", "gay/beginnings/farmer-in-my-bed"),
            ("Store Inventory", "gay/encounters/store-inventory"),
            ("Car Repair", "gay/adult-friends/car-repair"),
            ("Unrelated Tale", "gay/college/unrelated"),
            ("The Haunted House", "gay/sf-fantasy/haunted"),
        )

    def links(self, path, text):
        return core.content_links({path: text}, self.stories)

    def test_continued_from_a_sibling_title_is_a_link(self):
        links = self.links(
            "gay/encounters/store-inventory",
            "Store Inventory\n\nContinued from farmer in my bed\n\nThis is part fictional.",
        )
        self.assertEqual(
            [(link["later"], link["earlier"], link["relation"]) for link in links],
            [
                (
                    "gay/encounters/store-inventory",
                    "gay/beginnings/farmer-in-my-bed",
                    "continued from",
                )
            ],
        )
        self.assertIn("farmer in my bed", links[0]["quote"].lower())
        self.assertFalse(
            links[0]["quote"].startswith("ry "), "quotes must not start mid-word"
        )

    def test_other_wordings_and_direction(self):
        sequel = self.links(
            "gay/encounters/store-inventory",
            'This is the sequel to "Farmer in My Bed".',
        )
        self.assertEqual(
            (sequel[0]["later"], sequel[0]["earlier"]),
            ("gay/encounters/store-inventory", "gay/beginnings/farmer-in-my-bed"),
        )
        prequel = self.links(
            "gay/encounters/store-inventory",
            "A prequel to Farmer in My Bed, written later.",
        )
        self.assertEqual(
            (prequel[0]["later"], prequel[0]["earlier"]),
            ("gay/beginnings/farmer-in-my-bed", "gay/encounters/store-inventory"),
        )
        self.assertEqual(
            self.links("gay/encounters/store-inventory", "Part 2 of Farmer in My Bed")[
                0
            ]["relation"],
            "part of",
        )
        self.assertEqual(
            self.links(
                "gay/encounters/store-inventory", "Previous story: Farmer in My Bed"
            )[0]["relation"],
            "previous story",
        )

    def test_wording_without_a_sibling_title_is_ignored(self):
        for text in (
            "which continues on to this day, but to a lesser extent",
            "This story is a continuation of a scene in a British film called If",
            "sequel to Something Else Entirely",
            "To be continued.....",
            "Continued from my last message",
        ):
            self.assertEqual(
                self.links("gay/encounters/store-inventory", text), [], text
            )

    def test_a_story_never_links_to_itself_and_unknown_paths_are_skipped(self):
        self.assertEqual(
            self.links("gay/encounters/store-inventory", "a sequel to Store Inventory"),
            [],
        )
        self.assertEqual(
            core.content_links(
                {"gay/other/not-this-authors": "sequel to Car Repair"}, self.stories
            ),
            [],
        )

    def test_html_in_samples_does_not_hide_the_phrase(self):
        links = self.links(
            "gay/encounters/store-inventory",
            "<P><B>Continued from</B> <I>Farmer in My Bed</I></P>",
        )
        self.assertEqual(len(links), 1)

    def test_story_date(self):
        self.assertEqual(
            core.story_date("Date: Sat, 4 Apr 2026 15:42:12 +0000\nFrom: x\n"),
            "2026-04-04",
        )
        self.assertEqual(core.story_date("no header here"), "")

    def test_content_links_join_a_title_set_and_a_cross_folder_story(self):
        stories_ = stories(
            ("Texas Tails", "gay/a/texas-tails"),
            ("Texas Tails: Jeremiah", "gay/b/jeremiah"),
            ("Standalone Thing", "gay/c/standalone"),
            ("Loner", "gay/d/loner"),
        )
        texts = {
            "gay/c/standalone": "Date: Mon, 5 Jan 2026 10:00:00 +0000\n\nContinued from Texas Tails: Jeremiah\n"
        }
        series, rest = core.suggest_series_with_contents(stories_, texts)
        self.assertEqual(len(series), 1)
        entry = series[0]
        self.assertEqual(entry["name"], "Texas Tails")
        self.assertEqual(
            [s["title"] for s in entry["stories"]][-1],
            "Standalone Thing",
            "the continuation comes after what it continues",
        )
        self.assertEqual(
            {s["title"] for s in entry["stories"]},
            {"Texas Tails", "Texas Tails: Jeremiah", "Standalone Thing"},
        )
        self.assertIn("Series: Episode", entry["reason"])
        self.assertIn("refer to each other", entry["reason"])
        self.assertIn("Standalone Thing says", entry["evidence"][0])
        self.assertEqual([s["title"] for s in rest], ["Loner"])

    def test_chain_ordering_beats_alphabetical(self):
        s = stories(
            ("Aardvark Nights", "gay/a/c"),
            ("Beta Story", "gay/a/b"),
            ("Zeta Story", "gay/a/a"),
        )
        texts = {"gay/a/c": "sequel to Beta Story", "gay/a/b": "sequel to Zeta Story"}
        series, _ = core.suggest_series_with_contents(s, texts)
        self.assertEqual(
            [x["title"] for x in series[0]["stories"]],
            ["Zeta Story", "Beta Story", "Aardvark Nights"],
        )
        self.assertEqual(
            series[0]["name"],
            "Zeta Story",
            "a set found only from text is named after its first story",
        )

    def test_no_text_means_the_same_as_titles_alone(self):
        s = stories(
            ("Texas Tails", "gay/a/tt"),
            ("Texas Tails: Jeremiah", "gay/b/j"),
            ("Loner", "gay/d/loner"),
        )
        with_text, rest_a = core.suggest_series_with_contents(s, {})
        alone, rest_b = core.suggest_series(s)
        self.assertEqual(
            [[x["title"] for x in e["stories"]] for e in with_text],
            [[x["title"] for x in e["stories"]] for e in alone],
        )
        self.assertEqual(rest_a, rest_b)


class RangeScanTests(unittest.TestCase):
    class RangeFetcher(FakeFetcher):
        def __init__(self, pages, ranges):
            super().__init__(pages)
            self.ranges, self.range_calls = ranges, []

        def get_range(self, url, length=3000, tail=False):
            self.range_calls.append((url.replace(core.SITE, ""), length, tail))
            if url not in self.ranges:
                raise OSError("missing " + url)
            return self.ranges[url]

    def test_single_file_and_folder_samples(self):
        listing = (
            '<table><tr><td>3K</td><td>Oct 3 1999</td><td><a href="tt-1">tt-1</a></td></tr>'
            '<tr><td>3K</td><td>Oct 4 1999</td><td><a href="tt-2">tt-2</a></td></tr></table>'
        )
        f = self.RangeFetcher(
            {core.SITE + "gay/a/tt/": listing},
            {
                core.SITE + "gay/a/tt/tt-1": "FIRST-HEAD",
                core.SITE + "gay/a/tt/tt-2": "LAST-TAIL",
                core.SITE + "gay/b/one": "ONE",
            },
        )
        folder = core.sample_text(f, {"title": "TT", "path": "gay/a/tt", "dir": True})
        self.assertIn("FIRST-HEAD", folder)
        self.assertIn("LAST-TAIL", folder)
        self.assertEqual(
            [(c[0], c[2]) for c in f.range_calls],
            [("gay/a/tt/tt-1", False), ("gay/a/tt/tt-2", True)],
            "head of the first chapter and tail of the last",
        )
        self.assertIn(
            "ONE",
            core.sample_text(f, {"title": "One", "path": "gay/b/one", "dir": False}),
        )

    def test_scan_skips_what_is_known_survives_errors_and_keeps_progress_on_cancel(
        self,
    ):
        f = self.RangeFetcher(
            {}, {core.SITE + "gay/b/one": "ONE", core.SITE + "gay/b/two": "TWO"}
        )
        items = stories(
            ("One", "gay/b/one"), ("Two", "gay/b/two"), ("Broken", "gay/b/broken")
        )
        texts = {"gay/b/one": "already read"}
        core.scan_texts(f, items, texts)
        self.assertEqual(
            texts["gay/b/one"], "already read", "a sampled story is not fetched again"
        )
        self.assertIn("TWO", texts["gay/b/two"])
        self.assertEqual(
            texts["gay/b/broken"],
            "",
            "an unreadable story is remembered so it is not retried",
        )

        class Cancelling(self.RangeFetcher):
            def get_range(self, url, length=3000, tail=False):
                if url.endswith("/two"):
                    raise core.Cancelled()
                return super().get_range(url, length, tail)

        kept = {}
        with self.assertRaises(core.Cancelled):
            core.scan_texts(
                Cancelling({}, {core.SITE + "gay/b/one": "ONE"}),
                stories(("One", "gay/b/one"), ("Two", "gay/b/two")),
                kept,
            )
        self.assertIn("gay/b/one", kept)


class SeriesModeTests(unittest.TestCase):
    def setUp(self):
        self.a = {"title": "Part A", "path": "gay/a/part-a", "dir": False}
        self.b = {"title": "Part B", "path": "gay/b/part-b", "dir": False}

    def test_series_mode_builds_one_item_with_a_series_name(self):
        sel = core.build_selection(
            [("My Series", [self.a, self.b])], [], combine_series="series"
        )
        self.assertEqual(len(sel), 1)
        self.assertEqual(
            (sel[0]["title"], sel[0]["series"], len(sel[0]["addresses"])),
            (None, "My Series", 2),
        )

    def test_separate_mode_and_single_member_sets(self):
        self.assertEqual(
            [
                i["title"]
                for i in core.build_selection(
                    [("S", [self.a, self.b])], [], combine_series=False
                )
            ],
            [None, None],
        )
        sel = core.build_selection([("S", [self.a])], [], combine_series="series")
        self.assertEqual(
            sel,
            [{"title": None, "addresses": [core.story_address(self.a)]}],
            "one story is not a series",
        )

    def test_downloaded_books_are_numbered_in_reading_order(self):
        pages = {
            core.SITE + "gay/a/part-a": CHAPTER.format(subject="Part A"),
            core.SITE + "gay/b/part-b": CHAPTER.format(subject="Part B"),
        }
        sel = core.build_selection(
            [("My Series", [self.a, self.b])], [], combine_series="series"
        )
        groups, _, _ = core.download_selection(FakeFetcher(pages), sel)
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0]["title"], "not combined into one book")
        self.assertEqual(
            [
                (s["title"], s["series"], s["series_index"])
                for s in groups[0]["stories"]
            ],
            [("Part A", "My Series", 1.0), ("Part B", "My Series", 2.0)],
        )
