"""Reads tags, embedded cover art and length from audio files: MP3 (ID3v1, ID3v2.2/2.3/2.4), FLAC, Ogg Vorbis, Ogg Opus and
MP4 audio (M4A, M4B). Pure standard library so it can be tested outside Calibre; Calibre itself has no audio tag reader.

read_tags() never raises for a damaged or unknown file: it returns whatever could be read (possibly nothing) and the caller
falls back to guess_from_path(). Only the tag region of a file is read, never the audio, so large audiobooks are cheap.
"""

import base64
import os
import re
import struct

AUDIO_EXTENSIONS = {"mp3", "flac", "ogg", "oga", "opus", "m4a", "m4b", "aac", "wav", "wma"}
AUDIO_FORMATS = {e.upper() for e in AUDIO_EXTENSIONS}
AUDIOBOOK_GENRES = {
    "audiobook",
    "audio book",
    "audiobooks",
    "spoken word",
    "speech",
    "spoken",
    "book",
    "books & spoken",
}
MAX_TAG_BYTES = 64 * 1024 * 1024  # a tag (with its cover pictures) larger than this is treated as damaged

ID3V1_GENRES = [
    "Blues",
    "Classic Rock",
    "Country",
    "Dance",
    "Disco",
    "Funk",
    "Grunge",
    "Hip-Hop",
    "Jazz",
    "Metal",
    "New Age",
    "Oldies",
    "Other",
    "Pop",
    "R&B",
    "Rap",
    "Reggae",
    "Rock",
    "Techno",
    "Industrial",
    "Alternative",
    "Ska",
    "Death Metal",
    "Pranks",
    "Soundtrack",
    "Euro-Techno",
    "Ambient",
    "Trip-Hop",
    "Vocal",
    "Jazz+Funk",
    "Fusion",
    "Trance",
    "Classical",
    "Instrumental",
    "Acid",
    "House",
    "Game",
    "Sound Clip",
    "Gospel",
    "Noise",
    "Alternative Rock",
    "Bass",
    "Soul",
    "Punk",
    "Space",
    "Meditative",
    "Instrumental Pop",
    "Instrumental Rock",
    "Ethnic",
    "Gothic",
    "Darkwave",
    "Techno-Industrial",
    "Electronic",
    "Pop-Folk",
    "Eurodance",
    "Dream",
    "Southern Rock",
    "Comedy",
    "Cult",
    "Gangsta",
    "Top 40",
    "Christian Rap",
    "Pop/Funk",
    "Jungle",
    "Native American",
    "Cabaret",
    "New Wave",
    "Psychedelic",
    "Rave",
    "Showtunes",
    "Trailer",
    "Lo-Fi",
    "Tribal",
    "Acid Punk",
    "Acid Jazz",
    "Polka",
    "Retro",
    "Musical",
    "Rock & Roll",
    "Hard Rock",
]


def blank():
    return {
        "title": "",
        "artist": "",
        "album_artist": "",
        "album": "",
        "track": None,
        "track_total": None,
        "disc": None,
        "disc_total": None,
        "date": "",
        "year": None,
        "genre": "",
        "composer": "",
        "comment": "",
        "mb_recording": "",
        "mb_release": "",
        "audiobook_flag": False,
        "duration": None,
        "cover": None,
        "format": "",
        "tagged": False,
    }


def _int_pair(text):
    """'3/12' -> (3, 12); '3' -> (3, None); anything else -> (None, None)."""
    m = re.match(r"\s*(\d+)\s*(?:/\s*(\d+))?", text or "")
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2)) if m.group(2) else None


def _finish(tags):
    """Derive year from date, normalise numbers and drop empty strings' whitespace."""
    for key in (
        "title",
        "artist",
        "album_artist",
        "album",
        "date",
        "genre",
        "composer",
        "comment",
        "mb_recording",
        "mb_release",
    ):
        tags[key] = (tags[key] or "").strip()
    m = re.match(r"(\d{4})", tags["date"])
    tags["year"] = int(m.group(1)) if m else None
    if tags["genre"].lower() in AUDIOBOOK_GENRES:
        tags["audiobook_flag"] = True
    tags["tagged"] = any(tags[k] for k in ("title", "artist", "album"))
    return tags


