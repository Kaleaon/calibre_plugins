# Inkbunny & SoFurry Downloader

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

Stories are copyrighted by their authors; this is for personal reading copies. Requests are throttled (about one per second) and run in a background thread with a Cancel button; whatever was fetched before a cancel is still imported.

Paste one address per line, press **Fetch story list**, untick anything you do not want, then **Download checked stories**:

- **A story**: `https://inkbunny.net/s/1234567` (or the older `submissionview.php?id=…`) or `https://sofurry.com/s/z1V1z3Zn`.
- **An Inkbunny pool**: `https://inkbunny.net/poolview_process.php?pool_id=1234` (the address the site's pool links use today; the resolved `submissionsviewall.php?mode=pool&pool_id=…` form and the old `pools.php?pool_id=…` spelling are also accepted) — every writing submission in the pool, **compiled into one book** in pool order, titled after the pool.
- **A SoFurry folder**: `https://sofurry.com/u/author/gallery?folder=abcd1234` — every story in the folder, **compiled into one book**, oldest first, titled after the folder.
- **An author**: an Inkbunny member page or gallery (`inkbunny.net/SomeAuthor`, `inkbunny.net/gallery/SomeAuthor`) or a SoFurry member page (`sofurry.com/u/handle`, `handle.sofurry.com`) — all of that author's writing, as separate books unless you tick *Combine everything into one book*.

Options: skip stories already in the library (a compiled pool/folder book also counts its members as present), combine everything fetched into one book with your own title, and a chapter range (`1-5, 8, 10+`) that applies to each story's own files/chapters.

## Inkbunny access

Inkbunny is read through its [official API](https://wiki.inkbunny.net/wiki/API). With no login the plugin uses the API's **guest** session, which sees what a guest sees on the website (public submissions within the guest rating limits — adult-rated writing needs an account). To get everything your account can see, enter your Inkbunny username and password in the dialog: the account must have **Enable API access** ticked under its Inkbunny account settings, or the login is refused. The username and password are **saved** in the plugin's preferences (plain text in calibre's config folder on your machine, like other calibre plugins) so you do not have to re-enter them; they are used only to open that session with Inkbunny. Clear the fields and fetch once to forget them. Only *Writing - Document* submissions (API type 12, which includes poetry) carry story text. The API returns the story text itself (it is auto-filled from the first `.doc`/`.txt` file a submission carries); a multi-file submission is read file by file as chapters instead, and writing that exists only in the description (some poetry) is read from there. A submission whose text the API cannot extract and which has no text file is reported, not silently dropped.

## SoFurry access

SoFurry is read from the **current** site (sofurry.com): a story's data endpoint gives its metadata and its content files (`.txt`/`.html` chapters are fetched from the file addresses the site hands out, which expire in about ten minutes — a failed chapter with an expired-address message just needs a retry), and folders/galleries come from the site's profile listing API. Public stories and folders need no login. **Login-only stories cannot be read yet** — there is no SoFurry login in this plugin; they fail with a message saying so rather than a blank book. Old `www.sofurry.com/view/123456` addresses are resolved automatically when the old id is a real submission (the site redirects those to the current `/s/…` address); one that does not resolve is reported with that advice. One quirk: the story data endpoint carries tag *ids*, not names, so a story pasted alone gets no tags — stories grabbed through a folder or author list keep the tag names the listing provides.

## Metadata and limits

- Title, author, description (as the summary), rating and type (as categories), and the posting date come from the site; Inkbunny keywords and SoFurry tags become Calibre tags. Publisher is *Inkbunny* or *SoFurry*.
- Identifiers: `inkbunny:<submission id>` or `sofurry:<story id>` for single stories; a compiled pool/folder book carries `inkbunny:pool/<id>` or `sofurry:folder/<id>`, which is what the skip-existing check matches against.
- Covers are the suite's generated covers (the sites' cover/thumbnail images are not imported).
- Multi-file Inkbunny submissions and multi-chapter SoFurry stories keep their file/chapter titles as chapter headings when there is more than one.
- Not run inside Calibre yet, and not yet verified against the live sites from the build environment — see the status note on the [main page](../README.md). The parsing, API handling, turbo-stream decoding, EPUB building and grouping are covered by offline tests with canned site answers (`tests/test_furry.py`).
