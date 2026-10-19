"""Offline tests for the Media Matcher plugin's pure modules: tag reading, the service clients and the matching logic.

Tag readers are checked two ways: against hand-built bytes (every ID3 version, unsynchronisation, FLAC, Ogg, MP4, damaged files)
and, when ffmpeg is installed, against files a real encoder wrote in every supported format. The service clients run against a
scripted transport; matching runs against responses recorded from the live MusicBrainz and Open Library APIs.
"""
import base64
import importlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
from email.message import Message

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'media_fixtures'
pkg = types.ModuleType('media_pure')  # the plugin's modules use relative imports, so give them a package without running __init__
pkg.__path__ = [str(ROOT / 'media')]
sys.modules['media_pure'] = pkg
tags = importlib.import_module('media_pure.tags')
sources = importlib.import_module('media_pure.sources')
matching = importlib.import_module('media_pure.matching')

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 20
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20


def syncsafe(n):
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


def id3_text(enc, text):
    codec = {0: 'latin-1', 1: 'utf-16', 2: 'utf-16-be', 3: 'utf-8'}[enc]
    return bytes([enc]) + text.encode(codec)


def id3_tag(major, frames, flags=0):
    """frames: [(id, payload)]. v2.2 ids are 3 characters."""
    body = b''
    for frame_id, payload in frames:
        if major == 2:
            body += frame_id.encode() + len(payload).to_bytes(3, 'big') + payload
        elif major == 3:
            body += frame_id.encode() + struct.pack('>I', len(payload)) + b'\x00\x00' + payload
        else:
            body += frame_id.encode() + syncsafe(len(payload)) + b'\x00\x00' + payload
    return b'ID3' + bytes([major, 0, flags]) + syncsafe(len(body)) + body


def mp3_frames(count=100, xing=False):
    """MPEG1 layer 3, 128 kbps, 44.1 kHz stereo: 417-byte frames, 1152 samples each."""
    first = bytearray(b'\xff\xfb\x90\x00' + bytes(413))
    if xing:
        first[36:40] = b'Info'
        first[40:44] = struct.pack('>I', 1)
        first[44:48] = struct.pack('>I', count)
    return bytes(first) + (b'\xff\xfb\x90\x00' + bytes(413)) * (count - 1)


def vorbis_block(entries, vendor='test'):
    out = struct.pack('<I', len(vendor)) + vendor.encode() + struct.pack('<I', len(entries))
    for e in entries:
        raw = e.encode()
        out += struct.pack('<I', len(raw)) + raw
    return out


def flac_picture(kind, mime, image):
    m = mime.encode()
    return struct.pack('>II', kind, len(m)) + m + struct.pack('>I', 0) + struct.pack('>IIII', 1, 1, 24, 0) + struct.pack('>I', len(image)) + image


def flac_file(entries, picture=None, samples=88200, rate=44100, id3=b''):
    info = struct.pack('>HH', 4096, 4096) + bytes(6)  # block sizes, then min/max frame size
    packed = (rate << 44) | (1 << 41) | (15 << 36) | samples
    info += struct.pack('>Q', packed) + bytes(16)
    blocks = [(0, info), (4, vorbis_block(entries))] + ([(6, picture)] if picture else [])
    out = id3 + b'fLaC'
    for i, (kind, data) in enumerate(blocks):
        out += bytes([(0x80 if i == len(blocks) - 1 else 0) | kind]) + len(data).to_bytes(3, 'big') + data
    return out


def ogg_page(packets, seq, granule=0, flags=0):
    segs, body = [], b''
    for p in packets:
        n = len(p)
        while n >= 255:
            segs.append(255)
            n -= 255
        segs.append(n)
        body += p
    return b'OggS' + bytes([0, flags]) + struct.pack('<qIII', granule, 1, seq, 0) + bytes([len(segs)]) + bytes(segs) + body


def ogg_vorbis(entries, rate=44100, granule=88200):
    ident = b'\x01vorbis' + struct.pack('<IBIiii', 0, 2, rate, 0, 0, 0) + b'\xb8\x01'
    comment = b'\x03vorbis' + vorbis_block(entries) + b'\x01'
    return ogg_page([ident], 0, flags=2) + ogg_page([comment, b'\x05vorbis' + bytes(50)], 1) + ogg_page([bytes(30)], 2, granule, flags=4)


def mp4_box(kind, payload):
    return struct.pack('>I4s', 8 + len(payload), kind) + payload


def mp4_data(flag, value):
    return mp4_box(b'data', bytes([0, 0, 0, flag]) + bytes(4) + value)


def mp4_file(items, seconds=5):
    mvhd = mp4_box(b'mvhd', bytes(4) + bytes(8) + struct.pack('>II', 1000, seconds * 1000) + bytes(80))
    ilst = mp4_box(b'ilst', b''.join(items))
    udta = mp4_box(b'udta', mp4_box(b'meta', bytes(4) + ilst))
    return mp4_box(b'ftyp', b'M4B \x00\x00\x00\x00M4B mp42isom') + mp4_box(b'mdat', bytes(100)) + mp4_box(b'moov', mvhd + udta)


class TempFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, data):
        path = Path(self.tmp.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)