# -- ID3 ---------------------------------------------------------------------------------------------------------------
V22_TO_V23 = {
    "TT2": "TIT2",
    "TP1": "TPE1",
    "TP2": "TPE2",
    "TAL": "TALB",
    "TRK": "TRCK",
    "TPA": "TPOS",
    "TYE": "TYER",
    "TCO": "TCON",
    "TCM": "TCOM",
    "COM": "COMM",
    "PIC": "APIC",
    "UFI": "UFID",
    "TXX": "TXXX",
}


def _syncsafe(b):
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _text(enc, data):
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    try:
        return data.decode(codec)
    except UnicodeDecodeError:
        return data.decode("latin-1", "replace")


def _split_terminated(enc, data):
    """Splits at the first string terminator: one NUL byte, or an aligned pair of them for UTF-16."""
    if enc in (1, 2):
        i = 0
        while i + 1 < len(data):
            if data[i] == 0 and data[i + 1] == 0:
                return data[:i], data[i + 2 :]
            i += 2
        return data, b""
    i = data.find(b"\x00")
    return (data, b"") if i < 0 else (data[:i], data[i + 1 :])


def _id3_values(data):
    if not data:
        return []
    return [v.strip() for v in _text(data[0], data[1:]).replace("﻿", "").split("\x00") if v.strip()]


def _id3_genre(value):
    m = re.match(r"^\((\d+)\)(.*)$", value)
    if m:
        if m.group(2).strip():
            return m.group(2).strip()
        value = m.group(1)
    if value.isdigit():
        n = int(value)
        return ID3V1_GENRES[n] if n < len(ID3V1_GENRES) else ""
    return value


def _picture(data, v22):
    """ID3 APIC/PIC payload -> (picture type, mime, bytes) or None."""
    if len(data) < 5:
        return None
    enc = data[0]
    if v22:
        mime = {"JPG": "image/jpeg", "PNG": "image/png"}.get(
            data[1:4].decode("latin-1").upper(), "image/" + data[1:4].decode("latin-1").lower()
        )
        rest = data[4:]
    else:
        raw, rest = _split_terminated(0, data[1:])
        mime = raw.decode("latin-1")
    if not rest:
        return None
    kind = rest[0]
    _desc, image = _split_terminated(enc, rest[1:])
    return (kind, mime, image) if image else None


def _id3_frames(body, major, tag_unsync):
    pos = 0
    id_len, head = (3, 6) if major == 2 else (4, 10)
    while pos + head <= len(body):
        frame_id = body[pos : pos + id_len]
        if not re.fullmatch(rb"[A-Z0-9]+", frame_id):
            return  # padding
        if major == 2:
            size = int.from_bytes(body[pos + 3 : pos + 6], "big")
            flags = 0
        else:
            raw = body[pos + 4 : pos + 8]
            size = _syncsafe(raw) if major == 4 else int.from_bytes(raw, "big")
            flags = int.from_bytes(body[pos + 8 : pos + 10], "big")
        data = body[pos + head : pos + head + size]
        pos += head + size
        if len(data) < size:
            return
        unsync = tag_unsync and major == 4
        if major == 3:
            if flags & 0xC0:  # compressed or encrypted: unreadable
                continue
            if flags & 0x20:  # grouping identity byte
                data = data[1:]
        elif major == 4:
            if flags & 0x000C:  # compressed or encrypted
                continue
            if flags & 0x0040:
                data = data[1:]
            if flags & 0x0001:  # data length indicator
                data = data[4:]
            unsync = unsync or bool(flags & 0x0002)
        if unsync:
            data = data.replace(b"\xff\x00", b"\xff")
        name = frame_id.decode("ascii")
        yield V22_TO_V23.get(name, name) if major == 2 else name, data


