"""Build every plugin zip into dist/. Shared modules from common/ are bundled into the plugins that use them.

The fanfic plugin also bundles the FanFicFare library and the third-party packages it imports. Those come from the Python
environment running this script (`pip install fanficfare`); if they are missing that one zip is skipped with a message.
"""

import re
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent
SHARED = ["storykit.py", "guikit.py", "libkit.py", "task_queue.py", "queue_ui.py"]
PLUGINS = {
    "TakeoutBookFixer.zip": ("plugin", "takeout_book_fixer", SHARED, False),
    "EpubPromoScrubber.zip": ("scrubber_plugin", "epub_promo_scrubber", SHARED, False),
    "MetabodsDownloader.zip": ("metabods", "metabods_downloader", SHARED, False),
    "NiftyDownloader.zip": ("nifty", "nifty_downloader", SHARED, False),
    "StoryCollectionTagger.zip": ("tagger", "story_collection_tagger", [], False),
    "FanficDownloader.zip": ("fanfic", "fanfic_downloader", SHARED, True),
    "RedditStoryFollower.zip": ("reddit", "reddit_follower", SHARED, False),
    "GeminiLibraryFixer.zip": ("gemini", "gemini_library_fixer", ["storykit.py", "guikit.py"], False),
    "MediaMatcher.zip": ("media", "media_matcher", ["storykit.py", "guikit.py"], False),
}
PLUGINS_BY_IMPORT = [v[1] for v in PLUGINS.values()]

# FanFicFare parts left out: the command line, and the txt/html/mobi writers (the txt writer needs the GPL-licensed
# html2text; this plugin only ever writes EPUB).
FFF_EXCLUDE = {
    "cli.py",
    "mobi.py",
    "mobihtml.py",
    "writers/writer_txt.py",
    "writers/writer_html.py",
    "writers/writer_mobi.py",
}
FFF_WRITERS_INIT = """# Replaced when bundled into the fanfic plugin: only the EPUB writer is shipped.
from ..exceptions import FailedToDownload

from .writer_epub import EpubWriter


def getWriter(type, config, story):
    if type == "epub":
        return EpubWriter(config, story)
    raise FailedToDownload("(%s) is not a supported download format." % type)
"""
THIRD_PARTY = [
    ("FanFicFare", "fanficfare"),
    ("cloudscraper", "cloudscraper"),
    ("requests-file", "requests_file"),
    ("requests-toolbelt", "requests_toolbelt"),
    ("pyparsing", "pyparsing"),
]
NOTICE = """The fanfic plugin bundles FanFicFare (https://github.com/JimmXinu/FanFicFare), licensed under the Apache License 2.0
(text below), by Jim Miller and contributors. Modifications for bundling: it is installed as a sub-package of this plugin;
absolute "from fanficfare..." imports were changed to relative imports; the command line (cli.py) and the txt, html and
mobi writers were removed and writers/__init__.py replaced so that only EPUB output is available.

cloudscraper has one change: user_agent/__init__.py reads browsers.json through pkgutil instead of open(), so it works from
inside a zip. requests-file, requests-toolbelt, pyparsing and brotlidecpy are bundled unmodified. All are under their own
licenses, included below.
"""


