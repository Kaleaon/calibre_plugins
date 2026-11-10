# Reddit Story Follower

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

Follows story series that are posted on Reddit, such as chapters in r/HFY, as a **single EPUB per series that grows** as new chapters appear. Each matching post becomes a chapter, oldest first, with a contents list. When new posts arrive the book in your library is updated in place.

## Using it

1. Click **Reddit stories → Followed stories… → Add…**
2. **Book title**: what the book will be called, for example *Out of Cruel Space*.
3. **Where to look**: one of
   - a subreddit: `r/HFY`
   - an author: `u/name` (their submitted posts)
   - a Reddit search address: search the series name inside the subreddit on Reddit, then paste the address of the results page. This is the best way to follow one series in a busy subreddit.
4. **Title contains** (and optionally **Posted by**): keeps only the posts that belong to the series, for example `Out of Cruel Space`. Start the text with `re:` for a regular expression, such as `re:chapter\s+\d+`.
5. Press **Test selected** to make one request and see how many posts came back and how many match your filters (nothing is saved or changed), then **Check selected now**. The first check reads back through the feed; later checks read only until they reach posts they have already seen.

**Out of Cruel Space** is by u/KyleKKent on r/HFY; its first post is titled "Out of Cruel Space, Part 1" (2021-05-19). The quickest way to follow it is **Quick start → Out of Cruel Space (r/HFY, by KyleKKent)** in the Add dialog, which fills in: book title *Out of Cruel Space*, where to look `u/KyleKKent`, title contains `Out of Cruel Space`, posted by `KyleKKent`. Reading that real first post with the plugin gave the full 12,800-character chapter and removed the trailing "Next" link. I did not fetch the rest of the series, so how many parts there are, and whether the author's list reaches all of them, is unconfirmed.

## Automatic updates

While Calibre is open, the plugin looks every 15 minutes at which followed stories are due (this costs nothing) and quietly checks the ones that are, in a background thread. A status-bar note appears when a book was updated. Your **settings** control this:

- **Check each story at most every N hours**: default 6, never shorter than 1 hour.
- Automatic checking can be switched off; **Check for new chapters now** always works.
- There is **no background service**: nothing is checked while Calibre is closed. The first automatic check runs about two minutes after Calibre starts.
- If Reddit answers HTTP 429 (slow down), the run stops at once, keeps what it already read, and leaves Reddit alone for at least 15 minutes (longer if Reddit says so). It never retries into a rate limit.

