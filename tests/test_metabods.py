import importlib.util
import io
from pathlib import Path
import sys
import unittest
import xml.dom.minidom
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
import storykit
spec = importlib.util.spec_from_file_location('mcore', Path(__file__).resolve().parents[1] / 'metabods/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

PRINT = ('<body><div class="xyp_section"><table><tr><td>logo</td></tr></table></div>'
         '<div class="xyp_section"><h1>Big &amp; Bold</h1><p class="xyp_noindent"><strong>by Some Author</strong></p></div>'
         '<div class="xyp_section"><h3>Author&apos;s Note</h3><div class=" xyp_section_cols"><p><p>Thanks <a href="javascript:x()">all</a></p>\n<p></p></p></div></div>'
         '<div class="xyp_section"><h3>Part 1</h3><div class=" xyp_section_cols"><p><p>He said &ldquo;hi&rdquo; <em>twice</em><br />end</p>\n'
         '<p style="margin-left:40px;color:red;">indented<script>bad()</script></p><p></p></p></div></div>'
         '<div class="xyp_section"><h3>Part 2</h3><div class=" xyp_section_cols"><p><p>Second <b>part <i>text</b></i></p></p></div></div>'
         '<div class="xyp_section"><hr>story copyright &copy;  by Some Author <br>footer</div></body>')


def story(sid='one', parts=PRINT):
    s = core.parse_print_page(parts)
    s.update(id=sid, url=core.story_url(sid), tags=['Tag A', 'Tag B'], categories=['M/M'], summary='A summary & more')
    return s


def check_epub(test, data):
    z = zipfile.ZipFile(io.BytesIO(data))
    test.assertEqual(z.namelist()[0], 'mimetype')
    test.assertEqual(z.getinfo('mimetype').compress_type, zipfile.ZIP_STORED)
    test.assertIsNone(z.testzip())
    for name in z.namelist():
        if name.endswith(('.xhtml', '.opf', '.ncx', '.xml')):
            xml.dom.minidom.parseString(z.read(name))  # raises if not well-formed
    return z


class InputTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(core.classify('bennet-3120'), ('story', 'bennet-3120'))
        self.assertEqual(core.classify('https://www.metabods.com/mbxy/site/story.php?id=a-b-1'), ('story', 'a-b-1'))
        self.assertEqual(core.classify('metabods.com/mbxy/site/story_print.php?id=x'), ('story', 'x'))
        kind, url = core.classify('https://metabods.com/mbxy/site/archive.php?list=author&id=368')
        self.assertEqual(kind, 'list')
        self.assertTrue(url.endswith('list=author&id=368'))
        self.assertIsNone(core.classify('https://example.com/story.php?id=x'))
        self.assertIsNone(core.classify('   '))
        self.assertIsNone(core.classify('not a link!'))


class ListTests(unittest.TestCase):
    def test_rows_dedupe_and_author(self):
        page = ('<td><a href="/mbxy/site/story.php?id=a" >Alpha</a><wbr> by <a href="?list=author&id=5">Zed</a></td>'
                '<p><a href="/mbxy/site/story.php?id=a">Alpha</a> by <a href="/mbxy/site/archive.php?list=author&id=5">Zed</a></p>'
                '<td><a href="/mbxy/site/story.php?id=b" >Beta &amp; Co</a></td>'
                '<a href="/mbxy/site/story.php?id=c"><i class="icon"></i></a>')
        rows = core.parse_story_rows(page)
        self.assertEqual([r['id'] for r in rows], ['a', 'b'])
        self.assertEqual(rows[0]['author'], 'Zed')
        self.assertEqual(rows[1]['title'], 'Beta & Co')

    def test_tag_index_and_combination(self):
        page = '<a href="?list=tag&id=9">Zebra</a><a href="?list=tag&id=3">apple</a><a href="?list=tag&id=90">All Tags</a><a href="?list=tag&id=3">apple</a>'
        self.assertEqual(core.parse_tag_index(page), [(3, 'apple'), (9, 'Zebra')])
        r = lambda *ids: [{'id': i, 'title': i.upper(), 'author': ''} for i in ids]
        results = {1: r('a', 'b'), 2: r('b', 'c')}
        self.assertEqual([x['id'] for x in core.combine_tag_results(results, True)], ['b'])
        self.assertEqual([x['id'] for x in core.combine_tag_results(results, False)], ['a', 'b', 'c'])
        self.assertEqual(core.combine_tag_results({}, True), [])


class ConcurrentFakeFetcher:
    def __init__(self, min_interval=0, cancelled=lambda: False, delays=None, errors=None):
        self.min_interval = min_interval
        self.cancelled = cancelled
        self.delays = delays or {}
        self.errors = errors or {}

    def get(self, url):
        if self.cancelled():
            raise core.Cancelled()
        tid_str = url.rsplit('=', 1)[-1]
        if tid_str in self.errors:
            raise IOError(self.errors[tid_str])
        delay = self.delays.get(tid_str, 0.0)
        if delay > 0:
            import time
            time.sleep(delay)
        return f'<td><a href="/mbxy/site/story.php?id=story-{tid_str}">Story {tid_str}</a></td>'


class SearchTagsTests(unittest.TestCase):
    def test_search_tags_concurrent_and_progress(self):
        import time
        delays = {str(i): 0.15 for i in range(1, 6)}
        fetcher = ConcurrentFakeFetcher(delays=delays)
        progress_updates = []

        start = time.monotonic()
        results = core.search_tags(
            fetcher,
            [1, 2, 3, 4, 5],
            progress=lambda c, t: progress_updates.append((c, t)),
            max_workers=5,
        )
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.5, f"5 tags took {elapsed:.2f}s; expected parallel completion under 0.5s")
        self.assertEqual(len(results), 5)
        self.assertIn(1, results)
        self.assertEqual(results[1][0]['id'], 'story-1')
        self.assertEqual(len(progress_updates), 5)
        self.assertEqual(progress_updates[-1], (5, 5))

    def test_search_tags_cancellation(self):
        cancel_flag = False

        def is_cancelled():
            return cancel_flag

        fetcher = ConcurrentFakeFetcher(cancelled=is_cancelled, delays={str(i): 0.1 for i in range(1, 6)})
        cancel_flag = True
        with self.assertRaises(core.Cancelled):
            core.search_tags(fetcher, [1, 2, 3])

    def test_search_tags_error_propagation(self):
        fetcher = ConcurrentFakeFetcher(errors={'2': 'Network timeout'})
        with self.assertRaises(IOError) as ctx:
            core.search_tags(fetcher, [1, 2, 3])
        self.assertIn('Network timeout', str(ctx.exception))

    def test_search_tags_per_thread_fetchers(self):
        import time

        seen_fetchers = set()

        class TrackingFetcher(getattr(core, 'Fetcher', storykit.Fetcher)):
            def get(self, url):
                seen_fetchers.add(id(self))
                time.sleep(0.02)
                return '<td><a href="/mbxy/site/story.php?id=a">A</a></td>'

        base_fetcher = TrackingFetcher()
        results = core.search_tags(base_fetcher, [10, 20], max_workers=2)
        self.assertEqual(len(results), 2)
        self.assertNotIn(id(base_fetcher), seen_fetchers)
        self.assertEqual(len(seen_fetchers), 2)


