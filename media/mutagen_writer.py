"""Wrapper module that translates Calibre metadata dictionaries into Mutagen tag object calls.

Supports MP3 (ID3v2), FLAC, Ogg Vorbis, Ogg Opus, MP4/M4A/M4B, and other audio formats.
Operates with standard Mutagen APIs to persist tags and embedded cover art directly into audio files on disk.
"""

import base64
import os
import re
import sys
from pathlib import Path

# Try importing standard mutagen, then fall back to third_party vendored mutagen
try:
    import mutagen
except ImportError:
    _root = Path(__file__).resolve().parent.parent
    _third_party = _root / "third_party"
    if _third_party.exists() and str(_third_party) not in sys.path:
        sys.path.insert(0, str(_third_party))
    try:
        import mutagen
    except ImportError:
        mutagen = None

if mutagen is not None:
    from mutagen.flac import FLAC, Picture
    from mutagen.id3 import (
        APIC,
        COMM,
        ID3,
        TALB,
        TCOM,
        TCON,
        TDRC,
        TIT2,
        TPE1,
        TPE2,
        TPOS,
        TRCK,
        TXXX,
        TYER,
        UFID,
    )
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.oggopus import OggOpus
    from mutagen.oggvorbis import OggVorbis
else:
    FLAC = Picture = ID3 = TIT2 = TPE1 = TPE2 = TALB = TRCK = TPOS = TDRC = TYER = None
    TCON = TCOM = COMM = UFID = TXXX = APIC = MP3 = MP4 = MP4Cover = OggOpus = OggVorbis = None


def is_mutagen_available():
    """Returns True if Mutagen library is loaded and available."""
    return mutagen is not None


def _normalize_metadata(metadata):
    """Normalizes various Calibre and Media Matcher dictionary representations into standard tag fields."""
    if not metadata:
        return {}
    norm = dict(metadata)

    # Album and Track from 'album' tuple [album, track] or separate fields
    if "album" in norm and isinstance(norm["album"], (list, tuple)):
        album_val = norm["album"]
        norm["album"] = album_val[0] if len(album_val) > 0 else ""
        if len(album_val) > 1 and album_val[1] is not None and "track" not in norm:
            norm["track"] = album_val[1]

    if "series" in norm and "album" not in norm:
        norm["album"] = norm["series"]
    if "series_index" in norm and "track" not in norm:
        norm["track"] = norm["series_index"]

    # Artists / Authors
    if "authors" in norm and "artist" not in norm:
        authors = norm["authors"]
        if isinstance(authors, (list, tuple)):
            norm["artist"] = list(authors)
        elif isinstance(authors, str):
            norm["artist"] = [authors]
    elif "artist" in norm:
        if isinstance(norm["artist"], str):
            norm["artist"] = [norm["artist"]]
        elif isinstance(norm["artist"], (list, tuple)):
            norm["artist"] = list(norm["artist"])

    # Track number
    if "track" in norm and norm["track"] is not None:
        try:
            norm["track"] = int(norm["track"])
        except (ValueError, TypeError):
            pass

    # Date / Year
    if "pubdate" in norm and "date" not in norm:
        norm["date"] = norm["pubdate"]
    if "date" in norm and norm["date"]:
        if hasattr(norm["date"], "isoformat"):
            norm["date"] = norm["date"].isoformat()
        date_str = str(norm["date"])
        if "T" in date_str:
            date_str = date_str.split("T")[0]
        norm["date"] = date_str

    # Identifiers
    identifiers = norm.get("identifiers") or {}
    if isinstance(identifiers, dict):
        if "mb_recording" not in norm:
            norm["mb_recording"] = (
                identifiers.get("mb_recording")
                or identifiers.get("musicbrainz_recording")
                or identifiers.get("musicbrainz")
                or ""
            )
        if "mb_release" not in norm:
            norm["mb_release"] = (
                identifiers.get("mb_release")
                or identifiers.get("musicbrainz_album")
                or identifiers.get("musicbrainz_release")
                or ""
            )

    # Tags / Genre
    if "tags" in norm and "genre" not in norm:
        tags_val = norm["tags"]
        if isinstance(tags_val, (list, tuple)):
            norm["genre"] = ", ".join(str(t) for t in tags_val if t)
        elif tags_val:
            norm["genre"] = str(tags_val)

    return norm