def _read_id3v2(f):
    """Returns (tag dict with 'cover' possibly set, end offset of the tag) or (None, 0) if there is no ID3v2 tag at the start."""
    f.seek(0)
    head = f.read(10)
    if len(head) < 10 or head[:3] != b"ID3" or head[3] not in (2, 3, 4) or any(b & 0x80 for b in head[6:10]):
        return None, 0
    major, flags, size = head[3], head[5], _syncsafe(head[6:10])
    end = 10 + size + (10 if flags & 0x10 and major == 4 else 0)
    if size > MAX_TAG_BYTES:
        return None, end
    body = f.read(size)
    tag_unsync = bool(flags & 0x80)
    if tag_unsync and major != 4:
        body = body.replace(b"\xff\x00", b"\xff")
    if flags & 0x40 and len(body) >= 4:  # extended header
        skip = _syncsafe(body[:4]) if major == 4 else int.from_bytes(body[:4], "big") + 4
        body = body[skip:]
    t = blank()
    pictures = []
    for name, data in _id3_frames(body, major, tag_unsync):
        simple = {"TIT2": "title", "TPE1": "artist", "TPE2": "album_artist", "TALB": "album", "TCOM": "composer"}
        if name in simple:
            values = _id3_values(data)
            if values and not t[simple[name]]:
                t[simple[name]] = " / ".join(values) if name in ("TPE1", "TCOM") else values[0]
        elif name == "TRCK":
            v = _id3_values(data)
            if v:
                t["track"], t["track_total"] = _int_pair(v[0])
        elif name == "TPOS":
            v = _id3_values(data)
            if v:
                t["disc"], t["disc_total"] = _int_pair(v[0])
        elif name in ("TDRC", "TYER", "TORY") and not t["date"]:
            v = _id3_values(data)
            t["date"] = v[0] if v else ""
        elif name == "TCON":
            v = _id3_values(data)
            if v and not t["genre"]:
                t["genre"] = _id3_genre(v[0])
        elif name == "COMM" and len(data) > 4 and not t["comment"]:
            _desc, text = _split_terminated(data[0], data[4:])
            t["comment"] = _text(data[0], text).strip("\x00")
        elif name == "TXXX" and len(data) > 1:
            raw_desc, value = _split_terminated(data[0], data[1:])
            desc = _text(data[0], raw_desc).strip().lower()
            if desc == "musicbrainz album id":
                t["mb_release"] = _text(data[0], value).strip("\x00")
        elif name == "UFID":
            owner, ident = _split_terminated(0, data)
            if owner.decode("latin-1") == "http://musicbrainz.org":
                t["mb_recording"] = ident.decode("ascii", "ignore").strip("\x00")
        elif name == "APIC":
            pic = _picture(data, major == 2)
            if pic:
                pictures.append(pic)
    t["cover"] = _pick_picture(pictures)
    return t, end


def _pick_picture(pictures):
    """pictures: [(type, mime, bytes)]. Front cover (type 3) if present, else the first one."""
    for kind, mime, image in pictures:
        if kind == 3:
            return (mime, image)
    return (pictures[0][1], pictures[0][2]) if pictures else None


def _read_id3v1(f, size):
    if size < 128:
        return None
    f.seek(size - 128)
    block = f.read(128)
    if block[:3] != b"TAG":
        return None
    field = lambda a, b: block[a:b].split(b"\x00")[0].decode("latin-1").strip()
    t = blank()
    t["title"], t["artist"], t["album"] = field(3, 33), field(33, 63), field(63, 93)
    t["date"] = field(93, 97)
    if block[125] == 0 and block[126] != 0:
        t["track"] = block[126]
    t["comment"] = field(97, 125 if block[125] == 0 else 127)
    t["genre"] = ID3V1_GENRES[block[127]] if block[127] < len(ID3V1_GENRES) else ""
    return t


MP3_BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
MP3_BITRATES[(2, 3)] = MP3_BITRATES[(2, 2)]
MP3_RATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 25: [11025, 12000, 8000]}