class Id3Tests(TempFiles):
    def test_v23_text_numbers_genre_ids_and_front_cover_preferred(self):
        data = id3_tag(3, [
            ('TIT2', id3_text(1, 'Yesterday')), ('TPE1', id3_text(0, 'The Beatles')), ('TPE2', id3_text(0, 'Beatles')),
            ('TALB', id3_text(3, 'Help!')), ('TRCK', id3_text(0, '13/14')), ('TPOS', id3_text(0, '1/2')),
            ('TCON', id3_text(0, '(17)')), ('TYER', id3_text(0, '1965')), ('TCOM', id3_text(0, 'Lennon')),
            ('COMM', id3_text(0, 'x')[:1] + b'eng' + b'\x00' + b'nice song'),
            ('UFID', b'http://musicbrainz.org\x00' + b'0aa1938a-ee7f-487b-b742-8b2cfa110c85'),
            ('TXXX', b'\x00MusicBrainz Album Id\x00' + b'6f1a1c0a-3c7a-4d31-9e62-b32796043b6c'),
            ('APIC', b'\x00image/png\x00\x00\x00' + PNG),    # type 0: other
            ('APIC', b'\x00image/jpeg\x00\x03Front\x00' + JPEG)]) + mp3_frames()
        t = tags.read_tags(self.write('a.mp3', data))
        self.assertEqual((t['title'], t['artist'], t['album_artist'], t['album']), ('Yesterday', 'The Beatles', 'Beatles', 'Help!'))
        self.assertEqual((t['track'], t['track_total'], t['disc'], t['disc_total'], t['year'], t['genre']), (13, 14, 1, 2, 1965, 'Rock'))
        self.assertEqual((t['composer'], t['comment']), ('Lennon', 'nice song'))
        self.assertEqual(t['mb_recording'], '0aa1938a-ee7f-487b-b742-8b2cfa110c85')
        self.assertEqual(t['mb_release'], '6f1a1c0a-3c7a-4d31-9e62-b32796043b6c')
        self.assertEqual(t['cover'], ('image/jpeg', JPEG), 'the front cover wins over an earlier picture of another type')
        self.assertTrue(t['tagged'])

    def test_v24_utf8_and_utf16_and_multiple_values(self):
        data = id3_tag(4, [('TIT2', id3_text(3, 'Café Ünïcode')), ('TPE1', id3_text(2, 'Björk')),
                           ('TALB', id3_text(1, 'Vespertine')), ('TDRC', id3_text(3, '2001-08-27T00:00')),
                           ('TCON', id3_text(3, 'Electronic\x00Ambient'))]) + mp3_frames()
        t = tags.read_tags(self.write('a.mp3', data))
        self.assertEqual((t['title'], t['artist'], t['album'], t['date'], t['year']), ('Café Ünïcode', 'Björk', 'Vespertine', '2001-08-27T00:00', 2001))
        self.assertEqual(t['genre'], 'Electronic')

    def test_v22(self):
        data = id3_tag(2, [('TT2', id3_text(0, 'Old')), ('TP1', id3_text(0, 'Band')), ('TAL', id3_text(0, 'LP')), ('TRK', id3_text(0, '3')),
                           ('PIC', b'\x00JPG\x03\x00' + JPEG)]) + mp3_frames()
        t = tags.read_tags(self.write('a.mp3', data))
        self.assertEqual((t['title'], t['artist'], t['album'], t['track']), ('Old', 'Band', 'LP', 3))
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))

    def test_v23_unsynchronised_tag(self):
        payload = id3_text(0, 'Sync\xff\xfeTest')
        title = ('TIT2', payload)
        raw = id3_tag(3, [title])
        head, body = raw[:10], raw[10:]
        unsynced = body.replace(b'\xff', b'\xff\x00')
        # a v2.3 tag-level unsync flag: sizes in the header count the escaped bytes, frame sizes the original ones
        data = b'ID3' + bytes([3, 0, 0x80]) + syncsafe(len(unsynced)) + unsynced + mp3_frames()
        self.assertEqual(tags.read_tags(self.write('a.mp3', data))['title'], 'Sync\xff\xfeTest')

    def test_id3v1_only_with_track_and_genre(self):
        block = b'TAG' + b'Title'.ljust(30, b'\0') + b'Artist'.ljust(30, b'\0') + b'Album'.ljust(30, b'\0') + b'1999' + \
            b'Comment'.ljust(28, b'\0') + b'\0' + bytes([7, 17])
        t = tags.read_tags(self.write('a.mp3', mp3_frames() + block))
        self.assertEqual((t['title'], t['artist'], t['album'], t['year'], t['track'], t['genre']), ('Title', 'Artist', 'Album', 1999, 7, 'Rock'))

    def test_v2_wins_and_v1_fills_gaps(self):
        block = b'TAG' + b'V1 title'.ljust(30, b'\0') + b'V1 artist'.ljust(30, b'\0') + b'V1 album'.ljust(30, b'\0') + b'1999' + bytes(30) + bytes([255])
        data = id3_tag(3, [('TIT2', id3_text(0, 'V2 title'))]) + mp3_frames() + block
        t = tags.read_tags(self.write('a.mp3', data))
        self.assertEqual((t['title'], t['artist'], t['album']), ('V2 title', 'V1 artist', 'V1 album'))

    def test_mp3_duration_estimated_and_exact(self):
        cbr = tags.read_tags(self.write('cbr.mp3', mp3_frames(100)))
        self.assertAlmostEqual(cbr['duration'], 100 * 417 * 8 / 128000, places=3)
        exact = tags.read_tags(self.write('xing.mp3', id3_tag(3, [('TIT2', id3_text(0, 'x'))]) + mp3_frames(100, xing=True)))
        self.assertAlmostEqual(exact['duration'], 100 * 1152 / 44100, places=3)

    def test_damaged_or_foreign_files_never_raise(self):
        truncated = id3_tag(3, [('TIT2', id3_text(0, 'Title'))])[:14]
        junk = bytes(range(256)) * 20
        for name, data in (('empty.mp3', b''), ('junk.mp3', junk), ('trunc.mp3', truncated), ('x.flac', b'fLaC\x00'), ('x.ogg', b'OggS' + bytes(10)),
                           ('x.m4a', b'\x00\x00\x00\x08ftyp'), ('x.wav', b'RIFF....WAVE'), ('huge.mp3', b'ID3\x03\x00\x00' + syncsafe(100 * 1024 * 1024))):
            with self.subTest(name=name):
                t = tags.read_tags(self.write(name, data))
                self.assertFalse(t['tagged'])
        self.assertFalse(tags.read_tags(str(Path(self.tmp.name) / 'missing.mp3'))['tagged'])


