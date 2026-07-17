"""
distro.py — figures out which native package family this machine belongs to
(apt/dnf/pacman) and whether Flatpak is available.

Deliberately stdlib-only (no Qt, no fleetlib) so it can be imported and
tested without pulling in the GUI or the encrypted-DB core — see
installer_gui's own note on dependency isolation.

Detection is ID/ID_LIKE-based rather than a fixed table of five distros,
because the standalone download (unlike the pre-installed copy) has to work
on whatever Linux the customer happens to be running, not just fleetctl's
five build targets.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

# Search order matters: a distro can plausibly have more than one of these
# binaries present (e.g. an Arch box with `debtap`'s dpkg dependency pulled
# in), so os-release ID/ID_LIKE is checked first and PATH lookups are only a
# fallback for something os-release didn't tell us.
_ID_LIKE_FAMILY = {
    "debian": "apt", "ubuntu": "apt",
    "fedora": "dnf", "rhel": "dnf", "centos": "dnf", "suse": "dnf", "opensuse": "dnf",
    "arch": "pacman",
}

_BINARY_FAMILY = [
    ("apt-get", "apt"),
    ("dnf", "dnf"),
    ("pacman", "pacman"),
]


def _read_os_release() -> dict:
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            text = path.read_text()
        except OSError:
            continue
        fields = {}
        for line in text.splitlines():
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip().strip('"')
        return fields
    return {}


def detect_family() -> str | None:
    """Returns "apt", "dnf", "pacman", or None if none could be determined."""
    fields = _read_os_release()
    ids = [fields.get("ID", "")] + fields.get("ID_LIKE", "").split()
    for id_ in ids:
        family = _ID_LIKE_FAMILY.get(id_)
        if family:
            return family

    for binary, family in _BINARY_FAMILY:
        if shutil.which(binary):
            return family

    return None


def flatpak_available() -> bool:
    return shutil.which("flatpak") is not None


def flathub_remote_present() -> bool:
    """Best-effort check that the flathub remote is already configured.

    fleetctl's own post-install script adds this unconditionally on every
    target it builds (see scriptgen.py), so on an Obsidian Devices laptop
    this is always True. On a customer's own pre-existing Linux install
    (standalone download path) it might not be, so the GUI still checks
    rather than assuming.
    """
    if not flatpak_available():
        return False
    try:
        result = subprocess.run(
            ["flatpak", "remotes"], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "flathub" in result.stdout.lower()


FAMILY_LABELS = {"apt": "Debian/Ubuntu-based (apt)", "dnf": "Fedora-based (dnf)", "pacman": "Arch-based (pacman)"}