def _mp3_duration(f, start, size):
    """Length in seconds from the first MPEG frame: exact when the file has a Xing/Info/VBRI frame count, otherwise estimated
    from the bitrate (right for constant-bitrate files). None when no frame is found."""
    f.seek(start)
    chunk = f.read(65536)
    for i in range(len(chunk) - 4):
        if chunk[i] != 0xFF or (chunk[i + 1] & 0xE0) != 0xE0:
            continue
        h = int.from_bytes(chunk[i : i + 4], "big")
        version = {3: 1, 2: 2, 0: 25}.get((h >> 19) & 3)
        layer = {3: 1, 2: 2, 1: 3}.get((h >> 17) & 3)
        bitrate_index, rate_index = (h >> 12) & 15, (h >> 10) & 3
        if version is None or layer is None or bitrate_index in (0, 15) or rate_index == 3:
            continue
        rate = MP3_RATES[version][rate_index]
        bitrate = MP3_BITRATES[(1 if version == 1 else 2, layer)][bitrate_index] * 1000
        samples = 384 if layer == 1 else (1152 if version == 1 or layer == 2 else 576)
        mono = (h >> 6) & 3 == 3
        side = (17 if mono else 32) if version == 1 else (9 if mono else 17)
        for marker, offset in ((b"Xing", 4 + side), (b"Info", 4 + side)):
            at = i + offset
            if chunk[at : at + 4] == marker and int.from_bytes(chunk[at + 4 : at + 8], "big") & 1:
                frames = int.from_bytes(chunk[at + 8 : at + 12], "big")
                return frames * samples / rate if frames else None
        at = i + 4 + 32
        if chunk[at : at + 4] == b"VBRI":
            return int.from_bytes(chunk[at + 14 : at + 18], "big") * samples / rate
        audio_bytes = size - start - i
        return audio_bytes * 8 / bitrate if bitrate else None
    return None


def _read_mp3(f, size):
    t, end = _read_id3v2(f)
    v1 = _read_id3v1(f, size)
    if t is None:
        t = blank()
    if v1:
        for key in ("title", "artist", "album", "date", "comment", "genre", "track"):
            if not t[key]:
                t[key] = v1[key]
        if v1["title"] or v1["artist"]:
            end_audio = size - 128
        else:
            end_audio = size
    else:
        end_audio = size
    t["duration"] = _mp3_duration(f, end, end_audio)
    t["format"] = "MP3"
    return t


# -- Vorbis comments (FLAC and Ogg) --------------------------------------------------------------------------------------
def _vorbis_comments(data):
    """-> {UPPERCASE KEY: [values]} from a Vorbis comment block (without any packet header)."""
    out = {}
    try:
        n = struct.unpack_from("<I", data, 0)[0]
        pos = 4 + n
        count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        for _ in range(count):
            n = struct.unpack_from("<I", data, pos)[0]
            entry = data[pos + 4 : pos + 4 + n].decode("utf-8", "replace")
            pos += 4 + n
            if "=" in entry:
                key, value = entry.split("=", 1)
                out.setdefault(key.upper(), []).append(value)
    except struct.error:
        pass
    return out


def _flac_picture(data):
    """FLAC PICTURE block -> (type, mime, bytes) or None."""
    try:
        kind, n = struct.unpack_from(">II", data, 0)
        mime = data[8 : 8 + n].decode("ascii", "ignore")
        pos = 8 + n
        n = struct.unpack_from(">I", data, pos)[0]
        pos += 4 + n + 16  # description, then width, height, depth and colour count
        n = struct.unpack_from(">I", data, pos)[0]
        image = data[pos + 4 : pos + 4 + n]
        return (kind, mime, image) if image else None
    except struct.error:
        return None


