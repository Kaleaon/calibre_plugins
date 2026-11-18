#!/usr/bin/env python3
"""Bulk-install or update the whole Kaleaon calibre plugin pack from the command line.

The in-Calibre twin of this script is the Plugin Pack Manager plugin (pack_manager/); both use
the same pack list (pack_manager/manifest.py) and the same core logic (pack_manager/core.py).

Where the ZIPs come from (pick one):
  --dist            the repo's dist/ folder (default when it exists)
  --folder PATH     any folder holding the pack ZIPs (e.g. an extracted bundle)
  --download        fetch the ZIPs from the repo's dist/ on GitHub, verifying SHA-256 hashes

How they get installed (pick one):
  default           calibre-customize -a <zip> for each ZIP (found on PATH, or --calibre-customize)
  --copy-to DIR     copy the ZIPs straight into a calibre plugins folder, replacing older copies
                    of the same plugins (for portable calibre: the 'plugins' folder inside its
                    settings folder). Calibre must not be running in this mode.

Other flags: --config-dir DIR (the calibre configuration directory calibre-customize should use),
--only TEXT / --exclude TEXT (repeatable filters matched against name, ZIP and import name),
--skip-sources, --skip-bridge, --force (allow downgrades; by default a ZIP older than the
installed plugin is skipped), --list (show what would be installed, install nothing),
--make-bundle PATH (zip the pack + this installer + instructions into one file, install nothing).

Exit status is 0 when everything requested installed (or listed) cleanly, 1 on any failure.
"""

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(module_name, path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manifest = _load("pack_manifest", ROOT / "pack_manager" / "manifest.py")
core = _load("pack_core", ROOT / "pack_manager" / "core.py")
select_entries = manifest.select_entries

INSTALL_MD = """# Installing the calibre plugin pack

This bundle holds every plugin ZIP from the Kaleaon/calibre_plugins repo: the plugins, their
metadata sources, and Calibre Link.

## Easiest: the Plugin Pack Manager (inside Calibre)

1. Install `PluginPackManager.zip` once, the usual way: Calibre → Preferences → Plugins →
   Load plugin from file, then restart Calibre.
2. From then on, its toolbar menu does the rest: **Install / update all plugins…** downloads and
   installs the whole pack (verifying SHA-256 hashes), **Check for updates…** shows what is
   missing or outdated, and **Install from a local folder…** installs from a folder like this one
   with no network involved.

## From the command line

With Python 3 available:

    python install_all.py --folder <this folder>

That runs `calibre-customize -a` for every ZIP. For a portable Calibre, either run the portable
build's own `calibre-customize.exe`, or copy the ZIPs into the portable settings folder:

    python install_all.py --folder <this folder> --copy-to "<Calibre Portable>\\Calibre Settings\\plugins"

(with Calibre closed). `install-all.bat` / `install-all.sh` run the script in --download mode,
fetching the latest pack from GitHub instead of using local files.

## Manually

Each ZIP also installs on its own: Calibre → Preferences → Plugins → Load plugin from file.
Restart Calibre after installing or updating any plugin.
"""


def find_calibre_customize(explicit=None):
    if explicit:
        return explicit if Path(explicit).is_file() else None
    for name in ("calibre-customize", "calibre-customize.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def run_customize(customize, args, config_dir=None):
    env = dict(os.environ)
    if config_dir:
        env["CALIBRE_CONFIG_DIRECTORY"] = str(config_dir)
    return subprocess.run([customize, *args], capture_output=True, text=True, env=env)


def guess_plugins_dir(args):
    """Where the target calibre keeps its user plugin ZIPs, when it can be worked out. Used for
    the downgrade guard; a wrong or missing guess just means no guard, never a wrong install."""
    if getattr(args, "copy_to", None):
        return Path(args.copy_to)
    if getattr(args, "config_dir", None):
        return Path(args.config_dir) / "plugins"
    env_dir = os.environ.get("CALIBRE_CONFIG_DIRECTORY")
    if env_dir:
        return Path(env_dir) / "plugins"
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "calibre"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Preferences" / "calibre"
    else:
        base = Path.home() / ".config" / "calibre"
    return base / "plugins"


def installed_names(customize, config_dir=None):
    """The text of `calibre-customize -l`, or '' when it cannot be run."""
    try:
        result = run_customize(customize, ["-l"], config_dir)
    except OSError:
        return ""
    return result.stdout if result.returncode == 0 else ""


def make_bundle(source_dir, entries, out_path):
    """Zip every pack ZIP found in source_dir, plus this installer and instructions, into out_path."""
    source_dir = Path(source_dir)
    found, missing = core.folder_pack(source_dir, entries)
    out_path = Path(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        for entry in found:
            bundle.write(source_dir / entry["zip"], entry["zip"])
        bundle.write(Path(__file__), "install_all.py")
        for wrapper in ("install-all.bat", "install-all.sh"):
            if (ROOT / wrapper).is_file():
                bundle.write(ROOT / wrapper, wrapper)
        bundle.writestr("INSTALL.md", INSTALL_MD)
    return found, missing, out_path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bulk-install or update the Kaleaon calibre plugin pack.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--dist", action="store_true", help="use the repo's dist/ folder (default)")
    source.add_argument("--folder", metavar="PATH", help="folder holding the pack ZIPs")
    source.add_argument("--download", action="store_true", help="download the pack from GitHub, verifying hashes")
    parser.add_argument("--base-url", default=manifest.DEFAULT_BASE_URL, help="dist base URL for --download")
    parser.add_argument("--calibre-customize", metavar="PATH", help="path to calibre-customize")
    parser.add_argument("--config-dir", metavar="PATH", help="calibre configuration directory to install into")
    parser.add_argument(
        "--copy-to", metavar="DIR", help="copy ZIPs into this calibre plugins folder instead of using calibre-customize"
    )
    parser.add_argument("--only", action="append", default=[], help="only entries matching this text (repeatable)")
    parser.add_argument("--exclude", action="append", default=[], help="skip entries matching this text (repeatable)")
    parser.add_argument("--skip-sources", action="store_true", help="skip the metadata sources")
    parser.add_argument("--skip-bridge", action="store_true", help="skip Calibre Link")
    parser.add_argument(
        "--force",
        action="store_true",
        help="install even when the ZIP is older than the installed plugin (default: skip downgrades)",
    )
    parser.add_argument("--list", action="store_true", help="list what would be installed; install nothing")
    parser.add_argument("--make-bundle", metavar="PATH", help="build an all-in-one bundle ZIP; install nothing")
    args = parser.parse_args(argv)

    entries = select_entries(
        include_sources=not args.skip_sources,
        include_bridge=not args.skip_bridge,
        include_manager=True,
        only=args.only or None,
        exclude=args.exclude or None,
    )
    if not entries:
        print("No pack entries match those filters.", file=sys.stderr)
        return 2

    if args.make_bundle:
        source_dir = Path(args.folder) if args.folder else ROOT / "dist"
        found, missing, out_path = make_bundle(source_dir, entries, args.make_bundle)
        print(f"Bundle written: {out_path} ({out_path.stat().st_size / 1e6:.1f} MB, {len(found)} plugin ZIPs)")
        for entry in missing:
            print(f"  missing from {source_dir}: {entry['zip']}")
        return 1 if missing else 0

    tmp = None
    if args.download:
        tmp = tempfile.mkdtemp(prefix="plugin-pack-")
        sums = core.fetch_sums(args.base_url)
        results = core.download_pack(entries, tmp, args.base_url, sums=sums, verify=True, log=lambda line: print(line))
        failed = [r for r in results if not r["ok"]]
        for result in failed:
            print(f"✗ {result['entry']['name']}: {result['error']}", file=sys.stderr)
        if failed:
            shutil.rmtree(tmp, ignore_errors=True)
            return 1
        folder = Path(tmp)
    elif args.folder:
        folder = Path(args.folder)
    else:
        folder = ROOT / "dist"
    if not folder.is_dir():
        print(f"ZIP folder not found: {folder}", file=sys.stderr)
        return 2

    found, missing = core.folder_pack(folder, entries)
    for entry in missing:
        print(f"✗ {entry['name']}: {entry['zip']} not found in {folder}", file=sys.stderr)

    if args.list:
        for entry in found:
            try:
                version = core.format_version(core.zip_info(folder / entry["zip"])["version"])
            except Exception:
                version = "unknown"
            print(f"{entry['zip']:<42} {entry['name']} {version}")
        print(f"{len(found)} of {len(entries)} pack ZIPs present in {folder}")
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        return 1 if missing else 0

    failures = list(missing)
    installed_count = 0
    skipped = 0
    plugins_dir = guess_plugins_dir(args)
    installed = core.scan_plugins_dir(plugins_dir) if plugins_dir.is_dir() else {}

    def downgrade_skip(entry):
        """True (and counted) when installing this entry would lower the installed version."""
        nonlocal skipped
        current = installed.get(entry["import_name"])
        if args.force or not current:
            return False
        try:
            new_version = core.zip_info(folder / entry["zip"])["version"]
        except Exception:
            return False
        if core.is_downgrade(current["version"], new_version):
            skipped += 1
            print(
                f"• {entry['name']}: installed {core.format_version(current['version'])} is newer than "
                f"{core.format_version(new_version)} — skipped (use --force to downgrade)"
            )
            return True
        return False

    if args.copy_to:
        for entry in found:
            if downgrade_skip(entry):
                continue
            try:
                core.install_zip(folder / entry["zip"], plugins_dir=args.copy_to)
                installed_count += 1
                print(f"✓ {entry['name']}: copied into {args.copy_to}")
            except Exception as exc:
                failures.append(entry)
                print(f"✗ {entry['name']}: {exc}", file=sys.stderr)
    else:
        customize = find_calibre_customize(args.calibre_customize)
        if not customize:
            print(
                "calibre-customize not found. Pass --calibre-customize PATH, put calibre on PATH, "
                "or use --copy-to DIR with Calibre closed.",
                file=sys.stderr,
            )
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
            return 2
        done = []
        for entry in found:
            if downgrade_skip(entry):
                continue
            result = run_customize(customize, ["-a", str(folder / entry["zip"])], args.config_dir)
            if result.returncode == 0:
                installed_count += 1
                done.append(entry)
                print(f"✓ {entry['name']}: installed")
            else:
                failures.append(entry)
                detail = (result.stderr or result.stdout).strip().splitlines()
                print(
                    f"✗ {entry['name']}: calibre-customize failed" + (f" — {detail[-1]}" if detail else ""),
                    file=sys.stderr,
                )
        listing = installed_names(customize, args.config_dir).lower()
        if listing:
            unseen = [e["name"] for e in done if e["name"].lower() not in listing]
            if unseen:
                print("Not seen in `calibre-customize -l` afterwards (check manually): " + ", ".join(unseen))
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    print(
        f"Done: {installed_count} installed, {skipped} skipped, {len(failures)} failed. "
        "Restart Calibre to load the new plugins."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
