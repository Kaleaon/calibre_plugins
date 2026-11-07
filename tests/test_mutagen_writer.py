"""Unit tests for media/mutagen_writer.py and tag writing/undo in media/action.py."""

import importlib
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'third_party'))

# Ensure media package can be imported outside full Calibre environment
if 'media_pure' not in sys.modules:
    pkg = types.ModuleType('media_pure')
    pkg.__path__ = [str(ROOT / 'media')]
    sys.modules['media_pure'] = pkg

from common.error_handler import format_exception  # noqa: E402

tags = importlib.import_module('media_pure.tags')
mutagen_writer = importlib.import_module('media_pure.mutagen_writer')

JPEG = b'\xff\xd8\xff\xe0' + b'\x00' * 20
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 20


def mp3_frames(count=50):
    """Generates valid MPEG1 layer 3 frames."""
    return bytearray(b'\xff\xfb\x90\x00' + bytes(413)) * count


def vorbis_block(entries, vendor='test'):
    out = struct.pack('<I', len(vendor)) + vendor.encode() + struct.pack('<I', len(entries))
    for e in entries:
        raw = e.encode()
        out += struct.pack('<I', len(raw)) + raw
    return out


def flac_file(entries, picture=None, samples=88200, rate=44100):
    info = struct.pack('>HH', 4096, 4096) + bytes(6)
    packed = (rate << 44) | (1 << 41) | (15 << 36) | samples
    info += struct.pack('>Q', packed) + bytes(16)
    blocks = [(0, info), (4, vorbis_block(entries))] + ([(6, picture)] if picture else [])
    out = b'fLaC'
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


def ogg_opus(entries):
    head = b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0)
    tagpkt = b'OpusTags' + vorbis_block(entries)
    return ogg_page([head], 0, flags=2) + ogg_page([tagpkt], 1) + ogg_page([bytes(20)], 2, 96312, flags=4)


def mp4_box(kind, payload):
    return struct.pack('>I4s', 8 + len(payload), kind) + payload


def mp4_file(items, seconds=5):
    mvhd = mp4_box(b'mvhd', bytes(4) + bytes(8) + struct.pack('>II', 1000, seconds * 1000) + bytes(80))
    ilst = mp4_box(b'ilst', b''.join(items))
    udta = mp4_box(b'udta', mp4_box(b'meta', bytes(4) + ilst))
    return mp4_box(b'ftyp', b'M4B \x00\x00\x00\x00M4B mp42isom') + mp4_box(b'mdat', bytes(100)) + mp4_box(b'moov', mvhd + udta)


class MutagenWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write_file(self, name, data):
        path = Path(self.tmp.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def test_mutagen_is_available(self):
        self.assertTrue(mutagen_writer.is_mutagen_available())

    def test_mp3_tag_writing_and_verification(self):
        path = self.write_file('track.mp3', mp3_frames())
        metadata = {
            'title': 'Yesterday',
            'authors': ['The Beatles'],
            'album': ['Help!', 13],
            'track_total': 14,
            'date': '1965-08-06',
            'tags': ['Rock', 'Classic Pop'],
            'composer': 'Lennon-McCartney',
            'comment': 'A masterclass track',
            'identifiers': {
                'mb_recording': '0aa1938a-ee7f-487b-b742-8b2cfa110c85',
                'mb_release': '6f1a1c0a-3c7a-4d31-9e62-b32796043b6c',
            },
        }
        res = mutagen_writer.write_metadata_to_file(path, metadata, cover_bytes=JPEG)
        self.assertTrue(res)

        # Read back tags using media/tags.py
        t = tags.read_tags(path)
        self.assertEqual(t['title'], 'Yesterday')
        self.assertEqual(t['artist'], 'The Beatles')
        self.assertEqual(t['album'], 'Help!')
        self.assertEqual(t['track'], 13)
        self.assertEqual(t['track_total'], 14)
        self.assertEqual(t['year'], 1965)
        self.assertIn('Rock', t['genre'])
        self.assertEqual(t['composer'], 'Lennon-McCartney')
        self.assertEqual(t['comment'], 'A masterclass track')
        self.assertEqual(t['mb_recording'], '0aa1938a-ee7f-487b-b742-8b2cfa110c85')
        self.assertEqual(t['mb_release'], '6f1a1c0a-3c7a-4d31-9e62-b32796043b6c')
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))

    def test_flac_tag_writing_and_verification(self):
        path = self.write_file('song.flac', flac_file([]))
        metadata = {
            'title': 'Moonlight Sonata',
            'authors': ['Ludwig van Beethoven'],
            'album': 'Piano Sonatas',
            'track': 1,
            'track_total': 3,
            'date': '1801-01-01',
            'genre': 'Classical',
            'identifiers': {
                'mb_recording': 'rec-flac-001',
                'mb_release': 'rel-flac-002',
            },
        }
        res = mutagen_writer.write_metadata_to_file(path, metadata, cover_bytes=PNG)
        self.assertTrue(res)

        t = tags.read_tags(path)
        self.assertEqual(t['title'], 'Moonlight Sonata')
        self.assertEqual(t['artist'], 'Ludwig van Beethoven')
        self.assertEqual(t['album'], 'Piano Sonatas')
        self.assertEqual(t['track'], 1)
        self.assertEqual(t['track_total'], 3)
        self.assertEqual(t['year'], 1801)
        self.assertEqual(t['genre'], 'Classical')
        self.assertEqual(t['mb_recording'], 'rec-flac-001')
        self.assertEqual(t['mb_release'], 'rel-flac-002')
        self.assertEqual(t['cover'], ('image/png', PNG))

    def test_ogg_vorbis_tag_writing_and_verification(self):
        path = self.write_file('audio.ogg', ogg_vorbis([]))
        metadata = {
            'title': 'Ogg Vorbis Track',
            'authors': ['Open Source Band'],
            'album': 'Ogg Anthology',
            'track': 4,
            'date': '2025-05-10',
            'genre': 'Electronic',
        }
        res = mutagen_writer.write_metadata_to_file(path, metadata, cover_bytes=JPEG)
        self.assertTrue(res)

        t = tags.read_tags(path)
        self.assertEqual(t['title'], 'Ogg Vorbis Track')
        self.assertEqual(t['artist'], 'Open Source Band')
        self.assertEqual(t['album'], 'Ogg Anthology')
        self.assertEqual(t['track'], 4)
        self.assertEqual(t['year'], 2025)
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))

    def test_ogg_opus_tag_writing_and_verification(self):
        path = self.write_file('speech.opus', ogg_opus([]))
        metadata = {
            'title': 'Opus Speech',
            'authors': ['Voice Actor'],
            'album': 'Audiobook Chapter 1',
            'date': '2024',
        }
        res = mutagen_writer.write_metadata_to_file(path, metadata, cover_bytes=JPEG)
        self.assertTrue(res)

        t = tags.read_tags(path)
        self.assertEqual(t['title'], 'Opus Speech')
        self.assertEqual(t['artist'], 'Voice Actor')
        self.assertEqual(t['album'], 'Audiobook Chapter 1')
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))

    def test_mp4_m4b_tag_writing_and_verification(self):
        path = self.write_file('audiobook.m4b', mp4_file([], seconds=10))
        metadata = {
            'title': 'Dune Chapter 1',
            'authors': ['Frank Herbert'],
            'album': 'Dune',
            'track': 1,
            'track_total': 20,
            'date': '1965',
            'genre': 'Audiobook',
            'audiobook_flag': True,
            'identifiers': {
                'mb_recording': 'mb-dune-rec-001',
                'mb_release': 'mb-dune-rel-002',
            },
        }
        res = mutagen_writer.write_metadata_to_file(path, metadata, cover_bytes=JPEG)
        self.assertTrue(res)

        t = tags.read_tags(path)
        self.assertEqual(t['title'], 'Dune Chapter 1')
        self.assertEqual(t['artist'], 'Frank Herbert')
        self.assertEqual(t['album'], 'Dune')
        self.assertEqual(t['track'], 1)
        self.assertEqual(t['track_total'], 20)
        self.assertEqual(t['year'], 1965)
        self.assertEqual(t['mb_recording'], 'mb-dune-rec-001')
        self.assertEqual(t['mb_release'], 'mb-dune-rel-002')
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))
        self.assertTrue(t['audiobook_flag'])

    def test_cover_removal(self):
        path = self.write_file('cover_test.mp3', mp3_frames())
        mutagen_writer.write_metadata_to_file(path, {'title': 'Song'}, cover_bytes=JPEG)
        self.assertIsNotNone(tags.read_tags(path)['cover'])

        # Now remove cover
        mutagen_writer.write_metadata_to_file(path, {}, remove_cover=True)
        self.assertIsNone(tags.read_tags(path)['cover'])

    def test_apply_changes_to_file(self):
        path = self.write_file('apply_changes.mp3', mp3_frames())
        changes = {
            'title': ('Old Title', 'New Title'),
            'authors': (['Old Artist'], ['New Artist 1', 'New Artist 2']),
            'album': (['Old Album', 1.0], ['New Album', 5.0]),
            'date': ('2020-01-01', '2024-06-15'),
            'tags': (['Old Tag'], ['Pop', 'Rock']),
            'identifiers': ({}, {'mb_recording': 'rec-999', 'mb_release': 'rel-888'}),
            'cover': (None, ['http://example.com/cover.jpg']),
        }
        res = mutagen_writer.apply_changes_to_file(path, changes, cover_bytes=JPEG)
        self.assertTrue(res)

        t = tags.read_tags(path)
        self.assertEqual(t['title'], 'New Title')
        self.assertIn('New Artist 1', t['artist'])
        self.assertEqual(t['album'], 'New Album')
        self.assertEqual(t['track'], 5)
        self.assertEqual(t['year'], 2024)
        self.assertEqual(t['mb_recording'], 'rec-999')
        self.assertEqual(t['mb_release'], 'rel-888')
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))

    def test_apply_saved_to_file_undo(self):
        path = self.write_file('undo_test.mp3', mp3_frames())
        # First write initial metadata
        initial_changes = {
            'title': ('', 'Updated Title'),
            'authors': ([], ['Updated Artist']),
            'album': (['', None], ['Updated Album', 3.0]),
            'cover': (None, ['http://example.com/cover.jpg']),
        }
        mutagen_writer.apply_changes_to_file(path, initial_changes, cover_bytes=JPEG)
        self.assertEqual(tags.read_tags(path)['title'], 'Updated Title')
        self.assertIsNotNone(tags.read_tags(path)['cover'])

        # Simulate undo with saved dictionary
        saved = {
            'title': 'Original Title',
            'authors': ['Original Artist'],
            'album': ['Original Album', 1],
            'cover': None,  # Original had no cover
        }
        res = mutagen_writer.apply_saved_to_file(path, saved)
        self.assertTrue(res)

        t = tags.read_tags(path)
        self.assertEqual(t['title'], 'Original Title')
        self.assertEqual(t['artist'], 'Original Artist')
        self.assertEqual(t['album'], 'Original Album')
        self.assertEqual(t['track'], 1)
        self.assertIsNone(t['cover'], 'Undo successfully removed cover because original had none')

    def test_malformed_file_raises_and_formats_friendly_error(self):
        # Create a file with truncated/corrupt header
        corrupt_path = self.write_file('corrupt.mp3', b'ID3\x03\x00\x00\x00\x00\x00\x10\xff\xff\xff\xff' + b'garbage')
        with self.assertRaises(Exception) as ctx:
            mutagen_writer.write_metadata_to_file(corrupt_path, {'title': 'New'})

        friendly = format_exception(ctx.exception)
        self.assertEqual(friendly.category, "Audio Tagging Error")
        self.assertIn("audio file tags could not be read or written", friendly.explanation)
        self.assertTrue(any("intact and plays in a media player" in step for step in friendly.recovery))

    def test_missing_file_raises_not_found(self):
        with self.assertRaises(FileNotFoundError):
            mutagen_writer.write_metadata_to_file('/nonexistent/file.mp3', {'title': 'Title'})

    def test_fallback_when_mutagen_unavailable(self):
        with patch.object(mutagen_writer, 'is_mutagen_available', return_value=False):
            self.assertFalse(mutagen_writer.is_mutagen_available())
            with self.assertRaises(RuntimeError) as ctx:
                mutagen_writer.write_metadata_to_file('dummy.mp3', {})
            self.assertIn("Mutagen is not available", str(ctx.exception))


class ActionAudioTaggingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.saved_modules = {k: v for k, v in sys.modules.items() if k.startswith(('calibre', 'qt', 'calibre_plugins'))}

        # Setup Qt stubs if not present
        if 'qt' not in sys.modules or 'qt.core' not in sys.modules:
            try:
                import PyQt6.QtCore as _qtcore
                import PyQt6.QtWidgets as _qtwidgets
                try:
                    import PyQt6.QtGui as _qtgui
                except ImportError:
                    _qtgui = None
                qt = types.ModuleType('qt')
                qtcore = types.ModuleType('qt.core')
                for mod in filter(None, (_qtcore, _qtwidgets, _qtgui)):
                    for k, v in mod.__dict__.items():
                        setattr(qtcore, k, v)
                sys.modules['qt'] = qt
                sys.modules['qt.core'] = qtcore
            except ImportError:
                sys.modules['qt'] = MagicMock()
                sys.modules['qt.core'] = MagicMock()

        # Setup Calibre stubs
        for name in ('calibre', 'calibre.customize', 'calibre.gui2', 'calibre.utils'):
            if name not in sys.modules:
                sys.modules[name] = MagicMock()
        actions = types.ModuleType('calibre.gui2.actions')
        actions.InterfaceAction = type('InterfaceAction', (object,), {'__init__': lambda self, gui, name: setattr(self, 'gui', gui)})
        sys.modules['calibre.gui2.actions'] = actions

        for name in ('calibre.ebooks', 'calibre.ebooks.metadata', 'calibre.ebooks.metadata.book'):
            if name not in sys.modules:
                sys.modules[name] = MagicMock()
        base = types.ModuleType('calibre.ebooks.metadata.book.base')
        base.Metadata = object
        sys.modules['calibre.ebooks.metadata.book.base'] = base

        config = types.ModuleType('calibre.utils.config')
        config.JSONConfig = lambda name: type('Prefs', (dict,), {'defaults': {}})()
        sys.modules['calibre.utils.config'] = config

        # Setup calibre_plugins.media_matcher package mapping
        if 'calibre_plugins' not in sys.modules:
            sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
            sys.modules['calibre_plugins'].__path__ = []
        pkg_mm = types.ModuleType('calibre_plugins.media_matcher')
        pkg_mm.__path__ = [str(ROOT / 'media'), str(ROOT / 'common')]
        sys.modules['calibre_plugins.media_matcher'] = pkg_mm

    def tearDown(self):
        for k in list(sys.modules):
            if k.startswith(('calibre', 'qt', 'calibre_plugins')) and k not in self.saved_modules:
                del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def write_file(self, name, data):
        path = Path(self.tmp.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def test_action_write_changes_and_restore_persists_to_disk(self):
        # Set up a mock Calibre DB and MediaMatcherAction
        audio_file = self.write_file('track.mp3', mp3_frames())

        db_records = {
            1: {
                'title': 'Original Calibre Title',
                'authors': ('Original Calibre Artist',),
                'series': 'Original Series',
                'series_index': 1.0,
                'formats': ['MP3'],
                'path': audio_file,
                'cover': None,
            }
        }

        mock_db = MagicMock()
        mock_db.has_id.side_effect = lambda bid: bid in db_records
        mock_db.formats.side_effect = lambda bid: db_records[bid]['formats']
        mock_db.format_abspath.side_effect = lambda bid, fmt: db_records[bid]['path']
        mock_db.cover.side_effect = lambda bid: db_records[bid]['cover']

        def set_field(f, mapping):
            for bid, val in mapping.items():
                db_records[bid][f] = val

        def set_cover(mapping):
            for bid, val in mapping.items():
                db_records[bid]['cover'] = val

        mock_db.set_field.side_effect = set_field
        mock_db.set_cover.side_effect = set_cover

        from calibre_plugins.media_matcher import action as media_action

        # Create dummy action instance with our mock database
        act = media_action.MediaMatcherAction(None, 'Media Matcher')
        act.gui = MagicMock()
        act.gui.current_db.new_api = mock_db

        changes = {
            'title': ('Original Calibre Title', 'New Mutagen Title'),
            'authors': (['Original Calibre Artist'], ['New Mutagen Artist']),
            'album': (['Original Series', 1.0], ['New Album Series', 7.0]),
            'date': ('2020-01-01', '2026-05-12'),
            'tags': (['tag1'], ['Rock', 'Audiobook']),
            'identifiers': ({}, {'mb_recording': 'rec-uuid-1234', 'mb_release': 'rel-uuid-5678'}),
            'cover': (None, ['http://example.com/cover.jpg']),
        }
        items = [(1, changes)]
        covers = {1: JPEG}

        # 1. Execute write_changes()
        done, errors, undo = act.write_changes(items, covers)
        self.assertEqual(done, [1])
        self.assertEqual(errors, [])
        self.assertIn('1', undo)

        # Verify disk audio tags were updated via Mutagen!
        t = tags.read_tags(audio_file)
        self.assertEqual(t['title'], 'New Mutagen Title')
        self.assertEqual(t['artist'], 'New Mutagen Artist')
        self.assertEqual(t['album'], 'New Album Series')
        self.assertEqual(t['track'], 7)
        self.assertEqual(t['year'], 2026)
        self.assertEqual(t['mb_recording'], 'rec-uuid-1234')
        self.assertEqual(t['mb_release'], 'rel-uuid-5678')
        self.assertEqual(t['cover'], ('image/jpeg', JPEG))

        # 2. Execute restore() (Undo operation)
        done_undo, errors_undo = act.restore(undo)
        self.assertEqual(done_undo, [1])
        self.assertEqual(errors_undo, [])

        # Verify disk audio tags were restored to their previous values!
        t_restored = tags.read_tags(audio_file)
        self.assertEqual(t_restored['title'], 'Original Calibre Title')
        self.assertEqual(t_restored['artist'], 'Original Calibre Artist')
        self.assertEqual(t_restored['album'], 'Original Series')
        self.assertEqual(t_restored['track'], 1)
        self.assertIsNone(t_restored['cover'], 'Cover was removed because original had no cover')

    def test_action_write_changes_malformed_file_reports_friendly_error(self):
        corrupt_file = self.write_file('corrupt_track.mp3', b'ID3\x03\x00\x00\x00\x00\x00\x10\xff\xff\xff\xffgarbage')

        mock_db = MagicMock()
        mock_db.formats.return_value = ['MP3']
        mock_db.format_abspath.return_value = corrupt_file

        from calibre_plugins.media_matcher import action as media_action

        act = media_action.MediaMatcherAction(None, 'Media Matcher')
        act.gui = MagicMock()
        act.gui.current_db.new_api = mock_db

        changes = {'title': ('Old', 'New')}
        items = [(42, changes)]
        done, errors, undo = act.write_changes(items, {})

        self.assertEqual(done, [])
        self.assertTrue(len(errors) > 0)
        self.assertIn('book 42', errors[0])
        self.assertIn('The audio file tags could not be read or written', errors[0])



if __name__ == '__main__':
    unittest.main()
