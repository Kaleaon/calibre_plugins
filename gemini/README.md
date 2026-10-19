# Gemini Library Fixer

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

Asks the Google Gemini API to suggest cleaner metadata for the selected books (or the whole library), shows every suggestion, and applies only the ones you leave ticked.

You need a Google AI API key. A **free key from [Google AI Studio](https://aistudio.google.com/apikey) is enough**; no billing or prepaid credit is needed. Paste it into the window once (it is remembered), or set `GEMINI_API_KEY` before starting Calibre.

## Reading the covers

Tick **Also look at the cover pictures** and Gemini is shown pictures as well as the text details, and reads what is printed on them: title, author, series and volume number on the front, and the blurb, publisher, language and ISBN on the back. Printed text beats a messy file name, so this helps most with books named `scan_0001` or `untitled`. What it read is shown in the log (📖) next to the change it led to. It does not identify a book from artwork alone.

- **Front cover:** the one Calibre has for the book.
- **Back cover:** Calibre keeps only a front cover, so a back cover is found only inside the book file: an EPUB image whose name contains `back` or `rear` (for example `back-cover.jpg`), or the last page of a CBZ comic. Books with neither are checked with the front cover alone. Other formats (PDF, MOBI) are not searched for a back page.
- **Cost:** pictures are shrunk to at most 1000 pixels before sending, and requests carry at most 8 books while this is on. They still use noticeably more of a free key's limit; leave the option off for a first pass.
- **Privacy:** the pictures are sent to Google. It is off by default.

## Reviewing the suggestions

Results appear in a large list that shares the window with the activity log (drag the divider between them to resize either). The settings fold away while it works; **Show settings** brings them back. **Check all**, **Check none** and **Check all except big changes** tick everything at once, and the count beside them shows how many changes are ticked. Each book and each single change can still be ticked on its own.

## Batched processing and crash prevention

Large libraries (or the entire library) are processed in batches of 10 books at a time (configurable in settings) rather than loaded all at once in one large go:
- **Low memory consumption:** Covers and excerpt texts are loaded for only 10 books at a time and freed from memory immediately after each batch is processed.
- **Separate batch output saving:** Every completed batch's suggestions and skipped items are saved separately to disk (`batch_0001.json`, `batch_0002.json`, ...) right as it finishes.
- **Compiled together:** When all batches complete (or if cancelled or stopped by an error), all separate batch outputs are automatically compiled together into the unified suggestions preview.
- **Crash & quota resilience:** If a run is interrupted by network failure, daily quota limits, or an app crash, your completed batches are safe on disk.
- **Resume & compile saved:** When starting a check, the plugin detects previously saved batches and offers to resume remaining books or start fresh. You can also click **Compile saved batches** at any time to reload all saved batches from disk.

## Using the free tier

Free keys allow only a small number of requests per minute and per day, and Google sets and changes those numbers per model, so the plugin does not assume them:

- **Spacing:** *Seconds between requests* (default 7) leaves a gap between requests so a run does not hit the per-minute limit. Set it to 0 if your key is on a paid plan. Sending books in batches of 10 (the default) keeps requests manageable and reliable.
- **Per-minute limit hit anyway:** the plugin waits as long as Google's answer says (shown in the activity log, and Cancel still works) and tries again.
- **Daily limit used up:** the plugin stops asking, **keeps the suggestions already found**, and reports how many books were not checked. Run it again after the daily reset on the remaining books, or pick a lighter model with **List models** (names containing "flash-lite" are usually allowed more requests; check Google's current limits).
- **Privacy on the free tier:** Google's terms for the free tier may allow it to use what you send to improve its products, unlike paid use. I have not verified the current wording, so read [Google's Gemini API terms](https://ai.google.dev/gemini-api/terms) before sending books you consider private.

## Progress and activity log

While it works the window shows a progress bar (books read, then books answered), what it is doing right now (including seconds waited on Google), and a log with each book as it is read, the exact details being sent to Google for it, and every answer that comes back: ✔ a suggested change, – no change needed, ✖ dropped or failed. **Cancel** stops after the request already in flight.

## What it can fix

- **Titles**: removes junk such as `(z-lib.org)`, `[epub]`, file extensions, underscores used as spaces; fixes ALL CAPS / all lower case.
- **Numbered titles**: a title such as `book 1 - the force awakens` (also `Vol. 2: …`, `Part 3 - …`, `#4. …`) loses the marker and becomes `The Force Awakens`, with the number stored as the volume number. The plugin spots these titles itself and tells the model, and books by the same author are sent together so the series name can be recognised from sibling volumes. If the series name cannot be worked out, the title and number are still fixed and the series is left for you to set.
- **Authors**: turns `Last, First` or one joined string into separate `First Last` authors, and will not write "Unknown" back.
- **Series and volume number**: reads them from the title or file name (`Dune Messiah (Dune #2)` becomes series `Dune`, number 2), which also fixes the sort order inside the series.
- **Tags**: merges duplicates and fixes case. By default it can only *add* tags (at most 5 per book); tick *Let it remove or rename existing tags* to allow cleanup.
- **Language**: filled in only when the book has none.
- **ISBN**: read from a back cover or title page picture and added as the book's `isbn` identifier. It is only offered if the check digit is correct, so a misread number is thrown away rather than written; your other identifiers are kept.

"Rename" and "sort": when a title or author changes, Calibre itself renames the book's folder and files in your library and recomputes the title and author sort values (I checked in Calibre's source that its title and author writers do this). The plugin does not touch file names any other way.

## Safety

- **Preview first.** Nothing changes until you press **Apply ticked changes**. Each book and each field has its own checkbox.
- **Big changes are unticked.** If a new title or author looks like a different book rather than a tidy-up, it is marked BIG CHANGE and left unticked.
- **Confidence filter.** The model rates each book low/medium/high; suggestions below your minimum (default medium) are dropped. Answers are also checked again by the plugin: ids it was not asked about are ignored, lengths and values are limited, and a suggestion equal to the current value is not shown.
- **Undo last run.** The old values of everything applied are saved, and **Undo last run** puts them back (including the old file names). Only the most recent run is kept.
- It never touches book files' contents, covers or descriptions.

## What is sent to Google

Per book: title, authors, series, tags, publisher, language and file names. The first ~1500 characters of an EPUB are sent only if you tick that option. Nothing is sent until you press **Check books**. Your key is remembered in Calibre's settings (saved as soon as you type it and when the window closes, so you enter it once; stored unencrypted in the settings folder) and is sent only to Google, in a request header.

Google's safety filter can refuse requests with adult content. When a batch is refused, the plugin splits it to find the book responsible and reports that book as "could not be checked"; the rest still work. It does not change Google's safety settings.

## Model

The default model is `gemini-2.5-flash`; **List models** asks Google which models your key can use, and you can type any model name. Requests that hit a rate limit or a server error are retried a few times with a growing pause, then reported.

## Not verified

The request and response handling is covered by offline tests that replace Google with canned answers (rate limits and Google's retry delay, per-day quota, request spacing, blocked prompts, truncated output, bad keys, batches, cancel, numbered titles, ISBN check digits, picture parts in the request, finding a back cover in EPUB and CBZ files). It has **not** been run against the live Gemini API (no key was available), so how well the model handles your particular books, and how well it reads real covers, is untested; the picture tests use plain generated images. The window was run under real Qt (PyQt6, offscreen) against a stand-in library for check, progress log, cancel, apply and undo, but not inside Calibre; the Calibre calls used (`get_metadata`, `formats`, `format_abspath`, `set_field`, `refresh_ids`) were checked against Calibre's source. Try it on two or three books first.