class OtherFormatTests(TempFiles):
    ENTRIES = ['TITLE=Song', 'ARTIST=A', 'ARTIST=B', 'ALBUM=LP', 'ALBUMARTIST=Band', 'TRACKNUMBER=4/9', 'DISCNUMBER=2', 'TOTALDISCS=3',
               'DATE=2020-05-06', 'GENRE=Jazz', 'MUSICBRAINZ_TRACKID=aaaaaaaa-0000-0000-0000-000000000001', 'MUSICBRAINZ_ALBUMID=bbbbbbbb-0000-0000-0000-000000000002']

    def check(self, t, fmt):
        self.assertEqual((t['title'], t['artist'], t['album'], t['album_artist']), ('Song', 'A / B', 'LP', 'Band'))
        self.assertEqual((t['track'], t['track_total'], t['disc'], t['disc_total'], t['year'], t['genre']), (4, 9, 2, 3, 2020, 'Jazz'))
        self.assertEqual(t['mb_recording'], 'aaaaaaaa-0000-0000-0000-000000000001')
        self.assertEqual(t['format'], fmt)

    def test_flac_with_picture_and_length(self):
        t = tags.read_tags(self.write('a.flac', flac_file(self.ENTRIES, flac_picture(3, 'image/jpeg', JPEG))))
        self.check(t, 'FLAC')
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))
        self.assertAlmostEqual(t['duration'], 2.0, places=3)

    def test_flac_behind_an_id3v2_tag(self):
        t = tags.read_tags(self.write('a.flac', flac_file(self.ENTRIES, id3=id3_tag(3, [('TIT2', id3_text(0, 'ignored'))]))))
        self.check(t, 'FLAC')

    def test_ogg_vorbis_with_embedded_picture_and_length(self):
        picture = base64.b64encode(flac_picture(3, 'image/png', PNG)).decode()
        t = tags.read_tags(self.write('a.ogg', ogg_vorbis(self.ENTRIES + [f'METADATA_BLOCK_PICTURE={picture}'])))
        self.check(t, 'OGG')
        self.assertEqual(t['cover'], ('image/png', PNG))
        self.assertAlmostEqual(t['duration'], 2.0, places=3)

    def test_ogg_comment_packet_spanning_pages(self):
        big = ['TITLE=Song', 'ARTIST=A / B', 'COMMENT=' + 'x' * 70000] + self.ENTRIES[3:]
        comment = b'\x03vorbis' + vorbis_block(big) + b'\x01'
        ident = b'\x01vorbis' + struct.pack('<IBIiii', 0, 2, 44100, 0, 0, 0) + b'\xb8\x01'
        first, rest = comment[:30000], comment[30000:]
        # a packet continued over page boundaries: pages end in a full 255 lace and the next page starts as a continuation
        pages = ogg_page([ident], 0, flags=2)
        pos, seq = 0, 1
        while pos < len(comment):
            chunk = comment[pos:pos + 255 * 100]
            last = pos + len(chunk) >= len(comment)
            if last:
                pages += ogg_page([chunk], seq, flags=1 if pos else 0)
            else:
                segs = [255] * (len(chunk) // 255)
                pages += b'OggS' + bytes([0, 1 if pos else 0]) + struct.pack('<qIII', -1, 1, seq, 0) + bytes([len(segs)]) + bytes(segs) + chunk
            pos += len(chunk)
            seq += 1
        t = tags.read_tags(self.write('long.ogg', pages))
        self.assertEqual((t['title'], t['album']), ('Song', 'LP'))
        self.assertEqual(len(t['comment']), 70000)
        self.assertEqual(first + rest, comment)

    def test_opus(self):
        head = b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0)
        tagpkt = b'OpusTags' + vorbis_block(self.ENTRIES)
        data = ogg_page([head], 0, flags=2) + ogg_page([tagpkt], 1) + ogg_page([bytes(20)], 2, 96312, flags=4)
        t = tags.read_tags(self.write('a.opus', data))
        self.check(t, 'OPUS')
        self.assertAlmostEqual(t['duration'], 2.0, places=3)

    def test_mp4_items_cover_audiobook_flag_and_length(self):
        items = [mp4_box(b'\xa9nam', mp4_data(1, 'Chapter One'.encode())), mp4_box(b'\xa9ART', mp4_data(1, 'Frank Herbert'.encode())),
                 mp4_box(b'\xa9alb', mp4_data(1, 'Dune'.encode())), mp4_box(b'\xa9day', mp4_data(1, b'1965')),
                 mp4_box(b'trkn', mp4_data(0, b'\x00\x00' + struct.pack('>HH', 3, 10) + b'\x00\x00')),
                 mp4_box(b'stik', mp4_data(21, b'\x02')), mp4_box(b'covr', mp4_data(13, JPEG)),
                 mp4_box(b'gnre', mp4_data(0, struct.pack('>H', 18))),
                 mp4_box(b'----', mp4_box(b'mean', bytes(4) + b'com.apple.iTunes') + mp4_box(b'name', bytes(4) + b'MusicBrainz Track Id') +
                         mp4_data(1, b'cccccccc-0000-0000-0000-000000000003'))]
        t = tags.read_tags(self.write('a.m4b', mp4_file(items, seconds=5)))
        self.assertEqual((t['title'], t['artist'], t['album'], t['track'], t['track_total'], t['year']), ('Chapter One', 'Frank Herbert', 'Dune', 3, 10, 1965))
        self.assertTrue(t['audiobook_flag'] and tags.is_audiobook(t, 'a.m4b'))
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))
        self.assertEqual((t['genre'], t['format'], t['duration']), ('Rock', 'M4B', 5.0))
        self.assertEqual(t['mb_recording'], 'cccccccc-0000-0000-0000-000000000003')

    def test_audiobook_genre_marks_audiobook(self):
        t = tags.read_tags(self.write('a.mp3', id3_tag(3, [('TIT2', id3_text(0, 'x')), ('TCON', id3_text(0, 'Audiobook'))]) + mp3_frames()))
        self.assertTrue(tags.is_audiobook(t, 'a.mp3'))


class PathGuessTests(unittest.TestCase):
    def test_guess_from_folders_and_file_name(self):
        t = tags.guess_from_path('/music/The Band/Great Album/03 - Some Song.mp3')
        self.assertEqual((t['track'], t['title'], t['album'], t['artist']), (3, 'Some Song', 'Great Album', 'The Band'))
        t = tags.guess_from_path('/x/Artist Name - Track Title.flac')
        self.assertEqual((t['artist'], t['title']), ('Artist Name', 'Track Title'))
        t = tags.guess_from_path('/x/2-05. Disc Two Song.mp3')
        self.assertEqual((t['disc'], t['track'], t['title']), (2, 5, 'Disc Two Song'))

    def test_fallback_fills_only_what_tags_lack(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / 'Artist' / 'Album'
            folder.mkdir(parents=True)
            path = folder / '07 - Song.mp3'
            path.write_bytes(id3_tag(3, [('TIT2', id3_text(0, 'Real Title'))]) + mp3_frames())
            t = tags.tags_with_fallback(str(path))
            self.assertEqual((t['title'], t['album'], t['artist'], t['track']), ('Real Title', 'Album', 'Artist', 7))
            self.assertEqual(sorted(t['from_path']), ['album', 'artist', 'track'])

    def test_scan_folder_finds_audio_only_in_stable_order(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ('b/02.mp3', 'a/01.flac', 'a/notes.txt', 'a/._01.flac', 'c/x.M4B'):
                p = Path(d) / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b'x')
            found = [Path(p).relative_to(d).as_posix() for p in tags.scan_folder(d)]
            self.assertEqual(found, ['a/01.flac', 'b/02.mp3', 'c/x.M4B'])


@unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is not installed')
class RealEncoderTests(TempFiles):
    """Files written by real encoders, so the readers are not only checked against bytes built from the same understanding."""
    CODECS = {'mp3': '-c:a libmp3lame -b:a 128k', 'flac': '-c:a flac', 'ogg': '-c:a libvorbis', 'opus': '-c:a libopus',
              'm4a': '-c:a aac -b:a 96k -f ipod', 'm4b': '-c:a aac -b:a 96k -f ipod'}

    def encode(self, name, extra=''):
        ext = name.rsplit('.', 1)[1]
        out = str(Path(self.tmp.name) / name)
        cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3'] + self.CODECS[ext].split() + \
            ['-metadata', 'title=Yesterday', '-metadata', 'artist=The Beatles', '-metadata', 'album=Help!', '-metadata', 'album_artist=The Beatles',
             '-metadata', 'track=13/14', '-metadata', 'disc=1/2', '-metadata', 'date=1965-08-06', '-metadata', 'genre=Rock', out]
        subprocess.run(cmd, check=True, capture_output=True)
        return out

    def test_every_format(self):
        for ext in self.CODECS:
            with self.subTest(ext=ext):
                t = tags.read_tags(self.encode(f't.{ext}'))
                self.assertEqual((t['title'], t['artist'], t['album'], t['album_artist']), ('Yesterday', 'The Beatles', 'Help!', 'The Beatles'))
                self.assertEqual((t['track'], t['track_total'], t['disc'], t['disc_total'], t['year'], t['genre']), (13, 14, 1, 2, 1965, 'Rock'))
                self.assertAlmostEqual(t['duration'], 3.0, delta=0.1)
                self.assertEqual(t['format'], ext.upper())

    def test_embedded_covers(self):
        image = str(Path(self.tmp.name) / 'cover.jpg')
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=64x64:d=1,format=yuvj420p', '-frames:v', '1', image],
                       check=True, capture_output=True)
        want = Path(image).read_bytes()
        for ext, extra in (('mp3', ['-id3v2_version', '3']), ('mp3', ['-id3v2_version', '4']), ('flac', []), ('m4a', ['-f', 'ipod'])):
            with self.subTest(ext=ext, extra=extra):
                plain = self.encode(f'plain.{ext}')
                out = str(Path(self.tmp.name) / f'covered.{ext}')
                subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-i', plain, '-i', image, '-map', '0:a', '-map', '1:v', '-c', 'copy',
                                '-disposition:v', 'attached_pic'] + extra + [out], check=True, capture_output=True)
                t = tags.read_tags(out)
                self.assertEqual(t['cover'][1], want)
                self.assertEqual(t['title'], 'Yesterday')