def _write_id3(audio, norm, cover_bytes, remove_cover):
    """Write tags to an ID3-tagged container (MP3, AIFF, WAV)."""
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    if "title" in norm:
        tags.delall("TIT2")
        if norm["title"]:
            tags.add(TIT2(encoding=3, text=[str(norm["title"])]))

    if "artist" in norm:
        tags.delall("TPE1")
        artists = norm["artist"]
        if isinstance(artists, str):
            artists = [artists]
        if artists:
            tags.add(TPE1(encoding=3, text=[str(a) for a in artists]))

    if "album_artist" in norm:
        tags.delall("TPE2")
        if norm["album_artist"]:
            tags.add(TPE2(encoding=3, text=[str(norm["album_artist"])]))

    if "album" in norm:
        tags.delall("TALB")
        if norm["album"]:
            tags.add(TALB(encoding=3, text=[str(norm["album"])]))

    if "track" in norm:
        tags.delall("TRCK")
        if norm["track"] is not None:
            track_val = str(norm["track"])
            if norm.get("track_total") is not None:
                track_val = f"{track_val}/{norm['track_total']}"
            tags.add(TRCK(encoding=3, text=[track_val]))

    if "disc" in norm:
        tags.delall("TPOS")
        if norm["disc"] is not None:
            disc_val = str(norm["disc"])
            if norm.get("disc_total") is not None:
                disc_val = f"{disc_val}/{norm['disc_total']}"
            tags.add(TPOS(encoding=3, text=[disc_val]))

    if "date" in norm:
        tags.delall("TDRC")
        tags.delall("TYER")
        date_str = str(norm["date"]) if norm["date"] else ""
        if date_str:
            tags.add(TDRC(encoding=3, text=[date_str]))
            m = re.match(r"(\d{4})", date_str)
            if m:
                tags.add(TYER(encoding=3, text=[m.group(1)]))

    if "genre" in norm:
        tags.delall("TCON")
        if norm["genre"]:
            tags.add(TCON(encoding=3, text=[str(norm["genre"])]))

    if "composer" in norm:
        tags.delall("TCOM")
        if norm["composer"]:
            tags.add(TCOM(encoding=3, text=[str(norm["composer"])]))

    if "comment" in norm:
        tags.delall("COMM")
        if norm["comment"]:
            tags.add(COMM(encoding=3, lang="eng", desc="", text=[str(norm["comment"])]))

    if "mb_recording" in norm:
        tags.delall("UFID")
        rec_id = norm["mb_recording"]
        if rec_id:
            tags.add(
                UFID(
                    owner="http://musicbrainz.org",
                    data=rec_id.encode("ascii", "ignore"),
                )
            )

    if "mb_release" in norm:
        for f in list(tags.getall("TXXX")):
            if getattr(f, "desc", "").lower() == "musicbrainz album id":
                tags.delall(f.HashKey)
        rel_id = norm["mb_release"]
        if rel_id:
            tags.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=[str(rel_id)]))

    if cover_bytes:
        mime = "image/png" if cover_bytes.startswith(b"\x89PNG") else "image/jpeg"
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Front Cover", data=cover_bytes))
    elif remove_cover:
        tags.delall("APIC")


