import html
import http.server
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import xml.dom.minidom
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reddit_samples import entry, feed, md  # noqa: E402
spec = importlib.util.spec_from_file_location('rcore', Path(__file__).resolve().parents[1] / 'reddit/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'reddit'))
spec_sub = importlib.util.spec_from_file_location('subscription', Path(__file__).resolve().parents[1] / 'reddit/subscription.py')
subscription = importlib.util.module_from_spec(spec_sub)
spec_sub.loader.exec_module(subscription)


class SourceTests(unittest.TestCase):
    def test_subreddit_user_and_search_forms(self):
        for text in ('r/HFY', '/r/hfy/', 'https://www.reddit.com/r/HFY/new', 'old.reddit.com/r/HFY', 'https://www.reddit.com/r/HFY/.rss'):
            self.assertEqual(core.parse_source(text)['kind'], 'subreddit', text)
        self.assertEqual(core.parse_source('u/Some_Author')['user'], 'Some_Author')
        self.assertEqual(core.parse_source('https://www.reddit.com/user/Some_Author/submitted/')['kind'], 'user')
        s = core.parse_source('https://www.reddit.com/r/HFY/search/?q=Out+of+Cruel+Space&restrict_sr=1')
        self.assertEqual((s['kind'], s['subreddit'], s['query']), ('search', 'HFY', 'Out of Cruel Space'))
        self.assertEqual(core.parse_source('https://www.reddit.com/search/?q=cruel')['subreddit'], '')

    def test_source_text_round_trips(self):
        for text in ('r/HFY', 'u/Some_Author', 'https://www.reddit.com/r/HFY/search/?q=Out+of+Cruel+Space&restrict_sr=1',
                     'https://www.reddit.com/search/?q=weird+%26+words'):
            src = core.parse_source(text)
            self.assertEqual(core.parse_source(core.source_text(src)), src, text)

    def test_bad_input(self):
        for text in ('', 'hello world', 'https://example.com/r/HFY', 'https://www.reddit.com/r/HFY/comments/abc123/title/',
                     'https://www.reddit.com/r/HFY/search?restrict_sr=1', 'r/'):
            with self.assertRaises(core.SourceError, msg=text):
                core.parse_source(text)

    def test_addresses_are_built_and_encoded(self):
        sub = core.parse_source('r/HFY')
        self.assertEqual(core.feed_url(sub, 25), 'https://www.reddit.com/r/HFY/new.rss?limit=25')
        self.assertIn('after=t3_abc', core.feed_url(sub, 100, 't3_abc'))
        user = core.parse_source('u/name')
        self.assertTrue(core.feed_url(user).startswith('https://www.reddit.com/user/name/submitted.rss?sort=new'))
        found = core.parse_source('https://www.reddit.com/r/HFY/search/?q=Out+of+Cruel+Space&restrict_sr=1')
        url = core.feed_url(found)
        self.assertIn('/r/HFY/search.rss?', url)
        self.assertIn('q=Out%20of%20Cruel%20Space', url)
        self.assertIn('restrict_sr=on', url)
        self.assertIn('type=link', core.api_path(found))
        self.assertIn('raw_json=1', core.api_path(sub))
        self.assertEqual(core.describe_source(found), 'search “Out of Cruel Space” in r/HFY')


class CleaningTests(unittest.TestCase):
    def test_wrapper_and_footer_are_removed(self):
        out = core.main_html(md('<p>The ship <em>shook</em>.</p>').replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&'))
        self.assertEqual(out, '<p>The ship <em>shook</em>.</p>')
        self.assertNotIn('submitted', out)

    def test_escaped_api_html_is_understood(self):
        self.assertEqual(core.main_html('&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;Hello&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt;'), '<p>Hello</p>')
        self.assertEqual(core.main_html('<!-- SC_OFF --><div class="md"><p>Hello</p></div><!-- SC_ON -->'), '<p>Hello</p>')

    def test_navigation_lines_go_but_story_text_stays(self):
        body = ('<p><a href="https://x.org/a">First</a> | <a href="https://x.org/b">Previous</a> | <a href="https://x.org/c">Next</a></p>'
                '<p>Story text with a <a href="https://x.org/d">next</a> step.</p><p>More story.</p><hr/>'
                '<p><a href="https://x.org/a">First</a> | <a href="https://x.org/b">Previous</a></p>')
        out = core.strip_navigation(core.clean_fragment(body))
        self.assertEqual(out, '<p>Story text with a <a href="https://x.org/d">next</a> step.</p><p>More story.</p>')
        self.assertEqual(core.strip_navigation('<p>Only text.</p>'), '<p>Only text.</p>')
        self.assertEqual(core.strip_navigation('<p>Next</p>'), '', 'a post that is only a navigation line has no story left')
        self.assertEqual(core.strip_navigation('<p>Next chapter is where it gets good.</p>'), '<p>Next chapter is where it gets good.</p>')


class ParsingTests(unittest.TestCase):
    def test_atom_entries(self):
        xml_text = feed(entry('aaa111', 'Out of Cruel Space (Chapter 2)', '<p>Two.</p>', stamp='2026-10-08T12:00:00+00:00'),
                        entry('bbb222', 'A picture', None), entry('ccc333', 'Comment', '<p>x</p>', kind='t1'),
                        entry('ddd444', 'Out of Cruel Space (Chapter 1)', '<p>One &amp; more.</p>', author='/u/Writer', stamp='2026-10-07T12:00:00+00:00'))
        entries, after = core.parse_atom(xml_text)
        self.assertEqual([e['id'] for e in entries], ['t3_aaa111', 't3_bbb222', 't3_ddd444'], 'comments are skipped')
        self.assertEqual(after, 't3_ddd444', 'the oldest id is the token for the next page')
        first = entries[0]
        self.assertEqual((first['title'], first['author'], first['html'], first['subreddit']),
                         ('Out of Cruel Space (Chapter 2)', 'someone', '<p>Two.</p>', 'HFY'))
        self.assertEqual(entries[2]['author'], 'Writer')
        self.assertEqual(entries[2]['html'], '<p>One &amp; more.</p>')
        self.assertEqual(entries[1]['html'], '', 'a picture post has no story text')
        self.assertGreater(first['created'], entries[2]['created'])
        self.assertTrue(first['link'].endswith('/comments/aaa111/slug/'))

    def test_atom_that_is_not_xml_is_reported(self):
        for text in ('', '<html>403 Forbidden</html', 'Too Many Requests'):
            with self.assertRaises(ValueError):
                core.parse_atom(text)
        self.assertEqual(core.parse_atom(feed()), ([], None))

    def test_api_listing(self):
        data = {'kind': 'Listing', 'data': {'after': 't3_next', 'children': [
            {'kind': 't3', 'data': {'name': 't3_aaa', 'title': 'Chapter &amp; One', 'author': 'Writer', 'created_utc': 1790000000.0,
                                    'permalink': '/r/HFY/comments/aaa/x/', 'subreddit': 'HFY', 'is_self': True,
                                    'selftext_html': '<!-- SC_OFF --><div class="md"><p>Body.</p></div><!-- SC_ON -->'}},
            {'kind': 't3', 'data': {'name': 't3_bbb', 'title': 'Link post', 'author': 'Writer', 'created_utc': 1.0, 'permalink': '/r/HFY/comments/bbb/x/', 'selftext_html': None}},
            {'kind': 't1', 'data': {'name': 't1_ccc'}}]}}
        entries, after = core.parse_listing(json.dumps(data))
        self.assertEqual(after, 't3_next')
        self.assertEqual([e['id'] for e in entries], ['t3_aaa', 't3_bbb'])
        self.assertEqual((entries[0]['title'], entries[0]['html'], entries[0]['link']), ('Chapter & One', '<p>Body.</p>', 'https://www.reddit.com/r/HFY/comments/aaa/x/'))
        self.assertEqual(entries[1]['html'], '')


class RealPostShapeTests(unittest.TestCase):
    """Mirrors the shape of the first real Out of Cruel Space post: comma title, author, trailing Next link."""

    def real_shaped(self):
        body = ('<p>First paragraph of the chapter.</p><p>Second paragraph.</p>'
                '<p><a href="https://www.reddit.com/r/HFY/comments/nfsakq/out_of_cruel_space_part_1/">Next</a></p>')
        return feed(entry('nfsakq', 'Out of Cruel Space, Part 1', body, author='/u/KyleKKent', stamp='2021-05-19T14:00:00+00:00'))

    def test_entry_parses_and_the_trailing_next_link_is_removed(self):
        entries, _ = core.parse_atom(self.real_shaped())
        e = entries[0]
        self.assertEqual((e['id'], e['title'], e['author']), ('t3_nfsakq', 'Out of Cruel Space, Part 1', 'KyleKKent'))
        self.assertEqual(e['html'], '<p>First paragraph of the chapter.</p><p>Second paragraph.</p>')

    def test_preset_matches_the_series_posts_and_nothing_else(self):
        preset = core.PRESETS[0]
        follow = core.new_follow(preset['name'], preset['source'], preset['title_filter'], preset['author_filter'])
        self.assertEqual((follow['source']['kind'], follow['source']['user']), ('user', 'KyleKKent'))
        e = core.parse_atom(self.real_shaped())[0][0]
        self.assertTrue(core.matches(e, follow))
        for other in (dict(e, title='Something Else, Part 1'), dict(e, author='SomeoneElse')):
            self.assertFalse(core.matches(other, follow))
        for title in ('Out of Cruel Space, Part 2', 'Out of Cruel Space: Part 10', 'OUT OF CRUEL SPACE - Part 3'):
            self.assertTrue(core.matches(dict(e, title=title), follow), title)


class FilterTests(unittest.TestCase):
    def post(self, title='Out of Cruel Space (Chapter 3)', author='Writer', html='<p>x</p>'):
        return {'id': 't3_a', 'title': title, 'author': author, 'html': html}

    def test_filters(self):
        f = lambda **kw: dict({'title_filter': '', 'author_filter': ''}, **kw)
        self.assertTrue(core.matches(self.post(), f()))
        self.assertTrue(core.matches(self.post(), f(title_filter='cruel SPACE')))
        self.assertFalse(core.matches(self.post(), f(title_filter='something else')))
        self.assertTrue(core.matches(self.post(), f(title_filter=r're:chapter\s+\d+')))
        self.assertFalse(core.matches(self.post(), f(title_filter='re:((')), 'a broken regex matches nothing rather than everything')
        self.assertTrue(core.matches(self.post(), f(author_filter='writer')))
        self.assertFalse(core.matches(self.post(), f(author_filter='other')))
        self.assertFalse(core.matches(self.post(html=''), f()), 'posts without story text never match')

    def test_new_follow_cleans_input(self):
        follow = core.new_follow('  Out of Cruel Space ', 'https://www.reddit.com/r/HFY/search/?q=Out+of+Cruel+Space&restrict_sr=1', ' (Chapter ', '/u/Writer')
        self.assertEqual((follow['name'], follow['title_filter'], follow['author_filter']), ('Out of Cruel Space', '(Chapter', 'Writer'))
        self.assertEqual(len(follow['id']), 10)
        self.assertEqual(core.new_follow('', 'u/name')['name'], 'u/name')


class FakeSource:
    """Pages of canned posts, newest first, like a feed."""
    mode = 'rss'

    def __init__(self, posts, per_page=2, fail_on_page=None):
        self.posts, self.per_page, self.fail_on_page = posts, per_page, fail_on_page
        self.calls = []

    def page(self, src, after=None, limit=100):
        self.calls.append(after)
        if self.fail_on_page is not None and len(self.calls) == self.fail_on_page:
            raise core.RateLimited('https://www.reddit.com/x', 90)
        ids = [p['id'] for p in self.posts]
        start = ids.index(after) + 1 if after else 0
        chunk = self.posts[start:start + self.per_page]
        return chunk, (chunk[-1]['id'] if chunk and start + self.per_page < len(self.posts) else None)


def post(n, title=None, html=None, author='Writer'):
    return {'id': f't3_{n:03d}', 'title': title or f'Out of Cruel Space (Chapter {n})', 'author': author, 'created': 1790000000.0 + n * 86400,
            'link': f'https://www.reddit.com/r/HFY/comments/{n:03d}/x/', 'html': html if html is not None else f'<p>Chapter {n} text.</p>', 'subreddit': 'HFY'}


class LongSeriesTests(unittest.TestCase):
    """Found by running the plugin on the real, ~1800-post Out of Cruel Space history."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        preset = core.PRESETS[0]
        self.follow = core.new_follow(preset['name'], preset['source'], preset['title_filter'], preset['author_filter'])

    def test_preset_follows_the_series_across_its_rename(self):
        for title in ('Out of Cruel Space, Part 1', 'Out of Cruel Space, part 12', 'OOCS, Into A Wider Galaxy, Part 800',
                      'OOCS, Into The Wider Galaxy, Part 3', 'OOCS, Into A Wider Galaxy 77'):
            self.assertTrue(core.matches(post(1, title=title, author='KyleKKent'), self.follow), title)
        for title in ('Out of Cruel Space Side Story: Of Dog, Volpir, and Man', 'Something else, Part 4'):
            self.assertFalse(core.matches(post(1, title=title, author='KyleKKent'), self.follow), title)
        self.assertFalse(core.matches(post(1, title='OOCS, Into A Wider Galaxy, Part 9', author='Fan'), self.follow))

    def test_first_check_reads_a_history_longer_than_ten_pages(self):
        posts = [post(n, author='KyleKKent') for n in range(1500, 0, -1)]
        source = FakeSource(posts, per_page=100)
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        result = core.check_follow(source, self.follow, cache)
        self.assertEqual((len(cache.posts), result['pages']), (1500, 15))
        self.assertTrue(cache.data['complete'])

    def test_unfinished_backfill_continues_and_old_caches_are_topped_up(self):
        posts = [post(n, author='KyleKKent') for n in range(300, 0, -1)]
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        core.check_follow(FakeSource(posts, per_page=50), self.follow, cache, max_pages=2)
        self.assertEqual(len(cache.posts), 100)
        self.assertFalse(cache.data.get('complete'), 'stopped early, so the history is not complete')
        core.check_follow(FakeSource(posts, per_page=50), self.follow, cache)
        self.assertEqual(len(cache.posts), 300)
        # a later check stops once it reaches known posts
        source = FakeSource([post(301, author='KyleKKent')] + posts, per_page=50)
        result = core.check_follow(source, self.follow, cache)
        self.assertEqual((result['new'], result['pages']), (['Out of Cruel Space (Chapter 301)'], 1))
        # a cache written before completeness was tracked is read back through once
        del cache.data['complete'], cache.data['signature']
        again = core.check_follow(FakeSource([post(301, author='KyleKKent')] + posts, per_page=50), self.follow, cache)
        self.assertTrue(cache.data['complete'] and again['pages'] == 7)

    def test_changing_the_filter_rescans_the_history(self):
        posts = [post(n, author='KyleKKent') for n in range(60, 0, -1)]
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        core.check_follow(FakeSource(posts, per_page=20), self.follow, cache)
        self.follow['title_filter'] = ''
        result = core.check_follow(FakeSource(posts, per_page=20), self.follow, cache)
        self.assertEqual(result['pages'], 3, 'read all the way back again, not just to the newest known post')


class AuthorNoteTests(unittest.TestCase):
    COMMENTS = json.dumps([{'data': {'children': []}}, {'data': {'children': [
        {'kind': 't1', 'data': {'author': 'Reader', 'body_html': '<div class="md"><p>Great!</p></div>'}},
        {'kind': 't1', 'data': {'author': 'KyleKKent', 'body_html': '<div class="md"><p>Thanks for reading. <a href="https://x.example/">Wiki</a></p></div>'}},
        {'kind': 'more', 'data': {}}]}}])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.follow = core.new_follow('Series', 'u/KyleKKent', '', 'KyleKKent', author_note=True)

    def test_picks_the_authors_own_top_level_comment(self):
        self.assertIn('Thanks for reading', core.top_level_author_comment(self.COMMENTS, 'kylekkent'))
        self.assertEqual(core.top_level_author_comment(self.COMMENTS, 'Nobody'), '')
        self.assertEqual(core.top_level_author_comment('{}', 'x'), '')

    def test_notes_are_fetched_in_batches_kept_and_added_to_the_book(self):
        class Noted(FakeSource):
            mode = 'api'
            fetched = 0

            def author_comment(self, entry):
                Noted.fetched += 1
                return '' if entry['id'].endswith('002') else f"<p>Note for {entry['id']}</p>"
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        source = Noted([post(n, author='KyleKKent') for n in range(5, 0, -1)], per_page=10)
        core.check_follow(source, self.follow, cache)
        self.assertEqual(Noted.fetched, 5)
        left = core.fetch_notes(source, self.follow, cache)
        self.assertEqual((left, Noted.fetched), (0, 5), 'chapters with a recorded note (even an empty one) are not asked again')
        story = core.build_story(self.follow, cache)
        self.assertIn("Author's comment", story['sections'][0][1])
        self.assertIn('Note for t3_001', story['sections'][0][1])
        self.assertNotIn("Author's comment", story['sections'][1][1], 'no comment, nothing added')
        self.assertEqual(core.fetch_notes(source, self.follow, cache, batch=1), 0)
        cache.posts['t3_004'].pop('note')
        cache.posts['t3_003'].pop('note')
        self.assertEqual(core.fetch_notes(source, self.follow, cache, batch=1), 1, 'newest first, and a batch leaves the rest')

    def test_feed_reader_and_unwanting_follows_fetch_nothing(self):
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        core.check_follow(FakeSource([post(1, author='KyleKKent')]), self.follow, cache)
        self.assertNotIn('note', cache.posts['t3_001'])
        plain = core.new_follow('S', 'u/writer')
        self.assertEqual(core.fetch_notes(type('A', (), {'author_comment': lambda s, e: 'x'})(), plain, cache), 0)

    def test_follows_owing_comments_are_due_again_soon(self):
        follow = dict(self.follow, last_checked=1000.0, notes_pending=True)
        self.assertFalse(core.due(follow, 6, now=1000.0 + 600))
        self.assertTrue(core.due(follow, 6, now=1000.0 + 1000))
        self.assertFalse(core.due(dict(follow, notes_pending=False), 6, now=1000.0 + 1000))

    def test_warning_payload_when_author_note_set_on_feedsource(self):
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        res = core.check_follow(FakeSource([post(1, author='KyleKKent')]), self.follow, cache)
        self.assertEqual(res.get('warning'), 'author comments need the official API')

        plain = core.new_follow('S', 'u/writer', author_note=False)
        cache_plain = core.ChapterCache(self.tmp.name, plain['id'])
        res_plain = core.check_follow(FakeSource([post(1, author='writer')]), plain, cache_plain)
        self.assertNotIn('warning', res_plain)

        class NotedSource(FakeSource):
            def author_comment(self, entry):
                return ''
        res_noted = core.check_follow(NotedSource([post(1, author='KyleKKent')]), self.follow, cache)
        self.assertNotIn('warning', res_noted)


class OpeningTests(unittest.TestCase):
    """How HFY's Out of Cruel Space opens its posts: a title line, sometimes a ~First~ marker and a note in brackets."""

    def test_title_notes_and_markers_in_any_order(self):
        cases = {
            '<p>The Pirates &amp; The Bounty Hunters</p><p>\u200b</p><p>\u201cDamn they\u2019ve been here.\u201d Air-Farce remarks.</p>':
                ('The Pirates & The Bounty Hunters', [], '<p>\u201cDamn they\u2019ve been here.\u201d Air-Farce remarks.</p>'),
            '<p>(Today is just full of Derps.)</p><p>Capes and Conundrums</p><p>\u200b</p><p>He sat down.</p>':
                ('Capes and Conundrums', ['Today is just full of Derps.'], '<p>He sat down.</p>'),
            '<p>The Pirates</p><p>(I am so sorry, my brain clunked.)</p><p>\u201cAlright.\u201d</p>':
                ('The Pirates', ['I am so sorry, my brain clunked.'], '<p>\u201cAlright.\u201d</p>'),
            '<p>~First~</p><p>HHH/Herbert\u2019s Hundred Harem</p><p>He walked.</p>': ('HHH/Herbert\u2019s Hundred Harem', [], '<p>He walked.</p>'),
            '<p>Danger Zone!</p><p>He ran.</p>': ('Danger Zone!', [], '<p>He ran.</p>'),
            '<p>Miles Brent sighed to himself as he laid on the hard floor.</p><p>Next.</p>':
                ('', [], '<p>Miles Brent sighed to himself as he laid on the hard floor.</p><p>Next.</p>'),
            '<p>Run!</p><p>He ran.</p>': ('', [], '<p>Run!</p><p>He ran.</p>'),
            '<p>\u201cHello there,\u201d he said</p><p>x</p>': ('', [], '<p>\u201cHello there,\u201d he said</p><p>x</p>'),
            '<p>(Part of a longer aside that never closes</p><p>x</p>': ('', [], '<p>(Part of a longer aside that never closes</p><p>x</p>'),
        }
        for html, want in cases.items():
            self.assertEqual(core.split_opening(html), want, html[:50])
        self.assertEqual(core.split_body_title('<p>Title Here</p><p>He ran.</p>'), ('Title Here', '<p>He ran.</p>'))

    def test_notes_become_a_boxed_aside_in_front_and_the_authors_comment_one_behind(self):
        post = {'html': '<p>The Pirates</p><p>(Sorry, a short one today.)</p><p>Chapter text.</p>', 'note': '<p>Thanks for reading! <a href="https://x.example/">Wiki</a></p>'}
        plain = core.chapter_html(post)
        self.assertTrue(plain.startswith('<p>The Pirates</p>'), 'without the option the post is left as written')
        html = core.chapter_html(post, True)
        self.assertTrue(html.startswith('<div class="author-note"><p class="author-note-label">Author\'s note</p><p>Sorry, a short one today.</p></div><p>Chapter text.</p>'))
        self.assertTrue(html.endswith('</p></div>') and "Author's comment" in html and 'Thanks for reading!' in html)
        self.assertNotIn('The Pirates', html)
        import storykit
        cleaned = storykit.clean_fragment(html)
        self.assertIn('<div class="author-note">', cleaned)
        self.assertIn('<p class="author-note-label">', cleaned)
        self.assertNotIn('class="evil"', storykit.clean_fragment('<div class="evil"><p>x</p></div>'), 'other classes are still dropped')
        self.assertIn('.author-note', storykit.CSS)

    def test_chapters_are_named_from_the_title_line_with_or_without_the_index(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        follow = core.new_follow('Out of Cruel Space', 'u/KyleKKent', '', 'KyleKKent', title_from_body=True)
        cache = core.ChapterCache(tmp.name, follow['id'])
        posts = [{'id': 't3_a', 'title': 'Out of Cruel Space, Part 59', 'author': 'KyleKKent', 'created': 1625000000.0, 'link': '', 'flair': 'OC',
                  'html': '<p>The Pirates &amp; The Bounty Hunters</p><p>\u200b</p><p>Text one.</p>'},
                 {'id': 't3_b', 'title': 'Out of Cruel Space, Part 1', 'author': 'KyleKKent', 'created': 1621000000.0, 'link': '', 'flair': 'OC',
                  'html': '<p>Miles Brent sighed to himself as he laid on the hard floor.</p>'}]
        cache.posts.update({p['id']: p for p in posts})
        sections = core.build_story(follow, cache)['sections']
        self.assertEqual([t for t, _ in sections], ['Out of Cruel Space, Part 1', 'Out of Cruel Space, Part 59 \u2013 The Pirates & The Bounty Hunters'])
        self.assertEqual(sections[1][1], '<p>Text one.</p>', 'the title is not repeated in the text')
        cache.data['index'] = {'url': 'u', 'fetched': 1, 'error': '', 'rows': [
            {'n': 59, 'label': 'Chapter 059', 'note': 'Pirates; Bounty Hunters', 'ts': 1625000000.0}, {'n': 1, 'label': 'Chapter 001', 'note': 'Pirates', 'ts': 1621000000.0}]}
        self.assertEqual([t for t, _ in core.build_story(follow, cache)['sections']],
                         ['Chapter 001', 'Chapter 059 \u2013 The Pirates & The Bounty Hunters'], 'the title line replaces the sheet\'s storyline note')
        off = core.new_follow('x', 'u/KyleKKent', '', 'KyleKKent')
        self.assertEqual(core.build_story(off, cache)['sections'][1][0], 'Chapter 059 \u2013 Pirates; Bounty Hunters')


class IndexTests(unittest.TestCase):
    """A public chapter-list sheet (shape taken from the real Out of Cruel Space index) names chapters and catches mistitled posts."""
    CSV = ('Out of Cruel Space (an extension of the archive),,,,\n'
           'Date (CEST),Author,Chapter,Note,,Seq\n'
           '2021/05/19 04:30,KyleKKent,Chapter 001,Pirates,,1\n'
           '2021/05/20 01:00,KyleKKent,Chapter 002,Pirates; Bounty Hunters,,2\n'
           '2021/05/21 22:40,KyleKKent,Chapter 003 [NSFW],,,3\n'
           '2024/05/22 22:12,KyleKKent,"Into A Wider Galaxy, Chapter 010",AAA,,4\n'
           '2024/05/23 22:10,KyleKKent,"Into A Wider Galaxy, Chapter 011",RAK and Roll,,5\n'
           '2021/09/26 21:00,KamchatkasRevenge,Of Dog 1,Canon,,6\n'
           '2021/05/29 21:00,KyleKKent,Side story,Canon,,7\n'
           '2021/06/01 10:00,KyleKKent,Chapter 004,Never posted,,8\n')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def rows(self):
        return core.parse_index(self.CSV, 'KyleKKent')

    def test_sheet_address_becomes_a_csv_export(self):
        self.assertEqual(core.sheet_csv_url('https://docs.google.com/spreadsheets/d/AbC-1_2/edit?pli=1&gid=99#gid=99'),
                         'https://docs.google.com/spreadsheets/d/AbC-1_2/export?format=csv&gid=99')
        self.assertEqual(core.sheet_csv_url('https://docs.google.com/spreadsheets/d/AbC/edit'), 'https://docs.google.com/spreadsheets/d/AbC/export?format=csv')
        with self.assertRaises(core.SourceError):
            core.sheet_csv_url('https://example.com/sheet')

    def test_parsing_keeps_only_the_authors_numbered_chapters(self):
        rows = self.rows()
        self.assertEqual([r['label'] for r in rows], ['Chapter 001', 'Chapter 002', 'Chapter 003 [NSFW]', 'Into A Wider Galaxy, Chapter 010',
                                                       'Into A Wider Galaxy, Chapter 011', 'Chapter 004'])
        self.assertEqual((rows[1]['n'], rows[1]['note']), (2, 'Pirates; Bounty Hunters'))
        self.assertAlmostEqual(rows[0]['ts'], core.datetime(2021, 5, 19, 2, 30, tzinfo=core.timezone.utc).timestamp())
        with self.assertRaises(ValueError):
            core.parse_index('a,b\n1,2\n')

    def post(self, pid, title, when):
        return {'id': pid, 'title': title, 'author': 'KyleKKent', 'html': '<p>x</p>', 'link': '', 'flair': '',
                'created': core.datetime.strptime(when, '%Y-%m-%d %H:%M').replace(tzinfo=core.timezone.utc).timestamp()}

    def posts(self):
        return [self.post('t3_a', 'Out of Cruel Space, Part 1', '2021-05-19 03:00'), self.post('t3_b', 'Out of Cruel Space, Part 2', '2021-05-20 00:00'),
                self.post('t3_c', 'Out of Cruel Space, Part 3', '2021-05-21 20:50'),
                self.post('t3_d', 'OOCS, Into A Wider Galaxy, Part 1010', '2024-05-22 20:30'),   # a typo for Part 10
                self.post('t3_e', 'OOCS, Into A Wider Galaxy, Part 011', '2024-05-23 21:00'),
                self.post('t3_f', 'OOCS, Into A Wider Galaxy, Part 12', '2024-05-24 21:00')]  # newer than the sheet

    def test_matching_by_number_then_by_time_catches_typos_and_reports_both_sides(self):
        matched, missing, unlisted = core.match_index(self.posts(), self.rows())
        self.assertEqual({pid: r['label'] for pid, r in matched.items()}, {
            't3_a': 'Chapter 001', 't3_b': 'Chapter 002', 't3_c': 'Chapter 003 [NSFW]', 't3_d': 'Into A Wider Galaxy, Chapter 010',
            't3_e': 'Into A Wider Galaxy, Chapter 011'})
        self.assertEqual([r['label'] for r in missing], ['Chapter 004'], 'listed in the index but never posted')
        self.assertEqual([p['id'] for p in unlisted], ['t3_f'], 'posted but not yet in the index')
        self.assertEqual(core.index_title(matched['t3_b'], 'x'), 'Chapter 002 \u2013 Pirates; Bounty Hunters')
        self.assertEqual(core.index_title(matched['t3_c'], 'x'), 'Chapter 003 [NSFW]')
        self.assertEqual(core.index_title(None, 'fallback'), 'fallback')

    def test_same_chapter_number_in_two_eras_does_not_mix_up(self):
        rows = [{'n': 5, 'label': 'Chapter 005', 'note': '', 'ts': 1.6e9}, {'n': 5, 'label': 'Into A Wider Galaxy, Chapter 005', 'note': '', 'ts': 1.7e9}]
        posts = [dict(self.post('t3_x', 'Part 5', '2021-01-01 00:00'), created=1.7e9 + 100), dict(self.post('t3_y', 'Part 5', '2021-01-01 00:00'), created=1.6e9 + 100)]
        matched, _, _ = core.match_index(posts, rows)
        self.assertEqual((matched['t3_x']['label'], matched['t3_y']['label']), ('Into A Wider Galaxy, Chapter 005', 'Chapter 005'))

    def test_index_is_fetched_once_a_day_survives_failures_and_names_the_chapters(self):
        calls = []

        class Source:
            mode = 'rss'

            def __init__(s):
                s.fetcher = s
                s.fail = False

            def get(s, url, **kwargs):
                calls.append(url)
                if s.fail:
                    raise IOError('Could not fetch: HTTP Error 403')
                return IndexTests.CSV

            def page(s, src, after=None, limit=100):
                return IndexTests.posts_for(self), None
        follow = core.new_follow('Out of Cruel Space', 'u/KyleKKent', '', 'KyleKKent', index_url='https://docs.google.com/spreadsheets/d/AbC/edit?gid=5')
        cache = core.ChapterCache(self.tmp.name, follow['id'])
        source = Source()
        core.check_follow(source, follow, cache)
        self.assertEqual(calls, ['https://docs.google.com/spreadsheets/d/AbC/export?format=csv&gid=5'])
        core.check_follow(source, follow, cache)
        self.assertEqual(len(calls), 1, 'not fetched again within a day')
        titles = [t for t, _ in core.build_story(follow, cache)['sections']]
        self.assertEqual(titles[0], 'Chapter 001 \u2013 Pirates')
        self.assertIn('Into A Wider Galaxy, Chapter 010 \u2013 AAA', titles, 'the mistitled post is named by the index')
        self.assertEqual(titles[-1], 'OOCS, Into A Wider Galaxy, Part 12', 'a post the index does not list keeps its own title')
        report = core.index_report(follow, cache)
        self.assertEqual((report['rows'], report['matched'], report['missing'], report['unlisted']), (6, 5, ['Chapter 004'], ['OOCS, Into A Wider Galaxy, Part 12']))
        # a failed refresh keeps the copy we have and says so
        cache.data['index']['fetched'] = 0
        source.fail = True
        core.check_follow(source, follow, cache)
        self.assertIn('403', cache.data['index']['error'])
        self.assertEqual(len(cache.data['index']['rows']), 6)
        self.assertEqual(core.index_report(follow, cache)['matched'], 5)

    @staticmethod
    def posts_for(test):
        return [e for e in test.posts()]


class DiscoveryTests(unittest.TestCase):
    """Finding stories, and whole series, on a subreddit like r/HFY (title shapes taken from its real top posts)."""

    def test_part_numbers_in_the_shapes_hfy_uses(self):
        cases = {
            'The Nature of Predators 14': ('The Nature of Predators', 14),
            'Why Humans Avoid War VIII': ('Why Humans Avoid War', 8),
            'Why Humans Avoid War': ('Why Humans Avoid War', None),
            'Out of Cruel Space, Part 12': ('Out of Cruel Space', 12),
            'Salvage - Chapter 7': ('Salvage', 7),
            'Salvage (12)': ('Salvage', 12),
            'Salvage #12': ('Salvage', 12),
            'A job for a deathworlder [Chapter 7]': ('A job for a deathworlder', 7),
            'The Nature of Predators 2-99 [Final]': ('The Nature of Predators', 2),
            '[OC][Jenkinsverse] - Salvage 31': ('Salvage', 31),
            'Chrysalis 16: The Return': ('Chrysalis', 16),
            'The Civil War': ('The Civil War', None),
            'What Am I': ('What Am', 1),
            'A Silly Thought\u2026': ('A Silly Thought\u2026', None),
        }
        for title, want in cases.items():
            self.assertEqual(core.series_parts(title), want, title)

    def test_tag_only_titles_keep_their_tags_as_the_name(self):
        self.assertEqual(core.series_parts('[OC][JVerse] 4: Quarantine'), ('[OC][JVerse]', 4))
        self.assertEqual(core.series_parts('[OC][Jenkinsverse] Chapter 7'), ('[OC][Jenkinsverse]', 7))

    def posts(self):
        def p(i, title, author='Alpha', flair='OC', created=None, html='<p>x</p>'):
            return {'id': f't3_{i}', 'title': title, 'author': author, 'flair': flair, 'created': created or 1700000000.0 + i, 'html': html, 'link': ''}
        return [p(1, 'Salvage 3'), p(2, 'Salvage 1'), p(3, '[OC] Salvage 2'), p(4, 'Salvage 3'), p(5, 'Lone story'),
                p(6, 'Salvage 9', author='Bravo'), p(7, 'Salvage 10', author='Bravo'), p(8, 'Opener', flair='OC-FirstOfSeries'),
                p(9, 'Opener 2', flair='OC-Series'), p(10, 'Picture', html=''), p(11, 'Hello 2', author='Cee')]

    def test_grouping_by_author_and_name(self):
        groups = core.group_series(self.posts())
        by = {(g['author'], g['name']): g for g in groups}
        salvage = by[('Alpha', 'Salvage')]
        self.assertTrue(salvage['is_series'])
        self.assertEqual((sorted(salvage['numbers']), len(salvage['posts'])), ([1, 2, 3], 4), 'a repeated post number is kept, tags in front do not split it')
        self.assertTrue(by[('Bravo', 'Salvage')]['is_series'], 'the same name by another author is a different series')
        self.assertTrue(by[('Alpha', 'Opener')]['is_series'], 'a series flair counts with two posts')
        self.assertFalse(by[('Alpha', 'Lone story')]['is_series'])
        self.assertFalse(by[('Cee', 'Hello')]['is_series'], 'one numbered post is not yet a series')
        self.assertNotIn('Picture', [g['name'] for g in groups], 'posts without story text are ignored')
        self.assertTrue(all(g['is_series'] for g in groups[:3]) and not groups[-1]['is_series'], 'series come first')
        self.assertEqual([p['id'] for p in salvage['posts']], ['t3_1', 't3_2', 't3_3', 't3_4'], 'posts are listed by date')

    def scored(self):
        def p(i, title, author, score):
            return {'id': f't3_{i}', 'title': title, 'author': author, 'flair': 'OC', 'created': 1700000000.0 + i, 'html': '<p>x</p>', 'link': '', 'score': score}
        return [p(1, 'Big Saga 1', 'Alpha', 5000), p(2, 'Big Saga 2', 'Alpha', 4000), p(3, 'Big Saga 3', 'Alpha', 3000),
                p(4, 'Small Tale 1', 'Bravo', 100), p(5, 'Small Tale 2', 'Bravo', 90), p(6, 'Small Tale 3', 'Bravo', 80),
                p(7, 'Mid Epic 1', 'Cee', 900), p(8, 'Mid Epic 2', 'Cee', 800), p(9, 'Mid Epic 3', 'Cee', 700), p(10, 'Mid Epic 4', 'Cee', 600),
                p(11, 'Short Pair 1', 'Dee', 99999), p(12, 'Short Pair 2', 'Dee', 99999), p(13, 'One Shot', 'Eee', 500000)]

    def test_ratings_rank_series_and_top_rated_skips_short_followed_and_single(self):
        groups = core.group_series(self.scored())
        self.assertEqual({g['name']: g['score'] for g in groups}['Big Saga'], 12000)
        self.assertEqual([g['name'] for g in core.rank_groups(groups, 'score')][:4], ['Short Pair', 'Big Saga', 'Mid Epic', 'Small Tale'], 'series first, best rated first')
        self.assertEqual([g['name'] for g in core.rank_groups(groups, 'posts')][:2], ['Mid Epic', 'Big Saga'], 'or by parts found')
        top = core.top_series(groups, count=2, min_parts=3)
        self.assertEqual([g['name'] for g in top], ['Big Saga', 'Mid Epic'], 'two-part series and the single story are not eligible at three parts')
        self.assertEqual([g['name'] for g in core.top_series(groups, 10, 2)][:3], ['Short Pair', 'Big Saga', 'Mid Epic'])
        have = [core.series_follow(top[0])]
        self.assertEqual([g['name'] for g in core.top_series(groups, 2, 3, have)], ['Mid Epic', 'Small Tale'], 'already followed series are skipped')
        self.assertTrue(core.followed_keys(have) == {('alpha', 'big saga')})
        self.assertEqual(core.followed_keys([{'name': 'x', 'author_filter': ''}]), set(), 'follows without an author cannot be matched')

    def test_the_api_listing_carries_the_score_and_feeds_do_not(self):
        listing = json.dumps({'data': {'after': None, 'children': [{'kind': 't3', 'data': {
            'name': 't3_a', 'title': 'T', 'author': 'w', 'created_utc': 1.0, 'permalink': '/r/x/comments/a/t/',
            'selftext_html': '<div class="md"><p>Hi.</p></div>', 'score': 1234}}]}})
        self.assertEqual(core.parse_listing(listing)[0][0]['score'], 1234)
        self.assertEqual(core.group_series([post(1)])[0]['score'], 0)

    def test_filters_are_described_in_words_not_as_regular_expressions(self):
        # the stored title filters exactly as they appeared in the list of followed stories
        old_form = 're:^\\s*' + core._LEAD_TAGS + 'The\\ Nature\\ of\\ Predators(?![A-Za-z0-9])'
        follow = core.new_follow('The Nature of Predators', 'u/SpacePaladin15', old_form, 'SpacePaladin15')
        self.assertEqual(core.describe_filters(follow), '  title starts with \u201cThe Nature of Predators\u201d, by SpacePaladin15')
        made = core.series_follow({'name': "Humans don't Make Good Pets", 'author': 'guidosbestfriend'})
        self.assertNotIn('\\ ', made['title_filter'], 'spaces are not escaped in the patterns we write')
        self.assertEqual(core.describe_filters(made), '  title starts with \u201cHumans don\'t Make Good Pets\u201d, by guidosbestfriend')
        self.assertTrue(core.matches({'title': '[OC] Humans don\'t Make Good Pets 12', 'author': 'guidosbestfriend', 'html': '<p>x</p>'}, made))
        preset = core.PRESETS[0]
        self.assertEqual(core.describe_filters(core.new_follow('x', preset['source'], preset['title_filter'], preset['author_filter'])),
                         '  title starts with \u201c(Out of Cruel Space|OOCS)\u201d, by KyleKKent')
        self.assertEqual(core.describe_filters(core.new_follow('x', 'u/KyleKKent', 'Out of Cruel Space', 'KyleKKent')), '  title has \u201cOut of Cruel Space\u201d, by KyleKKent')
        self.assertEqual(core.describe_filters(core.new_follow('x', 'r/HFY')), '')
        self.assertEqual(core.describe_filters(core.new_follow('x', 'r/stories', layout='each', flair_filter='FICTION')),
                         '  flair \u201cFICTION\u201d, each post as its own book')
        self.assertEqual(core.describe_filters(core.new_follow('x', 'r/HFY', 're:^\\s*Odd(Pattern)\\d+')), '  title starts with \u201cOdd(Pattern)\\d+\u201d'.replace('\\d+', 'd+'))

    def test_follow_for_a_series_matches_its_parts_and_not_other_stories(self):
        group = [g for g in core.group_series(self.posts()) if g['name'] == 'Salvage' and g['author'] == 'Alpha'][0]
        follow = core.series_follow(group)
        self.assertEqual((follow['name'], follow['source']['user'], follow['author_filter'], follow['layout']), ('Salvage', 'Alpha', 'Alpha', 'series'))
        def hit(title, author='Alpha'):
            return core.matches({'title': title, 'author': author, 'html': '<p>x</p>', 'flair': ''}, follow)
        for title in ('Salvage', 'Salvage 40', '[OC][Jenkinsverse] - Salvage 41', 'Salvage, Part 42: The End', '(OC) Salvage 43'):
            self.assertTrue(hit(title), title)
        for title in ('Salvaged goods 1', 'Not Salvage 3'):
            self.assertFalse(hit(title), title)
        self.assertFalse(hit('Salvage 5', author='Bravo'))

    def test_discover_source(self):
        self.assertEqual(core.discover_source('r/HFY', 'deathworld', 'top-year'),
                         {'kind': 'search', 'subreddit': 'HFY', 'user': '', 'query': 'deathworld', 'sort': 'top', 't': 'year'})
        self.assertEqual(core.discover_source('HFY', '', 'top-all')['sort'], 'top')
        self.assertEqual(core.discover_source('HFY', '', 'relevance')['sort'], 'top', 'browsing without words has no relevance, so top')
        self.assertEqual(core.discover_source('HFY', '', 'new')['sort'], 'new')
        with self.assertRaises(core.SourceError):
            core.discover_source('not a name!', 'x')
        self.assertIn('/r/HFY/top?', core.api_path(core.discover_source('HFY', '', 'top-month')))
        self.assertIn('t=month', core.api_path(core.discover_source('HFY', '', 'top-month')))
        self.assertIn('sort=relevance', core.api_path(core.discover_source('HFY', 'x', 'relevance')))
        self.assertIn('/r/HFY/new?', core.api_path(core.parse_source('r/HFY')), 'following still reads the newest')

    def test_discover_reads_pages_filters_flair_and_the_preview_finds_the_whole_series(self):
        pages = [dict(p) for p in self.posts()]
        source = FakeSource(pages, per_page=4)
        groups = core.discover(source, {'kind': 'subreddit', 'subreddit': 'HFY'}, pages=2)
        self.assertEqual(len(source.calls), 2, 'only the pages asked for')
        only = core.discover(FakeSource(pages, per_page=20), {'kind': 'subreddit'}, flair='FirstOf')
        self.assertEqual([g['name'] for g in only], ['Opener'])
        history = [dict(self.posts()[0], id=f't3_h{n}', title=f'Salvage {n}', created=1600000000.0 + n * 86400) for n in range(1, 61)]
        follow = core.series_follow([g for g in core.group_series(self.posts()) if g['name'] == 'Salvage' and g['author'] == 'Alpha'][0])
        seen = core.preview_series(FakeSource(history, per_page=25), follow)
        self.assertEqual((seen['count'], seen['first'], seen['last']), (60, '2020-09-14', '2020-11-12'))
        self.assertIn('Salvage 1', seen['titles'][0])


class EachPostTests(unittest.TestCase):
    """A subreddit of stand-alone stories by many authors: every post becomes its own book."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.preset = [p for p in core.PRESETS if p.get('layout') == 'each'][0]
        self.follow = core.new_follow(self.preset['name'], self.preset['source'], layout='each')

    def story_post(self, n, title, flair='FICTION - Cousin', author=None):
        e = post(n, title=title, author=author or f'writer{n}')
        e['flair'] = flair
        return e

    def test_flair_filter_and_the_age_guard(self):
        follow = dict(self.follow, flair_filter='FICTION')
        self.assertTrue(core.matches(self.story_post(1, 'A summer at the lake (A story)'), follow))
        self.assertFalse(core.matches(self.story_post(1, 'A summer', flair='TRUE STORY - Uncle'), follow))
        self.assertTrue(core.matches(self.story_post(1, 'Long weekend', flair='x'), self.follow))
        self.assertTrue(core.matches(self.story_post(1, 'Reunion [44M] and [24M]'), self.follow))
        for title in ('Camping trip [16M] and [40M]', 'Me (17) and him', 'I am 15 yo and', 'About 17m things', 'We were 16 years old'):
            self.assertTrue(core.stated_minor_age(title), title)
            self.assertFalse(core.matches(self.story_post(1, title), self.follow), title)
        self.assertTrue(core.matches(self.story_post(1, 'Part (2)'), dict(self.follow, layout='series')), 'series follows are not age-filtered')
        self.assertFalse(core.stated_minor_age('Born in 1990, 21 chapters, [18M] and [45M]'))

    def test_first_check_takes_only_the_newest_few_and_later_checks_the_rest(self):
        posts = [self.story_post(n, f'Story number {n}') for n in range(40, 0, -1)]
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        result = core.check_follow(FakeSource(posts, per_page=100), self.follow, cache)
        self.assertEqual(len(cache.posts), core.EACH_FIRST_RUN)
        self.assertEqual(len(result['new']), core.EACH_FIRST_RUN)
        self.assertIn('t3_040', cache.posts)
        self.assertNotIn('t3_001', cache.posts, 'the old backlog is not pulled in')
        later = core.check_follow(FakeSource([self.story_post(41, 'Story number 41')] + posts, per_page=100), self.follow, cache)
        self.assertEqual(later['new'], ['Story number 41'])

    def test_books_are_made_once_oldest_first_and_edits_make_a_fresh_one(self):
        cache = core.ChapterCache(self.tmp.name, self.follow['id'])
        core.check_follow(FakeSource([self.story_post(n, f'Story {n}') for n in (3, 2, 1)], per_page=10), self.follow, cache)
        self.assertEqual([p['title'] for p in core.pending_each(self.follow, cache)], ['Story 1', 'Story 2', 'Story 3'])
        cache.data['added'] = ['t3_001', 't3_002']
        self.assertEqual([p['id'] for p in core.pending_each(self.follow, cache)], ['t3_003'])
        edited = dict(self.story_post(2, 'Story 2'), html='<p>edited</p>')
        core.check_follow(FakeSource([self.story_post(3, 'Story 3'), edited, self.story_post(1, 'Story 1')], per_page=10),
                          self.follow, cache, max_pages=1)
        self.assertEqual(cache.data['added'], ['t3_001'], 'an edited post is made into a book again')
        self.assertEqual(len(core.pending_each(self.follow, cache, limit=1)), 1, 'at most `limit` per run')

    def test_one_post_as_a_story_with_tags_and_series(self):
        one = dict(self.story_post(7, 'The Lake House, Part 3', flair='TRUE STORY - Brother/In-Law/Step', author='writer7'),
                   link='https://www.reddit.com/r/gayincest_stories/comments/abc/x/')
        story = core.build_each(self.follow, one)
        self.assertEqual((story['title'], story['author'], story['series'], story['series_index']), ('The Lake House, Part 3', 'writer7', 'The Lake House', 3.0))
        self.assertEqual(story['tags'], ['Reddit', 'r/gayincest_stories', 'TRUE STORY', 'Brother/In-Law/Step'])
        self.assertEqual(story['id'], 'reddit-t3_007')
        self.assertEqual(len(story['sections']), 1)
        self.assertNotIn('series', core.build_each(self.follow, self.story_post(8, 'Just a title')))
        self.assertNotIn('series', core.build_each(self.follow, self.story_post(8, 'Ch 2')), 'a stem too short to name a series is ignored')
        core.build_epub(story)

    def test_listing_keeps_the_flair(self):
        listing = json.dumps({'data': {'after': None, 'children': [{'kind': 't3', 'data': {
            'name': 't3_a', 'title': 'T', 'author': 'w', 'created_utc': 1.0, 'permalink': '/r/x/comments/a/t/',
            'selftext_html': '<div class="md"><p>Hi.</p></div>', 'link_flair_text': 'FICTION - Cousin'}}]}})
        self.assertEqual(core.parse_listing(listing)[0][0]['flair'], 'FICTION - Cousin')


class ProbeTests(unittest.TestCase):
    SRC = core.parse_source('r/HFY')

    def follow(self, **kw):
        return dict({'title_filter': 'Out of Cruel Space', 'author_filter': ''}, **kw)

    def test_a_good_answer_reports_counts_and_filter_matches(self):
        source = FakeSource([post(3), post(2), post(1, title='Unrelated tale'), post(9, html='')], per_page=10)
        ok, message = core.probe(source, self.SRC, self.follow())
        self.assertTrue(ok)
        self.assertIn('4 post(s), 3 with story text', message)
        self.assertIn('2 match your filters', message)
        self.assertIn('Out of Cruel Space (Chapter 3)', message, 'the newest match is named')
        self.assertEqual(len(source.calls), 1, 'a probe makes exactly one request')
        self.assertIn('public feed', message)

    def test_without_a_follow_and_when_nothing_matches_yet(self):
        ok, message = core.probe(FakeSource([post(1, title='Other')], per_page=5), self.SRC)
        self.assertTrue(ok)
        self.assertNotIn('match', message)
        ok, message = core.probe(FakeSource([post(1, title='Other')], per_page=5), self.SRC, self.follow())
        self.assertIn('none on this first page', message)

    def test_empty_listing(self):
        ok, message = core.probe(FakeSource([], per_page=5), self.SRC)
        self.assertTrue(ok)
        self.assertIn('no posts', message)

    def test_problems_are_reported_not_raised(self):
        class Raising:
            mode = 'rss'

            def __init__(self, exc):
                self.exc = exc

            def page(self, *args, **kwargs):
                raise self.exc
        ok, message = core.probe(Raising(core.RateLimited('u', 600)), self.SRC)
        self.assertFalse(ok)
        self.assertIn('429', message)
        self.assertIn('10 minute', message)
        ok, message = core.probe(Raising(core.RateLimited('u')), self.SRC)
        self.assertIn('429', message)
        ok, message = core.probe(Raising(IOError('Could not fetch x: HTTP Error 403')), self.SRC)
        self.assertFalse(ok)
        self.assertIn('403', message)
        ok, message = core.probe(Raising(ValueError('not XML')), self.SRC)
        self.assertIn('usable answer', message)
        ok, message = core.probe(core.ApiSource(core.Fetcher(min_interval=0), '', '', ''), self.SRC)
        self.assertFalse(ok)
        self.assertIn('client id', message)

    def test_make_source_picks_the_reader_and_the_pace(self):
        feed_source = core.make_source('rss')
        self.assertEqual((feed_source.mode, feed_source.fetcher.min_interval), ('rss', core.MIN_FEED_INTERVAL))
        api_source = core.make_source('api', 'abc', '', 'me')
        self.assertEqual((api_source.mode, api_source.fetcher.min_interval), ('api', core.MIN_API_INTERVAL))
        self.assertEqual(api_source.user_agent, 'calibre:reddit-follower:1.0 (by /u/me)')


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.follow = core.new_follow('Out of Cruel Space', 'https://www.reddit.com/r/HFY/search/?q=Out+of+Cruel+Space&restrict_sr=1', 'Out of Cruel Space')

    def tearDown(self):
        self.tmp.cleanup()

    def cache(self):
        return core.ChapterCache(self.tmp.name, self.follow['id'])

    def feed_posts(self, upto, noise=()):
        posts = [post(n) for n in range(upto, 0, -1)]  # newest first
        for n in noise:
            posts.insert(1, post(900 + n, title=f'Unrelated story {n}'))
        return posts

    def test_first_check_reads_back_through_the_feed(self):
        source = FakeSource(self.feed_posts(5, noise=(1, 2)))
        cache = self.cache()
        result = core.check_follow(source, self.follow, cache)
        self.assertEqual(len(result['new']), 5)
        self.assertEqual(len(cache.posts), 5, 'unrelated posts are not kept')
        self.assertEqual(result['pages'], 4, 'every page was read until the feed ended')
        self.assertEqual([p['title'][-3:-1] for p in cache.ordered()], ['r 1', 'r 2', 'r 3', 'r 4', 'r 5'][:0] or [p['title'][-3:-1] for p in cache.ordered()])
        self.assertEqual(cache.ordered()[0]['title'], 'Out of Cruel Space (Chapter 1)', 'oldest first')
        self.assertTrue(Path(cache.path).exists())

    def test_later_check_stops_at_what_it_has_seen_and_finds_only_new_posts(self):
        core.check_follow(FakeSource(self.feed_posts(5)), self.follow, self.cache())
        source = FakeSource(self.feed_posts(7), per_page=2)
        cache = self.cache()  # reloaded from disk
        self.assertEqual(len(cache.posts), 5)
        result = core.check_follow(source, self.follow, cache)
        self.assertEqual(sorted(result['new']), ['Out of Cruel Space (Chapter 6)', 'Out of Cruel Space (Chapter 7)'])
        self.assertLessEqual(result['pages'], 2, 'it must not page through the whole history again')
        self.assertEqual(len(cache.posts), 7)
        quiet = core.check_follow(FakeSource(self.feed_posts(7)), self.follow, self.cache())
        self.assertEqual((quiet['new'], quiet['changed']), ([], []))

    def test_an_edited_post_is_noticed(self):
        core.check_follow(FakeSource(self.feed_posts(3)), self.follow, self.cache())
        posts = self.feed_posts(3)
        posts[0] = post(3, html='<p>Chapter 3 text, corrected.</p>')
        cache = self.cache()
        result = core.check_follow(FakeSource(posts), self.follow, cache)
        self.assertEqual((result['new'], result['changed']), ([], ['Out of Cruel Space (Chapter 3)']))
        self.assertIn('corrected', cache.posts['t3_003']['html'])

    def test_a_rate_limit_keeps_what_was_read_and_propagates(self):
        source = FakeSource(self.feed_posts(8), per_page=2, fail_on_page=3)
        cache = self.cache()
        with self.assertRaises(core.RateLimited) as caught:
            core.check_follow(source, self.follow, cache)
        self.assertEqual(caught.exception.retry_after, 90)
        self.assertEqual(len(self.cache().posts), 4, 'two pages were read and saved before the limit')

    def test_cancel_stops_before_the_next_page(self):
        calls = []
        source = FakeSource(self.feed_posts(8), per_page=2)
        with self.assertRaises(core.Cancelled):
            core.check_follow(source, self.follow, self.cache(), cancelled=lambda: len(source.calls) >= 1)
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(calls, [])

    def test_a_damaged_cache_is_rebuilt(self):
        cache = self.cache()
        Path(cache.path).write_text('{not json')
        result = core.check_follow(FakeSource(self.feed_posts(2)), self.follow, self.cache())
        self.assertEqual(len(result['new']), 2)

    def test_book_is_built_oldest_first_with_chapters(self):
        cache = self.cache()
        core.check_follow(FakeSource(self.feed_posts(4)), self.follow, cache)
        story = core.build_story(self.follow, cache)
        self.assertEqual((story['title'], story['author'], len(story['sections'])), ('Out of Cruel Space', 'Writer', 4))
        self.assertEqual([h for h, _ in story['sections']][0], 'Out of Cruel Space (Chapter 1)')
        self.assertIn('4 chapters collected from Reddit', story['summary'])
        self.assertEqual(story['tags'], ['Reddit', 'r/HFY'])
        data = core.build_epub(story)
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIsNone(z.testzip())
        for name in z.namelist():
            if name.endswith(('.xhtml', '.opf', '.ncx', '.xml')):
                xml.dom.minidom.parseString(z.read(name))
        self.assertIn('<dc:publisher>Reddit</dc:publisher>', z.read('OEBPS/content.opf').decode())
        with self.assertRaises(ValueError):
            core.build_story(self.follow, core.ChapterCache(self.tmp.name, 'empty'))


class RunFollowsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.a = core.new_follow('Series A', 'u/alice', 'Chapter')
        self.b = core.new_follow('Series B', 'u/bob', 'Chapter')
        self.c = core.new_follow('Series C', 'u/carol', 'Chapter')

    def tearDown(self):
        self.tmp.cleanup()

    class BySource:
        """Feeds keyed by the followed user; can raise per user."""
        mode = 'rss'

        def __init__(self, feeds, raises=None):
            self.feeds, self.raises, self.asked = feeds, raises or {}, []

        def page(self, src, after=None, limit=100):
            self.asked.append(src['user'])
            if src['user'] in self.raises:
                raise self.raises[src['user']]
            return self.feeds[src['user']], None

    def test_due_respects_the_interval_and_never_polls_faster_than_the_minimum(self):
        follow = {'last_checked': 1000.0}
        self.assertFalse(core.due(follow, 6, now=1000.0 + 5 * 3600))
        self.assertTrue(core.due(follow, 6, now=1000.0 + 6 * 3600))
        self.assertTrue(core.due({}, 6, now=5000.0), 'never checked is always due')
        self.assertFalse(core.due(follow, 0.01, now=1000.0 + 1800), 'asking for a tiny interval still waits the minimum hour')

    def test_each_follow_is_checked_and_stamped(self):
        source = self.BySource({'alice': [post(1, 'Chapter 1')], 'bob': [post(2, 'Chapter 9', author='bob')], 'carol': []})
        results = core.run_follows(source, [self.a, self.b, self.c], self.tmp.name, now=lambda: 4242.0)
        self.assertEqual([r['new'] for r in results], [['Chapter 1'], ['Chapter 9'], []])
        self.assertEqual(self.a['last_checked'], 4242.0)
        self.assertEqual(self.a['last_status'], '1 new chapter(s)')
        self.assertEqual(self.c['last_status'], 'up to date')

    def test_an_error_in_one_follow_does_not_stop_the_others(self):
        source = self.BySource({'alice': [post(1, 'Chapter 1')], 'carol': [post(3, 'Chapter 3', author='carol')]},
                               raises={'bob': IOError('Could not fetch: HTTP 404')})
        results = core.run_follows(source, [self.a, self.b, self.c], self.tmp.name)
        self.assertEqual([bool(r['error']) for r in results], [False, True, False])
        self.assertIn('404', self.b['last_status'])
        self.assertEqual(results[2]['new'], ['Chapter 3'])

    def test_a_rate_limit_ends_the_run_and_skips_the_rest(self):
        source = self.BySource({'alice': [post(1, 'Chapter 1')]}, raises={'bob': core.RateLimited('u', 120)})
        results = core.run_follows(source, [self.a, self.b, self.c], self.tmp.name)
        self.assertEqual([r['skipped'] for r in results], [False, False, True])
        self.assertTrue(results[1]['rate_limited'])
        self.assertEqual(results[1]['retry_after'], 120)
        self.assertEqual(source.asked, ['alice', 'bob'], 'carol was never requested after the limit')
        self.assertEqual(self.c.get('last_checked'), 0.0, 'a skipped follow is not marked as checked')
        self.assertIn('slow down', self.b['last_status'])
        self.assertEqual(self.a['last_status'], '1 new chapter(s)', 'work done before the limit stands')

    def test_run_follows_propagates_warning_for_author_note_without_api(self):
        noted_follow = core.new_follow('Series N', 'u/alice', 'Chapter', author_note=True)
        source = self.BySource({'alice': [post(1, 'Chapter 1')]})
        results = core.run_follows(source, [noted_follow], self.tmp.name)
        self.assertEqual(results[0].get('warning'), 'author comments need the official API')
        self.assertIn('author comments need the official API', noted_follow['last_status'])


class FakeResponse(io.BytesIO):
    headers = type('H', (), {'get': lambda self, k, d=None: d, 'get_content_charset': lambda self: 'utf-8'})()
    __enter__ = lambda self: self
    __exit__ = lambda self, *a: False


class SpacingTests(unittest.TestCase):
    def test_requests_without_credentials_stay_under_ten_a_minute(self):
        self.assertGreaterEqual(core.MIN_FEED_INTERVAL, 6.0)
        self.assertGreaterEqual(core.MIN_API_INTERVAL, 1.0)
        self.assertGreaterEqual(core.MIN_CHECK_HOURS, 1.0)


class FeedSourceTests(unittest.TestCase):
    def test_feed_requests_are_polite_and_parsed(self):
        seen = []

        def opener(req, timeout):
            seen.append((req.full_url, dict(req.header_items())))
            return FakeResponse(feed(entry('aaa111', 'Chapter 1', '<p>One.</p>')).encode())

        source = core.FeedSource(core.Fetcher(min_interval=0, opener=opener))
        entries, after = source.page(core.parse_source('r/HFY'))
        self.assertEqual([e['id'] for e in entries], ['t3_aaa111'])
        url, headers = seen[0]
        self.assertTrue(url.startswith('https://www.reddit.com/r/HFY/new.rss'))
        self.assertIn('CalibreRedditFollower', headers['User-agent'], 'an honest, identifying user agent')
        self.assertNotIn('Authorization', headers)

    def test_429_is_not_retried(self):
        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 429, 'Too Many Requests', {'Retry-After': '60'}, None)

        with self.assertRaises(core.RateLimited):
            core.FeedSource(core.Fetcher(min_interval=0, opener=opener)).page(core.parse_source('r/HFY'))
        self.assertEqual(len(calls), 1)


class FakeReddit(http.server.BaseHTTPRequestHandler):
    log = []
    mode = 'ok'

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length'])).decode()
        type(self).log.append(('token', dict(self.headers), body))
        reply = {'error': 'unauthorized_client'} if type(self).mode == 'badcreds' else {'access_token': 'tok123', 'expires_in': 3600, 'token_type': 'bearer'}
        self._send(200, reply)

    def do_GET(self):
        type(self).log.append(('get', dict(self.headers), self.path))
        if type(self).mode == 'scope':
            self.send_response(403); self.send_header('WWW-Authenticate', 'Bearer realm="reddit", error="insufficient_scope"'); self.end_headers(); return
        if type(self).mode == 'limited':
            self.send_response(429); self.send_header('Retry-After', '45'); self.end_headers(); return
        self._send(200, {'kind': 'Listing', 'data': {'after': None, 'children': [{'kind': 't3', 'data': {
            'name': 't3_aaa', 'title': 'Chapter 1', 'author': 'Writer', 'created_utc': 1790000000.0, 'permalink': '/r/HFY/comments/aaa/x/',
            'selftext_html': '<!-- SC_OFF --><div class="md"><p>Hi.</p></div><!-- SC_ON -->'}}]}})

    def _send(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)


class ApiSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), FakeReddit)
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeReddit.log, FakeReddit.mode = [], 'ok'

    def api(self, secret='', client='abc123'):
        return core.ApiSource(core.Fetcher(min_interval=0), client, secret, 'Reader', token_url=self.base + '/token', base=self.base)

    def test_installed_app_flow_and_listing(self):
        entries, after = self.api().page(core.parse_source('r/HFY'))
        self.assertEqual([(e['id'], e['html']) for e in entries], [('t3_aaa', '<p>Hi.</p>')])
        token_call, get_call = FakeReddit.log
        self.assertIn('installed_client', token_call[2])
        self.assertIn('device_id=', token_call[2])
        self.assertTrue(token_call[1]['Authorization'].startswith('Basic '))
        self.assertEqual(get_call[1]['Authorization'], 'bearer tok123')
        self.assertEqual(get_call[1]['User-Agent'], 'calibre:reddit-follower:1.0 (by /u/Reader)')
        self.assertIn('/r/HFY/new?', get_call[2])
        self.assertIn('raw_json=1', get_call[2])

    def test_secret_uses_client_credentials_and_token_is_reused(self):
        api = self.api(secret='s3cret')
        api.page(core.parse_source('u/name'))
        api.page(core.parse_source('u/name'))
        tokens = [c for c in FakeReddit.log if c[0] == 'token']
        self.assertEqual(len(tokens), 1, 'one token serves later pages')
        self.assertIn('grant_type=client_credentials', tokens[0][2])

    def test_bad_credentials_and_missing_client_id(self):
        FakeReddit.mode = 'badcreds'
        with self.assertRaisesRegex(IOError, 'refused the credentials'):
            self.api().page(core.parse_source('r/HFY'))
        with self.assertRaises(core.SourceError):
            self.api(client='').page(core.parse_source('r/HFY'))

    def test_missing_scope_on_a_saved_login_says_to_log_in_again(self):
        FakeReddit.mode = 'scope'
        api = core.ApiSource(core.Fetcher(min_interval=0), 'abc123', '', 'Reader', token_url=self.base + '/token', base=self.base, refresh_token='r1')
        with self.assertRaisesRegex(core.SourceError, 'Log out'):
            api.page(core.parse_source('u/name'))
        with self.assertRaisesRegex(IOError, 'insufficient_scope'):  # without a saved login the reason is still shown
            self.api().page(core.parse_source('u/name'))

    def test_rate_limit_from_the_api(self):
        FakeReddit.mode = 'limited'
        with self.assertRaises(core.RateLimited) as caught:
            self.api().page(core.parse_source('r/HFY'))
        self.assertEqual(caught.exception.retry_after, 45)


class MultiAuthorTests(unittest.TestCase):
    """Tests for multi-author subscriptions, multireddit source parsing, set-based matching, cross-author series grouping, and EPUB metadata attribution."""

    def test_parse_authors_formatting_and_delimiters(self):
        self.assertEqual(core.parse_authors('KyleKKent'), ['KyleKKent'])
        self.assertEqual(core.parse_authors('u/Author1, u/Author2'), ['Author1', 'Author2'])
        self.assertEqual(core.parse_authors('Author1 | /u/Author2 | @Author3'), ['Author1', 'Author2', 'Author3'])
        self.assertEqual(core.parse_authors(['u/Author1', 'Author2', 'Author1']), ['Author1', 'Author2'])
        self.assertEqual(core.parse_authors(''), [])

    def test_multireddit_and_multiuser_source_parsing(self):
        src_sub = core.parse_source('r/HFY+writingprompts')
        self.assertEqual(src_sub, {'kind': 'subreddit', 'subreddit': 'HFY+writingprompts', 'user': '', 'query': ''})

        src_user = core.parse_source('u/Author1+Author2')
        self.assertEqual(src_user, {'kind': 'user', 'subreddit': '', 'user': 'Author1+Author2', 'query': ''})

        src_user_comma = core.parse_source('u/Author1, u/Author2')
        self.assertEqual(src_user_comma, {'kind': 'user', 'subreddit': '', 'user': 'Author1+Author2', 'query': ''})

        src_url = core.parse_source('https://www.reddit.com/r/sub1+sub2/new')
        self.assertEqual(src_url, {'kind': 'subreddit', 'subreddit': 'sub1+sub2', 'user': '', 'query': ''})

    def test_multi_author_set_based_matching(self):
        follow = core.new_follow('Collab Series', 'r/HFY', title_filter='Shared World', author_filter='Author1, Author2')
        self.assertEqual(follow['author_filter'], 'Author1, Author2')

        post1 = {'title': 'Shared World - Part 1', 'author': 'Author1', 'html': '<p>Text 1</p>', 'flair': ''}
        post2 = {'title': 'Shared World - Part 2', 'author': 'Author2', 'html': '<p>Text 2</p>', 'flair': ''}
        post3 = {'title': 'Shared World - Part 3', 'author': 'Author3', 'html': '<p>Text 3</p>', 'flair': ''}

        self.assertTrue(core.matches(post1, follow))
        self.assertTrue(core.matches(post2, follow))
        self.assertFalse(core.matches(post3, follow))

    def test_cross_author_series_grouping(self):
        entries = [
            {'id': 't3_1', 'title': 'Shared Universe 1', 'author': 'Author1', 'html': '<p>Chapter 1</p>', 'created': 100.0},
            {'id': 't3_2', 'title': 'Shared Universe 2', 'author': 'Author2', 'html': '<p>Chapter 2</p>', 'created': 200.0},
            {'id': 't3_3', 'title': 'Shared Universe 3', 'author': 'Author1', 'html': '<p>Chapter 3</p>', 'created': 300.0},
        ]
        groups = core.group_series(entries, cross_author=True)
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g['name'], 'Shared Universe')
        self.assertEqual(g['authors'], ['Author1', 'Author2'])
        self.assertEqual(g['author'], 'Author1, Author2')
        self.assertTrue(g['is_series'])

    def test_multi_author_epub_metadata_and_chapter_attribution(self):
        follow = core.new_follow('Collab Series', 'r/HFY', author_filter='Author1, Author2')
        cache = core.ChapterCache('/tmp/fake_cache', follow['id'])
        cache.data['posts'] = {
            't3_1': {'id': 't3_1', 'title': 'Collab 1', 'author': 'Author1', 'created': 100.0, 'html': '<p>Chapter 1 text</p>', 'link': 'https://reddit.com/1'},
            't3_2': {'id': 't3_2', 'title': 'Collab 2', 'author': 'Author2', 'created': 200.0, 'html': '<p>Chapter 2 text</p>', 'link': 'https://reddit.com/2'},
        }

        story = core.build_story(follow, cache)
        self.assertEqual(story['author'], 'Author1, Author2')
        self.assertIn('(by Author1)', story['sections'][0][0])
        self.assertIn('(by Author2)', story['sections'][1][0])

        epub_bytes = core.build_epub(story)
        extracted = core._build_epub.__module__  # check storykit extraction
        try:
            from common.storykit import extract_epub_story
        except ModuleNotFoundError:
            from calibre_plugins.common.storykit import extract_epub_story
        extracted_story = extract_epub_story(epub_bytes)
        self.assertEqual(extracted_story['author'], 'Author1, Author2')

    def test_followed_keys_multi_author(self):
        follows = [
            core.new_follow('Series A', 'r/HFY', author_filter='Author1, Author2'),
            core.new_follow('Series B', 'r/HFY', author_filter='Author3'),
        ]
        keys = core.followed_keys(follows)
        self.assertIn(('author1', 'series a'), keys)
        self.assertIn(('author2', 'series a'), keys)
        self.assertIn(('author3', 'series b'), keys)


class OpmlTests(unittest.TestCase):
    def test_export_opml_valid_xml_and_metadata(self):
        f1 = core.new_follow('HFY Main', 'r/HFY')
        f2 = core.new_follow('Out of Cruel Space', 'u/KyleKKent', title_filter='Out of Cruel Space',
                             author_filter='KyleKKent', author_note=True, title_from_body=True,
                             index_url='https://docs.google.com/sheet1')
        buf = io.BytesIO()
        cnt = core.export_opml([f1, f2], buf)
        self.assertEqual(cnt, 2)
        xml_str = buf.getvalue().decode('utf-8')

        root = ET.fromstring(xml_str)
        self.assertTrue(root.tag.endswith('opml'))
        self.assertEqual(root.attrib.get('version'), '2.0')

        outlines = root.findall('.//outline')
        self.assertEqual(len(outlines), 2)

        o1 = outlines[0]
        self.assertEqual(o1.attrib['title'], 'HFY Main')
        self.assertEqual(o1.attrib['type'], 'rss')
        self.assertEqual(o1.attrib['xmlUrl'], 'https://www.reddit.com/r/HFY/new.rss?limit=100')
        self.assertEqual(o1.attrib['htmlUrl'], 'https://www.reddit.com/r/HFY/')

        o2 = outlines[1]
        self.assertEqual(o2.attrib['title'], 'Out of Cruel Space')
        self.assertEqual(o2.attrib['xmlUrl'], 'https://www.reddit.com/user/KyleKKent/submitted.rss?sort=new&limit=100')
        self.assertEqual(o2.attrib['htmlUrl'], 'https://www.reddit.com/u/KyleKKent')

    def test_import_opml_roundtrip(self):
        f1 = core.new_follow('HFY Main', 'r/HFY')
        f2 = core.new_follow('Out of Cruel Space', 'u/KyleKKent', title_filter='Out of Cruel Space',
                             author_filter='KyleKKent', author_note=True, title_from_body=True,
                             index_url='https://docs.google.com/sheet1')
        buf = io.BytesIO()
        core.export_opml([f1, f2], buf)
        xml_str = buf.getvalue().decode('utf-8')

        imported, skipped = core.import_opml(xml_str, existing=[])
        self.assertEqual(len(imported), 2)
        self.assertEqual(skipped, 0)

        self.assertEqual(imported[0]['name'], 'HFY Main')
        self.assertEqual(imported[0]['source'], {'kind': 'subreddit', 'subreddit': 'HFY', 'user': '', 'query': ''})

        self.assertEqual(imported[1]['name'], 'Out of Cruel Space')
        self.assertEqual(imported[1]['title_filter'], 'Out of Cruel Space')
        self.assertEqual(imported[1]['author_filter'], 'KyleKKent')
        self.assertTrue(imported[1]['author_note'])
        self.assertTrue(imported[1]['title_from_body'])
        self.assertEqual(imported[1]['index_url'], 'https://docs.google.com/sheet1')

    def test_import_opml_external_aggregator(self):
        external_opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head><title>Feedly Subscriptions</title></head>
  <body>
    <outline text="Reddit HFY" title="Reddit HFY" type="rss" xmlUrl="https://www.reddit.com/r/HFY/.rss" htmlUrl="https://www.reddit.com/r/HFY/"/>
    <outline text="u/KyleKKent posts" title="u/KyleKKent posts" type="rss" xmlUrl="https://www.reddit.com/user/KyleKKent/submitted.rss?sort=new" htmlUrl="https://www.reddit.com/user/KyleKKent"/>
  </body>
</opml>"""
        imported, skipped = core.import_opml(external_opml, existing=[])
        self.assertEqual(len(imported), 2)
        self.assertEqual(skipped, 0)
        self.assertEqual(imported[0]['source']['kind'], 'subreddit')
        self.assertEqual(imported[0]['source']['subreddit'], 'HFY')
        self.assertEqual(imported[1]['source']['kind'], 'user')
        self.assertEqual(imported[1]['source']['user'], 'KyleKKent')

    def test_import_opml_duplicate_detection(self):
        f1 = core.new_follow('HFY Main', 'r/HFY')
        buf = io.BytesIO()
        core.export_opml([f1], buf)
        xml_str = buf.getvalue().decode('utf-8')

        imported, skipped = core.import_opml(xml_str, existing=[f1])
        self.assertEqual(len(imported), 0)
        self.assertEqual(skipped, 1)

    def test_import_opml_invalid_xml_raises_friendly_error(self):
        with self.assertRaises(ValueError) as cm:
            core.import_opml("<opml><unclosed_tag>", existing=[])
        self.assertIn('Invalid OPML XML document', str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            core.import_opml("<html>Not an OPML file</html>", existing=[])
        self.assertIn('Not a valid OPML file', str(cm.exception))


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.follow1 = core.new_follow('Out of Cruel Space', 'u/KyleKKent', 'Out of Cruel Space', 'KyleKKent')
        self.follow1['categories'] = ['Sci-Fi', 'Reddit Series']
        self.follow2 = core.new_follow('r/HFY', 'r/HFY')
        self.follow2['categories'] = ['Subreddits']

    def test_opml_parsing_nested_and_filtering(self):
        opml_text = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Feedly Export</title></head>
  <body>
    <outline text="Fiction Folder" title="Fiction Folder">
      <outline text="Sci-Fi Subfolder" title="Sci-Fi Subfolder">
        <outline text="Out of Cruel Space" title="Out of Cruel Space" type="rss"
                 xmlUrl="https://www.reddit.com/user/KyleKKent/submitted.rss"
                 titleFilter="Out of Cruel Space" authorFilter="KyleKKent"/>
      </outline>
    </outline>
    <outline text="Tech Blogs" title="Tech Blogs">
      <outline text="Hacker News" title="Hacker News" type="rss" xmlUrl="https://news.ycombinator.com/rss"/>
    </outline>
    <outline text="r/HFY" title="r/HFY" type="rss" xmlUrl="https://www.reddit.com/r/HFY/.rss"/>
  </body>
</opml>"""
        subs = subscription.parse_opml(opml_text)
        self.assertEqual(len(subs), 2, "Non-reddit Hacker News feed should be filtered out")
        self.assertEqual(subs[0]['name'], 'Out of Cruel Space')
        self.assertEqual(subs[0]['categories'], ['Fiction Folder', 'Sci-Fi Subfolder'])
        self.assertEqual(subs[0]['title_filter'], 'Out of Cruel Space')
        self.assertEqual(subs[1]['name'], 'r/HFY')
        self.assertEqual(subs[1]['categories'], [])

    def test_opml_large_file_performance(self):
        outlines = []
        for i in range(1000):
            outlines.append(f'<outline text="Sub {i}" title="Sub {i}" type="rss" xmlUrl="https://www.reddit.com/r/sub_{i}/.rss"/>')
        xml_text = f'<opml version="2.0"><body><outline text="Folder">{"".join(outlines)}</outline></body></opml>'
        t0 = time.time()
        subs = subscription.parse_opml(xml_text)
        duration = time.time() - t0
        self.assertEqual(len(subs), 1000)
        self.assertLess(duration, 1.5, "Parsing 1,000 OPML entries should take under 1.5 seconds")

    def test_opml_malformed_xml(self):
        with self.assertRaises(ValueError):
            subscription.parse_opml("<<not xml>>")

    def test_json_parsing_and_schema_validation(self):
        json_data = json.dumps({
            "version": 1,
            "subscriptions": [
                {
                    "name": "Out of Cruel Space",
                    "source_text": "u/KyleKKent",
                    "title_filter": "Out of Cruel Space",
                    "categories": ["Sci-Fi"]
                },
                {
                    "name": "Non-Reddit Feed",
                    "source_text": "https://example.com/rss"
                }
            ]
        })
        subs = subscription.parse_json(json_data)
        self.assertEqual(len(subs), 1, "Non-reddit item should be filtered out")
        self.assertEqual(subs[0]['name'], 'Out of Cruel Space')
        self.assertEqual(subs[0]['categories'], ['Sci-Fi'])

        with self.assertRaises(ValueError):
            subscription.parse_json("{invalid json")

    def test_serialization_round_trip(self):
        follows = [self.follow1, self.follow2]

        # OPML Roundtrip
        opml_out = subscription.generate_opml(follows)
        reimported_opml = subscription.parse_opml(opml_out)
        self.assertEqual(len(reimported_opml), 2)
        self.assertEqual(reimported_opml[0]['name'], 'Out of Cruel Space')
        self.assertEqual(reimported_opml[0]['categories'], ['Sci-Fi', 'Reddit Series'])

        # JSON Roundtrip
        json_out = subscription.generate_json(follows)
        reimported_json = subscription.parse_json(json_out)
        self.assertEqual(len(reimported_json), 2)
        self.assertEqual(reimported_json[0]['name'], 'Out of Cruel Space')
        self.assertEqual(reimported_json[0]['categories'], ['Sci-Fi', 'Reddit Series'])

    def test_merge_strategies(self):
        existing = [self.follow1]
        imported_sub = {
            'name': 'Out of Cruel Space',
            'source_text': 'u/KyleKKent',
            'source': core.parse_source('u/KyleKKent'),
            'title_filter': 'UPDATED FILTER',
            'author_filter': 'KyleKKent',
            'categories': ['Updated Category']
        }
        new_sub = {
            'name': 'New Series',
            'source_text': 'r/HFY',
            'source': core.parse_source('r/HFY'),
            'categories': ['New Category']
        }

        # Strategy 1: Skip
        merged, stats = subscription.merge_subscriptions(existing, [imported_sub, new_sub], strategy='skip')
        self.assertEqual(len(merged), 2)
        self.assertEqual(stats, {'added': 1, 'updated': 0, 'skipped': 1})
        self.assertEqual(merged[0]['title_filter'], 'Out of Cruel Space', "Existing item unchanged")

        # Strategy 2: Overwrite
        merged_ow, stats_ow = subscription.merge_subscriptions(existing, [imported_sub, new_sub], strategy='overwrite')
        self.assertEqual(len(merged_ow), 2)
        self.assertEqual(stats_ow, {'added': 1, 'updated': 1, 'skipped': 0})
        self.assertEqual(merged_ow[0]['title_filter'], 'UPDATED FILTER')
        self.assertEqual(merged_ow[0]['id'], self.follow1['id'], "ID preserved on overwrite")

        # Strategy 3: Add Suffix / Prefix
        merged_sfx, stats_sfx = subscription.merge_subscriptions(existing, [imported_sub, new_sub], strategy='add_suffix', prefix='', suffix=' (Imported)')
        self.assertEqual(len(merged_sfx), 3)
        self.assertEqual(stats_sfx, {'added': 2, 'updated': 0, 'skipped': 0})
        self.assertEqual(merged_sfx[1]['name'], 'Out of Cruel Space (Imported)')
        self.assertNotEqual(merged_sfx[1]['id'], self.follow1['id'], "New unique ID assigned")


_GLOBAL_QT_APP = None


def ensure_qt():
    global _GLOBAL_QT_APP
    try:
        from qt.core import QApplication
        if not QApplication.instance():
            _GLOBAL_QT_APP = QApplication([])
        else:
            _GLOBAL_QT_APP = QApplication.instance()
    except Exception:
        pass

    if 'qt.core' not in sys.modules:
        import types
        from unittest.mock import MagicMock

        try:
            from PyQt6 import QtCore, QtGui, QtWidgets
            app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
            qtcore = types.ModuleType('qt.core')
            for module in (QtWidgets, QtGui, QtCore):
                for name in dir(module):
                    setattr(qtcore, name, getattr(module, name))
            qtcore._ = lambda s: s
            sys.modules['qt'] = types.ModuleType('qt')
            sys.modules['qt.core'] = qtcore
        except ImportError:
            class MockSignal:
                def __init__(self):
                    self._callbacks = []
                def connect(self, cb):
                    self._callbacks.append(cb)
                def emit(self, *args):
                    for cb in self._callbacks:
                        cb(*args)

            class MockWidget:
                StandardButton = MagicMock()
                def __init__(self, *args, **kwargs):
                    self._visible = True
                    self._text = args[0] if args and isinstance(args[0], str) else ''
                    self.textChanged = MockSignal()
                    self.accepted = MockSignal()
                    self.rejected = MockSignal()
                def accept(self): pass
                def reject(self): pass
                def setWordWrap(self, b): pass
                def setToolTip(self, t): pass
                def setPlaceholderText(self, t): pass
                def addWidget(self, w, *a): pass
                def addLayout(self, l, *a): pass
                def addRow(self, *a): pass
                def setVisible(self, v): self._visible = bool(v)
                def isVisible(self): return self._visible
                def show(self): self._visible = True
                def hide(self): self._visible = False
                def setText(self, t):
                    self._text = t
                    self.textChanged.emit(t)
                def text(self): return self._text
                def setWindowTitle(self, *a): pass
                def resize(self, *a): pass
                def setHorizontalHeaderLabels(self, *a): pass
                def setSelectionBehavior(self, *a): pass
                def setEditTriggers(self, *a): pass
                def horizontalHeader(self): return self
                def setStretchLastSection(self, *a): pass
                def setRowCount(self, *a): pass
                def setItem(self, *a): pass

            class MockCheckBox(MockWidget):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._checked = False
                    self.toggled = MockSignal()
                def setChecked(self, c):
                    self._checked = bool(c)
                    self.toggled.emit(self._checked)
                def isChecked(self): return self._checked

            class MockComboBox(MockWidget):
                def addItem(self, *a): pass
                def setCurrentIndex(self, i): pass
                def currentData(self): return 'series'
                currentIndexChanged = MockSignal()

            qtcore = types.ModuleType('qt.core')
            for name in ['QAbstractItemView', 'QDialog', 'QDialogButtonBox', 'QDoubleSpinBox', 'QFormLayout', 'QHBoxLayout', 'QLineEdit', 'QMessageBox', 'QPushButton', 'QRadioButton', 'QSpinBox', 'QTableWidget', 'QTableWidgetItem', 'QVBoxLayout']:
                setattr(qtcore, name, MockWidget)
            qtcore.QCheckBox = MockCheckBox
            qtcore.QLabel = MockWidget
            qtcore.QComboBox = MockComboBox
            qtcore.Qt = MagicMock()
            qtcore._ = lambda s: s

            sys.modules['qt'] = types.ModuleType('qt')
            sys.modules['qt.core'] = qtcore

    import types
    from unittest.mock import MagicMock

    class StubPrefs(dict):
        def __init__(self):
            super().__init__()
            self.defaults = {'mode': 'rss', 'client_id': ''}
        def __getitem__(self, key):
            try:
                return dict.__getitem__(self, key)
            except KeyError:
                return self.defaults.get(key, '')

    if 'calibre' not in sys.modules:
        sys.modules['calibre'] = MagicMock()
        sys.modules['calibre.utils'] = MagicMock()
        sys.modules['calibre.utils.config'] = MagicMock()
        sys.modules['calibre.utils.config'].JSONConfig = lambda name: StubPrefs()

    calibre_plugins_mod = sys.modules.get('calibre_plugins') or types.ModuleType('calibre_plugins')
    sys.modules['calibre_plugins'] = calibre_plugins_mod
    pkg = sys.modules.get('calibre_plugins.reddit_follower') or types.ModuleType('calibre_plugins.reddit_follower')
    pkg.core = core
    setattr(calibre_plugins_mod, 'reddit_follower', pkg)
    sys.modules['calibre_plugins.reddit_follower'] = pkg
    sys.modules['calibre_plugins.reddit_follower.core'] = core
    if not hasattr(pkg, 'login'):
        pkg.login = MagicMock()
        sys.modules['calibre_plugins.reddit_follower.login'] = pkg.login
    if not hasattr(pkg, 'guikit'):
        spec_gk = importlib.util.spec_from_file_location('guikit', Path(__file__).resolve().parents[1] / 'common/guikit.py')
        gk = importlib.util.module_from_spec(spec_gk)
        spec_gk.loader.exec_module(gk)
        pkg.guikit = gk
        sys.modules['calibre_plugins.reddit_follower.guikit'] = gk
    if not hasattr(pkg, 'storykit'):
        pkg.storykit = MagicMock()
        sys.modules['calibre_plugins.reddit_follower.storykit'] = pkg.storykit
    if not hasattr(pkg, 'config'):
        spec_cfg = importlib.util.spec_from_file_location('config', Path(__file__).resolve().parents[1] / 'reddit/config.py')
        cfg = importlib.util.module_from_spec(spec_cfg)
        spec_cfg.loader.exec_module(cfg)
        pkg.config = cfg
        sys.modules['calibre_plugins.reddit_follower.config'] = cfg


class FollowDialogWarningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_qt()
        spec_ui = importlib.util.spec_from_file_location('rui', Path(__file__).resolve().parents[1] / 'reddit/ui.py')
        cls.rui = importlib.util.module_from_spec(spec_ui)
        spec_ui.loader.exec_module(cls.rui)

    def test_author_note_warning_banner_toggle(self):
        # 1. RSS mode (no API credentials): checking author_note shows warning
        self.rui.prefs['mode'] = 'rss'
        self.rui.prefs['client_id'] = ''
        dialog = self.rui.FollowDialog(None)
        dialog.show()
        self.assertFalse(dialog.author_note.isChecked())
        self.assertFalse(dialog.author_note_warning.isVisible())

        dialog.author_note.setChecked(True)
        self.assertTrue(dialog.author_note_warning.isVisible())

        dialog.author_note.setChecked(False)
        self.assertFalse(dialog.author_note_warning.isVisible())

        # 2. API mode but client_id empty: checking author_note shows warning
        self.rui.prefs['mode'] = 'api'
        self.rui.prefs['client_id'] = ''
        dialog2 = self.rui.FollowDialog(None)
        dialog2.show()
        dialog2.author_note.setChecked(True)
        self.assertTrue(dialog2.author_note_warning.isVisible())

        # 3. API mode and client_id provided: checking author_note does not show warning
        self.rui.prefs['mode'] = 'api'
        self.rui.prefs['client_id'] = 'my_client_id'
        dialog3 = self.rui.FollowDialog(None)
        dialog3.show()
        dialog3.author_note.setChecked(True)
        self.assertFalse(dialog3.author_note_warning.isVisible())


if __name__ == '__main__':
    unittest.main()
