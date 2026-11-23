# Muse Library Fixer

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

The Muse counterpart of the Gemini Library Fixer: it asks **Muse (Meta AI)** to suggest cleaner metadata for the selected books (or the whole library), shows every suggestion, and applies only the ones you leave ticked. Everything the Gemini fixer does — preview, per-field checkboxes, batched processing with saved batches and resume, confidence filtering, big-change protection, undo — works the same way here; only the assistant behind it is different.

## How Muse gets the books (no API key)

Muse has no API key to paste in. Instead, when you press **Check books** the plugin writes each batch as a **request file** into an exchange folder and waits for Muse's **answer file**:

- **Exchange folder:** by default a folder named `.muse-fixer` **inside your library folder**, with `pending` and `done` subfolders. (You can point the *Muse exchange folder* field somewhere else, or set `MUSE_FIXER_EXCHANGE_DIR` before starting Calibre.)
- **Request:** `pending/<id>.json` — the batch's books, the fix instructions, and where the answer belongs.
- **Answer:** Muse writes `done/<id>.json` — a JSON array of suggestions (the format is described inside the request file itself). The plugin validates every answer exactly like the Gemini/Claude fixers before showing it.
- **Reaching Muse:** because the exchange folder lives inside the library, Muse can pick requests up through the **Calibre Link** bridge plugin (its file endpoints reach the whole library folder) — so with Calibre Link running, the loop is automatic: Check books → Muse answers → suggestions appear. Without the bridge, hand the request file to Muse any way you like (open the folder from the window's *Check folder* button) and drop the answer into `done`.
- **Waiting:** the window waits up to 30 minutes per batch (the *wait_minutes* setting), showing a note every minute. **Cancel** stops waiting; the request stays in `pending`, so a later run — or Muse answering late — is never wasted work. If the wait runs out, the run pauses with the suggestions already found kept, exactly like a rate-limit pause in the other fixers.

There is no bill and no rate limit: Muse is your assistant, not a metered API.

## Reading the covers

Tick **Also look at the cover pictures** and the request includes pictures as well as the text details, and Muse reads what is printed on them: title, author, series and volume number on the front, and the blurb, publisher, language and ISBN on the back. Printed text beats a messy file name, so this helps most with books named `scan_0001` or `untitled`. What it read is shown in the log (📖) next to the change it led to. It does not identify a book from artwork alone.

- **Front cover:** the one Calibre has for the book.
- **Back cover:** Calibre keeps only a front cover, so a back cover is found only inside the book file: an EPUB image whose name contains `back` or `rear` (for example `back-cover.jpg`), or the last page of a CBZ comic. Books with neither are checked with the front cover alone. Other formats (PDF, MOBI) are not searched for a back page.
- **Size:** pictures are shrunk to at most 1000 pixels before they go into the request, and requests carry at most 8 books while this is on.
- **Privacy:** the pictures leave your machine only in the request file, for Muse. It is off by default.

## Reviewing the suggestions

Results appear in a large list that shares the window with the activity log (drag the divider between them to resize either). The settings fold away while it works; **Show settings** brings them back. **Check all**, **Check none** and **Check all except big changes** tick everything at once, and the count beside them shows how many changes are ticked. Each book and each single change can still be ticked on its own.

## Batched processing and crash prevention

Large libraries (or the entire library) are processed in batches of 10 books at a time (configurable in settings) rather than loaded all at once in one large go:

- **Low memory consumption:** Covers and excerpt texts are loaded for only 10 books at a time and freed from memory immediately after each batch is processed.
- **Separate batch output saving:** Every completed batch's suggestions and skipped items are saved separately to disk (`batch_0001.json`, `batch_0002.json`, ...) right as it finishes.
- **Compiled together:** When all batches complete (or if cancelled or stopped by an error), all separate batch outputs are automatically compiled together into the unified suggestions preview.
- **Crash & wait resilience:** If a run is interrupted — Calibre closed, a crash, or Muse not answering in time — your completed batches are safe on disk.
- **Resume & compile saved:** When starting a check, the plugin detects previously saved batches and offers to resume remaining books or start fresh. You can also click **Compile saved batches** at any time to reload all saved batches from disk.

## Progress and activity log

While it works the window shows an informative progress bar displaying the book being processed or fixed and percentage completion, a status line of what it is doing right now (including book titles, batches, and minutes waited on Muse), and a live log with each book as it is read, the exact details being handed to Muse for it, and every answer that comes back: ✔ a suggested change, – no change needed, ✖ dropped or failed.

When you click **Apply ticked changes**, the progress bar and status line show real-time progress as each book is being fixed in your library, including metadata embedding and file renaming. **Cancel** stops after the batch already in flight.

## What it can fix

- **Titles**: removes junk such as `(z-lib.org)`, `[epub]`, file extensions, underscores used as spaces; fixes ALL CAPS / all lower case.
- **Numbered titles**: a title such as `book 1 - the force awakens` (also `Vol. 2: …`, `Part 3 - …`, `#4. …`) loses the marker and becomes `The Force Awakens`, with the number stored as the volume number. The plugin spots these titles itself and tells Muse, and books by the same author are sent together so the series name can be recognised from sibling volumes. If the series name cannot be worked out, the title and number are still fixed and the series is left for you to set.
- **Authors**: turns `Last, First` or one joined string into separate `First Last` authors, and will not write "Unknown" back.
- **Series and volume number**: reads them from the title or file name (`Dune Messiah (Dune #2)` becomes series `Dune`, number 2), which also fixes the sort order inside the series.
- **Tags**: merges duplicates and fixes case. By default it can only *add* tags (at most 5 per book); tick *Let it remove or rename existing tags* to allow cleanup.
- **Language**: filled in only when the book has none.
- **ISBN**: read from a back cover or title page picture and added as the book's `isbn` identifier. It is only offered if the check digit is correct, so a misread number is thrown away rather than written; your other identifiers are kept.

"Rename" and "sort": when a title or author changes, Calibre itself renames the book's folder and files in your library and recomputes the title and author sort values. The plugin does not touch file names any other way.

## Safety

- **Preview first.** Nothing changes until you press **Apply ticked changes**. Each book and each field has its own checkbox.
- **Big changes are unticked.** If a new title or author looks like a different book rather than a tidy-up, it is marked BIG CHANGE and left unticked.
- **Confidence filter.** Muse rates each book low/medium/high; suggestions below your minimum (default medium) are dropped. Answers are also checked again by the plugin: ids it was not asked about are ignored, lengths and values are limited, and a suggestion equal to the current value is not shown.
- **Undo last run.** The old values of everything applied are saved, and **Undo last run** puts them back (including the old file names). Only the most recent run is kept.
- It never touches book files' contents, covers or descriptions.

## What is handed to Muse

Per book, in the request file: title, authors, series, tags, publisher, language and file names. The first ~1500 characters of an EPUB are included only if you tick that option. Nothing is written until you press **Check books**. No key or account is involved anywhere.

If Muse's answer file says a batch is blocked (for example a book it cannot process), the plugin splits the batch to find the one responsible and reports that book as "could not be checked"; the rest still work.

## Verified so far

The exchange loop is covered by offline tests that play Muse's part (a watcher thread answering request files), including a full round trip, timeout keeping the request, cancel, blocked answers, and the same validation spot checks as the other fixers — and by a live smoke run where the request file was answered by Muse itself and the validated suggestions came back. It has **not** been run inside Calibre yet (the Qt window test skips on machines without Qt system libraries, like the Gemini/Claude ones). Try it on two or three books first, ideally with Calibre Link running so Muse can reach the exchange folder.