def _write_flac(audio, norm, cover_bytes, remove_cover):
    """Write tags to a FLAC audio container."""
    if "title" in norm:
        if norm["title"]:
            audio["TITLE"] = [str(norm["title"])]
        else:
            audio.pop("TITLE", None)

    if "artist" in norm:
        artists = norm["artist"]
        if isinstance(artists, str):
            artists = [artists]
        if artists:
            audio["ARTIST"] = [str(a) for a in artists]
        else:
            audio.pop("ARTIST", None)

    if "album_artist" in norm:
        if norm["album_artist"]:
            audio["ALBUMARTIST"] = [str(norm["album_artist"])]
        else:
            audio.pop("ALBUMARTIST", None)

    if "album" in norm:
        if norm["album"]:
            audio["ALBUM"] = [str(norm["album"])]
        else:
            audio.pop("ALBUM", None)

    if "track" in norm:
        if norm["track"] is not None:
            audio["TRACKNUMBER"] = [str(norm["track"])]
        else:
            audio.pop("TRACKNUMBER", None)

    if "track_total" in norm:
        if norm["track_total"] is not None:
            audio["TRACKTOTAL"] = [str(norm["track_total"])]
        else:
            audio.pop("TRACKTOTAL", None)

    if "disc" in norm:
        if norm["disc"] is not None:
            audio["DISCNUMBER"] = [str(norm["disc"])]
        else:
            audio.pop("DISCNUMBER", None)

    if "disc_total" in norm:
        if norm["disc_total"] is not None:
            audio["DISCTOTAL"] = [str(norm["disc_total"])]
        else:
            audio.pop("DISCTOTAL", None)

    if "date" in norm:
        if norm["date"]:
            audio["DATE"] = [str(norm["date"])]
        else:
            audio.pop("DATE", None)

    if "genre" in norm:
        if norm["genre"]:
            audio["GENRE"] = [str(norm["genre"])]
        else:
            audio.pop("GENRE", None)

    if "composer" in norm:
        if norm["composer"]:
            audio["COMPOSER"] = [str(norm["composer"])]
        else:
            audio.pop("COMPOSER", None)

    if "comment" in norm:
        if norm["comment"]:
            audio["COMMENT"] = [str(norm["comment"])]
        else:
            audio.pop("COMMENT", None)

    if "mb_recording" in norm:
        if norm["mb_recording"]:
            audio["MUSICBRAINZ_TRACKID"] = [str(norm["mb_recording"])]
        else:
            audio.pop("MUSICBRAINZ_TRACKID", None)

    if "mb_release" in norm:
        if norm["mb_release"]:
            audio["MUSICBRAINZ_ALBUMID"] = [str(norm["mb_release"])]
        else:
            audio.pop("MUSICBRAINZ_ALBUMID", None)

    if cover_bytes:
        mime = "image/png" if cover_bytes.startswith(b"\x89PNG") else "image/jpeg"
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Front Cover"
        pic.data = cover_bytes
        audio.add_picture(pic)
    elif remove_cover:
        audio.clear_pictures()


