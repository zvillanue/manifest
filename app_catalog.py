"""
app_catalog.py — apps the post-install script generator (scriptgen.py) knows
how to install.

To add a new app, add an entry to APPS below. Fields:
  name          display name shown in the web UI / CLI listing
  apt/dnf/pacman  native package name for that family, or None if not
                  reliably available there
  flatpak       Flathub app id, or None if there isn't one
  prefer        "system" (try apt/dnf/pacman first, flatpak as fallback) or
                "flatpak" (try flatpak first — used where native packages
                vary too much across distros to be worth the inconsistency)
  manual_note   shown instead of an install step when neither a native
                package nor a flatpak is available; never guess a download
                URL here, just tell the operator where to get it
  special       marks an app that needs extra handling beyond "install a
                package" — see scriptgen.py's SPECIAL_HANDLERS
"""

APPS = {
    "firefox": {
        "name": "Firefox",
        "apt": "firefox", "dnf": "firefox", "pacman": "firefox",
        "flatpak": "org.mozilla.firefox",
        "prefer": "system",
    },
    "libreoffice": {
        "name": "LibreOffice",
        "apt": "libreoffice", "dnf": "libreoffice", "pacman": "libreoffice-fresh",
        "flatpak": "org.libreoffice.LibreOffice",
        "prefer": "system",
    },
    "thunderbird": {
        "name": "Thunderbird",
        "apt": "thunderbird", "dnf": "thunderbird", "pacman": "thunderbird",
        "flatpak": "org.mozilla.Thunderbird",
        "prefer": "system",
    },
    "vlc": {
        "name": "VLC",
        "apt": "vlc", "dnf": "vlc", "pacman": "vlc",
        "flatpak": "org.videolan.VLC",
        "prefer": "system",
    },
    "gimp": {
        "name": "GIMP",
        "apt": "gimp", "dnf": "gimp", "pacman": "gimp",
        "flatpak": "org.gimp.GIMP",
        "prefer": "system",
    },
    "timeshift": {
        "name": "Timeshift",
        "apt": "timeshift", "dnf": "timeshift", "pacman": None,
        "flatpak": None,
        "prefer": "system",
        "manual_note": (
            "Timeshift: not packaged for Arch outside the AUR, and redundant "
            "with snapper on a btrfs system — skip it if snapper is enabled."
        ),
    },
    "signal": {
        "name": "Signal Desktop",
        "apt": None, "dnf": None, "pacman": None,
        "flatpak": "org.signal.Signal",
        "prefer": "flatpak",
    },
    "keepassxc": {
        "name": "KeePassXC",
        "apt": "keepassxc", "dnf": "keepassxc", "pacman": "keepassxc",
        "flatpak": "org.keepassxc.KeePassXC",
        "prefer": "system",
    },
    "veracrypt": {
        "name": "VeraCrypt",
        "apt": None, "dnf": None, "pacman": None,
        "flatpak": None,
        "manual_note": "VeraCrypt: no repo/Flatpak package — install manually from veracrypt.fr and verify the signature.",
    },
    "torbrowser": {
        "name": "Tor Browser (via torbrowser-launcher)",
        "apt": "torbrowser-launcher", "dnf": "torbrowser-launcher", "pacman": None,
        "flatpak": "org.torproject.torbrowser-launcher",
        "prefer": "system",
    },
    "mullvad-browser": {
        "name": "Mullvad Browser",
        "apt": None, "dnf": None, "pacman": None,
        "flatpak": None,
        "manual_note": "Mullvad Browser: no repo/Flatpak package — download and verify from mullvad.net together with the buyer.",
    },
    "ublock-origin": {
        "name": "uBlock Origin (Firefox extension)",
        "apt": None, "dnf": None, "pacman": None,
        "flatpak": None,
        "special": "ublock_origin_firefox_policy",
    },
    "onionshare": {
        "name": "OnionShare",
        "apt": None, "dnf": None, "pacman": None,
        "flatpak": "org.onionshare.OnionShare",
        "prefer": "flatpak",
    },
    "dangerzone": {
        "name": "Dangerzone",
        "apt": None, "dnf": None, "pacman": None,
        "flatpak": "press.dangerzone.dangerzone",
        "prefer": "flatpak",
    },
}
