import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import types
import unittest
import urllib.error
import zipfile
from email.message import Message
from unittest.mock import MagicMock

spec = importlib.util.spec_from_file_location('engine', Path(__file__).resolve().parents[1] / 'gemini/engine.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def book(i, **kw):
    base = {'id': i, 'title': f'Book {i}', 'authors': ['A B'], 'series': '', 'series_index': None, 'tags': [], 'languages': [],
            'publisher': '', 'filenames': []}
    base.update(kw)
    return base


def reply(answers):
    return json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps(answers)}]}, 'finishReason': 'STOP'}]}).encode()


def http_error(code, message='x', retry_after=None):
    headers = Message()
    if retry_after:
        headers['Retry-After'] = retry_after
    return urllib.error.HTTPError('https://x', code, 'err', headers, io.BytesIO(json.dumps({'error': {'message': message}}).encode()))


class Scripted:
    """A transport that returns or raises the next scripted item and records the requests."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, body))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item(body) if callable(item) else item


class SanitizeTests(unittest.TestCase):
    F = engine.FIELDS

    def test_title_and_noop(self):
        b = book(1, title='dune messiah [epub]')
        self.assertEqual(engine.sanitize(b, {'title': ' Dune  Messiah '}, self.F)['title'], ('dune messiah [epub]', 'Dune Messiah'))
        self.assertEqual(engine.sanitize(b, {'title': 'dune messiah [epub]'}, self.F), {})
        self.assertEqual(engine.sanitize(b, {'title': None}, self.F), {})
        self.assertEqual(engine.sanitize(b, {'title': 'x' * 500}, self.F), {})

    def test_only_requested_fields(self):
        self.assertEqual(engine.sanitize(book(1), {'title': 'New'}, ('tags',)), {})

    def test_authors_drop_placeholders_and_dupes(self):
        b = book(1, authors=['Herbert, Frank'])
        got = engine.sanitize(b, {'authors': ['Frank Herbert', 'Frank Herbert', 'Unknown']}, self.F)
        self.assertEqual(got['authors'], (['Herbert, Frank'], ['Frank Herbert']))
        self.assertEqual(engine.sanitize(b, {'authors': ['Unknown']}, self.F), {})
        self.assertEqual(engine.sanitize(b, {'authors': 'Frank'}, self.F), {})

    def test_series_and_index(self):
        b = book(1, title='Dune Messiah (Dune #2)')
        got = engine.sanitize(b, {'series': 'Dune', 'series_index': 2}, self.F)
        self.assertEqual(got['series'], (('', None), ('Dune', 2.0)))
        self.assertEqual(engine.sanitize(b, {'series': 'Dune', 'series_index': 'abc'}, self.F)['series'][1], ('Dune', 1.0))
        self.assertEqual(engine.sanitize(b, {'series': 'Dune', 'series_index': -3}, self.F)['series'][1], ('Dune', 1.0))
        self.assertEqual(engine.sanitize(b, {'series': 'Dune', 'series_index': True}, self.F)['series'][1], ('Dune', 1.0))
        same = book(2, series='Dune', series_index=2.0)
        self.assertEqual(engine.sanitize(same, {'series': 'Dune', 'series_index': 2}, self.F), {})

    def test_tags_add_only_by_default(self):
        b = book(1, tags=['Sci-Fi', 'Keep'])
        got = engine.sanitize(b, {'tags': ['Science Fiction', 'Keep', 'sci-fi']}, self.F)
        self.assertEqual(got['tags'][1], ['Sci-Fi', 'Keep', 'Science Fiction'])
        cleaned = engine.sanitize(b, {'tags': ['Science Fiction', 'Keep']}, self.F, tags_may_be_removed=True)
        self.assertEqual(cleaned['tags'][1], ['Science Fiction', 'Keep'])
        flood = engine.sanitize(book(2), {'tags': [f't{i}' for i in range(30)]}, self.F)
        self.assertEqual(len(flood['tags'][1]), 5)
        self.assertEqual(engine.sanitize(b, {'tags': ['keep', 'SCI-FI']}, self.F), {})

    def test_language_only_when_empty_and_valid(self):
        self.assertEqual(engine.sanitize(book(1), {'language': 'ENG'}, self.F)['language'], ([], ['eng']))
        self.assertEqual(engine.sanitize(book(1, languages=['fra']), {'language': 'eng'}, self.F), {})
        self.assertEqual(engine.sanitize(book(1), {'language': 'English'}, self.F), {})

    def test_big_change_flag(self):
        tidy = {'title': ('the hobbit (retail)', 'The Hobbit')}
        self.assertFalse(engine.is_big_change(tidy))
        self.assertTrue(engine.is_big_change({'title': ('The Hobbit', 'Cooking With Gas')}))
        self.assertFalse(engine.is_big_change({'authors': (['Herbert, Frank'], ['Frank Herbert'])}))
        self.assertTrue(engine.is_big_change({'authors': (['Frank Herbert'], ['Jane Austen'])}))


class NumberedTitleTests(unittest.TestCase):
    def test_pattern(self):
        f = engine.numbering_in_title
        self.assertEqual(f('book 1 - the force awakens'), ('1', 'the force awakens'))
        self.assertEqual(f('Vol. 2: Title'), ('2', 'Title'))
        self.assertEqual(f('Part 3 \u2013 Title'), ('3', 'Title'))
        self.assertEqual(f('#4. Title'), ('4', 'Title'))
        self.assertEqual(f('Book 2.5 - Interlude'), ('2.5', 'Interlude'))
        self.assertIsNone(f('Book of Shadows'))
        self.assertIsNone(f('The Force Awakens'))
        self.assertIsNone(f('Part 3'))

    def test_prompt_carries_the_hint_only_for_numbered_titles(self):
        rows = json.loads(engine.build_prompt([book(1, title='book 1 - the force awakens'), book(2)]).split('\n', 1)[1])
        self.assertEqual(rows[0]['numbering_in_title'], {'volume': 1.0, 'title_without_it': 'the force awakens'})
        self.assertNotIn('numbering_in_title', rows[1])

    def test_numbered_title_is_fixed_end_to_end(self):
        b = book(1, title='book 1 - the force awakens', authors=['Jo Writer'])
        answer = [{'id': 1, 'title': 'The Force Awakens', 'series': 'The Force', 'series_index': 1, 'confidence': 'high', 'reason': 'volume marker'}]
        out, _ = engine.plan_fixes([b], 'k', transport=Scripted(reply(answer)))
        self.assertEqual(out[0]['changes']['title'], ('book 1 - the force awakens', 'The Force Awakens'))
        self.assertEqual(out[0]['changes']['series'][1], ('The Force', 1.0))
        self.assertFalse(out[0]['big'], 'dropping the marker is a tidy-up, not a different book')

    def test_unknown_series_still_fixes_the_title(self):
        b = book(1, title='book 1 - the force awakens')
        out, _ = engine.plan_fixes([b], 'k', transport=Scripted(reply([{'id': 1, 'title': 'The Force Awakens', 'series': None, 'series_index': 1, 'confidence': 'medium'}])))
        self.assertEqual(list(out[0]['changes']), ['title'])

    def test_volume_only_fix_for_a_book_already_in_a_series(self):
        b = book(1, title='book 2 - x', series='Saga', series_index=1.0)
        got = engine.sanitize(b, {'title': 'X', 'series': None, 'series_index': 2}, engine.FIELDS)
        self.assertEqual(got['series'], (('Saga', 1.0), ('Saga', 2.0)))

    def test_same_author_books_share_a_request(self):
        t = Scripted(reply([]), reply([]))
        books = [book(1, authors=['Zed']), book(2, authors=['Amy']), book(3, authors=['Zed']), book(4, authors=['Amy'])]
        engine.plan_fixes(books, 'k', batch_size=2, transport=t)
        first = [r['id'] for r in json.loads(json.loads(t.calls[0][2])['contents'][0]['parts'][0]['text'].split('\n', 1)[1])]
        self.assertEqual(sorted(first), [2, 4])


class ApiTests(unittest.TestCase):
    def test_request_shape_and_key_in_header_only(self):
        t = Scripted(reply([{'id': 1, 'title': 'T', 'confidence': 'high'}]))
        out = engine.generate('SECRET', 'gemini-test', 'prompt', transport=t)
        url, headers, body = t.calls[0]
        self.assertEqual(out[0]['id'], 1)
        self.assertTrue(url.endswith('/models/gemini-test:generateContent'))
        self.assertNotIn('SECRET', url)
        self.assertEqual(headers['x-goog-api-key'], 'SECRET')
        sent = json.loads(body)
        self.assertEqual(sent['generationConfig']['responseMimeType'], 'application/json')
        self.assertEqual(sent['contents'][0]['parts'][0]['text'], 'prompt')

    def test_retries_rate_limit_then_succeeds(self):
        sleeps = []
        t = Scripted(http_error(429, 'slow down', retry_after='7'), http_error(503), reply([]))
        self.assertEqual(engine.generate('k', 'm', 'p', transport=t, sleep=sleeps.append), [])
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(sum(sleeps[:7]), 7.0, 'the 7 second pause Google asked for, taken in one second slices')

    def test_gives_up_with_readable_error_without_key(self):
        t = Scripted(*[http_error(429, 'quota')] * 3)
        with self.assertRaises(engine.GeminiError) as cm:
            engine.generate('SECRET', 'm', 'p', transport=t, retries=2, sleep=lambda s: None)
        self.assertIn('limit', str(cm.exception))
        self.assertNotIn('SECRET', str(cm.exception))

    def test_bad_key_message(self):
        with self.assertRaises(engine.GeminiError) as cm:
            engine.generate('k', 'm', 'p', transport=Scripted(http_error(400, 'API key not valid. Please pass a valid API key.')))
        self.assertIn('rejected the API key', str(cm.exception))

    def test_network_failure_and_non_json(self):
        with self.assertRaises(engine.GeminiError) as cm:
            engine.generate('k', 'm', 'p', transport=Scripted(urllib.error.URLError('no route')), retries=0)
        self.assertIn('Could not reach Google', str(cm.exception))
        with self.assertRaises(engine.GeminiError):
            engine.generate('k', 'm', 'p', transport=Scripted(b'<html>'), retries=0)

    def test_blocked_prompt_and_empty_answer(self):
        blocked = json.dumps({'promptFeedback': {'blockReason': 'PROHIBITED_CONTENT'}}).encode()
        with self.assertRaises(engine.Blocked):
            engine.generate('k', 'm', 'p', transport=Scripted(blocked))
        safety = json.dumps({'candidates': [{'finishReason': 'SAFETY'}]}).encode()
        with self.assertRaises(engine.Blocked):
            engine.generate('k', 'm', 'p', transport=Scripted(safety))

    def test_truncated_json_is_reported(self):
        cut = json.dumps({'candidates': [{'content': {'parts': [{'text': '[{"id": 1, "tit'}]}}]}).encode()
        with self.assertRaises(engine.GeminiError) as cm:
            engine.generate('k', 'm', 'p', transport=Scripted(cut))
        self.assertIn('smaller batch', str(cm.exception))

    def test_list_models_filters_and_pages(self):
        page1 = json.dumps({'models': [{'name': 'models/gemini-b', 'supportedGenerationMethods': ['generateContent']},
                                       {'name': 'models/embed', 'supportedGenerationMethods': ['embedContent']}], 'nextPageToken': 'n'}).encode()
        page2 = json.dumps({'models': [{'name': 'models/gemini-a', 'supportedGenerationMethods': ['generateContent']}]}).encode()
        t = Scripted(page1, page2)
        self.assertEqual(engine.list_models('k', transport=t), ['gemini-a', 'gemini-b'])
        self.assertIn('pageToken=n', t.calls[1][0])


class PlanTests(unittest.TestCase):
    def test_plan_filters_confidence_noops_and_foreign_ids(self):
        books = [book(1, title='dune [epub]'), book(2, title='Fine'), book(3, title='x file'), book(4, title='Unsure')]
        answers = [{'id': 1, 'title': 'Dune', 'confidence': 'high', 'reason': 'junk'},
                   {'id': 2, 'title': 'Fine', 'confidence': 'high'},
                   {'id': 3, 'title': 'X', 'confidence': 'medium'},
                   {'id': 99, 'title': 'Injected', 'confidence': 'high'},
                   {'id': 4, 'title': 'Other', 'confidence': 'low'}]
        out, skipped = engine.plan_fixes(books, 'k', transport=Scripted(reply(answers)))
        self.assertEqual([s['id'] for s in out], [1, 3])
        self.assertEqual(out[0]['changes']['title'], ('dune [epub]', 'Dune'))
        self.assertEqual(skipped, [])

    def test_missing_answers_are_reported(self):
        out, skipped = engine.plan_fixes([book(1), book(2)], 'k', transport=Scripted(reply([{'id': 1, 'title': 'New', 'confidence': 'high'}])))
        self.assertEqual(len(out), 1)
        self.assertEqual(skipped, ['Book 2: no answer from the model'])

    def test_batches_are_sized(self):
        t = Scripted(reply([]), reply([]), reply([]))
        engine.plan_fixes([book(i) for i in range(5)], 'k', batch_size=2, transport=t)
        self.assertEqual(len(t.calls), 3)

    def test_blocked_batch_is_split_to_find_the_book(self):
        blocked = json.dumps({'promptFeedback': {'blockReason': 'SAFETY'}}).encode()

        def by_body(body):
            ids = [row['id'] for row in json.loads(json.loads(body)['contents'][0]['parts'][0]['text'].split('\n', 1)[1])]
            return blocked if 3 in ids else reply([{'id': i, 'title': f'T{i}', 'confidence': 'high'} for i in ids])

        t = Scripted(*[by_body] * 10)
        out, skipped = engine.plan_fixes([book(i) for i in (1, 2, 3, 4)], 'k', transport=t)
        self.assertEqual(sorted(s['id'] for s in out), [1, 2, 4])
        self.assertEqual(len(skipped), 1)
        self.assertTrue(skipped[0].startswith('Book 3:'))

    def test_cancel_returns_partial_results(self):
        t = Scripted(reply([{'id': 1, 'title': 'One', 'confidence': 'high'}]), reply([]))
        out, skipped = engine.plan_fixes([book(1), book(2)], 'k', batch_size=1, cancelled=lambda: len(t.calls) >= 1, transport=t)
        self.assertEqual([s['id'] for s in out], [1])
        self.assertEqual(len(t.calls), 1, 'no request is started after Cancel')

    def test_bad_key_stops_the_whole_run(self):
        with self.assertRaises(engine.GeminiError):
            engine.plan_fixes([book(1)], 'k', transport=Scripted(http_error(403, 'API key not valid')))


def http_error_with(code, message, details):
    return urllib.error.HTTPError('https://x', code, 'err', Message(), io.BytesIO(json.dumps({'error': {'message': message, 'details': details}}).encode()))


class FreeTierTests(unittest.TestCase):
    def test_per_minute_limit_waits_the_time_google_names_then_succeeds(self):
        sleeps = []
        limited = http_error_with(429, 'Resource exhausted', [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '12s'}])
        t = Scripted(limited, reply([]))
        engine.generate('k', 'm', 'p', transport=t, sleep=sleeps.append)
        self.assertEqual(sum(sleeps), 13.0, '12s from RetryInfo plus one second of margin')

    def test_retry_time_in_the_message_text_is_used_too(self):
        sleeps = []
        t = Scripted(http_error(429, 'Please retry in 4.2s.'), reply([]))
        engine.generate('k', 'm', 'p', transport=t, sleep=sleeps.append)
        self.assertEqual(sum(sleeps[:5]), 5.0)

    def test_daily_quota_is_not_retried(self):
        daily = http_error_with(429, 'Quota exceeded', [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                                                         'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}])
        t = Scripted(daily, reply([]))
        with self.assertRaises(engine.QuotaExhausted) as cm:
            engine.generate('k', 'm', 'p', transport=t, sleep=lambda s: None)
        self.assertEqual(len(t.calls), 1)
        self.assertIn('resets once a day', str(cm.exception))

    def test_daily_limit_midway_keeps_what_was_found(self):
        daily = http_error_with(429, 'Quota exceeded', [{'@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                                                         'violations': [{'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}])
        events = []
        t = Scripted(reply([{'id': 1, 'title': 'One', 'confidence': 'high'}]), daily)
        out, skipped = engine.plan_fixes([book(1), book(2), book(3)], 'k', batch_size=1, transport=t, sleep=lambda s: None,
                                         on_event=lambda kind, **i: events.append(kind))
        self.assertEqual([s['id'] for s in out], [1])
        self.assertEqual(skipped, ['2 book(s) not checked: request limit used up'])
        self.assertEqual(len(t.calls), 2, 'it stops asking once the daily limit is hit')
        self.assertIn('notice', events)

    def test_requests_are_spaced_out(self):
        now = [100.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        t = Scripted(reply([]), reply([]), reply([]))
        engine.plan_fixes([book(1), book(2), book(3)], 'k', batch_size=1, transport=t, min_interval=7, sleep=sleep, clock=lambda: now[0])
        self.assertEqual(len(t.calls), 3)
        self.assertEqual(sum(sleeps), 14.0, 'two gaps of 7 seconds, none before the first request')

    def test_no_spacing_by_default(self):
        sleeps = []
        engine.plan_fixes([book(1), book(2)], 'k', batch_size=1, transport=Scripted(reply([]), reply([])), sleep=sleeps.append)
        self.assertEqual(sleeps, [])

    def test_events_describe_the_work(self):
        events = []
        b = book(1, title='book 1 - x', tags=['a'], filenames=['x.epub'])
        engine.plan_fixes([b], 'k', transport=Scripted(reply([{'id': 1, 'title': 'X', 'confidence': 'high', 'reason': 'marker'}])),
                          on_event=lambda kind, **i: events.append((kind, i)))
        kinds = [k for k, _ in events]
        self.assertEqual(kinds, ['sending', 'received', 'answer', 'progress'])
        self.assertEqual(events[-1][1], {'done': 1, 'total': 1})
        self.assertTrue(events[2][1]['kept'])
        line = engine.describe_book(b)
        self.assertIn('“book 1 - x”', line)
        self.assertIn('files: x.epub', line)
        self.assertEqual(engine.describe_change('title', 'a', 'b'), 'title “a” → “b”')
        self.assertEqual(engine.describe_change('series', ('', None), ('Dune', 2.0)), 'series (none) → Dune #2')

    def test_a_dropped_low_confidence_answer_is_reported(self):
        events = []
        engine.plan_fixes([book(1)], 'k', transport=Scripted(reply([{'id': 1, 'title': 'Other', 'confidence': 'low'}])),
                          on_event=lambda kind, **i: events.append((kind, i)))
        answer = next(i for k, i in events if k == 'answer')
        self.assertFalse(answer['kept'])
        self.assertIn('below your minimum', answer['dropped_why'])


class CoverTests(unittest.TestCase):
    def test_isbn_checksums(self):
        f = engine.clean_isbn
        self.assertEqual(f('978-0-306-40615-7'), '9780306406157')
        self.assertEqual(f('ISBN-13: 978-0-306-40615-7'), '9780306406157')
        self.assertEqual(f('ISBN 13 978-0-306-40615-7'), '9780306406157')
        self.assertEqual(f('ISBN 10 0306406152'), '0306406152')
        self.assertEqual(f('ISBN 0-306-40615-2'), '0306406152')
        self.assertEqual(f('978-0-306-40615-7 (pbk.)'), '9780306406157')
        self.assertEqual(f('0306406152 (alk. paper)'), '0306406152')
        self.assertEqual(f('9780306406158 0306406152'), '0306406152')
        self.assertEqual(f('080442957X'), '080442957X')
        self.assertEqual(f('ISBN: 1034567891'), '', 'a wrong check digit is rejected, not "fixed"')
        self.assertEqual(f('9780306406158'), '')
        self.assertEqual(f('12345'), '')
        self.assertEqual(f(12345), '')
        self.assertEqual(f(None), '')

    def test_isbn_suggestion_only_with_a_valid_number_and_a_difference(self):
        b = book(1, identifiers={})
        got = engine.sanitize(b, {'isbn': '978-0-306-40615-7'}, engine.FIELDS)
        self.assertEqual(got['isbn'], ('', '9780306406157'))
        self.assertEqual(engine.sanitize(b, {'isbn': '9780306406158'}, engine.FIELDS), {})
        have = book(2, identifiers={'isbn': '978-0-306-40615-7'})
        self.assertEqual(engine.sanitize(have, {'isbn': '9780306406157'}, engine.FIELDS), {}, 'same number written differently is no change')
        self.assertEqual(engine.sanitize(b, {'isbn': '9780306406157'}, ('title',)), {})

    def test_images_go_in_the_request_labelled_by_book(self):
        books = [book(5, images=[{'label': 'front cover', 'mime': 'image/jpeg', 'data': 'QUJD'},
                                 {'label': 'back cover', 'mime': 'image/png', 'data': 'REVG'}]), book(6)]
        parts = engine.build_parts(books)
        self.assertIn('Books to check', parts[0]['text'])
        self.assertEqual(parts[1], {'text': 'Image for book id 5 (front cover):'})
        self.assertEqual(parts[2], {'inlineData': {'mimeType': 'image/jpeg', 'data': 'QUJD'}})
        self.assertEqual(parts[3], {'text': 'Image for book id 5 (back cover):'})
        self.assertEqual(parts[4]['inlineData']['mimeType'], 'image/png')
        self.assertEqual(len(parts), 5, 'a book without pictures adds no image parts')
        self.assertIn('picture: front cover, back cover', engine.describe_book(books[0]))

    def test_images_reach_the_wire_and_cover_text_is_reported(self):
        events = []
        t = Scripted(reply([{'id': 1, 'title': 'Real Title', 'isbn': '9780306406157', 'cover_text': 'REAL TITLE by Jo Writer',
                             'confidence': 'high', 'reason': 'printed on the cover'}]))
        b = book(1, title='scan_0001', images=[{'label': 'front cover', 'mime': 'image/jpeg', 'data': 'QUJD'}])
        out, _ = engine.plan_fixes([b], 'k', transport=t, on_event=lambda kind, **i: events.append((kind, i)))
        sent = json.loads(t.calls[0][2])['contents'][0]['parts']
        self.assertEqual(sent[2]['inlineData']['data'], 'QUJD')
        self.assertEqual(out[0]['changes']['title'], ('scan_0001', 'Real Title'))
        self.assertEqual(out[0]['changes']['isbn'][1], '9780306406157')
        answer = next(i for k, i in events if k == 'answer')
        self.assertEqual(answer['cover_text'], 'REAL TITLE by Jo Writer')

    def test_back_cover_from_epub_and_cbz(self):
        with tempfile.TemporaryDirectory() as d:
            epub = Path(d) / 'a.epub'
            with zipfile.ZipFile(epub, 'w') as z:
                z.writestr('OEBPS/images/cover.jpg', b'front')
                z.writestr('OEBPS/images/back-cover.jpg', b'BACK')
                z.writestr('OEBPS/images/feedback.png', b'no')
            got = engine.find_back_cover(epub, 'EPUB')
            self.assertEqual(got[0], b'BACK')
            self.assertIn('back-cover.jpg', got[1])
            plain = Path(d) / 'b.epub'
            with zipfile.ZipFile(plain, 'w') as z:
                z.writestr('cover.jpg', b'front')
            self.assertIsNone(engine.find_back_cover(plain, 'EPUB'))
            cbz = Path(d) / 'c.cbz'
            with zipfile.ZipFile(cbz, 'w') as z:
                for n in ('p01.jpg', 'p02.jpg', 'p10.jpg'):
                    z.writestr(n, n.encode())
            self.assertEqual(engine.find_back_cover(cbz, 'CBZ')[0], b'p10.jpg')
            unpadded_cbz = Path(d) / 'unpadded.cbz'
            with zipfile.ZipFile(unpadded_cbz, 'w') as z:
                for n in ('page1.jpg', 'page2.jpg', 'page10.jpg'):
                    z.writestr(n, n.encode())
            self.assertEqual(engine.find_back_cover(unpadded_cbz, 'CBZ')[0], b'page10.jpg')
            (Path(d) / 'junk.epub').write_bytes(b'not a zip')
            self.assertIsNone(engine.find_back_cover(Path(d) / 'junk.epub', 'EPUB'))
            self.assertIsNone(engine.find_back_cover(Path(d) / 'missing.epub', 'EPUB'))

    def test_natural_sort_key(self):
        f = engine._natural_sort_key
        filenames = ['page10.jpg', 'page1.jpg', 'page2.jpg', 'cover.jpg', 'PAGE1A.JPG']
        sorted_files = sorted(filenames, key=f)
        self.assertEqual(sorted_files, ['cover.jpg', 'page1.jpg', 'PAGE1A.JPG', 'page2.jpg', 'page10.jpg'])


class InputTests(unittest.TestCase):
    def test_prompt_contains_book_fields_and_excerpt(self):
        text = engine.build_prompt([book(7, title='T', filenames=['T - A.epub'], excerpt='Once upon')])
        rows = json.loads(text.split('\n', 1)[1])
        self.assertEqual((rows[0]['id'], rows[0]['file_names'], rows[0]['opening_text']), (7, ['T - A.epub'], 'Once upon'))
        self.assertNotIn('opening_text', json.loads(engine.build_prompt([book(1)]).split('\n', 1)[1])[0])

    def test_epub_excerpt_skips_short_pages_and_bad_files(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'b.epub'
            long_text = 'Word ' * 400
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('a_title.xhtml', '<html><body><p>Title</p></body></html>')
                z.writestr('b_ch1.xhtml', f'<html><head><title>no</title></head><body><script>bad()</script><p>{long_text}</p></body></html>')
            got = engine.epub_excerpt(path, limit=100)
            self.assertEqual(len(got), 100)
            self.assertTrue(got.startswith('Word Word'))
            self.assertNotIn('bad()', got)
            (Path(d) / 'junk.epub').write_bytes(b'not a zip')
            self.assertEqual(engine.epub_excerpt(Path(d) / 'junk.epub'), '')
            self.assertEqual(engine.epub_excerpt(Path(d) / 'missing.epub'), '')


class BackgroundScanTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        for name in ('calibre', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config'):
            sys.modules[name] = MagicMock()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        qt_mod = MagicMock()
        sys.modules['qt'] = qt_mod
        sys.modules['qt.core'] = qt_mod.core

        gemini_pkg = types.ModuleType('calibre_plugins.gemini_library_fixer')
        gemini_pkg.engine = engine
        gemini_pkg.config = MagicMock(prefs={
            'api_key': '', 'model': 'gemini-1.5-flash', 'fields': ['title'],
            'tags_remove': False, 'excerpt': False, 'covers': False,
            'min_confidence': 'medium', 'batch_size': 5, 'min_interval': 7, 'undo': {}
        })
        gemini_pkg.guikit = MagicMock()
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.gemini_library_fixer'] = gemini_pkg
        sys.modules['calibre_plugins.gemini_library_fixer.engine'] = engine
        sys.modules['calibre_plugins.gemini_library_fixer.config'] = gemini_pkg.config
        sys.modules['calibre_plugins.gemini_library_fixer.guikit'] = gemini_pkg.guikit

    def tearDown(self):
        for k in list(sys.modules.keys()):
            if k not in self.saved_modules:
                del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def test_books_for_runs_in_background_thread_with_queue(self):
        import queue
        import threading
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'gemini/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.GeminiFixerAction()

        class MockMetadata:
            title = "Test Title"
            authors = ["Author One"]
            identifiers = {"isbn": "1234567890"}
            series = "Series A"
            series_index = 1.0
            tags = ["Fiction"]
            languages = ["eng"]
            publisher = "Publisher"

        class MockDb:
            def get_metadata(self, book_id, get_cover=False):
                return MockMetadata()
            def formats(self, book_id):
                return ['EPUB']
            def format_abspath(self, book_id, fmt):
                return None
            def cover(self, book_id):
                return None

        class MockGui:
            current_db = MagicMock(new_api=MockDb())

        action.gui = MockGui()

        messages = queue.Queue()
        cancel_event = threading.Event()
        result_container = {}

        def worker():
            try:
                res = action.books_for(
                    [1, 2, 3],
                    with_excerpt=False,
                    on_book=lambda idx, total, b: messages.put(('progress', (idx, total, b))),
                    should_stop=cancel_event.is_set,
                    with_covers=False
                )
                result_container['books'] = res
            except Exception as e:
                result_container['error'] = e

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)

        if 'error' in result_container:
            raise result_container['error']
        self.assertFalse(t.is_alive())
        self.assertEqual(len(result_container['books']), 3)

        progress_events = []
        while not messages.empty():
            progress_events.append(messages.get())

        self.assertEqual(len(progress_events), 3)
        self.assertEqual(progress_events[0][0], 'progress')
        self.assertEqual(progress_events[0][1][0], 1)
        self.assertEqual(progress_events[0][1][1], 3)

    def test_books_for_cancellation(self):
        import queue
        import threading
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'gemini/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.GeminiFixerAction()

        class MockMetadata:
            title = "Test Title"
            authors = ["Author"]
            identifiers = {}
            series = ""
            series_index = None
            tags = []
            languages = []
            publisher = ""

        class MockDb:
            def get_metadata(self, book_id, get_cover=False):
                return MockMetadata()
            def formats(self, book_id):
                return []
            def format_abspath(self, book_id, fmt):
                return None
            def cover(self, book_id):
                return None

        action.gui = MagicMock(current_db=MagicMock(new_api=MockDb()))

        cancel_event = threading.Event()
        messages = queue.Queue()
        result_container = {}

        def on_book(idx, total, b):
            messages.put(('progress', idx))
            if idx == 2:
                cancel_event.set()

        def worker():
            result_container['books'] = action.books_for(
                [1, 2, 3, 4, 5],
                with_excerpt=False,
                on_book=on_book,
                should_stop=cancel_event.is_set,
                with_covers=False
            )

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)

        self.assertFalse(t.is_alive())
        self.assertEqual(len(result_container['books']), 2, "Should stop after 2 books due to cancellation")

    def test_show_dialog_scroll_area_and_sizing(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'gemini/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.GeminiFixerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]
        action.show_dialog()

        qt_core = sys.modules['qt.core']
        qt_core.QDialog.return_value.resize.assert_called_with(1000, 700)
        self.assertTrue(qt_core.QScrollArea.called)


class EstimateBatchUsageTests(unittest.TestCase):
    def test_zero_books_returns_zero_usage(self):
        est = engine.estimate_batch_usage([])
        self.assertEqual(est['books_count'], 0)
        self.assertEqual(est['text_chars'], 0)
        self.assertEqual(est['text_tokens'], 0)
        self.assertEqual(est['image_count'], 0)
        self.assertEqual(est['image_tokens'], 0)
        self.assertEqual(est['prompt_tokens'], 0)
        self.assertEqual(est['batch_count'], 0)
        self.assertEqual(est['estimated_duration'], 0)
        self.assertEqual(est['estimated_cost'], 0.0)

    def test_text_only_token_calculations(self):
        books = [book(i, title=f'Book Title {i}', excerpt='Sample opening excerpt text for testing.') for i in range(1, 4)]
        est = engine.estimate_batch_usage(books, batch_size=15, covers=False)
        self.assertEqual(est['books_count'], 3)
        self.assertEqual(est['image_count'], 0)
        self.assertEqual(est['image_tokens'], 0)
        self.assertGreaterThan(est['text_chars'], 0) if hasattr(self, 'assertGreaterThan') else self.assertTrue(est['text_chars'] > 0)
        self.assertEqual(est['text_tokens'], (est['text_chars'] + 3) // 4)
        self.assertEqual(est['prompt_tokens'], est['text_tokens'])
        self.assertEqual(est['batch_count'], 1)
        self.assertEqual(est['per_request'], 15)

    def test_batch_counts_and_covers_limit(self):
        books = [book(i) for i in range(1, 11)]
        est_no_covers = engine.estimate_batch_usage(books, batch_size=3, covers=False)
        self.assertEqual(est_no_covers['per_request'], 3)
        self.assertEqual(est_no_covers['batch_count'], 4)

        est_covers = engine.estimate_batch_usage(books, batch_size=15, covers=True)
        self.assertEqual(est_covers['per_request'], 8)
        self.assertEqual(est_covers['batch_count'], 2)

    def test_image_tokens_calculation(self):
        b1 = book(1, images=[{'label': 'front cover', 'mime': 'image/jpeg', 'data': 'A'}, {'label': 'back cover', 'mime': 'image/jpeg', 'data': 'B'}])
        b2 = book(2, images=[{'label': 'front cover', 'mime': 'image/jpeg', 'data': 'C'}])
        est = engine.estimate_batch_usage([b1, b2], batch_size=10, covers=True)
        self.assertEqual(est['image_count'], 3)
        self.assertEqual(est['image_tokens'], 3 * 258)
        self.assertEqual(est['prompt_tokens'], est['text_tokens'] + est['image_tokens'])

    def test_cost_and_duration_calculations(self):
        books = [book(i) for i in range(1, 6)]
        est_flash = engine.estimate_batch_usage(books, batch_size=2, covers=False, min_interval=7, model='gemini-2.5-flash')
        self.assertEqual(est_flash['batch_count'], 3)
        # 3 batches with interval=7: (3-1)*7 + 3*2 = 20
        self.assertEqual(est_flash['estimated_duration'], 20)
        self.assertAlmostEqual(est_flash['estimated_cost'], (est_flash['prompt_tokens'] / 1_000_000.0) * 0.075, places=6)

        est_pro = engine.estimate_batch_usage(books, batch_size=2, covers=False, min_interval=0, model='gemini-1.5-pro')
        self.assertAlmostEqual(est_pro['estimated_cost'], (est_pro['prompt_tokens'] / 1_000_000.0) * 1.25, places=6)


class PromptTemplateManagerTests(unittest.TestCase):
    def test_builtin_presets_exist_and_readonly(self):
        presets = engine.BUILTIN_PRESETS
        self.assertIn('Default Ebook Cleanup', presets)
        self.assertIn('Comics & Manga Metadata', presets)
        self.assertIn('Academic & Technical Papers', presets)

        for name, preset in presets.items():
            self.assertTrue(preset.get('read_only'), f"Preset {name} should be read-only")
            self.assertIn('text', preset)
            self.assertIn('description', preset)

    def test_get_all_templates_merges_custom(self):
        custom = {
            'My Custom Preset': {
                'name': 'My Custom Preset',
                'description': 'Custom rules',
                'text': 'Fix {fields}',
            }
        }
        all_tpls = engine.get_all_templates(custom)
        self.assertIn('Default Ebook Cleanup', all_tpls)
        self.assertIn('My Custom Preset', all_tpls)
        self.assertTrue(all_tpls['Default Ebook Cleanup']['read_only'])
        self.assertFalse(all_tpls['My Custom Preset']['read_only'])

    def test_get_default_template_vars(self):
        vars_dict = engine.get_default_template_vars(
            fields=['title', 'authors'],
            min_confidence='high',
            tags_remove=True,
            batch_size=10,
            model='gemini-2.5-pro',
            covers=True,
            excerpt=False
        )
        self.assertEqual(vars_dict['fields'], 'title, authors')
        self.assertEqual(vars_dict['min_confidence'], 'high')
        self.assertIn('allow tag removal', vars_dict['tag_policy'])
        self.assertEqual(vars_dict['batch_size'], '10')
        self.assertEqual(vars_dict['model'], 'gemini-2.5-pro')
        self.assertEqual(vars_dict['covers'], 'enabled')
        self.assertEqual(vars_dict['excerpt'], 'disabled')

    def test_render_template_variable_substitution(self):
        tpl = "Fix fields: {fields} with confidence {min_confidence}. Policy: {tag_policy}."
        vars_dict = {'fields': 'title, authors', 'min_confidence': 'medium', 'tag_policy': 'keep tags'}
        rendered = engine.render_template(tpl, vars_dict)
        self.assertEqual(rendered, "Fix fields: title, authors with confidence medium. Policy: keep tags.")

    def test_render_template_syntax_error_handling(self):
        unclosed = "Fix fields: {fields"
        with self.assertRaises(engine.TemplateError):
            engine.render_template(unclosed)

        unknown_var = "Fix fields: {unknown_placeholder}"
        with self.assertRaises(engine.TemplateError):
            engine.render_template(unknown_var)

    def test_compile_instructions_fallback_on_error(self):
        notices = []
        bad_tpl = "Invalid syntax {bad_var"
        compiled = engine.compile_instructions(bad_tpl, notify=notices.append)
        self.assertEqual(compiled, engine.INSTRUCTIONS)
        self.assertEqual(len(notices), 1)
        self.assertIn('Template syntax error', notices[0])

    def test_estimate_batch_usage_reflects_compiled_template_length(self):
        books = [book(1, title='Test Book')]
        long_custom_tpl = "Custom Instructions: " + ("Rule line. " * 500) + "Fields: {fields}."
        tpl_vars = engine.get_default_template_vars(fields=['title'])

        est_default = engine.estimate_batch_usage(books)
        est_custom = engine.estimate_batch_usage(books, instructions=long_custom_tpl, template_vars=tpl_vars)

        self.assertGreater(est_custom['text_chars'], est_default['text_chars'])
        self.assertGreater(est_custom['text_tokens'], est_default['text_tokens'])

    def test_plan_fixes_and_generate_send_interpolated_instructions(self):
        t = Scripted(reply([{'id': 1, 'title': 'Fixed Title', 'confidence': 'high'}]))
        custom_tpl = "Custom Instructions for comics fields: {fields} with threshold {min_confidence}."
        tpl_vars = {'fields': 'title', 'min_confidence': 'high', 'tag_policy': 'keep', 'batch_size': '15', 'model': 'm', 'covers': 'off', 'excerpt': 'off'}

        out, _ = engine.plan_fixes([book(1)], 'key', transport=t, instructions=custom_tpl, template_vars=tpl_vars)
        self.assertEqual(len(out), 1)

        sent_body = json.loads(t.calls[0][2])
        sys_inst = sent_body['systemInstruction']['parts'][0]['text']
        self.assertEqual(sys_inst, "Custom Instructions for comics fields: title with threshold high.")


class UndoSeriesTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        for name in ('calibre', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config'):
            sys.modules[name] = MagicMock()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        qt_mod = MagicMock()
        sys.modules['qt'] = qt_mod
        sys.modules['qt.core'] = qt_mod.core

        gemini_pkg = types.ModuleType('calibre_plugins.gemini_library_fixer')
        gemini_pkg.engine = engine
        gemini_pkg.config = MagicMock(prefs={})
        gemini_pkg.guikit = MagicMock()
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.gemini_library_fixer'] = gemini_pkg
        sys.modules['calibre_plugins.gemini_library_fixer.engine'] = engine
        sys.modules['calibre_plugins.gemini_library_fixer.config'] = gemini_pkg.config
        sys.modules['calibre_plugins.gemini_library_fixer.guikit'] = gemini_pkg.guikit

    def tearDown(self):
        for k in list(sys.modules.keys()):
            if k not in self.saved_modules:
                del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def test_restore_series_resets_series_and_index_for_book_lacking_series(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'gemini/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.GeminiFixerAction()

        class MockDb:
            def __init__(self):
                self.fields = {1: {'series': None, 'series_index': 1.0}}

            def has_id(self, book_id):
                return book_id in self.fields

            def set_field(self, name, mapping):
                for book_id, val in mapping.items():
                    self.fields[book_id][name] = val

            def field_for(self, name, book_id):
                return self.fields[book_id].get(name)

        mock_db = MockDb()
        action.gui = MagicMock(current_db=MagicMock(new_api=mock_db))

        items = [
            (1, {'series': (('', None), ('Harry Potter', 2.0))})
        ]
        done, errors, undo = action.write_changes(items)
        self.assertEqual(done, [1])
        self.assertEqual(errors, [])
        self.assertIn('1', undo)
        self.assertEqual(mock_db.fields[1]['series'], 'Harry Potter')
        self.assertEqual(mock_db.fields[1]['series_index'], 2.0)

        restored_done, restored_errors = action.restore(undo)
        self.assertEqual(restored_done, [1])
        self.assertEqual(restored_errors, [])
        self.assertIsNone(mock_db.fields[1]['series'])
        self.assertEqual(mock_db.fields[1]['series_index'], 1.0)

    def test_restore_series_keeps_original_index_when_book_had_series(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'gemini/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.GeminiFixerAction()

        class MockDb:
            def __init__(self):
                self.fields = {1: {'series': 'Dune', 'series_index': 3.0}}

            def has_id(self, book_id):
                return book_id in self.fields

            def set_field(self, name, mapping):
                for book_id, val in mapping.items():
                    self.fields[book_id][name] = val

        mock_db = MockDb()
        action.gui = MagicMock(current_db=MagicMock(new_api=mock_db))

        items = [
            (1, {'series': (('Dune', 3.0), ('Dune Chronicles', 1.0))})
        ]
        done, errors, undo = action.write_changes(items)
        self.assertEqual(mock_db.fields[1]['series'], 'Dune Chronicles')
        self.assertEqual(mock_db.fields[1]['series_index'], 1.0)

        restored_done, restored_errors = action.restore(undo)
        self.assertEqual(mock_db.fields[1]['series'], 'Dune')
        self.assertEqual(mock_db.fields[1]['series_index'], 3.0)


class TemplateManagerDialogUiTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        for name in ('calibre', 'calibre.gui2', 'calibre.gui2.actions', 'calibre.utils', 'calibre.utils.config'):
            sys.modules[name] = MagicMock()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        qt_mod = MagicMock()
        sys.modules['qt'] = qt_mod
        sys.modules['qt.core'] = qt_mod.core

        gemini_pkg = types.ModuleType('calibre_plugins.gemini_library_fixer')
        gemini_pkg.engine = engine
        gemini_pkg.config = MagicMock(prefs={
            'api_key': '', 'model': 'gemini-1.5-flash', 'fields': ['title'],
            'tags_remove': False, 'excerpt': False, 'covers': False,
            'min_confidence': 'medium', 'batch_size': 5, 'min_interval': 7, 'undo': {},
            'active_template': 'Default Ebook Cleanup',
            'custom_templates': {}
        })
        gemini_pkg.guikit = MagicMock()
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.gemini_library_fixer'] = gemini_pkg
        sys.modules['calibre_plugins.gemini_library_fixer.engine'] = engine
        sys.modules['calibre_plugins.gemini_library_fixer.config'] = gemini_pkg.config
        sys.modules['calibre_plugins.gemini_library_fixer.guikit'] = gemini_pkg.guikit

    def tearDown(self):
        for k in list(sys.modules.keys()):
            if k not in self.saved_modules:
                del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def test_show_dialog_contains_template_combo(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'gemini/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.GeminiFixerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]
        action.show_dialog()

        # Check QComboBox calls to ensure template selection dropdown was created and populated
        qt_core = sys.modules['qt.core']
        combo_instances = qt_core.QComboBox.return_value
        self.assertTrue(qt_core.QComboBox.called)

    def test_show_dialog_contains_compile_and_batch_buttons(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'gemini/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.GeminiFixerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]
        action.show_dialog()

        qt_core = sys.modules['qt.core']
        translated_strings = [call.args[0] for call in qt_core._.call_args_list if call.args]
        self.assertIn('Compile saved batches', translated_strings)
        self.assertIn('Open batch folder', translated_strings)


class BatchSeparationAndCompilationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_load_batch_output(self):
        suggs = [{
            'id': 101,
            'title': 'Test Book',
            'changes': {'series': (('Old Series', 1.0), ('New Series', 2.0)), 'title': ('Old Title', 'New Title')},
            'confidence': 'high',
            'reason': 'Cleaned series',
            'big': False
        }]
        path = engine.save_batch_output(
            self.tmp_dir, batch_index=1, suggestions=suggs, skipped=['Skipped book 102'],
            book_ids=[101, 102], total_batches=5
        )
        self.assertTrue(os.path.exists(path))
        self.assertEqual(os.path.basename(path), 'batch_0001.json')

        loaded = engine.load_batch_output(path)
        self.assertEqual(loaded['batch_index'], 1)
        self.assertEqual(loaded['total_batches'], 5)
        self.assertEqual(loaded['book_ids'], [101, 102])
        self.assertEqual(loaded['skipped'], ['Skipped book 102'])
        self.assertEqual(len(loaded['suggestions']), 1)
        self.assertEqual(loaded['suggestions'][0]['id'], 101)
        # Verify tuple restoration for series
        series_change = loaded['suggestions'][0]['changes']['series']
        self.assertEqual(series_change, (('Old Series', 1.0), ('New Series', 2.0)))

    def test_list_batch_files_sorting(self):
        for i in [3, 1, 12, 2]:
            engine.save_batch_output(self.tmp_dir, batch_index=i, suggestions=[], book_ids=[i])
        # Write dummy file that shouldn't match
        with open(os.path.join(self.tmp_dir, 'other.txt'), 'w') as f:
            f.write('ignore')

        files = engine.list_batch_files(self.tmp_dir)
        names = [os.path.basename(f) for f in files]
        self.assertEqual(names, ['batch_0001.json', 'batch_0002.json', 'batch_0003.json', 'batch_0012.json'])

    def test_compile_batch_outputs_merges_and_deduplicates(self):
        b1_suggs = [
            {'id': 1, 'title': 'Book 1', 'changes': {'title': ('Old 1', 'New 1')}, 'confidence': 'high', 'reason': '', 'big': False},
            {'id': 2, 'title': 'Book 2', 'changes': {'title': ('Old 2', 'New 2')}, 'confidence': 'high', 'reason': '', 'big': False},
        ]
        b2_suggs = [
            # Book 2 is repeated (updated), Book 3 is new
            {'id': 2, 'title': 'Book 2', 'changes': {'title': ('Old 2', 'New 2 Updated')}, 'confidence': 'high', 'reason': '', 'big': False},
            {'id': 3, 'title': 'Book 3', 'changes': {'title': ('Old 3', 'New 3')}, 'confidence': 'high', 'reason': '', 'big': False},
        ]
        engine.save_batch_output(self.tmp_dir, 1, b1_suggs, skipped=['Skipped A'], book_ids=[1, 2])
        engine.save_batch_output(self.tmp_dir, 2, b2_suggs, skipped=['Skipped B'], book_ids=[2, 3])

        compiled = engine.compile_batch_outputs(self.tmp_dir)
        # Check unpacks as tuple
        suggestions, skipped = compiled
        self.assertEqual(len(suggestions), 3)
        self.assertEqual([s['id'] for s in suggestions], [1, 2, 3])
        # Book 2 should have the updated title
        self.assertEqual(suggestions[1]['changes']['title'][1], 'New 2 Updated')
        self.assertEqual(skipped, ['Skipped A', 'Skipped B'])
        self.assertEqual(compiled.total_batches, 2)
        self.assertEqual(compiled.metadata['total_books'], 3)

    def test_clear_batch_outputs(self):
        engine.save_batch_output(self.tmp_dir, 1, [], book_ids=[1])
        engine.save_batch_output(self.tmp_dir, 2, [], book_ids=[2])
        self.assertEqual(len(engine.list_batch_files(self.tmp_dir)), 2)

        deleted = engine.clear_batch_outputs(self.tmp_dir)
        self.assertEqual(deleted, 2)
        self.assertEqual(len(engine.list_batch_files(self.tmp_dir)), 0)

    def test_get_completed_batch_book_ids(self):
        engine.save_batch_output(self.tmp_dir, 1, [{'id': 10}], book_ids=[10, 11])
        engine.save_batch_output(self.tmp_dir, 2, [{'id': 12}], book_ids=[12, 13])

        ids = engine.get_completed_batch_book_ids(self.tmp_dir)
        self.assertEqual(ids, {10, 11, 12, 13})

    def test_estimate_library_summary(self):
        est = engine.estimate_library_summary(100, batch_size=10, covers=False, min_interval=7)
        self.assertEqual(est['books_count'], 100)
        self.assertEqual(est['batch_count'], 10)
        self.assertEqual(est['per_request'], 10)
        self.assertTrue(est['text_tokens'] > 0)
        self.assertEqual(est['image_tokens'], 0)
        self.assertEqual(est['estimated_duration'], (10 - 1) * 7 + 10 * 2)

    def test_plan_fixes_batched_success(self):
        # 25 book IDs with batch_size 10 -> 3 batches (10, 10, 5)
        ids = list(range(1, 26))
        def fetch_books(chunk_ids):
            return [book(bid, title=f'Title {bid}') for bid in chunk_ids]

        canned = lambda books: [{'id': b['id'], 'confidence': 'high', 'title': f"Clean {b['id']}"} for b in books]
        def fake_transport(url, headers, body, timeout):
            data = json.loads(body.decode('utf-8'))
            prompt_text = data['contents'][0]['parts'][0]['text']
            matched = [int(m) for m in re.findall(r'"id":\s*(\d+)', prompt_text)]
            return json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps([
                {'id': i, 'title': f'Clean {i}', 'confidence': 'high'} for i in matched
            ])}]}}]}).encode('utf-8')

        batch_events = []
        def on_event(kind, **info):
            if kind == 'batch_saved':
                batch_events.append(info)

        compiled = engine.plan_fixes_batched(
            fetch_books, ids, 'test-key', self.tmp_dir, batch_size=10,
            transport=fake_transport, on_event=on_event
        )

        self.assertEqual(len(batch_events), 3)
        self.assertEqual(len(engine.list_batch_files(self.tmp_dir)), 3)
        suggestions, skipped = compiled
        self.assertEqual(len(suggestions), 25)
        self.assertEqual(len(skipped), 0)

    def test_plan_fixes_batched_resume(self):
        # Pre-populate batch 1 for IDs 1..10
        engine.save_batch_output(self.tmp_dir, 1, [{'id': 1, 'title': 'Clean 1', 'changes': {'title': ('Old', 'Clean 1')}, 'confidence': 'high', 'reason': '', 'big': False}], book_ids=list(range(1, 11)))

        ids = list(range(1, 21)) # Total 20 books (1..20)
        fetched_chunks = []
        def fetch_books(chunk_ids):
            fetched_chunks.append(list(chunk_ids))
            return [book(bid, title=f'Title {bid}') for bid in chunk_ids]

        def fake_transport(url, headers, body, timeout):
            data = json.loads(body.decode('utf-8'))
            prompt_text = data['contents'][0]['parts'][0]['text']
            matched = [int(m) for m in re.findall(r'"id":\s*(\d+)', prompt_text)]
            return json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps([
                {'id': i, 'title': f'Clean {i}', 'confidence': 'high'} for i in matched
            ])}]}}]}).encode('utf-8')

        compiled = engine.plan_fixes_batched(
            fetch_books, ids, 'test-key', self.tmp_dir, batch_size=10,
            transport=fake_transport, resume=True
        )

        # Since IDs 1..10 were completed, only IDs 11..20 should be fetched!
        self.assertEqual(fetched_chunks, [list(range(11, 21))])
        self.assertEqual(len(engine.list_batch_files(self.tmp_dir)), 2)
        suggestions, _ = compiled
        # Batch 1 had 1 suggestion, Batch 2 had 10 suggestions -> total 11
        self.assertEqual(len(suggestions), 11)

    def test_plan_fixes_batched_partial_error_preserves_batches(self):
        ids = list(range(1, 25))
        call_count = [0]
        def fetch_books(chunk_ids):
            return [book(bid, title=f'Title {bid}') for bid in chunk_ids]

        def fake_transport(url, headers, body, timeout):
            data = json.loads(body.decode('utf-8'))
            prompt_text = data['contents'][0]['parts'][0]['text']
            matched = [int(m) for m in re.findall(r'"id":\s*(\d+)', prompt_text)]
            if 11 in matched:
                raise urllib.error.HTTPError(url, 429, 'rate limit exhausted', {}, None)
            return json.dumps({'candidates': [{'content': {'parts': [{'text': json.dumps([
                {'id': i, 'title': f'Clean {i}', 'confidence': 'high'} for i in matched
            ])}]}}]}).encode('utf-8')

        with self.assertRaises(engine.GeminiPartialError) as ctx:
            engine.plan_fixes_batched(
                fetch_books, ids, 'test-key', self.tmp_dir, batch_size=10,
                transport=fake_transport, retries=0
            )

        # Batch 1 succeeded and was saved to disk
        self.assertEqual(len(engine.list_batch_files(self.tmp_dir)), 1)
        # Error contains the suggestions from batch 1
        self.assertEqual(len(ctx.exception.suggestions), 10)


if __name__ == '__main__':
    unittest.main()



