# Calibre Link — a bridge that lives inside calibre

**v1.1.0 (Oct 26, 2026) — Tailscale blockers fixed.** The v1.0.0 zip
shipped without the Tailscale sidecars, and the sidecar lookup code
treated calibre's resource API as if it returned file paths — the
calibre docs say it returns raw bytes, so the node could never have
started in a real install. v1.1.0 extracts the bundled node to the
calibre config folder (cached by content hash, executable bit set),
listens for tsnet's own login-URL log line as well as polling its
status, wires tsnet's `UserLogf` (where its auth URL actually goes),
and ships both Windows and Linux sidecars in the zip. Verified
headless: extraction + cache, the extracted node starting and
waiting for login, and the HTTP API (auth, /stats, /books).

Calibre Link is a calibre plugin. When calibre starts, Calibre Link
starts with it and serves a small, token-protected HTTP API on this
PC. That lets Muse (Tinker) list, search, audit, back up and fix
metadata in your library over your private Tailscale network — for
organizing books and repairing metadata.

## Why a plugin instead of the old standalone bridge?

The old bridge shelled out to `calibredb`, which must not write to a
library while the calibre GUI has it open. The plugin runs *inside*
calibre and writes through calibre's own database object — the same
path calibre itself uses — so the GUI, its cache and its on-screen
refresh all stay consistent. No "close calibre before bulk fixes"
dance: calibre can stay open while Tinker works.

## Integrated Tailscale

The plugin bundles a tiny embedded Tailscale node (built with
`tsnet` from github.com/tailscale/tailscale). When calibre starts,
the node starts with it; when calibre quits, it stops. It joins your
tailnet as **its own device** (default name `calibre-link`) and
proxies tailnet requests to the bridge inside calibre.

- **No Tailscale install needed on the PC** for calibre itself —
  works on the borrowed/shared machines and with calibre Portable.
  (Your phone still uses the regular Tailscale app.)
- **First run only:** the node needs a one-time approval. The
  Calibre Link status dialog (toolbar button) and
  `CalibreLink-connection.txt` in your library folder will show a
  login link — open it, sign in, approve the `calibre-link` device.
  After that it reconnects on its own every time calibre starts.
  (Alternatively paste an auth key in the plugin settings to skip
  the link step.)
- The device appears in your Tailscale admin console like any other
  machine; remove it there, or untick "Join my tailnet" in the
  plugin settings, to disconnect calibre.
- Tinker reaches the library at `http://calibre-link:8765` (or the
  device's 100.x address) from anywhere on the tailnet — **and the
  token is still required**. The tailnet is the transport; the token
  is still the key.

## Install

1. Open calibre → Preferences → Plugins → **Load plugin from file**.
2. Choose `CalibreLink.zip`. Accept the security prompt (it is a
   local plugin you asked for).
3. Restart calibre when it asks.
4. Done — the bridge starts automatically with calibre from now on.
   A **Calibre Link** button appears in the toolbar; clicking it
   shows status and connection info.

## Connecting Tinker

Every time the bridge starts, it writes `CalibreLink-connection.txt`
into your library folder. It contains:

- the **token** (a secret; treat it like a password for your library)
- the **port** (default 8765)
- this PC's addresses — the one starting with **100.** is the
  Tailscale address, the one Tinker uses.

Read Tinker the token and the 100.x address (or just tell him the
bridge is up — he can see this PC on the tailnet and ask). Windows
Firewall may pop up the first time calibre listens; allow it on
private networks so the Tailscale address can be reached.

Settings (address, port, token) live under Preferences → Plugins →
Calibre Link → Customize plugin. Changes apply immediately.

## What Tinker can do through it

- `GET /health` — library path + book count (no token needed)
- `GET /stats` — audit: missing ISBN / cover / author / tags, dup titles
- `GET /books?q=&limit=&offset=` — list/search (calibre search syntax)
- `GET /book/<id>` — full metadata for one book
- `POST /backup` — consistent hot backup of metadata.db into
  `<library>/backups/` (SQLite online backup; safe while calibre runs)
- `POST /set-metadata/<id>` — fix fields (title, authors, author_sort,
  tags, isbn, series, series_index, publisher, pubdate, rating,
  comments, language). **A backup is taken before every write.**
- `POST /calibredb` — compatibility shim: read-only subcommands and
  `set_metadata` are translated onto the live database; other write
  subcommands are refused with an explanation.
- `GET /fs-list?path=` — list a folder inside the library folder
  (v1.8.0; folders that are live book folders are flagged)
- `POST /fs-move` — move a file or folder within the library folder
  (v1.8.0). Built for cleanup: orphaned folders, the recycle bin,
  staging files. Paths are confined to the library folder,
  metadata.db is protected, existing destinations are never
  overwritten, and live book folders (or anything containing /
  inside one) are refused — calibre owns those paths; renaming a
  book's folder is done by editing its metadata.
- `POST /fs-upload` — write a file Tinker downloaded (e.g. a book
  from Google Drive) into a folder inside the library folder
  (v1.9.0), under the same confinement. From there it can be added
  to the library properly or moved with /fs-move.
- `POST /swap-title-authors/<id>` — repair a record whose title
  and author(s) hold each other's values (v1.9.0). Backup first;
  a "Last, First" title is inverted back to display form and kept
  as the author_sort.
- `GET /book-file/<id>?format=` — download a book's format file
  (v1.10.0), and `GET /fs-download?path=` — download any file in
  the library folder. These are the library → Google Drive leg:
  Tinker pulls the file through the bridge, then uploads it to
  Drive. metadata.db is refused here — /backup is the consistent
  way to copy the database.
- Chunked, resumable transfers (v1.11.0) — for files over the
  64 MB single-shot cap, and for transfers that shouldn't start
  over when a connection drops. Downloads take `offset`/`length`
  ranges; uploads run begin → chunk → complete with the state
  kept on disk in `.calibrelink-parts/` inside the library, so a
  transfer survives even a calibre restart. Pausing is just
  stopping; resuming picks up from the byte already saved, in
  either direction. Completed uploads are verified by size and
  sha256 before they land.

Rules Tinker follows stay the same as with the old bridge: backups
before writes, no deletions or merges without Joe's list first, and
every batch verified by re-running the audit.

## Safety notes

- The token is the only gate. Anyone on a network this PC listens on
  who has the token can edit metadata. The token is long, random and
  shown only in the config, the status dialog and the connection
  file. Regenerate it in the plugin settings if it ever leaks.
- To stop the bridge: quit calibre, or disable the plugin under
  Preferences → Plugins.
- Uninstall: Preferences → Plugins → Calibre Link → Remove plugin.

## Files in this plugin

- `__init__.py` — plugin registration
- `ui.py` — toolbar action, server + Tailscale lifecycle, status dialog
- `server.py` — the HTTP API (no calibre imports; separately testable)
- `adapter.py` — talks to calibre's database; writes the connection file
- `config.py` — settings + preferences widget (bridge + Tailscale)
- `tailscale/calibre-tsnet-<platform>` — the embedded Tailscale node
  (Go/tsnet sidecar, launched by the plugin; Windows + Linux builds)
