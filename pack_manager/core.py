"""Bulk install/update logic for the plugin pack. Pure stdlib: no Calibre or Qt imports, so the
same code runs inside Calibre (via action.py), from install_all.py, and under the test suite.

How "is an update available?" is decided: the repo publishes SHA256SUMS.txt next to the ZIPs in
dist/, so a check costs one small text fetch — an installed plugin whose ZIP file hashes to the
published value is current, anything else is an update. Installs prefer Calibre's own
calibre.customize.ui.add_plugin(); the copy fallback (drop the ZIP into the plugins folder,
replacing the file that carries the same plugin-import-name marker) is what add_plugin does
underneath, and is used by tests and by install_all.py-style flows.
"""

import hashlib
import re
import shutil
import urllib.request
import zipfile
from pathlib import Path

_NAME_RE = re.compile(r"^\s*name\s*=\s*['\"]([^'\"]+)['\"]", re.M)
_VERSION_RE = re.compile(r"^\s*version\s*=\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", re.M)
_MARKER_RE = re.compile(r"^plugin-import-name-(.+)\.txt$")
_SUM_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$")


def parse_sha256sums(text):
    """Parse sha256sum output ('<hash>  <name>' or '<hash> *<name>') into {filename: hash}."""
    sums = {}
    for line in text.splitlines():
        match = _SUM_LINE_RE.match(line.strip())
        if match:
            sums[Path(match.group(2)).name] = match.group(1).lower()
    return sums


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_version(version):
    return ".".join(str(part) for part in version) if version else "unknown"


def is_downgrade(installed_version, new_version):
    """True when replacing the installed plugin with the new file would lower its version.
    Unknown versions never count as a downgrade — a hash difference alone is not proof."""
    if not installed_version or not new_version:
        return False
    return tuple(new_version) < tuple(installed_version)


def zip_info(source):
    """Read a plugin ZIP's identity: {'name', 'version', 'import_name'} from its __init__.py and
    its plugin-import-name marker. `source` is a path or raw ZIP bytes."""
    if isinstance(source, bytes | bytearray):
        import io

        archive = zipfile.ZipFile(io.BytesIO(bytes(source)))
    else:
        archive = zipfile.ZipFile(source)
    with archive:
        names = archive.namelist()
        if "__init__.py" not in names:
            raise ValueError("not a calibre plugin zip: no __init__.py at its root")
        text = archive.read("__init__.py").decode("utf-8", errors="replace")
        import_name = None
        for name in names:
            match = _MARKER_RE.match(name)
            if match:
                import_name = match.group(1)
                break
    name_match = _NAME_RE.search(text)
    version_match = _VERSION_RE.search(text)
    return {
        "name": name_match.group(1) if name_match else None,
        "version": tuple(int(part) for part in version_match.groups()) if version_match else None,
        "import_name": import_name,
    }


def scan_plugins_dir(plugins_dir):
    """Map import_name -> {'file', 'name', 'version', 'sha256'} for every plugin ZIP in a folder.
    ZIPs that are not readable calibre plugins are skipped."""
    installed = {}
    folder = Path(plugins_dir)
    if not folder.is_dir():
        return installed
    for path in sorted(folder.glob("*.zip")):
        try:
            info = zip_info(path)
        except (ValueError, zipfile.BadZipFile, OSError):
            continue
        if not info["import_name"]:
            continue
        installed[info["import_name"]] = {
            "file": str(path),
            "name": info["name"],
            "version": info["version"],
            "sha256": sha256_file(path),
        }
    return installed


def plan_pack(entries, installed, remote_sums=None):
    """One row per entry: action is 'install' (not present), 'update' (present but its file hash
    differs from the published one), 'current' (hashes match) or 'check' (present, but no
    published hash to compare against, so only a download can tell)."""
    rows = []
    for entry in entries:
        current = installed.get(entry["import_name"])
        row = {"entry": entry, "installed": current}
        if current is None:
            row.update(action="install", reason="not installed")
        elif remote_sums is not None and entry["zip"] in remote_sums:
            if current["sha256"] == remote_sums[entry["zip"]]:
                row.update(action="current", reason="installed file matches the published build")
            else:
                row.update(action="update", reason="published build differs from the installed file")
        else:
            row.update(action="check", reason="installed; no published hash to compare against")
        rows.append(row)
    return rows


def fetch_bytes(url, timeout=60):
    request = urllib.request.Request(url, headers={"User-Agent": "PluginPackManager/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_sums(base_url, fetcher=None):
    """The published SHA256SUMS.txt for a dist base URL, or None when it cannot be fetched."""
    fetcher = fetcher or fetch_bytes
    try:
        return parse_sha256sums(fetcher(base_url.rstrip("/") + "/SHA256SUMS.txt").decode("utf-8", errors="replace"))
    except Exception:
        return None


def download_pack(entries, dest_dir, base_url, fetcher=None, sums=None, verify=True, log=None, should_cancel=None):
    """Download each entry's ZIP into dest_dir. When `sums` has a hash for a ZIP and verify is on,
    a mismatch fails that entry and the file is not kept. Returns one result dict per entry."""
    fetcher = fetcher or fetch_bytes
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    results = []
    for entry in entries:
        if should_cancel and should_cancel():
            results.append({"entry": entry, "ok": False, "error": "cancelled"})
            continue
        url = base_url.rstrip("/") + "/" + entry["zip"]
        if log:
            log(f"Downloading {entry['zip']}…")
        try:
            data = fetcher(url)
        except Exception as exc:
            results.append({"entry": entry, "ok": False, "error": f"download failed: {exc}"})
            continue
        digest = sha256_bytes(data)
        if verify and sums and entry["zip"] in sums and digest != sums[entry["zip"]]:
            results.append(
                {
                    "entry": entry,
                    "ok": False,
                    "error": "SHA-256 mismatch — the download does not match the published hash, so it was not kept",
                }
            )
            continue
        path = dest / entry["zip"]
        path.write_bytes(data)
        results.append({"entry": entry, "ok": True, "path": str(path), "sha256": digest})
    return results


def install_zip(zip_path, plugins_dir=None, add_plugin=None):
    """Install one plugin ZIP. With add_plugin (calibre.customize.ui.add_plugin) Calibre validates
    and registers it; otherwise the ZIP is copied into plugins_dir, replacing any file there that
    carries the same plugin-import-name marker."""
    zip_path = Path(zip_path)
    if add_plugin is not None:
        add_plugin(str(zip_path))
        return {"method": "calibre", "path": str(zip_path)}
    if plugins_dir is None:
        raise ValueError("install_zip needs either add_plugin or plugins_dir")
    folder = Path(plugins_dir)
    folder.mkdir(parents=True, exist_ok=True)
    info = zip_info(zip_path)
    if info["import_name"]:
        existing = scan_plugins_dir(folder).get(info["import_name"])
        if existing and Path(existing["file"]).name != zip_path.name:
            Path(existing["file"]).unlink()
    target = folder / zip_path.name
    if target.resolve() != zip_path.resolve():
        shutil.copy2(zip_path, target)
    return {"method": "copy", "path": str(target)}


def folder_pack(folder, entries):
    """Split entries into (found, missing) by whether their ZIP sits in `folder`."""
    folder = Path(folder)
    found, missing = [], []
    for entry in entries:
        (found if (folder / entry["zip"]).is_file() else missing).append(entry)
    return found, missing