def _from_vorbis(comments, pictures):
    t = blank()
    first = lambda *keys: next((comments[k][0] for k in keys if comments.get(k)), "")
    t["title"] = first("TITLE")
    t["artist"] = " / ".join(comments.get("ARTIST", [])[:4])
    t["album_artist"] = first("ALBUMARTIST", "ALBUM ARTIST")
    t["album"] = first("ALBUM")
    t["track"], total = _int_pair(first("TRACKNUMBER"))
    t["track_total"] = total or _int_pair(first("TRACKTOTAL", "TOTALTRACKS"))[0]
    t["disc"], total = _int_pair(first("DISCNUMBER"))
    t["disc_total"] = total or _int_pair(first("DISCTOTAL", "TOTALDISCS"))[0]
    t["date"] = first("DATE", "YEAR", "ORIGINALDATE")
    t["genre"] = first("GENRE")
    t["composer"] = first("COMPOSER")
    t["comment"] = first("COMMENT", "DESCRIPTION")
    t["mb_recording"] = first("MUSICBRAINZ_TRACKID")
    t["mb_release"] = first("MUSICBRAINZ_ALBUMID")
    for block in comments.get("METADATA_BLOCK_PICTURE", []):
        try:
            pic = _flac_picture(base64.b64decode(block))
        except ValueError:
            pic = None
        if pic:
            pictures.append(pic)
    t["cover"] = _pick_picture(pictures)
    return t


def _read_flac(f, start, size):
    f.seek(start)
    if f.read(4) != b"fLaC":
        return None
    t, pictures, duration = blank(), [], None
    comments = {}
    while True:
        head = f.read(4)
        if len(head) < 4:
            break
        last, kind, length = head[0] & 0x80, head[0] & 0x7F, int.from_bytes(head[1:4], "big")
        if kind in (0, 4, 6) and length <= MAX_TAG_BYTES:
            data = f.read(length)
            if kind == 0 and len(data) >= 18:
                rate = (data[10] << 12) | (data[11] << 4) | (data[12] >> 4)
                samples = ((data[13] & 0x0F) << 32) | int.from_bytes(data[14:18], "big")
                duration = samples / rate if rate and samples else None
            elif kind == 4:
                comments = _vorbis_comments(data)
            elif kind == 6:
                pic = _flac_picture(data)
                if pic:
                    pictures.append(pic)
        else:
            f.seek(length, 1)
        if last:
            break
    t = _from_vorbis(comments, pictures)
    t["duration"] = duration
    t["format"] = "FLAC"
    return t


def _ogg_packets(f, wanted, byte_limit=MAX_TAG_BYTES):
    packets, cur, read = [], b"", 0
    while len(packets) < wanted and read < byte_limit:
        hdr = f.read(27)
        if len(hdr) < 27 or hdr[:4] != b"OggS":
            break
        table = f.read(hdr[26])
        body = f.read(sum(table))
        read += 27 + len(table) + len(body)
        pos = 0
        for lacing in table:
            cur += body[pos : pos + lacing]
            pos += lacing
            if lacing < 255:
                packets.append(cur)
                cur = b""
    return packets


def _ogg_last_granule(f, size):
    f.seek(max(0, size - 65536))
    tail = f.read()
    i = tail.rfind(b"OggS")
    if i < 0 or len(tail) < i + 14:
        return None
    granule = struct.unpack_from("<q", tail, i + 6)[0]
    return granule if granule > 0 else None


def _read_ogg(f, size):
    f.seek(0)
    first = _ogg_packets(f, 1)
    if not first:
        return None
    ident = first[0]
    f.seek(0)
    if ident[:7] == b"\x01vorbis" and len(ident) >= 16:
        packets = _ogg_packets(f, 2)
        comment = packets[1][7:] if len(packets) > 1 and packets[1][:7] == b"\x03vorbis" else b""
        rate = struct.unpack_from("<I", ident, 12)[0]
        granule = _ogg_last_granule(f, size)
        duration = granule / rate if granule and rate else None
        fmt = "OGG"
    elif ident[:8] == b"OpusHead" and len(ident) >= 19:
        packets = _ogg_packets(f, 2)
        comment = packets[1][8:] if len(packets) > 1 and packets[1][:8] == b"OpusTags" else b""
        pre_skip = struct.unpack_from("<H", ident, 10)[0]
        granule = _ogg_last_granule(f, size)
        duration = (granule - pre_skip) / 48000 if granule and granule > pre_skip else None
        fmt = "OPUS"
    else:
        return None
    pictures = []
    t = _from_vorbis(_vorbis_comments(comment), pictures)
    t["duration"] = duration
    t["format"] = fmt
    return t


