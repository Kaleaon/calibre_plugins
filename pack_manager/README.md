# Plugin Pack Manager

Installs and updates the whole plugin pack from this repository — the 10 plugins, their 10
metadata sources, and Calibre Link — in one go, instead of loading 22 ZIPs one at a time through
Preferences → Plugins.

Install this one ZIP the usual way (**Preferences → Plugins → Load plugin from file**), restart
Calibre, then use its toolbar menu:

- **Install / update all plugins…** — downloads every pack ZIP from the repo's `dist/` folder on
  GitHub, verifies each against the published `SHA256SUMS.txt`, and installs the ones that are
  missing or changed through Calibre's own plugin installer. Plugins whose installed file already
  matches the published build are skipped.
- **Check for updates…** — fetches only the small hash list and shows, per plugin, whether it is
  missing, up to date, or has an update available.
- **Install from a local folder…** — the same install, from a folder of ZIPs on disk (for example
  an extracted `CalibrePluginsPack.zip` bundle), with no network involved.
- **Pack status…** — what is installed, at which version.
- **Settings…** — the download base URL, whether the metadata sources and Calibre Link are
  included, and whether hash verification is on.

Notes:

- Installing or updating a plugin takes effect after a **Calibre restart**; the result log says
  so when anything changed. The manager can update itself the same way.
- "Up to date" is decided by comparing the installed ZIP file's SHA-256 against the published
  hash, so a rebuilt ZIP counts as an update even if its version number did not change.
- The same bulk install is available outside Calibre via `install_all.py` in the repo root
  (it drives `calibre-customize`), which also builds the all-in-one bundle:
  `python install_all.py --make-bundle CalibrePluginsPack.zip`.

The pack list lives in `manifest.py` and is test-checked against the build script, so it cannot
silently drift from what the repo actually ships.