def _write_ogg(audio, norm, cover_bytes, remove_cover):
    """Write tags to an Ogg Vorbis or Ogg Opus container."""
    if "title" in norm:
        if norm["title"]:
            audio["TITLE"] = [str(norm["title"])]
        else:
            audio.pop("TITLE", None)

    if "artist" in norm:
        artists = norm["artist"]
        if isinstance(artists, str):
            artists = [artists]
        if artists:
            audio["ARTIST"] = [str(a) for a in artists]
        else:
            audio.pop("ARTIST", None)

    if "album_artist" in norm:
        if norm["album_artist"]:
            audio["ALBUMARTIST"] = [str(norm["album_artist"])]
        else:
            audio.pop("ALBUMARTIST", None)

    if "album" in norm:
        if norm["album"]:
            audio["ALBUM"] = [str(norm["album"])]
        else:
            audio.pop("ALBUM", None)

    if "track" in norm:
        if norm["track"] is not None:
            audio["TRACKNUMBER"] = [str(norm["track"])]
        else:
            audio.pop("TRACKNUMBER", None)

    if "track_total" in norm:
        if norm["track_total"] is not None:
            audio["TRACKTOTAL"] = [str(norm["track_total"])]
        else:
            audio.pop("TRACKTOTAL", None)

    if "disc" in norm:
        if norm["disc"] is not None:
            audio["DISCNUMBER"] = [str(norm["disc"])]
        else:
            audio.pop("DISCNUMBER", None)

    if "disc_total" in norm:
        if norm["disc_total"] is not None:
            audio["DISCTOTAL"] = [str(norm["disc_total"])]
        else:
            audio.pop("DISCTOTAL", None)

    if "date" in norm:
        if norm["date"]:
            audio["DATE"] = [str(norm["date"])]
        else:
            audio.pop("DATE", None)

    if "genre" in norm:
        if norm["genre"]:
            audio["GENRE"] = [str(norm["genre"])]
        else:
            audio.pop("GENRE", None)

    if "composer" in norm:
        if norm["composer"]:
            audio["COMPOSER"] = [str(norm["composer"])]
        else:
            audio.pop("COMPOSER", None)

    if "comment" in norm:
        if norm["comment"]:
            audio["COMMENT"] = [str(norm["comment"])]
        else:
            audio.pop("COMMENT", None)

    if "mb_recording" in norm:
        if norm["mb_recording"]:
            audio["MUSICBRAINZ_TRACKID"] = [str(norm["mb_recording"])]
        else:
            audio.pop("MUSICBRAINZ_TRACKID", None)

    if "mb_release" in norm:
        if norm["mb_release"]:
            audio["MUSICBRAINZ_ALBUMID"] = [str(norm["mb_release"])]
        else:
            audio.pop("MUSICBRAINZ_ALBUMID", None)

    if cover_bytes:
        mime = "image/png" if cover_bytes.startswith(b"\x89PNG") else "image/jpeg"
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Front Cover"
        pic.data = cover_bytes
        audio["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode("ascii")]
    elif remove_cover:
        audio.pop("METADATA_BLOCK_PICTURE", None)


def _write_mp4(audio, norm, cover_bytes, remove_cover):
    """Write tags to an MP4/M4A/M4B audio container."""
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    if "title" in norm:
        if norm["title"]:
            tags["\xa9nam"] = [str(norm["title"])]
        else:
            tags.pop("\xa9nam", None)

    if "artist" in norm:
        artists = norm["artist"]
        if isinstance(artists, (list, tuple)):
            artist_str = " / ".join(str(a) for a in artists)
        else:
            artist_str = str(artists)
        if artist_str:
            tags["\xa9ART"] = [artist_str]
        else:
            tags.pop("\xa9ART", None)

    if "album_artist" in norm:
        if norm["album_artist"]:
            tags["aART"] = [str(norm["album_artist"])]
        else:
            tags.pop("aART", None)

    if "album" in norm:
        if norm["album"]:
            tags["\xa9alb"] = [str(norm["album"])]
        else:
            tags.pop("\xa9alb", None)

    if "track" in norm:
        if norm["track"] is not None:
            track_num = int(norm["track"])
            track_tot = int(norm.get("track_total") or 0)
            tags["trkn"] = [(track_num, track_tot)]
        else:
            tags.pop("trkn", None)

    if "disc" in norm:
        if norm["disc"] is not None:
            disc_num = int(norm["disc"])
            disc_tot = int(norm.get("disc_total") or 0)
            tags["disk"] = [(disc_num, disc_tot)]
        else:
            tags.pop("disk", None)

    if "date" in norm:
        if norm["date"]:
            tags["\xa9day"] = [str(norm["date"])]
        else:
            tags.pop("\xa9day", None)

    if "genre" in norm:
        if norm["genre"]:
            tags["\xa9gen"] = [str(norm["genre"])]
        else:
            tags.pop("\xa9gen", None)

    if "composer" in norm:
        if norm["composer"]:
            tags["\xa9wrt"] = [str(norm["composer"])]
        else:
            tags.pop("\xa9wrt", None)

    if "comment" in norm:
        if norm["comment"]:
            tags["\xa9cmt"] = [str(norm["comment"])]
        else:
            tags.pop("\xa9cmt", None)

    if norm.get("audiobook_flag"):
        tags["stik"] = [2]

    if "mb_recording" in norm:
        key = "----:com.apple.iTunes:MusicBrainz Track Id"
        if norm["mb_recording"]:
            tags[key] = [str(norm["mb_recording"]).encode("utf-8")]
        else:
            tags.pop(key, None)

    if "mb_release" in norm:
        key = "----:com.apple.iTunes:MusicBrainz Album Id"
        if norm["mb_release"]:
            tags[key] = [str(norm["mb_release"]).encode("utf-8")]
        else:
            tags.pop(key, None)

    if cover_bytes:
        fmt = MP4Cover.FORMAT_PNG if cover_bytes.startswith(b"\x89PNG") else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover_bytes, imageformat=fmt)]
    elif remove_cover:
        tags.pop("covr", None)