If you delete a followed book from your library, the next check rebuilds it from the chapters the plugin has kept (in Calibre's `plugins/reddit_follower_cache` folder). Removing a story from the list deletes its kept chapters but never the book.

**Settings → Test connection** makes one request to r/HFY with whatever is on screen and tells you whether it worked, without saving anything. Use it, and **Test selected**, to find out on your own computer how Reddit treats the feed or your API credentials: Reddit rate-limits shared and cloud networks much more than a home connection, which is what I ran into while testing.

## Log in with Reddit

In **Settings**, choose the official API, enter your app's client id (and secret, if it is a "web app"), and press **Log in with Reddit…**. Your browser opens Reddit's own approval page; after you approve, Reddit sends the browser back to a small listener on your own computer (`127.0.0.1:8844` only), the plugin trades the one-time code for a login token, and the window says you can close the tab. Your password never reaches the plugin. Only the token is saved (in Calibre's plugin settings on this computer), with read-only scope. **Log out** asks Reddit to cancel it and removes it; you can also remove it at reddit.com/prefs/apps.

Register the app at reddit.com/prefs/apps with the redirect uri exactly `http://127.0.0.1:8844/callback`. Anyone who gets your client secret or saved token could act as that app or read as you, so never paste them into chats, issues or commits; if you have, make a new secret there.

Whether Reddit currently lets you create such an app without approval, and how it treats this login, is not something I could check; this flow is written to Reddit's documented OAuth2 and tested against a stand-in server, not the real service.

## Finding stories and whole series (r/HFY and similar)

**Reddit stories → Find stories…** (also a button in the followed-stories window) searches or browses a subreddit and groups what it finds into series:

- Choose the subreddit (HFY by default), optional search words, and an order: best match for the words, top of all time / this year / this month, or newest. *Flair contains* narrows to a flair such as `OC-FirstOfSeries` (official API only).
- Posts are grouped by **author and title name**, reading part numbers in the shapes HFY uses: `Name 14`, `Name, Part 14`, `Name - Chapter 14`, `Name (14)`, `Name #14`, `Name VIII` (capital Roman numerals), with `[OC]` or `[Universe]` tags in front ignored. Two different part numbers, or a *series* flair on two posts, make it a series; the rest are listed as single stories. The same name by two authors stays two series.
- **Follow the whole series…** then reads the author's own posts back through their history (up to 3,000 posts at a time) and shows how many parts it found and from when to when, with the first and last titles. If you go on, the usual Add dialog opens filled in (the author, and a title filter that matches the name with any tags in front) so you can change it, and the series is collected as one growing book.

Checked on the real r/HFY: the top-of-all-time list showed 121 parts of *The Nature of Predators* (SpacePaladin15), and following it found 284 (2022-04-11 to 2025-01-04, ending "2-99 [Final]"); *Salvage* (Rantarian, Jenkinsverse) showed 86 and following found 89, 2014 to 2020, from the first post to "Chapter 100" (11 numbers have no post under that name, probably titled differently or posted by someone else).

**Adding the top rated.** The results are listed best rated first, with a *Rating* column (the total score of that series' posts that were found; ratings need the official API, the public feeds carry no scores). Choose *Order: Top of all time* (or this year, or this month), set *Pages to look through* (each is up to 100 posts and one request), then **Add top rated…**: it picks the highest rated real series with at least the number of parts you set (3 by default), skips any you already follow, shows the list for you to confirm, and follows them all, each collected in full from its author's posts (this can take a few minutes; Cancel keeps what was read and the next check carries on). On the real r/HFY top of all time (500 posts looked through) the leaders were *The Nature of Predators* (rating about 834,000, 139 parts seen), *Why Humans Avoid War* (about 211,000, 27 parts), *A job for a deathworlder*, *The New Species* and *Jennifer is NOT an Eldritch Horror*. A series' rating counts only the posts that fall inside the pages looked through, so a series with a few huge posts can outrank a longer, steadier one.

What it cannot do: it knows a series only by its author and title, so a series retitled partway still needs the *Title contains* box adjusted by hand — with one exception: a retitled series whose chapter index proves the two titles are one series (Out of Cruel Space, whose index runs in one continuous sequence across the rename, see *Chapter index* below) is grouped as a single series here, and following it uses the ready-made follow that matches both titles and carries the index, instead of creating one book per title. A series with tag-only titles such as `[Jenkinsverse] 5.` shows up as pieces; and parts posted by several authors are not joined. Search and browse use Reddit's search, which returns only the first pages (the dialog looks through three).

## Chapter titles and author's notes from the post itself

Out of Cruel Space opens every post with its chapter title, such as `The Pirates & The Bounty Hunters` (Part 59), `Capes and Conundrums` or `Danger Zone!`, followed by an empty line. Tick *The first line of each post is its chapter title* (on in the Quick start) and the plugin:

- names the chapter from it: `Chapter 059 – The Pirates & The Bounty Hunters` with the chapter index, `Out of Cruel Space, Part 59 – The Pirates & The Bounty Hunters` without it;
- takes the line out of the text so it is not printed twice, and drops the empty zero-width paragraphs (these blank paragraphs are removed from every chapter now);
- drops comment-race markers such as `~First~`, and turns a note to readers in brackets, for example `(I am so sorry, my brain clunked HARD and remained BLANK today.)`, into a boxed **Author's note** at the top of the chapter. The author's own comment (the optional setting above) is a boxed **Author's comment** at the end.

A line counts as the title only if it is short (80 characters or fewer), does not end like a sentence, is not a quotation and has no brackets; a title ending in `!` must be capitalised like a title (`Danger Zone!`, but not `Run!`). Checked on the real series: 1,748 of 1,802 posts have a title line (the first ~45 chapters mostly have none and keep their `Part N` name), 76 distinct titles, 328 posts with a bracketed note. The titles repeat (`A Scion of Many Worlds` heads 181 chapters, `The Bounty Hunters` 172), so they work as a storyline name more than a unique chapter title; the index's chapter number still tells chapters apart. A note that shares a paragraph with a marker (`~First~ (note)`) is left in the text, and the box styling depends on your e-reader supporting the book's stylesheet.

## Chapter index (a public spreadsheet)

*Chapter index* in the Add/Edit dialog takes the address of a public Google Sheet that lists a series' chapters with the columns **Date, Author, Chapter** and (optionally) **Note**. The plugin reads it (at most once a day, as CSV; the sheet must be shared so anyone with the link can view it) and:

- names each collected chapter from it, such as `Chapter 001 – Pirates` or `Into A Wider Galaxy, Chapter 010 – AAA` (the Note column is the storyline), instead of `…Part 1010`;
- pairs posts with rows by chapter number and date, then by posting time, so **mistitled posts are fixed** (the real Out of Cruel Space has `Part 1010` for Chapter 10 and `Part 365` for Chapter 635);
- tells you in the status column when the index lists a chapter Reddit has no post for (a missing chapter) or the other way round. A post the index does not list yet keeps its own title.

A chapter is identified by more than its number. The numbering **restarts** when a series is retitled — Out of Cruel Space's `Chapter 001` (2021) and `Into A Wider Galaxy, Chapter 001` (2024) are different chapters — so a post that names the newer title only takes a row from that era, told apart by the label's part before "Chapter". Suffixed rows are separate posts, not duplicates, and are told apart by their suffix and their posting time: the real sheet has `Into A Wider Galaxy, Chapter 030 Plot` and `Chapter 030 [NSFW]` two minutes apart, and `Chapter 097-A [NSFW]` / `Chapter 097-B SFW` likewise. When the sheet has **Seq** and **Short Link** columns the plugin reads those too (`OOCS 001`, `IAWG 030-P`); the Seq is the sheet's own continuous sequence, and for this series it runs unbroken across the rename (OOCS 999 is Seq 1528, IAWG 001 is Seq 1529), which is the proof the two titles are one series. Dates are mostly `2021/05/19 04:30`, but an occasional row in the ISO form (`2026-07-19T20:44`, the sheet's IAWG Chapter 726) is read too — it used to be dropped, which lost that chapter's index entry.

The Quick start for Out of Cruel Space includes the community's sheet (an extension of Kerserv's archive, which the author links in his comments). Checked against it: 1,800 rows for KyleKKent, every one matched to a Reddit post, 65 storylines, and two Reddit posts not yet in the sheet (the newest chapter and a second post numbered 726). Read again in November 2026, the sheet lists 2,853 rows in all (Seq 1–2853), 1,825 of them KyleKKent's, with Into A Wider Galaxy up to Chapter 823; it also covers other authors' side stories and fan works (for example 695 by KamchatkasRevenge), which is likely where a count above 2,000 comes from; the plugin follows only the author you name. If the sheet cannot be read the older copy is kept and the status says why.

## Long series, and comments

Tried against the real history of Out of Cruel Space (by u/KyleKKent) with the official API: the author's post list reaches back to Part 1 (2021-05-19) and held 1,802 chapter posts, collected in about 40 seconds. The series was renamed part-way, from "Out of Cruel Space, Part N" (Parts 1-999) to "OOCS, Into A Wider Galaxy, Part N" (the index lists those up to Part 823), so the Quick start now follows both names (but not the separate side stories), and *Find stories* groups both names as the one series. Earlier versions only matched the old name inside the newest 1,000 posts, which is how a book could stop at a couple of hundred chapters. A first check now reads back up to 30 pages at a time and keeps going on the next check until it reaches the beginning; editing a follow's filters makes it read back again.

If you counted more than about 1,800 chapters, I could not find the difference: the listing ends at 1,802 matching posts, with a few numbers skipped (for example Part 760 of the first run). Posts Reddit does not list, or counting side stories, could explain it; I do not know.

**Comments.** The author puts one top-level comment under each chapter: a preface on Part 1, later mostly Patreon and book links, a blurb for other stories, "most relevant chapters" links and fan links, and sometimes a real note (a schedule change, for example). Reader comments are discussion and are not included. Tick *Also keep the author's own comment* in the Add/Edit dialog to append it under each chapter as "Author's comment". It needs the official API and costs one request per chapter, so a long series is filled in batches of 300 chapters per check (about ten minutes each, newest first), with the next batch due again within 15 minutes. Other comments mention chapters only by link (a bot lists earlier parts); I found no separate chapter names in them.

## A subreddit of stand-alone stories (r/gayincest_stories and similar)

Some communities are not one author's series but many authors posting one story per post. In the Add/Edit dialog choose **A separate book for each post** (the Quick start has an entry for r/gayincest_stories). Then:

- Each matching post becomes its own book, with the post's author as the book's author and its date as the publication date.
- The post's **flair** becomes tags: `TRUE STORY - Uncle` gives the tags `TRUE STORY` and `Uncle`; `FICTION - Dad/In-Law/Step` gives `FICTION` and `Dad/In-Law/Step`. Use *Flair contains* to take only some (for example `FICTION`, or `re:Uncle|Cousin`). Flair is only available through the official API, not the public feed.
- A title like `The Lake House, Part 3` joins the Calibre series `The Lake House` as number 3; the other parts are matched by the same wording before "Part". Parts whose titles word the start differently are not grouped.
- The first check takes only the **newest 25** matching posts, not the community's backlog, and at most 50 new books are added per check (the rest wait for the next one). Edited posts become a fresh book.
- Posts whose title states an age under 18 (such as `[16M]`, `(17)` or `15 yo`) are skipped. This only reads the title: it cannot know ages that are not in the title, and a bracketed number that is not an age also counts, so it errs on the side of skipping. Whether every post in a large community follows its rules is something the plugin cannot check; the Add dialog's *Posted by* box can restrict a follow to authors you trust.

What I looked at: the community's public listing, with the official API (it is marked 18+, about 96,000 members, not quarantined). Of the newest 100 posts every one was a text post with flair, from 59 different authors, with a median length of about 4,600 characters; the plugin's own run on the newest 25 produced 25 books, 11 of them in 9 Calibre series. I did not read any story text.

## Two ways to read Reddit, and what is known about each

| | Public feeds (default) | Official API (your own credentials) |
| --- | --- | --- |
| Needs | nothing | a Reddit app's client id (and secret, for a "script" app) |
| Reach | the newest posts only (up to 100 per page, up to 10 pages on the first check) | the same, through the API |
| Pace | one request every 7 seconds | one request every 2 seconds |
| Status | **read the way a feed reader does**; Reddit's `robots.txt` asks automated clients to stay out of the whole site, and Reddit answers repeated requests with HTTP 429 | the route Reddit documents; new API access now needs Reddit's approval (its Responsible Builder Policy) |

Choose the API if you have, or can get, approval from Reddit: it is the supported route. I could not confirm Reddit's current approval process from Reddit's own pages; look at its developer documentation before relying on it. The public-feed route is light and personal, and is offered for people without API access; whether it fits Reddit's rules for your use is your call, and Reddit may block it at any time.

## What was and was not verified

- Verified against a **real r/HFY feed**: the Atom parser read 25 posts correctly, each with its full story text (4 to 26 KB), author, date and next-page token. Also verified on the real first post of Out of Cruel Space (one post, with its trailing "Next" link removed).
- **Not verified live**: the search-feed form and the page-by-page backfill (Reddit rate-limited my test requests, and I stopped rather than push it), and the whole **official API path and Log in with Reddit** (no credentials were available). The API code is written to Reddit's documentation and tested against a stand-in server.
- Verified with real Qt and a stand-in Calibre library: creating a book, updating it in place when a chapter arrives, leaving it alone when nothing changed, recreating it after you delete it, backing off after a rate limit, and the automatic background check respecting the schedule. **Not run inside Calibre itself.**

## Limits

- Text posts only. Image and link posts have no story text and are skipped; images inside posts are dropped. Comments are not collected.
- The feed reaches back about 1,000 posts at most (fewer in a busy subreddit if the series is old). Use an author or search source to stay within that, and expect very old chapters to be missed.
- A chapter that its author edits is refreshed only when it is read again within the newest pages.
- "First | Previous | Next" navigation lines at the start or end of a post are removed; other text is left exactly as posted.
- Reddit content belongs to its authors and is subject to Reddit's terms; this is meant for personal reading copies.
