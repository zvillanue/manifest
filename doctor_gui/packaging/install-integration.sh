#!/bin/sh
# install-integration.sh — registers the built obsidian-doctor binary in the
# application menu.
#
# Usage: sudo ./install-integration.sh [path-to-obsidian-doctor-binary]
#   (defaults to ./obsidian-doctor next to this script, i.e. the output of
#   build_standalone.sh)
#
# Called by fleetctl's post-install script generator (scriptgen.py) during
# imaging, where it's already running as root. A standalone-download user
# can also run it by hand after building/downloading the binary.
#
# No MIME/file-association work here (unlike obsidian-installer) — Doctor
# doesn't open on a file, it's just a menu entry the buyer launches when
# something's broken.

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
BINARY_SRC="${1:-$SCRIPT_DIR/obsidian-doctor}"

if [ ! -f "$BINARY_SRC" ]; then
    echo "error: no binary at $BINARY_SRC — build it first with build_standalone.sh" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root (this writes to /usr/local/bin and /usr/share)" >&2
    exit 1
fi

install -Dm755 "$BINARY_SRC" /usr/local/bin/obsidian-doctor
install -Dm644 "$SCRIPT_DIR/obsidian-doctor.desktop" /usr/share/applications/obsidian-doctor.desktop

update-desktop-database /usr/share/applications >/dev/null 2>&1 || true

echo "Obsidian Doctor added to the application menu."
