import importlib.util
import io
import json
import sys
import unittest
import urllib.parse
import xml.dom.minidom
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
spec = importlib.util.spec_from_file_location('fcore', Path(__file__).resolve().parents[1] / 'furry/core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def turbo_encode(root):
    """Smallest encoder for the turbo-stream table format decode_turbo reads (None uses the -5 null slot)."""
    slots = []

    def add(value):
        if value is None:
            return -5
        idx = len(slots)
        slots.append(None)
        if isinstance(value, dict):
            slots[idx] = {f'_{add(k)}': add(v) for k, v in value.items()}
        elif isinstance(value, list):
            slots[idx] = [add(v) for v in value]
        else:
            slots[idx] = value
        return idx

    add(root)
    return json.dumps(slots)


class FakeFetcher:
    """Routes by URL through a handler function; records what was asked for."""

    def __init__(self, handler):
        self.handler = handler
        self.requested = []

    def get(self, url, **kwargs):
        self.requested.append(url)
        return self.handler(url)


def query_of(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}


# ---------------------------------------------------------------- Inkbunny fixtures

IB_SUBS = {
    '101': {'submission_id': '101', 'title': 'Fox Tale', 'username': 'writer1', 'create_datetime': '2024-03-04 05:06:07',
            'submission_type_id': '12', 'type_name': 'Writing - Document', 'rating_name': 'General', 'description': 'A tale of a fox.',
            'keywords': [{'keyword_name': 'fox'}, {'keyword_name': 'forest'}], 'pools': [],
            'writing': 'The fox ran through the trees.', 'writing_bbcode_parsed': '<p>The fox ran through the trees.</p><p>He was quick.</p>',
            'files': [{'file_name': 'fox tale.txt', 'mimetype': 'text/plain', 'file_url_full': 'https://files.example/101.txt', 'submission_file_order': 0}]},
    '102': {'submission_id': '102', 'title': 'Fox Picture', 'username': 'writer1', 'create_datetime': '2024-03-05 05:06:07',
            'submission_type_id': '1', 'type_name': 'Picture/Pinup', 'rating_name': 'General', 'description': '', 'keywords': [], 'pools': [],
            'files': [{'file_name': 'fox.png', 'mimetype': 'image/png', 'file_url_full': 'https://files.example/102.png', 'submission_file_order': 0}]},
    '103': {'submission_id': '103', 'title': 'Fox Poem', 'username': 'writer1', 'create_datetime': '2024-03-06 05:06:07',
            'submission_type_id': '12', 'type_name': 'Writing - Document', 'rating_name': 'General',
            'description': 'A fox in snow\nwalks soft and slow\nthrough pines that glow\nwith moonlight low\nand dreams in tow\nwhere rivers flow',
            'keywords': [], 'pools': [], 'files': []},
    '201': {'submission_id': '201', 'title': 'Saga One', 'username': 'writer2', 'create_datetime': '2023-01-01 01:00:00',
            'submission_type_id': '12', 'type_name': 'Writing - Document', 'rating_name': 'Mature', 'description': 'First part.',
            'keywords': [], 'pools': [{'pool_id': '9', 'name': 'The Saga'}],
            'files': [{'file_name': 'saga1.txt', 'mimetype': 'text/plain', 'file_url_full': 'https://files.example/201.txt', 'submission_file_order': 0}]},
    '202': {'submission_id': '202', 'title': 'Saga Two', 'username': 'writer2', 'create_datetime': '2023-02-01 01:00:00',
            'submission_type_id': '12', 'type_name': 'Writing - Document', 'rating_name': 'Mature', 'description': 'Second part.',
            'keywords': [], 'pools': [{'pool_id': '9', 'name': 'The Saga'}],
            'files': [{'file_name': 'saga2b.txt', 'mimetype': 'text/plain', 'file_url_full': 'https://files.example/202b.txt', 'submission_file_order': 1},
                      {'file_name': 'saga2a.html', 'mimetype': 'text/html', 'file_url_full': 'https://files.example/202a.html', 'submission_file_order': 0}]},
}


def ib_summary(sid):
    sub = IB_SUBS[sid]
    return {k: sub[k] for k in ('submission_id', 'title', 'username', 'create_datetime', 'submission_type_id', 'type_name', 'rating_name')}

IB_FILES = {
    'https://files.example/101.txt': 'The fox ran\nthrough the trees.\n\nHe was quick.',
    'https://files.example/201.txt': 'Saga begins here.',
    'https://files.example/202a.html': '<html><body><p>Part <b>two</b> opens.</p><script>bad()</script></body></html>',
    'https://files.example/202b.txt': 'And closes here.',
}


def ib_handler(url):
    if url in IB_FILES:
        return IB_FILES[url]
    q = query_of(url)
    if 'api_login.php' in url:
        if q.get('username') == 'bad':
            return json.dumps({'error_code': 1, 'error_message': 'Invalid login'})
        return json.dumps({'sid': 'SID1', 'user_id': '7'})
    if 'api_search.php' in url:
        if q.get('username') == 'writer1':
            return json.dumps({'submissions': [ib_summary(i) for i in ('101', '102', '103')], 'pages_count': 1})
        if q.get('pool_id') == '9':
            return json.dumps({'submissions': [ib_summary(i) for i in ('201', '202')], 'pages_count': 1})
        return json.dumps({'submissions': [], 'pages_count': 1})
    if 'api_submissions.php' in url:
        ids = q['submission_ids'].split(',')
        return json.dumps({'submissions': [IB_SUBS[i] for i in ids if i in IB_SUBS]})
    raise OSError('missing ' + url)


# ---------------------------------------------------------------- SoFurry fixtures

SF_STORY = {'routes/submission.$id': {'data': {'submission': {
    'title': 'River Story', 'description': 'Down by the river.', 'publishedAt': '2025-06-07T08:09:10+00:00', 'rating': 10,
    'author': {'username': 'Bob', 'handle': 'bob'}, 'tags': [99, 100],  # the .data endpoint gives tag ids, not names
    'content': [{'title': 'Start', 'extension': 'txt', 'displayUrl': 'https://files.example/sf1.txt', 'meta': {'wordCount': 10}},
                {'title': 'End', 'extension': 'html', 'displayUrl': 'https://files.example/sf2.html', 'meta': {'wordCount': 5}},
                {'title': 'Cover', 'extension': 'png', 'displayUrl': 'https://files.example/sf.png', 'meta': {}}],
}}}}

SF_FOLDER_PAGE = {'profile': {'data': {'profile': {'handle': 'bob', 'username': 'Bob'}}}}

SF_FOLDER_LISTING = {'folder': {'name': 'Bob Saga'}, 'submissions': {'hasNextPage': False, 'data': [
    {'id': 's2', 'type': 'story', 'title': 'River Story 2', 'publishedAt': '2025-07-01T00:00:00+00:00', 'tags': ['river']},
    {'id': 'a1', 'type': 'artwork', 'title': 'River Picture', 'publishedAt': '2025-06-15T00:00:00+00:00'},
    {'id': 's1', 'type': 'story', 'title': 'River Story', 'publishedAt': '2025-06-07T00:00:00+00:00', 'tags': ['river', 'otters']},
]}}

SF_FILES = {
    'https://files.example/sf1.txt': 'Otters splash\nin the cold river.',
    'https://files.example/sf2.html': '<p>The river <i>ends</i> here.</p>',
}


def sf_handler(url):
    if url in SF_FILES:
        return SF_FILES[url]
    if url == 'https://sofurry.com/s/abc123.data':
        return turbo_encode(SF_STORY)
    if url == 'https://sofurry.com/view/1670329.data':
        return turbo_encode({'routes/submission.$id': {'data': {'submission': {'id': 'zz9'}}}})
    if url == 'https://sofurry.com/u/bob/gallery.data?folder=f1':
        return turbo_encode(SF_FOLDER_PAGE)
    if url.startswith('https://sofurry.com/api/profile'):
        q = query_of(url)
        if q.get('tab') == 'folder' and q.get('folder_id') == 'f1':
            return json.dumps(SF_FOLDER_LISTING)
        if q.get('tab') == 'gallery':
            return json.dumps({'submissions': {'hasNextPage': False, 'data': SF_FOLDER_LISTING['submissions']['data']}})
        return json.dumps({'submissions': {'hasNextPage': False, 'data': []}})
    raise OSError('missing ' + url)


# ---------------------------------------------------------------- tests

class AddressTests(unittest.TestCase):
    def test_classify(self):
        cases = [
            ('https://inkbunny.net/s/123', ('inkbunny', 'story', '123')),
            ('inkbunny.net/submissionview.php?id=456', ('inkbunny', 'story', '456')),
            ('https://inkbunny.net/submissionviewdetail.php?id=77', ('inkbunny', 'story', '77')),
            ('https://inkbunny.net/pools.php?pool_id=9', ('inkbunny', 'pool', '9')),
            ('https://inkbunny.net/poolview_process.php?pool_id=107989', ('inkbunny', 'pool', '107989')),
            ('https://inkbunny.net/submissionsviewall.php?rid=79f72d3948&mode=pool&pool_id=107989&page=1&orderby=pool_order',
             ('inkbunny', 'pool', '107989')),
            ('https://inkbunny.net/s/3902216?pool_id=107989&pool_pos=2', ('inkbunny', 'story', '3902216')),
            ('https://inkbunny.net/gallery/writer1', ('inkbunny', 'author', 'writer1')),
            ('https://inkbunny.net/writer1', ('inkbunny', 'author', 'writer1')),
            ('https://sofurry.com/s/z1V1z3Zn', ('sofurry', 'story', 'z1V1z3Zn')),
            ('https://sofurry.com/u/bob/gallery?folder=f1', ('sofurry', 'folder', 'f1')),
            ('https://sofurry.com/u/bob', ('sofurry', 'author', 'bob')),
            ('https://bob.sofurry.com/', ('sofurry', 'author', 'bob')),
            ('https://www.sofurry.com/view/123456', ('sofurry', 'legacy', '123456')),
        ]
        for line, expected in cases:
            got = core.classify(line)
            self.assertEqual((got['site'], got['kind'], got['key']), expected, line)
        self.assertIsNone(core.classify('https://example.com/s/1'))
        self.assertIsNone(core.classify('https://inkbunny.net/login.php'))
        self.assertIsNone(core.classify('https://inkbunny.net/poolview_process.php'))  # no pool id, and not an author
        self.assertIsNone(core.classify('https://inkbunny.net/submissionsviewall.php?mode=search&text=fox'))
        self.assertIsNone(core.classify(''))


class TurboTests(unittest.TestCase):
    def test_decode_roundtrip_and_sentinels(self):
        payload = turbo_encode({'a': [1, 'x', None], 'b': {'c': True}, 'd': 'text'})
        self.assertEqual(core.decode_turbo(payload), {'a': [1, 'x', None], 'b': {'c': True}, 'd': 'text'})

    def test_decode_tagged_date(self):
        # hand-built table: root dict {_1: 2}; 1='when'; 2 = ['D', '2025-01-01']
        table = [{'_1': 2}, 'when', ['D', '2025-01-01T00:00:00+00:00']]
        self.assertEqual(core.decode_turbo(json.dumps(table)), {'when': '2025-01-01T00:00:00+00:00'})


class TextTests(unittest.TestCase):
    def test_file_to_html(self):
        self.assertEqual(core.file_to_html('Wrapped\nline.\n\n***\n\nEnd.'), '<p>Wrapped line.</p><hr/><p>End.</p>')
        out = core.file_to_html('<html><body><p>Hi <b>there</b></p><script>x()</script></body></html>')
        self.assertIn('Hi', out)
        self.assertNotIn('x()', out)


class InkbunnyTests(unittest.TestCase):
    def setUp(self):
        self.f = FakeFetcher(ib_handler)

    def test_login_guest_and_failure(self):
        self.assertEqual(core.ib_login(self.f), 'SID1')
        guest = [u for u in self.f.requested if 'api_login' in u][0]
        self.assertIn('username=guest', guest)
        self.assertNotIn('password', query_of(guest))  # a guest login sends no password at all
        with self.assertRaises(core.InkbunnyLoginError):
            core.ib_login(FakeFetcher(ib_handler), 'bad', 'nope')

    def test_author_expand_filters_to_writing_oldest_first(self):
        refs, errors = core.expand_targets(self.f, ['https://inkbunny.net/gallery/writer1'])
        self.assertEqual(errors, [])
        self.assertEqual([r['key'] for r in refs], ['101', '103'])  # the Picture is dropped
        search = [u for u in self.f.requested if 'api_search' in u][0]
        q = query_of(search)
        self.assertEqual((q['type'], q['submissions_per_page']), ('12', '100'))  # the API's real parameter names

    def test_fetch_story_from_text_file(self):
        story = core.fetch_story(self.f, core.ref_for(core.INKBUNNY, '101'))
        self.assertEqual((story['title'], story['author'], story['pubdate']), ('Fox Tale', 'writer1', '2024-03-04'))
        self.assertEqual(story['tags'], ['fox', 'forest'])
        self.assertEqual(len(story['sections']), 1)
        self.assertIn('The fox ran through the trees.', story['sections'][0][1])

    def test_fetch_poem_from_description(self):
        story = core.fetch_story(self.f, core.ref_for(core.INKBUNNY, '103'))
        self.assertIn('A fox in snow', story['sections'][0][1])

    def test_non_writing_story_rejected(self):
        with self.assertRaises(ValueError):
            core.fetch_story(self.f, core.ref_for(core.INKBUNNY, '102'))

    def test_pool_expands_in_order_and_compiles_to_one_book(self):
        refs, errors = core.expand_targets(self.f, ['https://inkbunny.net/pools.php?pool_id=9'])
        self.assertEqual(errors, [])
        self.assertEqual([(r['key'], r['group'], r['group_index']) for r in refs],
                         [('201', 'The Saga', 1), ('202', 'The Saga', 2)])
        stories, skipped, failures = core.download_refs(self.f, refs)
        self.assertEqual((len(stories), skipped, failures), (2, 0, []))
        self.assertEqual([h for h, _ in stories[1]['sections']], ['saga2a', 'saga2b'])  # multi-file = chapters
        groups = core.compile_groups(stories)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0][0], 'pool/9')
        data = core.build_epub(groups[0][1], title='The Saga')
        z = zipfile.ZipFile(io.BytesIO(data))
        for name in z.namelist():
            if name.endswith(('.xhtml', '.opf', '.ncx', '.xml')):
                xml.dom.minidom.parseString(z.read(name))
        self.assertIn('<dc:publisher>Inkbunny</dc:publisher>', z.read('OEBPS/content.opf').decode())