# -- MP4 -----------------------------------------------------------------------------------------------------------------
def _boxes(buf, start, end):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", buf, pos)
        head = 8
        if size == 1 and pos + 16 <= end:
            size, head = struct.unpack_from(">Q", buf, pos + 8)[0], 16
        elif size == 0:
            size = end - pos
        if size < head or pos + size > end:
            return
        yield kind, pos + head, pos + size
        pos += size


def _mp4_data(buf, start, end):
    """First 'data' box under an ilst item -> (type flag, value bytes)."""
    for kind, s, e in _boxes(buf, start, end):
        if kind == b"data" and e - s >= 8:
            return int.from_bytes(buf[s + 1 : s + 4], "big"), buf[s + 8 : e]
    return None, b""


def _read_mp4(f, size):
    f.seek(0)
    moov = None
    pos = 0
    while pos + 8 <= size:
        f.seek(pos)
        head = f.read(16)
        if len(head) < 8:
            break
        length, kind = struct.unpack_from(">I4s", head, 0)
        hlen = 8
        if length == 1 and len(head) == 16:
            length, hlen = struct.unpack_from(">Q", head, 8)[0], 16
        elif length == 0:
            length = size - pos
        if length < hlen:
            break
        if kind == b"moov":
            if length > MAX_TAG_BYTES:
                return None
            f.seek(pos)
            moov = f.read(length)
            break
        pos += length
    if moov is None:
        return None
    t = blank()
    t["format"] = "M4A"
    for kind, s, e in _boxes(moov, 8, len(moov)):
        if kind == b"mvhd" and e - s >= 20:
            if moov[s] == 1 and e - s >= 32:
                scale, dur = struct.unpack_from(">IQ", moov, s + 20)
            else:
                scale, dur = struct.unpack_from(">II", moov, s + 12)
            t["duration"] = dur / scale if scale and dur else None
        elif kind == b"udta":
            for k2, s2, e2 in _boxes(moov, s, e):
                if k2 == b"meta":
                    for k3, s3, e3 in _boxes(moov, s2 + 4, e2):
                        if k3 == b"ilst":
                            _mp4_items(moov, s3, e3, t)
    return t


def _mp4_items(buf, start, end, t):
    for kind, s, e in _boxes(buf, start, end):
        flag, value = _mp4_data(buf, s, e)
        text = lambda: value.decode("utf-8", "replace") if flag in (1, 0, None) else ""
        if kind == b"\xa9nam":
            t["title"] = text()
        elif kind == b"\xa9ART":
            t["artist"] = text()
        elif kind == b"aART":
            t["album_artist"] = text()
        elif kind == b"\xa9alb":
            t["album"] = text()
        elif kind == b"\xa9day":
            t["date"] = text()
        elif kind == b"\xa9gen":
            t["genre"] = text()
        elif kind == b"\xa9wrt":
            t["composer"] = text()
        elif kind in (b"\xa9cmt", b"desc") and not t["comment"]:
            t["comment"] = text()
        elif kind == b"gnre" and len(value) >= 2 and not t["genre"]:
            n = int.from_bytes(value[:2], "big") - 1
            t["genre"] = ID3V1_GENRES[n] if 0 <= n < len(ID3V1_GENRES) else ""
        elif kind == b"trkn" and len(value) >= 6:
            number, total = struct.unpack_from(">HH", value, 2)
            t["track"], t["track_total"] = number or None, total or None
        elif kind == b"disk" and len(value) >= 6:
            number, total = struct.unpack_from(">HH", value, 2)
            t["disc"], t["disc_total"] = number or None, total or None
        elif kind == b"stik" and value[:1] == b"\x02":
            t["audiobook_flag"] = True
        elif kind == b"covr" and value:
            t["cover"] = ("image/png" if flag == 14 else "image/jpeg", value)
        elif kind == b"----":
            name = mean = ""
            for k2, s2, e2 in _boxes(buf, s, e):
                if k2 == b"name":
                    name = buf[s2 + 4 : e2].decode("utf-8", "replace")
                elif k2 == b"mean":
                    mean = buf[s2 + 4 : e2].decode("utf-8", "replace")
            if mean == "com.apple.iTunes" and name.lower() == "musicbrainz track id":
                t["mb_recording"] = value.decode("ascii", "ignore")
            elif mean == "com.apple.iTunes" and name.lower() == "musicbrainz album id":
                t["mb_release"] = value.decode("ascii", "ignore")