# -- services ------------------------------------------------------------------------------------------------------------
def http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers['Retry-After'] = str(retry_after)
    return urllib.error.HTTPError('https://x/?client=SECRETKEY', code, 'err', headers, io.BytesIO(b''))


class Scripted:
    """A transport that returns or raises the next scripted item, or routes by URL substring; records every call."""

    def __init__(self, *script, routes=None):
        self.script, self.routes, self.calls = list(script), routes or {}, []

    def __call__(self, url, headers, data, timeout):
        self.calls.append((url, headers, data))
        for needle, answer in self.routes.items():
            if needle in url:
                item = answer.pop(0) if isinstance(answer, list) else answer
                break
        else:
            item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, bytes) else json.dumps(item).encode()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make_http(*script, routes=None, **kwargs):
    clock = FakeClock()
    fetch = Scripted(*script, routes=routes)
    return sources.Http(fetch=fetch, sleep=clock.sleep, clock=clock.clock, **kwargs), fetch, clock


class HttpTests(unittest.TestCase):
    def test_requests_to_the_same_host_are_spaced_and_identify_the_app(self):
        http, fetch, clock = make_http({'a': 1}, {'b': 2}, {'c': 3}, contact='me@example.org')
        http.json('https://musicbrainz.org/ws/2/x')
        http.json('https://musicbrainz.org/ws/2/y')
        self.assertGreaterEqual(clock.now, 1.1 - 0.001, 'MusicBrainz allows one request a second')
        before = clock.now
        http.json('https://openlibrary.org/search.json')
        self.assertLess(clock.now - before, 0.3, 'a different host has its own pacing')
        agent = fetch.calls[0][1]['User-Agent']
        self.assertIn('CalibreMediaMatcher/', agent)
        self.assertIn('me@example.org', agent)
        self.assertIn(sources.PROJECT_URL, sources.user_agent(''))

    def test_busy_answers_are_retried_and_retry_after_is_honoured(self):
        http, fetch, clock = make_http(http_error(503), http_error(429, retry_after=7), {'ok': True})
        waits = []
        http.on_wait = lambda seconds, why: waits.append((seconds, why))
        self.assertEqual(http.json('https://musicbrainz.org/ws/2/x'), {'ok': True})
        self.assertEqual(len(fetch.calls), 3)
        self.assertIn('busy (HTTP 503)', waits[0][1])
        self.assertTrue(any(seconds >= 7 for seconds, _ in waits), 'Retry-After: 7 is respected')

    def test_gives_up_after_the_retries_with_the_status(self):
        http, fetch, _ = make_http(*[http_error(503)] * 10, retries=2)
        with self.assertRaises(sources.HttpStatus) as ctx:
            http.json('https://musicbrainz.org/ws/2/x')
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(len(fetch.calls), 3)

    def test_other_errors_are_not_retried_and_never_show_the_key(self):
        http, fetch, _ = make_http(http_error(400))
        with self.assertRaises(sources.HttpStatus) as ctx:
            http.json('https://api.acoustid.org/v2/lookup?client=SECRETKEY')
        self.assertNotIn('SECRETKEY', str(ctx.exception))
        self.assertEqual(len(fetch.calls), 1)

    def test_network_failure_is_retried_then_reported_in_plain_words(self):
        http, fetch, _ = make_http(*[urllib.error.URLError('no route')] * 10, retries=1)
        with self.assertRaises(sources.ServiceError) as ctx:
            http.json('https://openlibrary.org/x?secret=1')
        self.assertIn('Could not reach openlibrary.org', str(ctx.exception))
        self.assertNotIn('secret', str(ctx.exception))
        self.assertEqual(len(fetch.calls), 2)

    def test_cancel_interrupts_a_wait(self):
        state = {'cancel': False}
        http, fetch, _ = make_http(http_error(503), {'ok': 1}, cancelled=lambda: state['cancel'])
        http.on_wait = lambda seconds, why: state.update(cancel=True)
        with self.assertRaises(sources.Cancelled):
            http.json('https://musicbrainz.org/ws/2/x')
        self.assertEqual(len(fetch.calls), 1)

    def test_not_json_is_a_service_error(self):
        http, *_ = make_http(b'<html>oops</html>')
        with self.assertRaises(sources.ServiceError):
            http.json('https://musicbrainz.org/ws/2/x')


