# Media Matcher

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

Lets Calibre hold **music files and audiobooks next to your books**, and matches all three against free, open databases. The button has two commands: **Add music and audiobooks…** and **Match with MusicBrainz / Open Library…**.

## Free services used

| Service | Used for | Key needed? |
| --- | --- | --- |
| [MusicBrainz](https://musicbrainz.org) | Identifying music: title, artist(s), album, track number, release date, ids | No |
| [Cover Art Archive](https://coverartarchive.org) | Front covers for MusicBrainz releases | No |
| [AcoustID](https://acoustid.org) + [Chromaprint](https://acoustid.org/chromaprint) `fpcalc` | Optional: identifying a track from its *sound* when its tags are missing or wrong | Free application key from [acoustid.org/new-application](https://acoustid.org/new-application), and the free `fpcalc` program |
| [Open Library](https://openlibrary.org) | Audiobooks and ordinary books: title, authors, first publication year, cover | No |

The plugin identifies itself in the `User-Agent` (add your own email or web page in the window if you like, which MusicBrainz asks for), keeps to MusicBrainz's limit of one request a second, and waits and retries when a service says it is busy. A run of 100 tracks therefore takes a couple of minutes. Cancel keeps what was found so far.

## How music is stored

Calibre keeps **one file per format per book**, so an album cannot be one book holding twelve MP3s. Instead **each audio file becomes its own book**: the artist is the author, the **album is the series**, the **track number is the series number**, the genre is a tag, and the book is tagged `Music` or `Audiobook`. Browsing by series gives you albums in track order. The file is stored as the book's format (`MP3`, `FLAC`, `OGG`, `OPUS`, `M4A`, `M4B`), and embedded cover art becomes the book's cover. Calibre has no music player; the file sits in the book's folder in your library (**Open containing folder**), so you can open it in your player from there. I have not tested how Calibre's own viewer commands behave with audio formats.

## Adding files

**Add music and audiobooks…** reads the tags of files or of a whole folder (subfolders included) and shows a list to tick before anything is added.

- Reads **MP3** (ID3v1, ID3v2.2/2.3/2.4), **FLAC**, **Ogg Vorbis**, **Opus**, and **M4A/M4B** tags, embedded cover art and length. Calibre has no audio tag reader of its own, so these readers are part of the plugin. `AAC`, `WAV` and `WMA` files can be added too but their tags are not read.
- Missing title/artist/album/track are filled from the file and folder names (`Artist/Album/03 - Song.mp3`); those rows say *(from file name)*.
- **M4B** files, files tagged as audiobooks (MP4 `stik`, or an *Audiobook*/*Spoken Word* genre) are treated as audiobooks; the *Treat as* box overrides that.
- Files already in the library (same title, artist, album and format) are skipped by default.
- Tick *look the new books up…* to go straight to matching.

## Matching

Select books and press **Match…** (it works on music, audiobooks and ordinary books, and each is detected from its formats and tags unless you choose).

- **Music** → MusicBrainz. It searches by title and artist (and album if known), scores each result on title, artist, album and length, and picks the album release that fits your album name, preferring official studio albums and the earliest release. A track already tagged with a MusicBrainz id (for example by Picard) is looked up directly. With **audio fingerprints** on, the track is fingerprinted with `fpcalc`, looked up on AcoustID, and the result checked on MusicBrainz, so even a file called `Track 01` can be identified.
- **Audiobooks and books** → Open Library, by title and author. For a folder of chapter files the book title is taken from the album (the series) rather than the chapter title.
- Every suggestion shows its **score** and each field as *old → new* with a tick box. **Weak matches (below 60%) are never offered.** Matches are left unticked when they are below 85%, when a title would change a lot (**BIG TITLE CHANGE**), or when several different recordings fit equally well and nothing singles one out (*similar recordings, check this one*).
- You choose which fields to fix: titles, artists/authors, album and track number, release date, the `Music`/`Audiobook` tag, MusicBrainz / Open Library ids, covers, and (off by default, because they are noisy) Open Library subjects as tags.
- Covers are only added to books **without** one; an existing cover is never replaced.
- **Undo last run** restores titles, authors, series, dates, tags and identifiers (not covers).

### Choices made on purpose

- **No ISBN or publisher for books.** Open Library's search finds a *work*, not the edition you own, so any ISBN or publisher it returned would be a guess about someone else's edition. Only the id of the edition whose cover is offered is stored (`olid`).
- **No genres from MusicBrainz.** Its search results do not include them; the genre tag from your files is kept.
- **Audiobook chapter files keep their own titles**; only a single-file audiobook has its title corrected.

## Privacy

Lookups send only the title, artist, album and length of a track (or an audiobook's title and author) to MusicBrainz and Open Library. With fingerprints on, a short Chromaprint fingerprint of the audio goes to AcoustID. Cover downloads fetch images from the Cover Art Archive, archive.org and Open Library. No audio file or library content is uploaded. The AcoustID key is stored unencrypted in Calibre's settings folder.

## Getting `fpcalc` (optional)

Download Chromaprint from [acoustid.org/chromaprint](https://acoustid.org/chromaprint) (Linux: the `libchromaprint-tools` or `chromaprint` package; macOS: `brew install chromaprint`). Leave the *fpcalc* box empty if it is on your `PATH`, otherwise enter its location. Tick the fingerprint option and enter your AcoustID key.

## Not verified

- **Not run inside Calibre.** The Calibre calls used (`add_books`, `set_cover`, `set_field`, `all_field_for`, `field_for`, `get_metadata`, `format_abspath`, `create_menu_action`, `refresh_ids`) were checked against Calibre's source on GitHub. Calibre's database layer does not restrict format names, so audio extensions are stored as formats, but I have not seen Calibre actually do it, and have not checked how it shows or syncs them (for example to e-reader devices).
- **Live checks done:** the full lookup (search, scoring, release choice, cover download) was run against the live MusicBrainz, Cover Art Archive and Open Library services, including MusicBrainz's "busy" answer being retried. `fpcalc` output parsing was checked against Chromaprint 1.5.1, and AcoustID was reached with an invalid key to confirm the request is accepted and a bad key is reported plainly.
- **Not done:** a *successful* AcoustID lookup (it needs a registered key, which was not available), so the fingerprint path is tested only with canned AcoustID answers. How accurate the matching is on a real music collection is untested beyond a few tracks; start with a few books and read the scores.
- Tag readers were tested on files written by ffmpeg (MP3 ID3v2.3/2.4, FLAC, Ogg Vorbis, Opus, M4A, M4B, with and without cover art) and on hand-built bytes for the rest (ID3v1, v2.2, unsynchronisation, multi-page Ogg comments, truncated and junk files). Files from other encoders and taggers may differ.
- The two windows were run under real Qt (PyQt6, offscreen) against a stand-in library: adding from a folder, lookup, apply, undo, cancel and saved settings.

## Metadata source

This plugin also ships as a Calibre metadata source in a separate ZIP, `MediaMatcherMetadataSource.zip` (Calibre allows only one plugin per ZIP). The source **Media Matcher Lookup** answers Download metadata requests with the plugin's own clients and scoring: MusicBrainz for music (album becomes the series, track number the series index, covers from the Cover Art Archive) and Open Library for books and audiobooks, including direct lookup by ISBN, MusicBrainz recording/release id or Open Library id. It uses the plugin's saved settings (contact string, kind, AcoustID options). Install it alongside Media Matcher and enable it in Calibre's Download metadata source list.
