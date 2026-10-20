"""The main-page plugin index must agree with the plugins themselves: versions, download files and links."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def rows():
    out = []
    for line in README.splitlines():
        if line.startswith("| **"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            out.append(
                {
                    "name": cells[0].strip("*"),
                    "version": cells[2],
                    "zips": re.findall(r"\[([^\]]+\.zip)\]", cells[3]),
                    "folder": re.search(r"\[`([^`]+)/`\]", cells[4]).group(1),
                }
            )
    return out


class ReadmeIndexTests(unittest.TestCase):
    def test_every_plugin_folder_is_listed(self):
        folders = {
            p.parent.name for p in ROOT.glob("*/__init__.py") if "actual_plugin" in p.read_text(encoding="utf-8")
        }
        self.assertEqual(
            {r["folder"] for r in rows()}, folders, "a plugin folder is missing from (or extra in) the README index"
        )

    def test_versions_match_the_plugins(self):
        for row in rows():
            init = (ROOT / row["folder"] / "__init__.py").read_text(encoding="utf-8")
            major, minor, patch = re.search(r"version\s*=\s*\((\d+),\s*(\d+),\s*(\d+)\)", init).groups()
            self.assertEqual(row["version"], f"{major}.{minor}.{patch}", row["name"])

    def test_download_links_point_at_files_in_dist(self):
        for row in rows():
            self.assertEqual(len(row["zips"]), 1, row["name"])
            target = ROOT / "dist" / row["zips"][0]
            if not target.is_file():
                import build_plugins

                build_plugins.build_all(ROOT / "dist")
            self.assertTrue(target.is_file(), f"{row['name']}: dist/{row['zips'][0]} is missing")

    def test_relative_links_resolve(self):
        for readme in ROOT.rglob("README.md"):
            if any(part in ("third_party", "__pycache__") for part in readme.parts):
                continue
            for target in re.findall(r"\]\(([^)\s]+)\)", readme.read_text(encoding="utf-8")):
                if target.startswith(("http", "#", "mailto")):
                    continue
                path = (readme.parent / target.split("#")[0]).resolve()
                self.assertTrue(path.exists(), f"{readme.relative_to(ROOT)} links to missing {target}")


if __name__ == "__main__":
    unittest.main()