class StoryTests(unittest.TestCase):
    def test_print_page(self):
        s = core.parse_print_page(PRINT)
        self.assertEqual((s['title'], s['author']), ('Big & Bold', 'Some Author'))
        self.assertEqual([h for h, _ in s['sections']], ["Author's Note", 'Part 1', 'Part 2'])
        with self.assertRaises(ValueError):
            core.parse_print_page('<html></html>')

    def test_parse_print_page_chapter_range(self):
        s1 = core.parse_print_page(PRINT, chapter_range='1')
        self.assertEqual([h for h, _ in s1['sections']], ["Author's Note"])

        s1_2 = core.parse_print_page(PRINT, chapter_range='1-2')
        self.assertEqual([h for h, _ in s1_2['sections']], ["Author's Note", 'Part 1'])

        s2_plus = core.parse_print_page(PRINT, chapter_range='2+')
        self.assertEqual([h for h, _ in s2_plus['sections']], ['Part 1', 'Part 2'])

        s_all = core.parse_print_page(PRINT, chapter_range='')
        self.assertEqual([h for h, _ in s_all['sections']], ["Author's Note", 'Part 1', 'Part 2'])

        with self.assertRaises(ValueError) as ctx:
            core.parse_print_page(PRINT, chapter_range='99')
        self.assertIn('No chapters match range "99"', str(ctx.exception))

    def test_print_page_without_part_headings(self):
        page = ('<div class="xyp_section"><h1>Solo</h1><p><strong>by A</strong></p></div>'
                '<div class="xyp_section"><p>&nbsp;</p><div class=" xyp_section_cols"><p><p>Only text</p></p></div></div>'
                '<div class="xyp_section"><hr>footer</div>')
        s = core.parse_print_page(page)
        self.assertEqual([h for h, _ in s['sections']], [''])
        s.update(id='solo', url='u', tags=[], categories=[], summary='')
        ncx = check_epub(self, core.build_epub([s])).read('OEBPS/toc.ncx').decode()
        self.assertIn('<text>Solo</text>', ncx)

    def test_cleaner(self):
        out = storykit.clean_fragment('<p><p>One <b>bold <i>x</b></i></p>\n<p></p>stray<script>no()</script><img src="x"><a href="javascript:1">l</a></p>')
        xml.dom.minidom.parseString(f'<r>{out}</r>')
        self.assertNotIn('script', out)
        self.assertNotIn('javascript', out)
        self.assertNotIn('<p></p>', out)
        self.assertIn('stray', out)
        styled = storykit.clean_fragment('<p style="margin-left:40px;color:red;background:url(x)">t</p>')
        self.assertIn('margin-left:40px', styled)
        self.assertNotIn('color', styled)

    def test_single_story_epub(self):
        z = check_epub(self, core.build_epub([story()]))
        names = z.namelist()
        self.assertEqual(len([n for n in names if n.startswith('OEBPS/s001_')]), 3)
        ncx = z.read('OEBPS/toc.ncx').decode()
        self.assertIn("Author's Note", ncx)
        self.assertIn('Part 2', ncx)
        opf = z.read('OEBPS/content.opf').decode()
        self.assertIn('Big &amp; Bold', opf)
        self.assertIn('<dc:subject>Tag A</dc:subject>', opf)

    def test_single_part_uses_story_title(self):
        one = PRINT.replace('<div class="xyp_section"><h3>Part 2</h3>', '<div class="xyp_section"><h3 class="x">Skip</h3>')
        s = core.parse_print_page(one.split('<div class="xyp_section"><h3 class="x">')[0] + '</body>')
        s.update(id='x', url='u', tags=[], categories=[], summary='')
        ncx = check_epub(self, core.build_epub([s])).read('OEBPS/toc.ncx').decode()
        self.assertIn('<text>Big &amp; Bold</text>', ncx)

    def test_combined_epub_nests_parts(self):
        z = check_epub(self, core.build_epub([story('one'), story('two')], title='Omnibus'))
        ncx = z.read('OEBPS/toc.ncx').decode()
        self.assertEqual(ncx.count('<navPoint'), 1 + 2 + 6)  # title page, two stories, six parts
        self.assertIn('<dc:title>Omnibus</dc:title>', z.read('OEBPS/content.opf').decode())
        with self.assertRaises(ValueError):
            core.build_epub([])


class FakeResponse(io.BytesIO):
    headers = type('H', (), {'get': lambda self, k, d=None: d, 'get_content_charset': lambda self: 'utf-8'})()
    __enter__ = lambda self: self
    __exit__ = lambda self, *a: False


class FetcherTests(unittest.TestCase):
    def test_retry_and_cancel(self):
        calls = []

        def opener(req, timeout):
            calls.append(1)
            if len(calls) < 2:
                raise OSError('boom')
            return FakeResponse('café'.encode())

        self.assertEqual(storykit.Fetcher(min_interval=0, opener=opener).get('https://metabods.com/x'), 'café')
        self.assertEqual(len(calls), 2)
        with self.assertRaises(storykit.Cancelled):
            storykit.Fetcher(min_interval=0, cancelled=lambda: True, opener=opener).get('https://metabods.com/x')


if __name__ == '__main__':
    unittest.main()