class ServiceCallTests(unittest.TestCase):
    def test_musicbrainz_query_is_quoted_and_escaped(self):
        http, fetch, _ = make_http({'recordings': [{'id': 'r'}]})
        out = sources.mb_search_recordings(http, 'He said "Hi" \\ now', 'AC/DC', 'Back In Black')
        self.assertEqual(out, [{'id': 'r'}])
        url = fetch.calls[0][0]
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(url).query)['query'][0]
        self.assertEqual(query, 'recording:"He said \\"Hi\\" \\\\ now" AND artist:"AC/DC" AND release:"Back In Black"')
        self.assertEqual(parse_qs(urlsplit(url).query)['fmt'], ['json'])

    def test_search_by_recording_id_and_empty_query(self):
        http, fetch, _ = make_http({'recordings': []})
        sources.mb_search_recordings(http, recording_id='abc-123')
        self.assertIn('query=rid%3Aabc-123', fetch.calls[0][0])
        self.assertEqual(sources.mb_search_recordings(http), [])
        self.assertEqual(len(fetch.calls), 1, 'nothing to ask, so nothing is sent')

    def test_batched_recording_ids_lucene_or_query(self):
        http, fetch, _ = make_http({'recordings': [{'id': 'id1'}, {'id': 'id2'}]})
        out = sources.mb_search_recordings(http, recording_id=['id1', 'id2', 'id3'])
        self.assertEqual(len(fetch.calls), 1)
        url = fetch.calls[0][0]
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(url).query)['query'][0]
        self.assertEqual(query, 'rid:id1 OR rid:id2 OR rid:id3')
        self.assertEqual(out, [{'id': 'id1'}, {'id': 'id2'}])

    def test_batched_recording_ids_limited_to_four(self):
        http, fetch, _ = make_http({'recordings': []})
        sources.mb_search_recordings(http, recording_ids=['id1', 'id2', 'id3', 'id4', 'id5', 'id6'])
        url = fetch.calls[0][0]
        from urllib.parse import parse_qs, urlsplit
        query = parse_qs(urlsplit(url).query)['query'][0]
        self.assertEqual(query, 'rid:id1 OR rid:id2 OR rid:id3 OR rid:id4')

    def test_unparseable_query_is_no_result_not_a_failure(self):
        http, *_ = make_http(http_error(400))
        self.assertEqual(sources.mb_search_recordings(http, 'x', 'y'), [])

    def test_cover_art_tries_release_then_release_group_and_skips_non_images(self):
        urls = sources.cover_art_url('rel', 'grp')
        self.assertEqual(urls, ['https://coverartarchive.org/release/rel/front-500', 'https://coverartarchive.org/release-group/grp/front-500'])
        http, fetch, _ = make_http(http_error(404), JPEG)
        self.assertEqual(sources.fetch_image(http, urls), JPEG)
        http, *_ = make_http(b'<html>not an image</html>', http_error(404))
        self.assertIsNone(sources.fetch_image(http, urls))
        http, *_ = make_http(http_error(503), retries=0)
        with self.assertRaises(sources.HttpStatus):
            sources.fetch_image(http, urls)

    def test_acoustid_lookup_posts_and_sorts_results(self):
        answer = {'status': 'ok', 'results': [{'id': 'a', 'score': 0.4, 'recordings': [{'id': 'r1'}]},
                                              {'id': 'b', 'score': 0.97, 'recordings': [{'id': 'r2'}, {'id': 'r3'}]}, {'id': 'c', 'score': 0.9}]}
        http, fetch, _ = make_http(answer)
        self.assertEqual(sources.acoustid_lookup(http, 'KEY', 187, 'AQAD'), [(0.97, ['r2', 'r3']), (0.4, ['r1'])])
        url, _headers, data = fetch.calls[0]
        self.assertNotIn('KEY', url, 'the key and fingerprint go in the POST body, not the URL')
        self.assertIn(b'client=KEY', data)
        self.assertIn(b'duration=187', data)

    def test_acoustid_bad_key_message_does_not_contain_the_key(self):
        http, *_ = make_http(http_error(400))
        with self.assertRaises(sources.ServiceError) as ctx:
            sources.acoustid_lookup(http, 'SECRETKEY', 10, 'x')
        self.assertNotIn('SECRETKEY', str(ctx.exception))
        self.assertIn('key', str(ctx.exception))
        http, *_ = make_http({'status': 'error', 'error': {'code': 4, 'message': 'invalid API key'}})
        with self.assertRaises(sources.ServiceError) as ctx:
            sources.acoustid_lookup(http, 'SECRETKEY', 10, 'x')
        self.assertIn('invalid API key', str(ctx.exception))

    def test_fingerprint_runs_fpcalc_json(self):
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            return types.SimpleNamespace(returncode=0, stdout=b'{"duration": 187.4, "fingerprint": "AQADxx"}', stderr=b'')
        self.assertEqual(sources.fingerprint('/m/a.mp3', '/bin/fpcalc', runner), (187, 'AQADxx'))
        self.assertEqual(calls[0], ['/bin/fpcalc', '-json', '/m/a.mp3'])
        bad = lambda cmd, **kw: types.SimpleNamespace(returncode=2, stdout=b'', stderr=b'ERROR: could not open\n')
        with self.assertRaises(sources.FingerprintError) as ctx:
            sources.fingerprint('/m/a.mp3', '/bin/fpcalc', bad)
        self.assertIn('could not open', str(ctx.exception))
        garbled = lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=b'DURATION=1\nFINGERPRINT=x', stderr=b'')
        with self.assertRaises(sources.FingerprintError):
            sources.fingerprint('/m/a.mp3', '/bin/fpcalc', garbled)

        def missing(cmd, **kw):
            raise FileNotFoundError(cmd[0])
        with self.assertRaises(sources.FingerprintError):
            sources.fingerprint('/m/a.mp3', '/nope/fpcalc', missing)

    def test_find_fpcalc_accepts_a_file_or_a_folder(self):
        with tempfile.TemporaryDirectory() as d:
            name = 'fpcalc.exe' if sys.platform == 'win32' else 'fpcalc'
            exe = Path(d) / name
            exe.write_bytes(b'x')
            self.assertEqual(sources.find_fpcalc(str(exe)), str(exe))
            self.assertEqual(sources.find_fpcalc(d), str(exe))
            self.assertIsNone(sources.find_fpcalc(str(Path(d) / 'nothing')))

    def test_open_library_search_retries_without_the_author(self):
        http, fetch, _ = make_http({'docs': []}, {'docs': [{'title': 'Dune'}]})
        self.assertEqual(sources.ol_search(http, 'Dune', 'Frank Herbart'), [{'title': 'Dune'}])
        self.assertIn('author=Frank+Herbart', fetch.calls[0][0])
        self.assertNotIn('author=', fetch.calls[1][0])
        self.assertEqual(sources.ol_cover_urls(None), [])
        self.assertEqual(sources.ol_cover_urls(5), ['https://covers.openlibrary.org/b/id/5-L.jpg?default=false'])


# -- matching ------------------------------------------------------------------------------------------------------------
MB = json.loads((FIXTURES / 'mb_yesterday_help.json').read_text(encoding='utf-8'))['recordings']
OL = json.loads((FIXTURES / 'ol_dune.json').read_text(encoding='utf-8'))['docs']


def want(**kw):
    base = {'title': 'Yesterday', 'artist': 'The Beatles', 'album': 'Help!', 'track': 13, 'year': None, 'duration': None}
    base.update(kw)
    return base


def library_book(**kw):
    base = {'id': 1, 'title': 'Yesterday', 'authors': ['The Beatles'], 'series': 'Help!', 'series_index': 13.0, 'tags': [], 'formats': ['MP3'],
            'identifiers': {}, 'pubdate': None, 'has_cover': False, 'path': ''}
    base.update(kw)
    return base


class SimilarityTests(unittest.TestCase):
    def test_names_that_differ_only_in_style_match(self):
        s = matching.similarity
        self.assertEqual(s('Yesterday', 'Yesterday (Remastered 2009)'), 1.0)
        self.assertEqual(s('Yesterday - 2009 Remaster', 'yesterday'), 1.0)
        self.assertEqual(s('Beatles, The', 'The Beatles'), 1.0)
        self.assertEqual(s('Beyoncé', 'Beyonce'), 1.0)
        self.assertEqual(s('Simon & Garfunkel', 'Simon and Garfunkel'), 1.0)
        self.assertEqual(s('Help!', 'Help'), 1.0)

    def test_different_names_do_not(self):
        self.assertLess(matching.similarity('Yesterday', 'Eleanor Rigby'), 0.3)
        self.assertEqual(matching.similarity('', 'x'), 0.0)

    def test_classify(self):
        c = matching.classify
        self.assertEqual(c({'formats': ['EPUB'], 'tags': []}), 'book')
        self.assertEqual(c({'formats': ['MP3'], 'tags': []}), 'music')
        self.assertEqual(c({'formats': ['MP3'], 'tags': ['Audiobook']}), 'audiobook')
        self.assertEqual(c({'formats': ['M4B'], 'tags': []}), 'audiobook')
        self.assertEqual(c({'formats': ['EPUB'], 'tags': []}, 'music'), 'music')


