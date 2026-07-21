"""
distro.py — figures out which native package family this machine belongs to
(apt/dnf/pacman) and whether Flatpak is available.

Deliberate copy of installer_gui/distro.py rather than a cross-import: each
GUI here gets embedded standalone into the generated post-install script
(base64-into-heredoc, see scriptgen.py) and built standalone via its own
packaging/build_standalone.sh, so every doctor_gui/*.py file needs to stand
on its own without reaching into a sibling GUI's directory. Keep the two in
sync by hand if detection logic changes — same tradeoff installer_gui itself
made deliberately (stdlib-only, no shared package) for the same reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

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
    target it builds, so on an Obsidian Devices laptop this is always True.
    On a customer's own pre-existing Linux install (standalone download
    path) it might not be, so Doctor still checks rather than assuming.
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
