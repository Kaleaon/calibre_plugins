# Metabods Downloader

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

Stories are copyrighted by their authors, and the sites ask that they not be reposted, so this is meant for personal reading copies. Requests are throttled and run in a background thread with a Cancel button; whatever was fetched before a cancel is still imported. No cover is added (use Calibre's *Generate cover*).

It builds EPUBs directly from the site's HTML print version (`story_print.php`, the page behind "Print / PDF") rather than converting the PDF, so text, italics and part structure come through cleanly and no PDF conversion step is needed.

- **Download stories by link…**: paste story links, bare story ids, or list pages (author, tag, category, archive), one per line.
- **Search by tag…**: browse the site's tag list with a filter box, check tags, choose *any* or *all* matching, then tick the stories you want. **★ Toggle favorite** saves a highlighted tag; favorites sort first, can be filtered with *Favorites only*, and **Check all favorites** selects them in one click. Favorites and the cached tag list live in Calibre's plugin settings (*Refresh tag list* re-reads the site).
- **Multi-part stories**: all parts of a story are one download and become chapters of a single EPUB with a table of contents. **Combine everything into one book** merges several separate stories into one omnibus EPUB with one nested contents entry per story.
- Imported books get title, author, site tags and category as Calibre tags, the site summary as the comment, publisher *Metabods*, and a `metabods:<story id>` identifier, which **Skip stories already in this library** uses to avoid duplicates. Combined books carry no identifier.
- Images, scripts and most inline styling are dropped; italics, bold, line breaks, indents and centering are kept.

## Metadata source

This plugin also ships as a Calibre metadata source in a separate ZIP, `MetabodsMetadataSource.zip` (Calibre allows only one plugin per ZIP). The source **Metabods** answers Download metadata requests with the site's own story metadata — author, tags, categories and summary — for books carrying a `metabods` story id or a story URL, and can also find stories by author through the site's author index (Metabods offers no title search). Install it alongside the downloader and enable it in Calibre's Download metadata source list.
