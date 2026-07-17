#!/bin/sh
# install-integration.sh — registers the built obsidian-installer binary as
# a file-manager double-click handler for .deb/.rpm/.flatpak/.flatpakref.
#
# Usage: sudo ./install-integration.sh [path-to-obsidian-installer-binary]
#   (defaults to ./obsidian-installer next to this script, i.e. the output
#   of build_standalone.sh)
#
# Called by fleetctl's post-install script generator (scriptgen.py) during
# imaging, where it's already running as root. A standalone-download user
# can also run it by hand after building/downloading the binary, though it's
# optional there — "Open With -> Obsidian Installer" once in the file
# manager works just as well without a system-wide default.

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
BINARY_SRC="${1:-$SCRIPT_DIR/obsidian-installer}"

if [ ! -f "$BINARY_SRC" ]; then
    echo "error: no binary at $BINARY_SRC — build it first with build_standalone.sh" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "error: run as root (this writes to /usr/local/bin and /usr/share)" >&2
    exit 1
fi

install -Dm755 "$BINARY_SRC" /usr/local/bin/obsidian-installer
install -Dm644 "$SCRIPT_DIR/obsidian-installer.desktop" /usr/share/applications/obsidian-installer.desktop
install -Dm644 "$SCRIPT_DIR/mime/obsidian-installer-mime.xml" /usr/share/mime/packages/obsidian-installer.xml

update-mime-database /usr/share/mime >/dev/null 2>&1 || true
update-desktop-database /usr/share/applications >/dev/null 2>&1 || true

# Make it the system-wide default for these types, so a plain double-click
# in the file manager just works on a fresh unit, no "Open With" step.
MIMEAPPS=/usr/share/applications/mimeapps.list
if [ ! -f "$MIMEAPPS" ]; then
    printf '[Default Applications]\n' > "$MIMEAPPS"
elif ! grep -q '^\[Default Applications\]' "$MIMEAPPS"; then
    printf '[Default Applications]\n' >> "$MIMEAPPS"
fi
for mime in \
    application/vnd.debian.binary-package \
    application/x-rpm \
    application/x-redhat-package-manager \
    application/vnd.flatpak \
    application/vnd.flatpak.ref
do
    if grep -q "^${mime}=" "$MIMEAPPS" 2>/dev/null; then
        sed -i "s|^${mime}=.*|${mime}=obsidian-installer.desktop|" "$MIMEAPPS"
    else
        sed -i "/^\[Default Applications\]/a ${mime}=obsidian-installer.desktop" "$MIMEAPPS"
    fi
done

echo "Obsidian Installer registered as the default handler for .deb/.rpm/.flatpak/.flatpakref."