def write_metadata_to_file(path, metadata=None, cover_bytes=None, remove_cover=False):
    """Writes metadata dictionary and cover image into the audio file at path using Mutagen.

    Raises MutagenError or OSError on failure.
    """
    if not is_mutagen_available():
        raise RuntimeError("Mutagen is not available; cannot write audio tags to disk")

    if not os.path.exists(path):
        raise FileNotFoundError(f"Audio file not found: {path}")

    norm = _normalize_metadata(metadata)

    if cover_bytes is None and "cover" in norm:
        c = norm["cover"]
        if isinstance(c, bytes):
            cover_bytes = c
        elif isinstance(c, str):
            try:
                cover_bytes = base64.b64decode(c)
            except Exception:
                cover_bytes = None
        elif c is None:
            remove_cover = True

    audio = mutagen.File(path)
    if audio is None:
        raise mutagen.MutagenError(f"Unsupported or unrecognized audio format for file: {path}")

    # Dispatch to appropriate writer based on audio file type
    if isinstance(audio, (MP4, mutagen.mp4.MP4)):
        _write_mp4(audio, norm, cover_bytes, remove_cover)
    elif isinstance(audio, (FLAC, mutagen.flac.FLAC)):
        _write_flac(audio, norm, cover_bytes, remove_cover)
    elif isinstance(audio, (OggVorbis, OggOpus, mutagen.ogg.OggFileType)):
        _write_ogg(audio, norm, cover_bytes, remove_cover)
    elif isinstance(audio, (MP3, mutagen.mp3.MP3)) or (hasattr(audio, "tags") and isinstance(audio.tags, ID3)):
        _write_id3(audio, norm, cover_bytes, remove_cover)
    elif hasattr(audio, "add_tags") and not hasattr(audio, "tags"):
        _write_id3(audio, norm, cover_bytes, remove_cover)
    elif hasattr(audio, "__setitem__"):
        _write_ogg(audio, norm, cover_bytes, remove_cover)
    else:
        raise mutagen.MutagenError(f"Cannot determine appropriate tag format for {path} ({type(audio)})")

    audio.save()
    return True


def apply_changes_to_file(path, changes, cover_bytes=None):
    """Applies a Media Matcher changes dictionary {field: (old, new)} to the audio file."""
    metadata = {}
    for field, (old, new) in changes.items():
        if field == "title":
            metadata["title"] = new
        elif field == "authors":
            metadata["authors"] = new
        elif field == "album":
            if isinstance(new, (list, tuple)):
                metadata["album"] = new[0]
                if len(new) > 1 and new[1] is not None:
                    metadata["track"] = new[1]
            else:
                metadata["album"] = new
        elif field == "date":
            metadata["date"] = new
        elif field == "tags":
            metadata["tags"] = new
        elif field == "identifiers":
            metadata["identifiers"] = new
        elif field == "cover" and cover_bytes:
            metadata["cover"] = cover_bytes

    return write_metadata_to_file(path, metadata=metadata, cover_bytes=cover_bytes, remove_cover=False)


def apply_saved_to_file(path, saved):
    """Restores saved metadata {field: old_value} to the audio file on disk for undo."""
    metadata = {}
    cover_bytes = None
    remove_cover = False

    for field, old in saved.items():
        if field == "title":
            metadata["title"] = old
        elif field == "authors":
            metadata["authors"] = old
        elif field == "album":
            if isinstance(old, (list, tuple)):
                metadata["album"] = old[0]
                if len(old) > 1 and old[1] is not None:
                    metadata["track"] = old[1]
            else:
                metadata["album"] = old
        elif field == "date":
            metadata["date"] = old
        elif field == "tags":
            metadata["tags"] = old
        elif field == "identifiers":
            metadata["identifiers"] = old
        elif field == "cover":
            if old is None:
                remove_cover = True
            else:
                cover_bytes = base64.b64decode(old) if isinstance(old, str) else old

    return write_metadata_to_file(path, metadata=metadata, cover_bytes=cover_bytes, remove_cover=remove_cover)