# -- entry points --------------------------------------------------------------------------------------------------------
def read_tags(path):
    """Tags of one audio file as a dict (see blank()). Unreadable or unsupported files give an untagged dict, never an error."""
    result = None
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(12)
            f.seek(0)
            if head[:4] == b"OggS":
                result = _read_ogg(f, size)
            elif head[4:8] == b"ftyp":
                result = _read_mp4(f, size)
                if result and os.path.splitext(path)[1].lower() == ".m4b":
                    result["format"] = "M4B"
            elif head[:4] == b"fLaC":
                result = _read_flac(f, 0, size)
            elif head[:3] == b"ID3":
                _tag, end = _read_id3v2(f)
                f.seek(end)
                if f.read(4) == b"fLaC":  # a FLAC file with an ID3v2 tag in front of it
                    result = _read_flac(f, end, size)
                    if result and _tag:
                        for key in ("title", "artist", "album", "date", "genre"):
                            result[key] = result[key] or _tag[key]
                else:
                    result = _read_mp3(f, size)
            elif (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0) or (
                size >= 128 and _has_id3v1(f, size)
            ):
                result = _read_mp3(f, size)
    except Exception:  # a damaged or unusual file must never break an import; whatever was read is lost, the caller falls back to the path
        result = None
    if result is None:
        result = blank()
        result["format"] = os.path.splitext(path)[1].lstrip(".").upper()
    return _finish(result)


def _has_id3v1(f, size):
    f.seek(size - 128)
    return f.read(3) == b"TAG"


def guess_from_path(path):
    """Best effort from the file and folder names: '<folder=artist>/<folder=album>/03 - Title.mp3'. Used for fields the tags lack."""
    t = blank()
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^\s*(?:(\d{1,2})[-_. ]+)?(\d{1,3})\s*[-_.)]\s*(.+)$", stem)
    if m:
        t["track"] = int(m.group(2))
        if m.group(1):
            t["disc"] = int(m.group(1))
        stem = m.group(3)
    parts = stem.split(" - ")
    if len(parts) == 2 and not t["track"]:
        t["artist"], stem = parts[0].strip(), parts[1]
    t["title"] = stem.replace("_", " ").strip()
    parent = os.path.basename(os.path.dirname(path))
    grand = os.path.basename(os.path.dirname(os.path.dirname(path)))
    if parent:
        t["album"] = parent.replace("_", " ").strip()
    if grand and not t["artist"]:
        t["artist"] = grand.replace("_", " ").strip()
    return t


def tags_with_fallback(path):
    """read_tags() with missing title/artist/album/track filled from the path. Adds 'from_path' listing the guessed fields."""
    t = read_tags(path)
    guess = guess_from_path(path)
    t["from_path"] = []
    for key in ("title", "artist", "album", "track"):
        if not t[key] and guess[key]:
            t[key] = guess[key]
            t["from_path"].append(key)
    if not t["title"]:
        t["title"] = os.path.splitext(os.path.basename(path))[0]
    return t


def is_audiobook(tags, path=""):
    return (
        bool(tags.get("audiobook_flag")) or os.path.splitext(path)[1].lower() == ".m4b" or tags.get("format") == "M4B"
    )


def scan_folder(folder, cancelled=lambda: False):
    """Audio files under a folder, in a stable order (album folders stay together, tracks sort by name)."""
    found = []
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        if cancelled():
            break
        for name in sorted(files):
            if os.path.splitext(name)[1].lower().lstrip(".") in AUDIO_EXTENSIONS and not name.startswith("._"):
                found.append(os.path.join(root, name))
    return found
