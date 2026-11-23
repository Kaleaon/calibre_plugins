# Claude Library Fixer

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

The Muse counterpart of the Gemini Library Fixer: it asks the **Anthropic Muse API** to suggest cleaner metadata for the selected books (or the whole library), shows every suggestion, and applies only the ones you leave ticked. Everything the Gemini fixer does — preview, per-field checkboxes, batched processing with saved batches and resume, confidence filtering, big-change protection, undo — works the same way here; only the model behind it is different.

You need an **Anthropic API key** from the [Anthropic Console](https://console.anthropic.com/settings/keys). Unlike Gemini there is no free tier: API use is billed per token (new accounts may have starter credit — check the Console). Paste the key into the window once (it is remembered), or set `ANTHROPIC_API_KEY` before starting Calibre.

## Reading the covers

Tick **Also look at the cover pictures** and Muse is shown pictures as well as the text details, and reads what is printed on them: title, author, series and volume number on the front, and the blurb, publisher, language and ISBN on the back. Printed text beats a messy file name, so this helps most with books named `scan_0001` or `untitled`. What it read is shown in the log (📖) next to the change it led to. It does not identify a book from artwork alone.

- **Front cover:** the one Calibre has for the book.
- **Back cover:** Calibre keeps only a front cover, so a back cover is found only inside the book file: an EPUB image whose name contains `back` or `rear` (for example `back-cover.jpg`), or the last page of a CBZ comic. Books with neither are checked with the front cover alone. Other formats (PDF, MOBI) are not searched for a back page.
- **Cost:** pictures are shrunk to at most 1000 pixels before sending, and requests carry at most 8 books while this is on. They still cost noticeably more tokens; leave the option off for a first pass.
- **Privacy:** the pictures are sent to Anthropic. It is off by default.

## Reviewing the suggestions

Results appear in a large list that shares the window with the activity log (drag the divider between them to resize either). The settings fold away while it works; **Show settings** brings them back. **Check all**, **Check none** and **Check all except big changes** tick everything at once, and the count beside them shows how many changes are ticked. Each book and each single change can still be ticked on its own.

## Batched processing and crash prevention

Large libraries (or the entire library) are processed in batches of 10 books at a time (configurable in settings) rather than loaded all at once in one large go:

- **Low memory consumption:** Covers and excerpt texts are loaded for only 10 books at a time and freed from memory immediately after each batch is processed.
- **Separate batch output saving:** Every completed batch's suggestions and skipped items are saved separately to disk (`batch_0001.json`, `batch_0002.json`, ...) right as it finishes.
- **Compiled together:** When all batches complete (or if cancelled or stopped by an error), all separate batch outputs are automatically compiled together into the unified suggestions preview.
- **Crash & limit resilience:** If a run is interrupted by network failure, rate limits, or an app crash, your completed batches are safe on disk.
- **Resume & compile saved:** When starting a check, the plugin detects previously saved batches and offers to resume remaining books or start fresh. You can also click **Compile saved batches** at any time to reload all saved batches from disk.

## Rate limits

Anthropic limits requests and tokens per minute, by usage tier:

- **Spacing:** *Seconds between requests* (default 2) leaves a gap between requests so a run stays under your tier's limits. Raise it if you are on a low tier and see rate-limit pauses; set it to 0 on a high tier.
- **Per-minute limit hit anyway:** the plugin waits as long as Anthropic's answer says (shown in the activity log, and Cancel still works) and tries again.
- **Limit or billing stop:** the plugin stops asking, **keeps the suggestions already found**, and reports how many books were not checked. Run it again later on the remaining books, or pick a lighter model with **List models** (Haiku models are the cheapest and usually allowed the most requests).

## Progress and activity log

While it works the window shows an informative progress bar displaying the book being processed or fixed and percentage completion, a status line of what it is doing right now (including book titles, batches, and seconds waited on Anthropic), and a live log with each book as it is read, the exact details being sent to Anthropic for it, and every answer that comes back: ✔ a suggested change, – no change needed, ✖ dropped or failed.

When you click **Apply ticked changes**, the progress bar and status line show real-time progress as each book is being fixed in your library, including metadata embedding and file renaming. **Cancel** stops after the request already in flight.

## What it can fix

- **Titles**: removes junk such as `(z-lib.org)`, `[epub]`, file extensions, underscores used as spaces; fixes ALL CAPS / all lower case.
- **Numbered titles**: a title such as `book 1 - the force awakens` (also `Vol. 2: …`, `Part 3 - …`, `#4. …`) loses the marker and becomes `The Force Awakens`, with the number stored as the volume number. The plugin spots these titles itself and tells the model, and books by the same author are sent together so the series name can be recognised from sibling volumes. If the series name cannot be worked out, the title and number are still fixed and the series is left for you to set.
- **Authors**: turns `Last, First` or one joined string into separate `First Last` authors, and will not write "Unknown" back.
- **Series and volume number**: reads them from the title or file name (`Dune Messiah (Dune #2)` becomes series `Dune`, number 2), which also fixes the sort order inside the series.
- **Tags**: merges duplicates and fixes case. By default it can only *add* tags (at most 5 per book); tick *Let it remove or rename existing tags* to allow cleanup.
- **Language**: filled in only when the book has none.
- **ISBN**: read from a back cover or title page picture and added as the book's `isbn` identifier. It is only offered if the check digit is correct, so a misread number is thrown away rather than written; your other identifiers are kept.

"Rename" and "sort": when a title or author changes, Calibre itself renames the book's folder and files in your library and recomputes the title and author sort values. The plugin does not touch file names any other way.

## Safety

- **Preview first.** Nothing changes until you press **Apply ticked changes**. Each book and each field has its own checkbox.
- **Big changes are unticked.** If a new title or author looks like a different book rather than a tidy-up, it is marked BIG CHANGE and left unticked.
- **Confidence filter.** The model rates each book low/medium/high; suggestions below your minimum (default medium) are dropped. Answers are also checked again by the plugin: ids it was not asked about are ignored, lengths and values are limited, and a suggestion equal to the current value is not shown.
- **Undo last run.** The old values of everything applied are saved, and **Undo last run** puts them back (including the old file names). Only the most recent run is kept.
- It never touches book files' contents, covers or descriptions.

## What is sent to Anthropic

Per book: title, authors, series, tags, publisher, language and file names. The first ~1500 characters of an EPUB are sent only if you tick that option. Nothing is sent until you press **Check books**. Your key is remembered in Calibre's settings (saved as soon as you type it and when the window closes, so you enter it once; stored unencrypted in the settings folder) and is sent only to Anthropic, in a request header.

Anthropic can refuse requests with adult content. When a batch is refused, the plugin splits it to find the book responsible and reports that book as "could not be checked"; the rest still work.

## Model

The default model is `claude-sonnet-4-5`, with `claude-haiku-4-5` as the automatic lighter fallback; **List models** asks Anthropic which models your key can use, and you can type any model name. Requests that hit a rate limit or a server error are retried a few times with a growing pause, then reported.

## Not verified

The request and response handling is covered by offline tests that replace Anthropic with canned answers (rate limits and retry delay, request spacing, refusals, truncated output, bad keys, batches, cancel, numbered titles, ISBN check digits, picture blocks in the request, finding a back cover in EPUB and CBZ files). It has **not** been run against the live Anthropic API or inside Calibre yet. Try it on two or three books first.

## Metadata source

This plugin also ships as a Calibre metadata source in a separate ZIP, `ClaudeMetadataSource.zip` (Calibre allows only one plugin per ZIP). The source **Claude Metadata** answers Download metadata requests two ways: provider enrichment first (when enabled in the fixer's settings, books whose identifiers point at Metabods, Nifty, a FanFicFare site or Open Library get that provider's own metadata), then — when an Anthropic API key is saved — a validated Muse cleanup of the book's title, authors, series, tags, language and ISBN, using the fixer's model and field settings. Install it alongside the fixer and enable it in Calibre's Download metadata source list.
