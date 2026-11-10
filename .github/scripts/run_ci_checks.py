#!/usr/bin/env python3
"""CI check helper script to scope linting and test execution to changed files in PRs."""

import glob
import os
import subprocess
import sys


def get_changed_files(base_ref=None):
    """Determine changed files against base branch or upstream."""
    base = base_ref or os.environ.get("BASE_REF")
    diff_succeeded = False
    files = []

    if base:
        # Fetch base ref if not fully available
        subprocess.run(
            ["git", "fetch", "origin", f"{base}:refs/remotes/origin/{base}"],
            capture_output=True,
        )
        subprocess.run(["git", "fetch", "origin", base], capture_output=True)
        for ref in [f"origin/{base}", base, "FETCH_HEAD"]:
            for sep in ["...", ".."]:
                try:
                    proc = subprocess.run(
                        [
                            "git",
                            "diff",
                            "--name-only",
                            "--diff-filter=d",
                            f"{ref}{sep}HEAD",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
                    diff_succeeded = True
                    break
                except Exception:
                    continue
            if diff_succeeded:
                break

    if not diff_succeeded:
        # Fallback to diffing against previous commit
        for ref in ["HEAD~1...HEAD", "HEAD~1..HEAD"]:
            try:
                proc = subprocess.run(
                    ["git", "diff", "--name-only", "--diff-filter=d", ref],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                files = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
                diff_succeeded = True
                break
            except Exception:
                continue

    return files, bool(base)


def check_lint():
    changed_files, is_pr = get_changed_files()
    python_files = [f for f in changed_files if f.endswith(".py") and os.path.exists(f)]

    if is_pr:
        if not python_files:
            print(f"No Python files changed in this PR (base: {os.environ.get('BASE_REF')}). Skipping ruff checks.")
            return 0
        targets = python_files
    else:
        # On push without PR base_ref
        targets = python_files if python_files else ["."]

    targets = [os.path.normpath(f) for f in targets]
    print(f"Running ruff checks on {len(targets)} target(s): {targets}")
    subprocess.run([sys.executable, "-m", "ruff", "check"] + targets, check=True)
    subprocess.run([sys.executable, "-m", "ruff", "format", "--check"] + targets, check=True)
    return 0


def check_tests():
    changed_files, is_pr = get_changed_files()
    test_targets = []

    # Map changed files to relevant test suites
    module_test_map = {
        "common": ["tests/test_guikit*.py"],
        "fanfic": ["tests/test_fanfic*.py", "tests/test_flaresolverr*.py"],
        "furry": ["tests/test_furry*.py"],
        "gemini": ["tests/test_gemini*.py"],
        "media": ["tests/test_media*.py"],
        "metabods": ["tests/test_metabods*.py"],
        "nifty": ["tests/test_nifty*.py"],
        "reddit": ["tests/test_reddit*.py"],
        "scrubber": ["tests/test_scrubber*.py"],
        "scrubber_plugin": ["tests/test_scrubber*.py"],
        "story": ["tests/test_story*.py"],
        "takeout": ["tests/test_takeout*.py"],
        "takeout_plugin": ["tests/test_takeout*.py"],
    }

    for f in changed_files:
        f_norm = f.replace("\\", "/")
        if f_norm.startswith("tests/test_") and f_norm.endswith(".py") and os.path.exists(f_norm):
            test_targets.append(f_norm)
        else:
            top_dir = f_norm.split("/")[0].lower()
            if top_dir in module_test_map:
                for pattern in module_test_map[top_dir]:
                    for match in glob.glob(pattern):
                        if os.path.exists(match):
                            test_targets.append(match.replace("\\", "/"))
            else:
                for match in glob.glob(f"tests/test_{top_dir}*.py"):
                    if os.path.exists(match):
                        test_targets.append(match.replace("\\", "/"))

    test_targets = sorted(list(set(test_targets)))

    if is_pr:
        if not test_targets:
            print(f"No unit test suites affected by PR changes (base: {os.environ.get('BASE_REF')}). Skipping tests.")
            return 0
        targets = test_targets
    else:
        targets = test_targets if test_targets else ["tests/"]

    print(f"Running unit tests on {len(targets)} target(s): {targets}")
    cmd = [sys.executable, "-m", "pytest"] + targets
    subprocess.run(cmd, check=True)
    return 0


def verify_zips():
    expected_zips = [
        "TakeoutBookFixer.zip",
        "EpubPromoScrubber.zip",
        "MetabodsDownloader.zip",
        "NiftyDownloader.zip",
        "StoryCollectionTagger.zip",
        "FanficDownloader.zip",
        "RedditStoryFollower.zip",
        "GeminiLibraryFixer.zip",
        "MediaMatcher.zip",
        "InkbunnySoFurryDownloader.zip",
        "CalibreLink.zip",
    ]
    dist_dir = "dist"
    missing = [z for z in expected_zips if not os.path.exists(os.path.join(dist_dir, z))]
    if missing:
        print(f"Error: Missing expected zip files in dist/: {missing}", file=sys.stderr)
        sys.exit(1)
    print(f"Successfully verified all {len(expected_zips)} plugin archives in dist/.")
    return 0


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_ci_checks.py [lint|test|verify-zips]", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]
    if command == "lint":
        sys.exit(check_lint())
    elif command == "test":
        sys.exit(check_tests())
    elif command == "verify-zips":
        sys.exit(verify_zips())
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