class MusicMatchTests(unittest.TestCase):
    def test_canonical_recording_wins_with_release_cover_and_track(self):
        best = matching.best_music_candidate(MB, want(duration=125.6))
        self.assertEqual(best['recording_id'], '0aa1938a-ee7f-487b-b742-8b2cfa110c85')
        self.assertEqual((best['title'], best['authors'], best['album'], best['track'], best['disc'], best['track_total']),
                         ('Yesterday', ['The Beatles'], 'Help!', 13, 1, 14))
        self.assertEqual(best['date'], '1965-08-06')
        self.assertGreater(best['score'], 0.95)
        self.assertFalse(best['ambiguous'], 'the other recordings are the same song on the same album: nothing would change')
        self.assertEqual(best['cover_urls'][0], 'https://coverartarchive.org/release/6f1a1c0a-3c7a-4d31-9e62-b32796043b6c/front-500')
        self.assertTrue(best['cover_urls'][1].startswith('https://coverartarchive.org/release-group/'))

    def test_length_prefers_the_matching_recording(self):
        far = matching.best_music_candidate(MB, want(duration=300))
        self.assertLess(far['score'], matching.best_music_candidate(MB, want(duration=125.6))['score'])

    def test_a_different_song_by_the_same_artist_is_never_offered(self):
        self.assertIsNone(matching.best_music_candidate(MB, want(title='Eleanor Rigby')))

    def test_a_fingerprint_can_vouch_for_a_record_with_a_junk_title(self):
        junk = want(title='Track 01', album='')
        self.assertIsNone(matching.best_music_candidate(MB, junk))
        best = matching.best_music_candidate(MB, junk, {'0aa1938a-ee7f-487b-b742-8b2cfa110c85': 0.98})
        self.assertEqual(best['recording_id'], '0aa1938a-ee7f-487b-b742-8b2cfa110c85')
        self.assertFalse(best['ambiguous'])

    def test_untagged_track_acoustid_match_scores_above_tick_threshold(self):
        untagged = want(title='', artist='', album='', duration=125.6)
        self.assertIsNone(matching.best_music_candidate(MB, untagged))
        best = matching.best_music_candidate(MB, untagged, {'0aa1938a-ee7f-487b-b742-8b2cfa110c85': 0.95})
        self.assertIsNotNone(best)
        self.assertEqual(best['recording_id'], '0aa1938a-ee7f-487b-b742-8b2cfa110c85')
        self.assertGreaterEqual(best['score'], matching.TICK_SCORE)

    def test_tagged_musicbrainz_id_is_a_strong_match(self):
        best = matching.best_music_candidate(MB, want(duration=500), exact_ids=('8083d5ff-d097-4636-9c30-77171b97aa43',))
        self.assertEqual((best['recording_id'], best['by_id']), ('8083d5ff-d097-4636-9c30-77171b97aa43', True))
        self.assertGreaterEqual(best['score'], 0.95)

    def test_same_song_on_different_albums_is_ambiguous_without_an_album(self):
        compilation = json.loads(json.dumps(MB[0]))
        compilation['id'] = 'comp-1'
        compilation['releases'][0].update({'id': 'rel-comp', 'title': 'Now 7', 'release-group': {'id': 'rg-comp', 'primary-type': 'Album', 'secondary-types': ['Compilation']}})
        best = matching.best_music_candidate([MB[0], compilation], want(album='', track=None))
        self.assertTrue(best['ambiguous'])
        self.assertEqual(best['rivals'], 1)
        # naming the album settles it
        settled = matching.best_music_candidate([MB[0], compilation], want())
        self.assertFalse(settled['ambiguous'])
        self.assertEqual(settled['album'], 'Help!')

    def test_tied_recordings_go_to_the_better_release_then_the_earliest(self):
        def variant(rec_id, title, year, status='Official', secondary=()):
            rec = json.loads(json.dumps(MB[0]))
            rec['id'] = rec_id
            rec['releases'][0].update({'id': 'rel-' + rec_id, 'title': title, 'date': year, 'status': status})
            rec['releases'][0]['release-group'].update({'id': 'rg-' + rec_id, 'secondary-types': list(secondary)})
            return rec
        late = variant('late', 'Greatest Hits', '2021-05-31', secondary=['Compilation'])
        early = variant('early', 'Greatest Hits', '1975-01-01', secondary=['Compilation'])
        studio = variant('studio', 'Real Album', '1990-01-01')
        no_album = want(album='', track=None, duration=125.6)
        self.assertEqual(matching.best_music_candidate([late, early], no_album)['recording_id'], 'early')
        self.assertEqual(matching.best_music_candidate([late, early, studio], no_album)['recording_id'], 'studio')
        self.assertTrue(matching.best_music_candidate([late, early, studio], no_album)['ambiguous'], 'still flagged: it is a guess among several')

    def test_no_album_known_prefers_the_studio_album_then_the_earliest(self):
        later = json.loads(json.dumps(MB[0]))
        later['releases'][0].update({'id': 'later', 'title': 'Help! (Deluxe)', 'date': '2020-01-01'})
        chosen = matching.score_recording({**MB[0], 'releases': [later['releases'][0], MB[0]['releases'][0]]}, want(album='', track=None))
        self.assertEqual(chosen['release_id'], MB[0]['releases'][0]['id'])


class ChangePlanTests(unittest.TestCase):
    def music(self, **kw):
        c = matching.best_music_candidate(MB, want(duration=125.6))
        c.update(kw)
        return c

    def test_music_changes_only_what_differs(self):
        book = library_book(title='yesterday', authors=['Beatles'], series='', series_index=1.0)
        changes = matching.plan_changes(book, 'music', self.music(), set(matching.DEFAULT_FIELDS))
        self.assertEqual(changes['title'], ('yesterday', 'Yesterday'))
        self.assertEqual(changes['authors'], (['Beatles'], ['The Beatles']))
        self.assertEqual(changes['album'], (['', 1.0], ['Help!', 13.0]))
        self.assertEqual(changes['date'], (None, '1965-08-06T00:00:00+00:00'))
        self.assertEqual(changes['tags'], ([], ['Music']))
        self.assertEqual(changes['identifiers'][1], {'mb_recording': '0aa1938a-ee7f-487b-b742-8b2cfa110c85', 'mb_release': '6f1a1c0a-3c7a-4d31-9e62-b32796043b6c'})
        self.assertEqual(changes['cover'][0], None)

    def test_nothing_to_change_gives_nothing(self):
        book = library_book(tags=['Music'], pubdate='1965-08-06T00:00:00+00:00', has_cover=True,
                            identifiers={'mb_recording': '0aa1938a-ee7f-487b-b742-8b2cfa110c85', 'mb_release': '6f1a1c0a-3c7a-4d31-9e62-b32796043b6c'})
        self.assertEqual(matching.plan_changes(book, 'music', self.music(), set(matching.DEFAULT_FIELDS)), {})

    def test_year_only_date_does_not_replace_a_full_date_and_cover_is_never_replaced(self):
        book = library_book(pubdate='1965-08-06T00:00:00+00:00', has_cover=True)
        changes = matching.plan_changes(book, 'music', self.music(date='1965'), {'date', 'cover'})
        self.assertEqual(changes, {})
        changes = matching.plan_changes(library_book(pubdate='1999-01-01T00:00:00+00:00'), 'music', self.music(date='1965'), {'date'})
        self.assertEqual(changes['date'][1], '1965-01-01T00:00:00+00:00')

    def test_authors_differing_only_in_punctuation_are_left_alone(self):
        cand = matching.best_book_candidate(OL, {'title': 'Dune', 'artist': 'Frank Herbert'})
        book = library_book(authors=['Herbert, Frank'], formats=['EPUB'])
        self.assertIn('authors', matching.plan_changes(book, 'book', cand, {'authors'}))
        self.assertNotIn('authors', matching.plan_changes(library_book(authors=['frank  herbert.'], formats=['EPUB']), 'book', cand, {'authors'}))

    def test_only_selected_fields_are_touched(self):
        book = library_book(title='yesterday', series='', series_index=1.0)
        self.assertEqual(set(matching.plan_changes(book, 'music', self.music(), {'title'})), {'title'})

    def test_books_get_edition_id_but_no_guessed_isbn_or_publisher(self):
        cand = matching.best_book_candidate(OL, {'title': 'Dune', 'artist': 'Frank Herbert'})
        changes = matching.plan_changes(library_book(formats=['EPUB'], series='', title='dune'), 'book', cand, set(matching.FIELDS))
        self.assertEqual(changes['title'], ('dune', 'Dune'))
        self.assertEqual(set(changes['identifiers'][1]) - {'olid'}, set(), 'no isbn: a search cannot tell which edition you own')
        self.assertNotIn('publisher', changes)
        self.assertIn('tags', changes, 'subjects were ticked, so they are offered as tags')
        self.assertFalse(set(changes['tags'][1]) - set(cand['subjects']) - {'Music'})
        no_subjects = matching.plan_changes(library_book(formats=['EPUB']), 'book', cand, set(matching.DEFAULT_FIELDS))
        self.assertNotIn('tags', no_subjects, 'subjects are opt-in; the Music/Audiobook tag does not apply to books')

    def test_chapter_files_keep_their_titles_and_whole_audiobooks_do_not(self):
        cand = matching.best_book_candidate(OL, {'title': 'Dune', 'artist': 'Frank Herbert'})
        chapter = library_book(title='Chapter 3', series='Dune', formats=['MP3'], tags=['Audiobook'])
        self.assertNotIn('title', matching.plan_changes(chapter, 'audiobook', cand, {'title'}))
        whole = library_book(title='dune', series='', formats=['M4B'], tags=['Audiobook'])
        self.assertEqual(matching.plan_changes(whole, 'audiobook', cand, {'title'})['title'], ('dune', 'Dune'))
        self.assertEqual(matching.plan_changes(whole, 'audiobook', cand, {'tags'}), {}, 'already tagged')


