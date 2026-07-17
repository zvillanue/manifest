#!/bin/sh
# build_standalone.sh — builds the portable single-file obsidian-installer
# binary (PyInstaller --onefile), for both the pre-installed and the
# website-download paths.
#
# IMPORTANT — Linux PyInstaller binaries are NOT truly distro-portable:
# they still dynamically link against the build machine's glibc, and a
# binary built on a newer glibc (e.g. Arch, which tracks bleeding-edge)
# will fail with a "version `GLIBC_2.XX' not found" error on an older
# system (e.g. Debian stable). There is no --onefile flag that fixes this.
#
# So: build this on the OLDEST glibc among fleetctl's targets, not on your
# Arch dev machine. Debian stable is the oldest of the five
# (mint/ubuntu/debian/fedora/arch), so build there — a plain Debian stable
# container is enough, no need for a full desktop install:
#
#   docker run --rm -v "$PWD/..:/src" -w /src/installer_gui debian:stable sh packaging/build_standalone.sh
#
# A binary built this way runs fine on newer-glibc systems (Ubuntu, Fedora,
# Arch) since glibc is backwards-compatible — just not the reverse.

set -e

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
INSTALLER_GUI_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

if ! command -v python3 >/dev/null 2>&1; then
    echo "error: python3 not found" >&2
    exit 1
fi

cd "$INSTALLER_GUI_DIR"

python3 -m venv .build-venv
. .build-venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt pyinstaller >/dev/null

pyinstaller --onefile --name obsidian-installer --noconfirm --clean app.py

deactivate

echo
echo "Built: $INSTALLER_GUI_DIR/dist/obsidian-installer"
echo "Copy that binary next to install-integration.sh before running it,"
echo "or upload it directly for the website standalone-download path."
