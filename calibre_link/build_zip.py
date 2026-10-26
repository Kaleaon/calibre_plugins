#!/usr/bin/env python3
"""Build CalibreLink.zip from this folder (repo layout).

The plugin sources live flat in this folder, as calibre expects in
the zip. The tsnet sidecar binaries live in tailscale/ and are built
by sidecar/build.sh from the Go sources in sidecar/. The build fails
if they are missing — a plugin without its Tailscale node would
install "successfully" and then do nothing, which is the worst
outcome.

Note: the repo-wide build_plugins.py only bundles .py/.json files,
so it cannot produce this plugin's zip yet; use this script (the
sidecar build needs a Go toolchain, which the repo CI does not
currently install).
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "CalibreLink.zip")
FILES = ["__init__.py", "ui.py", "config.py", "adapter.py", "server.py",
         "plugin-import-name-calibre_link.txt", "README.md"]
SIDECARS = ["tailscale/calibre-tsnet-windows-amd64.exe",
            "tailscale/calibre-tsnet-linux-amd64"]

missing = [f for f in FILES + SIDECARS
           if not os.path.exists(os.path.join(HERE, f))]
if missing:
    print("MISSING FILES:", *missing, sep="\n  ")
    print("Build the sidecars first: sidecar/build.sh")
    sys.exit(1)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for name in FILES + SIDECARS:
        z.write(os.path.join(HERE, name), name)
print("built", OUT)
with zipfile.ZipFile(OUT) as z:
    for i in z.infolist():
        print("  ", i.filename, i.file_size)