class BookMatchTests(unittest.TestCase):
    def test_best_open_library_work(self):
        best = matching.best_book_candidate(OL, {'title': 'Dune', 'artist': 'Frank Herbert'})
        self.assertEqual((best['title'], best['authors'], best['date']), ('Dune', ['Frank Herbert'], '1965'))
        self.assertGreater(best['score'], 0.95)
        self.assertEqual(best['cover_urls'], ['https://covers.openlibrary.org/b/id/11481354-L.jpg?default=false'])
        messiah = matching.best_book_candidate(OL, {'title': 'Dune Messiah', 'artist': 'Frank Herbert'})
        self.assertEqual(messiah['title'], 'Dune Messiah')

    def test_unrelated_title_is_not_offered(self):
        self.assertIsNone(matching.best_book_candidate(OL, {'title': 'Cooking for One', 'artist': 'Frank Herbert'}))

    def test_want_for_audiobooks_uses_the_book_title_not_the_chapter(self):
        chapter = library_book(title='Chapter 12 - The Spice', series='Dune: Audiobook', formats=['MP3'], tags=['Audiobook'], authors=['Frank Herbert'])
        w = matching.want_for(chapter, 'audiobook', {})
        self.assertEqual((w['title'], w['artist']), ('Dune', 'Frank Herbert'))
        self.assertEqual(matching.want_for(library_book(title='Part 2 - Dune (Unabridged)', series='', formats=['M4B']), 'audiobook', {})['title'], 'Dune')
        self.assertEqual(matching.want_for(library_book(authors=['Unknown']), 'music', {})['artist'], '')


