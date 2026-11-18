"""Tests for the Plugin Pack Manager (pack_manager/) and the install_all.py bulk installer.

The manifest is checked against the build script and against each plugin's own __init__.py, so
the pack list cannot drift from what the repo ships. Core logic is exercised with fake fetchers
and temp folders — no network, no Calibre.
"""

import hashlib
import importlib
import importlib.util
import re
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_plugins  # noqa: E402

# Register pack_manager as a package without running its __init__ (which imports calibre).
_pkg = types.ModuleType("pack_manager")
_pkg.__path__ = [str(ROOT / "pack_manager")]
sys.modules.setdefault("pack_manager", _pkg)
core = importlib.import_module("pack_manager.core")
manifest = importlib.import_module("pack_manager.manifest")


def load_install_all():
    spec = importlib.util.spec_from_file_location("install_all", ROOT / "install_all.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_plugin_zip(path, name="Fake Plugin", version=(1, 2, 3), import_name="fake_plugin"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("__init__.py", f"class Fake:\n    name = '{name}'\n    version = {version}\n")
        archive.writestr(f"plugin-import-name-{import_name}.txt", "")


class ManifestTests(unittest.TestCase):
    def test_manifest_covers_every_built_zip_plus_bridge_and_itself(self):
        expected = (
            set(build_plugins.PLUGINS) | set(build_plugins.SOURCES) | {"CalibreLink.zip", "PluginPackManager.zip"}
        )
        self.assertEqual({entry["zip"] for entry in manifest.PACK}, expected)

    def test_manifest_import_names_match_the_build(self):
        built = {}
        for zips in (build_plugins.PLUGINS, build_plugins.SOURCES):
            for zip_name, (_, import_name, *_rest) in zips.items():
                built[zip_name] = import_name
        built["CalibreLink.zip"] = "calibre_link"
        built["PluginPackManager.zip"] = "plugin_pack_manager"
        for entry in manifest.PACK:
            self.assertEqual(entry["import_name"], built[entry["zip"]], entry["zip"])

    def test_manifest_names_match_each_plugins_init(self):
        folders = {zip_name: spec[0] for zip_name, spec in build_plugins.PLUGINS.items()}
        folders.update({zip_name: spec[0] for zip_name, spec in build_plugins.SOURCES.items()})
        folders["CalibreLink.zip"] = "calibre_link"
        folders["PluginPackManager.zip"] = "pack_manager"
        for entry in manifest.PACK:
            text = (ROOT / folders[entry["zip"]] / "__init__.py").read_text(encoding="utf-8")
            declared = re.search(r"^\s*name\s*=\s*['\"]([^'\"]+)['\"]", text, re.M).group(1)
            self.assertEqual(entry["name"], declared, entry["zip"])

    def test_select_entries_filters(self):
        self.assertEqual(len(manifest.select_entries()), len(manifest.PACK))
        no_sources = manifest.select_entries(include_sources=False)
        self.assertTrue(all(entry["kind"] != "source" for entry in no_sources))
        self.assertEqual(len(manifest.select_entries(only=["reddit"])), 2)  # follower + its source
        self.assertNotIn("CalibreLink.zip", [e["zip"] for e in manifest.select_entries(exclude=["calibre link"])])


class CoreTests(unittest.TestCase):
    def test_parse_sha256sums(self):
        digest = "a" * 64
        text = f"{digest}  TakeoutBookFixer.zip\n{digest.upper()} *dist/CalibreLink.zip\nnot a sum line\n"
        self.assertEqual(core.parse_sha256sums(text), {"TakeoutBookFixer.zip": digest, "CalibreLink.zip": digest})

    def test_zip_info_reads_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Fake.zip"
            make_plugin_zip(path)
            self.assertEqual(
                core.zip_info(path), {"name": "Fake Plugin", "version": (1, 2, 3), "import_name": "fake_plugin"}
            )
            self.assertEqual(core.zip_info(path.read_bytes())["version"], (1, 2, 3))

    def test_zip_info_rejects_non_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plain.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("readme.txt", "hello")
            with self.assertRaises(ValueError):
                core.zip_info(path)

    def test_scan_and_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            make_plugin_zip(folder / "Fake.zip")
            installed = core.scan_plugins_dir(folder)
            self.assertEqual(installed["fake_plugin"]["version"], (1, 2, 3))
            entry = {"zip": "Fake.zip", "name": "Fake Plugin", "import_name": "fake_plugin", "kind": "plugin"}
            digest = hashlib.sha256((folder / "Fake.zip").read_bytes()).hexdigest()
            self.assertEqual(core.plan_pack([entry], installed, {"Fake.zip": digest})[0]["action"], "current")
            self.assertEqual(core.plan_pack([entry], installed, {"Fake.zip": "b" * 64})[0]["action"], "update")
            self.assertEqual(core.plan_pack([entry], installed, None)[0]["action"], "check")
            self.assertEqual(core.plan_pack([entry], {}, {"Fake.zip": digest})[0]["action"], "install")

    def test_is_downgrade(self):
        self.assertTrue(core.is_downgrade((1, 14, 0), (1, 5, 0)))
        self.assertFalse(core.is_downgrade((1, 5, 0), (1, 14, 0)))
        self.assertFalse(core.is_downgrade((1, 5, 0), (1, 5, 0)))
        self.assertFalse(core.is_downgrade(None, (1, 0, 0)))
        self.assertFalse(core.is_downgrade((1, 0, 0), None))

    def test_download_pack_verifies_hashes(self):
        entry = {"zip": "Fake.zip", "name": "Fake Plugin", "import_name": "fake_plugin", "kind": "plugin"}
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src.zip"
            make_plugin_zip(source)
            data = source.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            fetcher = lambda url: data  # noqa: E731
            good = core.download_pack(
                [entry], Path(tmp) / "out", "https://example.test/dist", fetcher=fetcher, sums={"Fake.zip": digest}
            )
            self.assertTrue(good[0]["ok"])
            self.assertEqual((Path(tmp) / "out" / "Fake.zip").read_bytes(), data)
            bad = core.download_pack(
                [entry], Path(tmp) / "out2", "https://example.test/dist", fetcher=fetcher, sums={"Fake.zip": "b" * 64}
            )
            self.assertFalse(bad[0]["ok"])
            self.assertFalse((Path(tmp) / "out2" / "Fake.zip").exists())

    def test_download_pack_reports_fetch_errors(self):
        entry = {"zip": "Fake.zip", "name": "Fake Plugin", "import_name": "fake_plugin", "kind": "plugin"}

        def boom(url):
            raise OSError("no network")

        with tempfile.TemporaryDirectory() as tmp:
            results = core.download_pack([entry], tmp, "https://example.test/dist", fetcher=boom)
        self.assertFalse(results[0]["ok"])
        self.assertIn("no network", results[0]["error"])

    def test_install_zip_copy_replaces_same_import_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "plugins"
            folder.mkdir()
            make_plugin_zip(folder / "OldName.zip", version=(1, 0, 0))
            make_plugin_zip(folder / "Other.zip", name="Other", import_name="other_plugin")
            new = Path(tmp) / "Fake.zip"
            make_plugin_zip(new, version=(2, 0, 0))
            result = core.install_zip(new, plugins_dir=folder)
            self.assertEqual(result["method"], "copy")
            self.assertFalse((folder / "OldName.zip").exists(), "the old file for the same plugin is replaced")
            self.assertTrue((folder / "Other.zip").exists(), "other plugins are untouched")
            self.assertEqual(core.scan_plugins_dir(folder)["fake_plugin"]["version"], (2, 0, 0))

    def test_install_zip_prefers_calibre_add_plugin(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Fake.zip"
            make_plugin_zip(path)
            result = core.install_zip(path, add_plugin=calls.append)
        self.assertEqual(result["method"], "calibre")
        self.assertEqual(calls, [str(path)])

    def test_folder_pack_splits_found_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_plugin_zip(Path(tmp) / "Fake.zip")
            entries = [
                {"zip": "Fake.zip", "name": "Fake", "import_name": "fake_plugin", "kind": "plugin"},
                {"zip": "Gone.zip", "name": "Gone", "import_name": "gone", "kind": "plugin"},
            ]
            found, missing = core.folder_pack(tmp, entries)
            self.assertEqual([e["zip"] for e in found], ["Fake.zip"])
            self.assertEqual([e["zip"] for e in missing], ["Gone.zip"])


class InstallAllTests(unittest.TestCase):
    def test_make_bundle_contents(self):
        install_all = load_install_all()
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / "dist"
            dist.mkdir()
            entries = manifest.select_entries()
            for entry in entries[:3]:
                make_plugin_zip(dist / entry["zip"], name=entry["name"], import_name=entry["import_name"])
            out = Path(tmp) / "bundle.zip"
            found, missing, _ = install_all.make_bundle(dist, entries[:3], out)
            self.assertEqual(len(found), 3)
            self.assertEqual(missing, [])
            with zipfile.ZipFile(out) as bundle:
                names = bundle.namelist()
            for entry in entries[:3]:
                self.assertIn(entry["zip"], names)
            self.assertIn("install_all.py", names)
            self.assertIn("INSTALL.md", names)

    def test_list_mode_reports_versions(self):
        install_all = load_install_all()
        with tempfile.TemporaryDirectory() as tmp:
            for entry in manifest.select_entries():
                make_plugin_zip(Path(tmp) / entry["zip"], name=entry["name"], import_name=entry["import_name"])
            self.assertEqual(install_all.main(["--folder", tmp, "--list"]), 0)
            self.assertEqual(install_all.main(["--folder", tmp, "--list", "--only", "reddit"]), 0)

    def test_copy_mode_installs_and_skips_downgrades(self):
        install_all = load_install_all()
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "zips"
            source.mkdir()
            target = Path(tmp) / "plugins"
            target.mkdir()
            entries = manifest.select_entries()
            for entry in entries:
                make_plugin_zip(
                    source / entry["zip"], name=entry["name"], import_name=entry["import_name"], version=(1, 0, 0)
                )
            # A newer Calibre Link is already installed: the pack's older file must not replace it.
            make_plugin_zip(
                target / "CalibreLink.zip", name="Calibre Link", import_name="calibre_link", version=(9, 9, 9)
            )
            rc = install_all.main(["--folder", str(source), "--copy-to", str(target)])
            self.assertEqual(rc, 0)
            installed = core.scan_plugins_dir(target)
            self.assertEqual(installed["calibre_link"]["version"], (9, 9, 9), "downgrade guard held")
            self.assertEqual(len(installed), len(entries))
            rc = install_all.main(["--folder", str(source), "--copy-to", str(target), "--force"])
            self.assertEqual(rc, 0)
            self.assertEqual(
                core.scan_plugins_dir(target)["calibre_link"]["version"], (1, 0, 0), "--force allows the downgrade"
            )


if __name__ == "__main__":
    unittest.main()
