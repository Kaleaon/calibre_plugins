"""Verification script to confirm all expected plugin zip packages were built into dist/."""

import os
import sys

EXPECTED_ZIPS = [
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


def main() -> int:
    dist_dir = "dist"
    missing = [
        z for z in EXPECTED_ZIPS if not os.path.exists(os.path.join(dist_dir, z))
    ]
    if missing:
        print(f"Error: Missing expected zip files in dist/: {missing}", file=sys.stderr)
        return 1
    print(f"Successfully verified all {len(EXPECTED_ZIPS)} plugin archives in dist/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
