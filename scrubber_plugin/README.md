# EPUB Promo Scrubber for Calibre

Install `dist/EpubPromoScrubber.zip` the same way (Preferences → Plugins → Load plugin from file), restart, and add **EPUB Promo Scrubber** to a toolbar if needed. It removes marks that sites add on top of a book: "Downloaded from …" banner pages and lines, hyperlinks to those sites, and site names in the book's metadata. Built-in names are OceanofPDF, PDFDrive, Z-Library, LibGen and Library Genesis. **Settings…** lets you add more site names, domains or `re:` regular expressions, and toggle the options below.

- **Scrub selected books in Calibre** replaces each selected book's EPUB with the cleaned version and also cleans the library's title, publisher and comments fields. By default the unmodified EPUB is kept as ORIGINAL_EPUB. Untick that in **Settings…** to discard the original (and any existing ORIGINAL_EPUB) so only the cleaned version remains.
- **Scrub EPUB file or folder** writes cleaned copies and a `scrub-report.json` to a separate folder; originals are untouched. Tick **replace the original files** in **Settings…** to overwrite the originals instead (after a confirmation prompt); only files that actually changed are rewritten, and the report is saved in the source folder.

What it does inside the EPUB: deletes short blocks (250 characters or fewer) that name a blocked site; unwraps or removes links to it; trims leftover mentions in longer paragraphs; deletes pages that end up empty, along with their spine, contents and guide entries; cleans Dublin Core metadata and the ZIP comment. Optionally, short "Downloaded from <any website>" / "free ebooks" blocks from unlisted sites are removed too. Links to an author's or publisher's own site are kept. Every change is listed in the report.

Limits: watermarks baked into images or cover art are not detected. XHTML files that are not well-formed XML (including older files relying on `&nbsp;`-style DTD entities, and encrypted files) are left unchanged and flagged in the report. DRM is not touched. The text-matching rules can remove legitimate short passages that mention a blocked name, which is why the original is kept by default and each removal is reported.

Testing: the cleaning logic has unit tests (`python -m unittest tests.test_scrubber`) run against synthetic EPUBs. The Calibre GUI wrapper (`scrubber_plugin/action.py`) has **not** been run inside Calibre, and no real-world EPUBs have been tried.

## Metadata source

This plugin also ships as a Calibre metadata source in a separate ZIP, `EpubPromoScrubberSource.zip` (Calibre allows only one plugin per ZIP). The source **Promo Scrubber** has no external catalogue: it returns the book's own title with promo-site marks (OceanofPDF, PDFDrive, …) removed, using the same patterns and extra patterns as the scrubber, and returns nothing when the title is already clean. It touches the title only. Install it alongside the scrubber and enable it in Calibre's Download metadata source list.
