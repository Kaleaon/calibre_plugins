#!/bin/sh
# Build the calibre-tsnet sidecar for Windows + Linux into ../tailscale/
# (repo layout: calibre_link/tailscale/). The plugin zip built by
# ../build_zip.py bundles those binaries; they are build artifacts and
# are not committed (the repo ignores *.zip, and the binaries are
# produced here from the Go sources in this folder).
#
# Sources: github.com/tailscale/tailscale (module tailscale.com), tsnet.
# Requires Go >= the version in go.mod. If Go is not on PATH, the
# Tinker VM layout (~/workspace/tools/go) is tried as a fallback.
set -e
cd "$(dirname "$0")"
if ! command -v go >/dev/null 2>&1; then
    export PATH="$HOME/workspace/tools/go/bin:$PATH"
fi
# Keep Go's caches and temp dir off small tmpfs mounts when the caller
# has not configured them (the Tinker VM's /tmp is 512 MB and the
# tailscale module build fills it). Respect caller-set values.
export GOPATH="${GOPATH:-$HOME/workspace/tools/gopath}"
export GOMODCACHE="${GOMODCACHE:-$GOPATH/pkg/mod}"
export GOCACHE="${GOCACHE:-$HOME/workspace/tools/gocache}"
mkdir -p "$HOME/workspace/tools/gotmp"
export GOTMPDIR="${GOTMPDIR:-$HOME/workspace/tools/gotmp}"
export TMPDIR="${TMPDIR:-$HOME/workspace/tools/gotmp}"
mkdir -p ../tailscale
CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -trimpath -ldflags "-s -w" \
    -o ../tailscale/calibre-tsnet-windows-amd64.exe .
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags "-s -w" \
    -o ../tailscale/calibre-tsnet-linux-amd64 .
ls -la ../tailscale/