class PlanMatchesTests(unittest.TestCase):
    def run_plan(self, books, routes, options=None, **kw):
        http, fetch, _ = make_http(routes=routes, **kw)
        events = []
        result = matching.plan_matches(books, http, options or {}, on_event=lambda event, **info: events.append((event, info)))
        return result, fetch, events

    def test_music_audiobook_book_and_unknown_in_one_run(self):
        books = [library_book(id=1, title='yesterday', authors=['Beatles'], series='Help!'),
                 library_book(id=2, title='Dune', authors=['Frank Herbert'], series='', formats=['M4B'], tags=['Audiobook']),
                 library_book(id=3, title='Dune', authors=['Frank Herbert'], series='', formats=['EPUB']),
                 library_book(id=4, title='Qzxv Plmk', authors=['Nobody'], series='', formats=['MP3'])]
        routes = {'musicbrainz.org/ws/2/recording': [{'recordings': MB}, {'recordings': []}], 'openlibrary.org/search.json': [{'docs': OL}, {'docs': OL}]}
        (found, skipped), fetch, events = self.run_plan(books, routes)
        self.assertEqual([s['id'] for s in found], [1, 2, 3])
        self.assertEqual([s['kind'] for s in found], ['music', 'audiobook', 'book'])
        self.assertEqual(skipped, [])
        self.assertIn('nomatch', [e for e, _ in events])
        self.assertIn('title', found[0]['changes'])
        self.assertFalse(found[0]['big'])
        urls = [c[0] for c in fetch.calls]
        self.assertTrue(urls[0].startswith('https://musicbrainz.org/ws/2/recording/'))
        self.assertTrue(urls[1].startswith('https://openlibrary.org/search.json'))

    def test_album_search_is_widened_when_nothing_good_comes_back(self):
        routes = {'musicbrainz.org': [{'recordings': []}, {'recordings': MB}]}
        (found, _), fetch, _ = self.run_plan([library_book(series='Greatest Hits')], routes)
        self.assertEqual(len(fetch.calls), 2)
        self.assertNotIn('release', fetch.calls[1][0].replace('release-group', ''))
        self.assertEqual(len(found), 1)

    def test_weak_matches_are_dropped(self):
        routes = {'musicbrainz.org': [{'recordings': MB}] * 2}
        (found, _), *_ = self.run_plan([library_book(title='Totally Other Song')], routes)
        self.assertEqual(found, [])

    def test_a_refused_lookup_skips_that_book_but_a_dead_network_stops_the_run(self):
        books = [library_book(id=1, title='Xx'), library_book(id=2)]
        routes = {'musicbrainz.org': [http_error(404)] + [{'recordings': MB}] * 3}
        (found, skipped), *_ = self.run_plan(books, routes)
        self.assertEqual([s['id'] for s in found], [2], 'the refused book is skipped, the next one still runs')
        self.assertTrue(skipped[0].startswith('Xx'))
        books = [library_book(id=1), library_book(id=2), library_book(id=3)]
        routes = {'musicbrainz.org': [{'recordings': MB}, urllib.error.URLError('down'), urllib.error.URLError('down')]}
        (found, skipped), *_ = self.run_plan(books, routes, retries=0)
        self.assertEqual([s['id'] for s in found], [1], 'what was found before the failure is kept')
        self.assertEqual(len(skipped), 1)
        self.assertIn('Stopped at', skipped[0])

    def test_cancel_stops_before_the_next_book_and_keeps_results(self):
        state = {'stop': False}
        http, fetch, _ = make_http(routes={'musicbrainz.org': [{'recordings': MB}] * 3})

        def on_event(event, **info):
            if event == 'match':
                state['stop'] = True
        found, _ = matching.plan_matches([library_book(id=i) for i in (1, 2, 3)], http, {}, cancelled=lambda: state['stop'], on_event=on_event)
        self.assertEqual([s['id'] for s in found], [1])
        self.assertEqual(len(fetch.calls), 1)

    def test_tagged_musicbrainz_id_in_the_file_is_looked_up_directly(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'a.mp3'
            path.write_bytes(id3_tag(3, [('TIT2', id3_text(0, 'Yesterday')), ('UFID', b'http://musicbrainz.org\x00' + b'8083d5ff-d097-4636-9c30-77171b97aa43')]) + mp3_frames())
            routes = {'musicbrainz.org': [{'recordings': [MB[1]]}, {'recordings': MB}]}
            (found, _), fetch, _ = self.run_plan([library_book(path=str(path))], routes)
            self.assertIn('rid%3A8083d5ff', fetch.calls[0][0])
            self.assertEqual(found[0]['candidate']['recording_id'], '8083d5ff-d097-4636-9c30-77171b97aa43')

    def test_fingerprint_flow_uses_acoustid_then_musicbrainz_and_survives_a_bad_file(self):
        calls = {}
        real = sources.fingerprint

        def fake_fingerprint(path, fpcalc):
            calls['fp'] = (path, fpcalc)
            return 200, 'AQAD'
        sources.fingerprint = fake_fingerprint
        try:
            book = library_book(title='Track 01', series='', path='/m/a.mp3')
            routes = {'acoustid': [{'status': 'ok', 'results': [{'score': 0.97, 'recordings': [{'id': MB[0]['id']}]}]}],
                      'musicbrainz.org': [{'recordings': [MB[0]]}, {'recordings': []}]}
            opts = {'fingerprint': True, 'acoustid_key': 'K', 'fpcalc': '/bin/fpcalc'}
            (found, _), fetch, _ = self.run_plan([book], routes, opts)
            self.assertEqual(calls['fp'], ('/m/a.mp3', '/bin/fpcalc'))
            self.assertEqual(found[0]['candidate']['recording_id'], MB[0]['id'])
            self.assertGreater(found[0]['score'], 0.8)
            self.assertFalse(found[0]['ambiguous'])

            def broken(path, fpcalc):
                raise sources.FingerprintError('fpcalc could not read this file')
            sources.fingerprint = broken
            (found, skipped), _, events = self.run_plan([library_book(path='/m/a.mp3')], {'musicbrainz.org': [{'recordings': MB}]}, opts)
            self.assertEqual(len(found), 1, 'the text search still runs')
            self.assertEqual(skipped, [])
            self.assertIn('notice', [e for e, _ in events])
        finally:
            sources.fingerprint = real

    def test_candidate_recording_resolution_batched_in_single_http_request(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'a.mp3'
            path.write_bytes(id3_tag(3, [('TIT2', id3_text(0, 'Track 01')), ('UFID', b'http://musicbrainz.org\x00' + b'rec-exact-1')]) + mp3_frames())
            real = sources.fingerprint
            sources.fingerprint = lambda p, f: (200, 'AQAD')
            try:
                book = library_book(title='Track 01', series='', path=str(path))
                acoustid_res = {'status': 'ok', 'results': [{'score': 0.95, 'recordings': [{'id': MB[0]['id']}, {'id': 'rec-fp-2'}, {'id': 'rec-fp-3'}]}]}
                mb_cand_res = {'recordings': [MB[0]]}
                mb_text_res = {'recordings': []}
                routes = {'acoustid': [acoustid_res], 'musicbrainz.org': [mb_cand_res, mb_text_res]}
                opts = {'fingerprint': True, 'acoustid_key': 'K', 'fpcalc': '/bin/fpcalc'}
                http, fetch, clock = make_http(routes=routes)
                found, skipped = matching.plan_matches([book], http, opts)
                mb_calls = [c for c in fetch.calls if 'musicbrainz.org' in c[0]]
                cand_call_url = mb_calls[0][0]
                from urllib.parse import parse_qs, urlsplit
                query = parse_qs(urlsplit(cand_call_url).query)['query'][0]
                expected_query = f"rid:rec-exact-1 OR rid:{MB[0]['id']} OR rid:rec-fp-2 OR rid:rec-fp-3"
                self.assertEqual(query, expected_query)
                self.assertEqual(len(found), 1)
            finally:
                sources.fingerprint = real

    def test_big_title_changes_are_flagged(self):
        books = [library_book(title='Yesterday', authors=['Frank Herbert'], series='', formats=['EPUB'])]
        docs = [dict(OL[0], title='Yesterday and Today')]
        self.assertTrue(matching.MIN_TITLE_SIM < matching.similarity('Yesterday', 'Yesterday and Today') < matching.MIN_SIMILAR_TITLE)
        (found, _), *_ = self.run_plan(books, {'openlibrary.org': [{'docs': docs}]})
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]['big'], 'a quite different title is flagged, which starts it unticked')
        self.assertFalse(self.run_plan([library_book(title='dune', authors=['Frank Herbert'], series='', formats=['EPUB'])],
                                       {'openlibrary.org': [{'docs': OL}]})[0][0][0]['big'], 'a tidy-up of the same title is not')

    def test_untagged_audio_lookup_decoupled_from_title_presence(self):
        calls = {}
        real = sources.fingerprint

        def fake_fingerprint(path, fpcalc):
            calls['fp'] = (path, fpcalc)
            return 125, 'AQAD'
        sources.fingerprint = fake_fingerprint
        try:
            book = library_book(title='', authors=[], series='', series_index=None, path='/m/untagged.mp3')
            routes = {'acoustid': [{'status': 'ok', 'results': [{'score': 0.97, 'recordings': [{'id': MB[0]['id']}]}]}],
                      'musicbrainz.org': [{'recordings': [MB[0]]}]}
            opts = {'fingerprint': True, 'acoustid_key': 'K', 'fpcalc': '/bin/fpcalc'}
            (found, _), fetch, _ = self.run_plan([book], routes, opts)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0]['candidate']['recording_id'], MB[0]['id'])
            self.assertGreaterEqual(found[0]['score'], matching.TICK_SCORE)
            self.assertFalse(found[0]['big'])
            changes = found[0]['changes']
            self.assertIn('title', changes)
            self.assertEqual(changes['title'], ('', 'Yesterday'))
            self.assertIn('authors', changes)
            self.assertEqual(changes['authors'], ([], ['The Beatles']))
            self.assertIn('album', changes)
            self.assertEqual(changes['album'], (['', None], ['Help!', 13.0]))
            self.assertIn('date', changes)
            self.assertIn('identifiers', changes)
        finally:
            sources.fingerprint = real

    def test_untagged_audio_unconfigured_fingerprint_notice(self):
        book = library_book(title='', authors=[], series='', path='/m/untagged.mp3')
        opts = {'fingerprint': False}
        (found, skipped), _, events = self.run_plan([book], {}, opts)
        self.assertEqual(found, [])
        notices = [info['text'] for event, info in events if event == 'notice']
        self.assertTrue(any('unconfigured' in n for n in notices))


class AudiobookEndToEndTests(unittest.TestCase):
    def test_chapter_file_from_a_folder_is_matched_as_its_book(self):
        books = [library_book(id=7, title='Chapter 3', authors=['Frank Herbert'], series='Dune', series_index=3.0, formats=['MP3'], tags=['Audiobook'])]
        http, fetch, _ = make_http(routes={'openlibrary.org': [{'docs': OL}]})
        found, _ = matching.plan_matches(books, http, {})
        self.assertEqual(found[0]['kind'], 'audiobook')
        self.assertNotIn('title', found[0]['changes'])
        self.assertIn('cover', found[0]['changes'])
        self.assertIn('title=Dune', fetch.calls[0][0])


if __name__ == '__main__':
    unittest.main()