def _package_files(package_dir):
    for path in sorted(package_dir.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            yield path


def _licenses():
    from importlib import metadata

    parts = [NOTICE]
    for dist_name, _ in THIRD_PARTY:
        try:
            dist = metadata.distribution(dist_name)
        except metadata.PackageNotFoundError:
            continue
        for f in dist.files or []:
            if re.search(r"(?i)(^|/)(licen[cs]e|notice)[^/]*$", str(f)):
                parts.append(
                    f"\n===== {dist_name} {dist.version}: {f.name} =====\n"
                    + dist.locate_file(f).read_text(encoding="utf-8", errors="replace")
                )
    return "\n".join(parts)


CLOUDSCRAPER_OLD = "with open(os.path.join(os.path.dirname(__file__), 'browsers.json'), 'r') as fp:"
CLOUDSCRAPER_NEW = (
    "import io, pkgutil  # patched: read the data file through the import system so it works from a zip\n"
    "        with io.StringIO(pkgutil.get_data(__name__, 'browsers.json').decode('utf-8')) as fp:"
)


def _patch_cloudscraper(text):
    if CLOUDSCRAPER_OLD not in text:
        raise RuntimeError("cloudscraper changed: the browsers.json patch no longer applies")
    return text.replace(CLOUDSCRAPER_OLD, CLOUDSCRAPER_NEW)


def add_vendored(z):
    """Copy FanFicFare and its imported dependencies from the running environment into the zip."""
    try:
        import cloudscraper
        import fanficfare
        import pyparsing
        import requests_file
        import requests_toolbelt
    except ImportError as exc:
        raise ImportError(f'{exc}; run "pip install fanficfare" in this Python to build the fanfic plugin') from exc
    fff_root = Path(fanficfare.__file__).parent
    for path in _package_files(fff_root):
        rel = path.relative_to(fff_root).as_posix()
        if rel in FFF_EXCLUDE:
            continue
        arcname = "fanficfare/" + rel
        if rel == "writers/__init__.py":
            z.writestr(arcname, FFF_WRITERS_INIT)
        elif path.suffix == ".py":
            depth = rel.count("/")
            dots = "." * (depth + 1)
            text = path.read_text(encoding="utf-8")
            text = re.sub(
                r"^(\s*)from fanficfare(?:\.([\w.]+))?\s+import\b",
                lambda m: f"{m.group(1)}from {dots}{m.group(2) or ''} import",
                text,
                flags=re.M,
            )
            if re.search(r"^\s*import fanficfare\b", text, re.M):
                raise RuntimeError(f"{rel} still imports fanficfare by its absolute name")
            z.writestr(arcname, text)
        else:
            z.write(path, arcname)
    for module in (cloudscraper, requests_file, requests_toolbelt, pyparsing):
        base = Path(module.__file__).parent
        for path in _package_files(base):
            arcname = f"{base.name}/{path.relative_to(base).as_posix()}"
            if arcname == "cloudscraper/user_agent/__init__.py":
                z.writestr(arcname, _patch_cloudscraper(path.read_text(encoding="utf-8")))
            else:
                z.write(path, arcname)
    bro = root / "third_party" / "brotlidecpy"
    for path in _package_files(bro):
        if path.name == "LICENSE":
            continue
        z.write(path, f"brotlidecpy/{path.name}")
    z.write(
        root / "docs" / "royalroad-flaresolverr.md", "royalroad-flaresolverr.md"
    )  # shown by the plugin's setup dialog
    z.writestr(
        "THIRD_PARTY_LICENSES.txt",
        _licenses()
        + "\n===== brotlidecpy 1.0.3 (pure-Python Brotli decoder) =====\n"
        + (bro / "LICENSE").read_text(encoding="utf-8")
        + "\nSource: https://github.com/sidney/brotlidecpy\n",
    )


def build_all(dest, skip_unavailable=True):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    built = {}
    for zip_name, (folder, import_name, shared, vendored) in PLUGINS.items():
        target = dest / zip_name
        try:
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                for path in sorted((root / folder).glob("*.py")):
                    z.write(path, path.name)
                for name in shared:
                    z.write(root / "common" / name, name)
                z.writestr(f"plugin-import-name-{import_name}.txt", "")
                if vendored:
                    add_vendored(z)
        except ImportError as exc:
            target.unlink(missing_ok=True)
            if not skip_unavailable:
                raise
            print(f"Skipped {zip_name}: {exc}", file=sys.stderr)
            continue
        built[import_name] = target
    return built


if __name__ == "__main__":
    for target in build_all(root / "dist", skip_unavailable="--strict" not in sys.argv).values():
        print(target)