class SoFurryTests(unittest.TestCase):
    def setUp(self):
        self.f = FakeFetcher(sf_handler)

    def test_fetch_story(self):
        story = core.fetch_story(self.f, core.ref_for(core.SOFURRY, 'abc123', tags=['river', 'otters']))
        self.assertEqual((story['title'], story['author'], story['pubdate']), ('River Story', 'Bob', '2025-06-07'))
        self.assertEqual(story['tags'], ['river', 'otters'])  # listing names win; the .data tag ids are not tags
        bare = core.fetch_story(self.f, core.ref_for(core.SOFURRY, 'abc123'))
        self.assertEqual(bare['tags'], [])
        self.assertEqual(story['categories'], ['Mature'])
        self.assertEqual([h for h, _ in story['sections']], ['Start', 'End'])  # the png content is not a chapter
        self.assertIn('Otters splash in the cold river.', story['sections'][0][1])
        data = core.build_epub([story])
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIn('<dc:publisher>SoFurry</dc:publisher>', z.read('OEBPS/content.opf').decode())

    def test_chapter_range(self):
        story = core.fetch_story(self.f, core.ref_for(core.SOFURRY, 'abc123'), chapter_range='2')
        self.assertEqual([h for h, _ in story['sections']], ['End'])

    def test_folder_expands_oldest_first_stories_only(self):
        refs, errors = core.expand_targets(self.f, ['https://sofurry.com/u/bob/gallery?folder=f1'])
        self.assertEqual(errors, [])
        self.assertEqual([(r['key'], r['group'], r['group_index']) for r in refs],
                         [('s1', 'Bob Saga', 1), ('s2', 'Bob Saga', 2)])
        self.assertEqual(refs[0]['tags'], ['river', 'otters'])  # listing rows carry tag names

    def test_author_expand(self):
        refs, errors = core.expand_targets(self.f, ['https://sofurry.com/u/bob'])
        self.assertEqual(errors, [])
        self.assertEqual([r['key'] for r in refs], ['s1', 's2'])
        self.assertTrue(all(not r['group_id'] for r in refs))

    def test_legacy_address_resolves_when_the_old_id_is_real(self):
        refs, errors = core.expand_targets(self.f, ['https://www.sofurry.com/view/1670329'])
        self.assertEqual(errors, [])
        self.assertEqual([r['id'] for r in refs], ['sofurry:zz9'])

    def test_legacy_address_reports_how_to_fix_when_it_does_not_resolve(self):
        refs, errors = core.expand_targets(self.f, ['https://www.sofurry.com/view/123456'])
        self.assertEqual(refs, [])
        self.assertEqual(len(errors), 1)
        self.assertIn('sofurry.com/s/', errors[0])


class DispatchTests(unittest.TestCase):
    def test_mixed_lines_and_junk(self):
        f = FakeFetcher(lambda url: ib_handler(url) if 'inkbunny' in url or 'files.example' in url else sf_handler(url))
        refs, errors = core.expand_targets(f, ['https://inkbunny.net/s/101', 'https://sofurry.com/s/abc123', 'junk line'])
        self.assertEqual([r['id'] for r in refs], ['inkbunny:101', 'sofurry:abc123'])
        self.assertEqual(len(errors), 1)

    def test_compile_groups_keeps_loose_stories_separate(self):
        loose = {'id': 'inkbunny:1', 'title': 'A', 'author': 'x', 'sections': []}
        grouped = [dict(loose, id='sofurry:2', group_id='folder/f1', series_index=2.0),
                   dict(loose, id='sofurry:3', group_id='folder/f1', series_index=1.0)]
        groups = core.compile_groups([loose] + grouped)
        self.assertEqual(groups[0], ('', [loose]))
        self.assertEqual([s['id'] for s in groups[1][1]], ['sofurry:3', 'sofurry:2'])  # group order restored


if __name__ == '__main__':
    unittest.main()
