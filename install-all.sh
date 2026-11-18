#!/bin/sh
# Download and install/update the whole calibre plugin pack.
# Extra arguments are passed through to install_all.py.
exec python3 "$(dirname "$0")/install_all.py" --download "$@"
