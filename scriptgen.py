"""
scriptgen.py — generates a post-install shell script for a chosen OS target,
app selection, and set of policy toggles (passwords, snapper, firewall, etc).

Used by both the CLI (`fleetctl script generate`) and the web GUI's
/generate page — neither talks to bash directly, they both just call
generate_script() and get a string back.

Design notes worth knowing before you extend this:

- The generated script is meant to be reusable across many physical units of
  the same build (consistent with fleetctl's builds/units split), so it
  never bakes in a specific unit's serial or temp passphrase. The LUKS
  password step prompts for the current (temp) and new passphrase
  interactively via cryptsetup's own prompts — the operator/buyer types the
  temp passphrase from the handoff card once, same as changing any password.

- Nothing here adds a third-party repo/PPA/COPR automatically except RPM
  Fusion on Fedora for codecs, which is treated by the Fedora project itself
  as a standard, expected part of a Fedora desktop setup (documented on
  Fedora's own wiki) — not a random unverified source. Anywhere else a
  feature would require a third-party repo (e.g. grub-btrfs outside Arch),
  the script skips it and prints where to get it manually instead.

- UEFI/BIOS passwords cannot be set from inside Linux in a way that works
  across vendors — there's no such generic OS-level API. The "UEFI
  password" toggle only controls whether a manual reminder is printed.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

from app_catalog import APPS

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

FAMILIES = ("apt", "dnf", "pacman")

OS_TARGETS = {
    "mint": {"name": "Linux Mint Cinnamon", "family": "apt"},
    "ubuntu": {"name": "Ubuntu", "family": "apt"},
    "debian": {"name": "Debian stable", "family": "apt"},
    "fedora": {"name": "Fedora Workstation", "family": "dnf"},
    "arch": {"name": "Arch Linux", "family": "pacman"},
}

# Desktop environment each target actually ships, as far as fleetctl's own
# builds are concerned — used only to decide how (or whether) to set the OEM
# wallpaper. Arch has no default DE (it's a DIY install), so wallpaper setup
# is skipped there rather than guessing.
OS_DESKTOP = {"mint": "cinnamon", "ubuntu": "gnome", "debian": "gnome", "fedora": "gnome", "arch": None}

PKG_MANAGER_INFO = {
    "apt": {"name": "apt", "install": "sudo apt install <package>",
            "update": "sudo apt update && sudo apt upgrade", "search": "apt search <name>"},
    "dnf": {"name": "dnf", "install": "sudo dnf install <package>",
            "update": "sudo dnf upgrade", "search": "dnf search <name>"},
    "pacman": {"name": "pacman", "install": "sudo pacman -S <package>",
               "update": "sudo pacman -Syu", "search": "pacman -Ss <name>"},
}

# "Stay safe online" default bookmarks (Firefox policy) — a small,
# well-known, non-partisan set, not an exhaustive list.
OEM_BOOKMARKS = [
    {"Title": "Electronic Frontier Foundation", "URL": "https://www.eff.org/",
     "Placement": "toolbar", "Folder": "Stay Safe Online"},
    {"Title": "Privacy Guides", "URL": "https://www.privacyguides.org/",
     "Placement": "toolbar", "Folder": "Stay Safe Online"},
    {"Title": "Tor Project", "URL": "https://www.torproject.org/",
     "Placement": "toolbar", "Folder": "Stay Safe Online"},
    {"Title": "Terms of Service; Didn't Read", "URL": "https://tosdr.org/",
     "Placement": "toolbar", "Folder": "Stay Safe Online"},
    {"Title": "Have I Been Pwned", "URL": "https://haveibeenpwned.com/",
     "Placement": "toolbar", "Folder": "Stay Safe Online"},
]

GUIDE_FOLDER_NAME = "Getting Started with Linux"

# (source file in assets/guide/, display filename on the buyer's Desktop) —
# an explicit mapping rather than derived from the filename, since the
# display names are referenced by name inside 01-welcome.txt itself.
GUIDE_FILES = [
    ("01-welcome.txt", "01 - Welcome.txt"),
    ("02-installing-software.txt", "02 - Installing software.txt"),
    ("03-finding-your-files.txt", "03 - Finding your files.txt"),
    ("04-staying-secure.txt", "04 - Staying secure.txt"),
    ("05-using-the-terminal-optional.txt", "05 - Using the terminal (optional).txt"),
    ("06-getting-help.txt", "06 - Getting help.txt"),
]

# Ubuntu ships Firefox/Thunderbird as transitional snap packages by default,
# which fights with "I want apps installed via the default package manager
# and Flatpak" — force these two to Flatpak on Ubuntu specifically rather
# than fighting the snap transitional package.
OS_APP_OVERRIDES = {
    "ubuntu": {"firefox": "flatpak", "thunderbird": "flatpak"},
}


@dataclass
class GenOptions:
    os_id: str
    apps: list = field(default_factory=list)
    luks_password_enabled: bool = True     # False -> enroll TPM2 instead
    uefi_password_reminder: bool = False
    force_user_password_change: bool = True
    auto_updates: bool = True
    firewall: bool = True
    zram_tlp: bool = True
    printing_codecs: bool = True
    grub_btrfs: bool = True
    snapper: bool = True
    oem_wallpaper: bool = True
    oem_bookmarks: bool = True
    oem_guide_folder: bool = True


def _resolve_app_install(app_id: str, os_id: str, family: str):
    """Returns (method, value) where method is system/flatpak/manual/special/skip."""
    app = APPS[app_id]
    if app.get("special"):
        return ("special", app["special"])

    override = OS_APP_OVERRIDES.get(os_id, {}).get(app_id)
    prefer = override or app.get("prefer", "system")
    order = [prefer, "flatpak" if prefer == "system" else "system"]

    for method in order:
        if method == "system":
            pkg = app.get(family)
            if pkg:
                return ("system", pkg)
        elif method == "flatpak":
            fid = app.get("flatpak")
            if fid:
                return ("flatpak", fid)

    if app.get("manual_note"):
        return ("manual", app["manual_note"])
    return ("skip", f"No install method available for {app['name']} on this OS")


def _section_header(opts: GenOptions) -> str:
    os_name = OS_TARGETS[opts.os_id]["name"]
    return f"""\
#!/usr/bin/env bash
# Generated by fleetctl's script generator for: {os_name}
# Re-run is safe (idempotent-ish) but not required — this is meant to run
# once per unit during refurb, as the account that will be handed to the buyer.
set -uo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo." >&2
    exit 1
fi

TARGET_USER="${{SUDO_USER:-$(logname 2>/dev/null || echo root)}}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
echo "==> Running post-install for user: $TARGET_USER"
"""


def _section_package_manager(family: str) -> str:
    if family == "apt":
        return """\
echo "==> Updating system (apt) =="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y

echo "==> Flatpak + Flathub =="
apt-get install -y flatpak
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
"""
    if family == "dnf":
        return """\
echo "==> Updating system (dnf) =="
dnf upgrade -y --refresh

echo "==> Flatpak + Flathub =="
dnf install -y flatpak
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
"""
    return """\
echo "==> Updating system (pacman) =="
pacman -Syu --noconfirm

echo "==> Flatpak + Flathub =="
pacman -S --noconfirm --needed flatpak
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
"""


def _section_auto_updates(family: str) -> str:
    if family == "apt":
        return """\
echo "==> Automatic security updates (unattended-upgrades) =="
apt-get install -y unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades
"""
    if family == "dnf":
        return """\
echo "==> Automatic security updates (dnf-automatic) =="
dnf install -y dnf-automatic
sed -i 's/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf
systemctl enable --now dnf-automatic-install.timer
"""
    # pacman: notify-only, never unattended (see module docstring / README)
    return """\
echo "==> Update checker (notify-only — Arch upgrades need a human) =="
pacman -S --noconfirm --needed pacman-contrib libnotify
cat > /usr/local/bin/fleetctl-update-check.sh <<'EOS'
#!/usr/bin/env bash
COUNT=$(checkupdates 2>/dev/null | wc -l)
if [ "$COUNT" -gt 0 ]; then
    su - "$TARGET_USER" -c "notify-send 'Updates available' '$COUNT package(s) can be updated. Run: sudo pacman -Syu'" || true
fi
EOS
chmod +x /usr/local/bin/fleetctl-update-check.sh
cat > /etc/systemd/system/fleetctl-update-check.service <<EOS
[Unit]
Description=Check for pacman updates (notify only, never installs)

[Service]
Type=oneshot
Environment=TARGET_USER=$TARGET_USER
ExecStart=/usr/local/bin/fleetctl-update-check.sh
EOS
cat > /etc/systemd/system/fleetctl-update-check.timer <<'EOS'
[Unit]
Description=Daily pacman update check

[Timer]
OnBootSec=10min
OnUnitActiveSec=1d

[Install]
WantedBy=timers.target
EOS
systemctl daemon-reload
systemctl enable --now fleetctl-update-check.timer
"""


def _section_fwupd(family: str) -> str:
    install = {"apt": "apt-get install -y fwupd", "dnf": "dnf install -y fwupd",
               "pacman": "pacman -S --noconfirm --needed fwupd"}[family]
    return f"""\
echo "==> Firmware updates (fwupd) =="
{install}
systemctl enable --now fwupd.service 2>/dev/null || true
fwupdmgr refresh --force || true
fwupdmgr update -y || true
"""


def _section_firewall(family: str) -> str:
    if family == "dnf":
        # Fedora Workstation ships firewalld enabled by default — don't fight it.
        return """\
echo "==> Firewall (firewalld, already Fedora's default) =="
systemctl enable --now firewalld 2>/dev/null || true
"""
    pkg = "ufw"
    install = {"apt": "apt-get install -y ufw", "pacman": "pacman -S --noconfirm --needed ufw"}[family]
    return f"""\
echo "==> Firewall ({pkg}) =="
{install}
ufw default deny incoming
ufw default allow outgoing
ufw --force enable
"""


def _section_zram_tlp(family: str) -> str:
    tlp_install = {"apt": "apt-get install -y tlp", "dnf": "dnf install -y tlp",
                   "pacman": "pacman -S --noconfirm --needed tlp"}[family]
    lines = [f'echo "==> Power management (TLP) =="', tlp_install, "systemctl enable --now tlp 2>/dev/null || true", ""]
    if family == "apt":
        lines += [
            'echo "==> zram swap (zram-tools) =="',
            "apt-get install -y zram-tools",
            "systemctl enable --now zramswap.service 2>/dev/null || true",
        ]
    elif family == "dnf":
        lines += [
            'echo "==> zram swap (Fedora enables this by default since F33 — ensuring it'"'"'s present) =="',
            "dnf install -y zram-generator-defaults",
        ]
    else:
        lines += [
            'echo "==> zram swap (zram-generator) =="',
            "pacman -S --noconfirm --needed zram-generator",
            "cat > /etc/systemd/zram-generator.conf <<'EOS'\n[zram0]\nzram-size = min(ram / 2, 4096)\n"
            "compression-algorithm = zstd\nEOS",
            "systemctl daemon-reload",
            "systemctl start /dev/zram0 2>/dev/null || true",
        ]
    return "\n".join(lines) + "\n"


def _section_printing_codecs(family: str) -> str:
    if family == "apt":
        return """\
echo "==> Printing (CUPS) =="
apt-get install -y cups system-config-printer avahi-daemon
systemctl enable --now cups avahi-daemon 2>/dev/null || true

echo "==> Media codecs (GStreamer) =="
apt-get install -y gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \\
    gstreamer1.0-plugins-ugly gstreamer1.0-libav ffmpeg
"""
    if family == "dnf":
        return """\
echo "==> Printing (CUPS) =="
dnf install -y cups system-config-printer avahi
systemctl enable --now cups avahi-daemon 2>/dev/null || true

echo "==> Media codecs (RPM Fusion + GStreamer) =="
# RPM Fusion is Fedora's own standard third-party repo for codecs/drivers —
# documented on Fedora's wiki, not an arbitrary third-party source.
dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" \\
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$(rpm -E %fedora).noarch.rpm" || \\
    echo "RPM Fusion setup failed — codecs skipped, install manually: https://rpmfusion.org/Configuration"
dnf install -y gstreamer1-plugins-good gstreamer1-plugins-bad-free gstreamer1-plugins-ugly \\
    gstreamer1-libav ffmpeg || true
"""
    return """\
echo "==> Printing (CUPS) =="
pacman -S --noconfirm --needed cups avahi
systemctl enable --now cups avahi-daemon 2>/dev/null || true

echo "==> Media codecs (GStreamer) =="
pacman -S --noconfirm --needed gst-plugins-good gst-plugins-bad gst-plugins-ugly gst-libav ffmpeg
"""


def _resolve_apps(opts: GenOptions, family: str) -> dict:
    system_pkgs, flatpak_ids, manual_notes, special_ids, skipped = [], [], [], [], []
    for app_id in opts.apps:
        if app_id not in APPS:
            skipped.append(f"Unknown app id '{app_id}' — skipped.")
            continue
        method, value = _resolve_app_install(app_id, opts.os_id, family)
        if method == "system":
            system_pkgs.append(value)
        elif method == "flatpak":
            flatpak_ids.append(value)
        elif method == "manual":
            manual_notes.append(value)
        elif method == "special":
            special_ids.append(value)
        else:
            skipped.append(value)
    return {
        "system_pkgs": system_pkgs, "flatpak_ids": flatpak_ids,
        "manual_notes": manual_notes, "special_ids": special_ids, "skipped": skipped,
    }


def _section_apps(resolved: dict, family: str) -> str:
    lines = []
    if resolved["system_pkgs"]:
        install = {"apt": "apt-get install -y", "dnf": "dnf install -y",
                   "pacman": "pacman -S --noconfirm --needed"}[family]
        lines.append('echo "==> Installing apps (system package manager) =="')
        lines.append(f"{install} {' '.join(resolved['system_pkgs'])}")
    if resolved["flatpak_ids"]:
        lines.append('echo "==> Installing apps (Flatpak) =="')
        lines.append(f"flatpak install -y flathub {' '.join(resolved['flatpak_ids'])}")
    for note in resolved["manual_notes"]:
        lines.append(f'echo "NOTE: {note}"')
    for note in resolved["skipped"]:
        lines.append(f'echo "SKIPPED: {note}"')
    return "\n".join(lines) + "\n" if lines else ""


def _firefox_policies_snippet(flatpak_ids: list, ublock_requested: bool, bookmarks_requested: bool) -> str:
    # Firefox's enterprise policies.json is one file — uBlock Origin's forced
    # install and the OEM bookmarks both need to land in it, so this builds
    # one combined policy object and writes it once, rather than each
    # feature clobbering the other's write.
    if not (ublock_requested or bookmarks_requested):
        return ""

    policies: dict = {}
    if ublock_requested:
        # Pre-installs and locks the extension so it's there on first
        # launch without the buyer needing to find it themselves.
        policies["ExtensionSettings"] = {
            "uBlock0@raymondhill.net": {
                "install_url": "https://addons.mozilla.org/firefox/downloads/latest/ublock-origin/latest.xpi",
                "installation_mode": "force_installed",
            }
        }
    if bookmarks_requested:
        policies["Bookmarks"] = OEM_BOOKMARKS

    firefox_is_flatpak = "org.mozilla.firefox" in flatpak_ids
    policy_dir = (
        '"$TARGET_HOME/.var/app/org.mozilla.firefox/config/firefox/distribution"'
        if firefox_is_flatpak else "/etc/firefox/policies"
    )
    policy_json = json.dumps({"policies": policies}, indent=2)
    label = " + ".join(
        n for n, requested in (("uBlock Origin", ublock_requested), ("bookmarks", bookmarks_requested)) if requested
    )
    return f"""\
echo "==> Firefox policy ({label}) =="
mkdir -p {policy_dir}
cat > {policy_dir}/policies.json <<'EOS'
{policy_json}
EOS
"""


def _section_snapper(opts: GenOptions) -> str:
    if not opts.snapper:
        return ""
    install = {"apt": "apt-get install -y snapper", "dnf": "dnf install -y snapper",
               "pacman": "pacman -S --noconfirm --needed snapper"}[OS_TARGETS[opts.os_id]["family"]]
    return f"""\
echo "==> Snapper (btrfs snapshots) =="
ROOT_FSTYPE=$(findmnt -n -o FSTYPE /)
if [ "$ROOT_FSTYPE" = "btrfs" ]; then
    {install}
    if [ ! -e /etc/snapper/configs/root ]; then
        snapper -c root create-config /
    fi
    sed -i 's/^TIMELINE_LIMIT_HOURLY=.*/TIMELINE_LIMIT_HOURLY="5"/' /etc/snapper/configs/root 2>/dev/null || true
    sed -i 's/^TIMELINE_LIMIT_DAILY=.*/TIMELINE_LIMIT_DAILY="7"/' /etc/snapper/configs/root 2>/dev/null || true
    sed -i 's/^TIMELINE_LIMIT_WEEKLY=.*/TIMELINE_LIMIT_WEEKLY="4"/' /etc/snapper/configs/root 2>/dev/null || true
    sed -i 's/^TIMELINE_LIMIT_MONTHLY=.*/TIMELINE_LIMIT_MONTHLY="2"/' /etc/snapper/configs/root 2>/dev/null || true
    systemctl enable --now snapper-timeline.timer snapper-cleanup.timer 2>/dev/null || true
    echo "snapper configured on / — automatic timeline snapshots + cleanup enabled."
else
    echo "Root filesystem is $ROOT_FSTYPE, not btrfs — skipping snapper. This build assumes btrfs; check the OS install if that's unexpected."
fi
"""


def _section_grub_btrfs(opts: GenOptions) -> str:
    if not opts.grub_btrfs:
        return ""
    family = OS_TARGETS[opts.os_id]["family"]
    if family == "pacman":
        body = """\
    pacman -S --noconfirm --needed grub-btrfs
    systemctl enable --now grub-btrfsd.service 2>/dev/null || true
    echo "grub-btrfs enabled — snapshots will appear as boot options in the GRUB menu."
"""
    else:
        where = "a PPA" if family == "apt" else "a COPR repo"
        body = f"""\
    echo "grub-btrfs requested but needs {where} on this OS — not adding third-party repos unattended. See https://github.com/Antynea/grub-btrfs to add it manually."
"""
    return f"""\
echo "==> grub-btrfs (boot menu snapshot rollback) =="
ROOT_FSTYPE=$(findmnt -n -o FSTYPE /)
if [ "$ROOT_FSTYPE" = "btrfs" ] && [ -f /etc/default/grub ]; then
{body}
else
    echo "Not btrfs+GRUB — skipping grub-btrfs."
fi
"""


def _section_passwords(opts: GenOptions) -> str:
    lines = []
    if opts.force_user_password_change:
        lines.append('echo "==> Forcing Unix password change at next login =="')
        lines.append('passwd --expire "$TARGET_USER"')

    if opts.luks_password_enabled:
        lines.append(_luks_change_wizard_snippet())
    else:
        lines.append(_tpm_enroll_snippet())

    if opts.uefi_password_reminder:
        lines.append(
            'echo "REMINDER: set a UEFI/BIOS admin password manually in firmware setup before handoff '
            '(cannot be automated from Linux across vendors)."'
        )
    return "\n".join(lines) + "\n" if lines else ""


def _luks_change_wizard_snippet() -> str:
    # No baked-in secrets: this prompts interactively via cryptsetup's own
    # UX, so the same generated script works for any unit of this build.
    return """\
echo "==> LUKS passphrase change (first boot) =="
CRYPT_NAME=$(awk '$1 !~ /^#/ {print $1; exit}' /etc/crypttab 2>/dev/null || true)
if [ -n "$CRYPT_NAME" ]; then
    mkdir -p "$TARGET_HOME/.config/autostart"
    cat > "$TARGET_HOME/.config/autostart/fleetctl-luks-change.desktop" <<EOS
[Desktop Entry]
Type=Application
Exec=/usr/local/bin/fleetctl-luks-change.sh
Name=Set your disk encryption password
X-GNOME-Autostart-Delay=5
EOS
    chown "$TARGET_USER:$TARGET_USER" "$TARGET_HOME/.config/autostart/fleetctl-luks-change.desktop"

    cat > /usr/local/bin/fleetctl-luks-change.sh <<EOS
#!/usr/bin/env bash
MARKER="\\$HOME/.local/state/fleetctl-luks-done"
mkdir -p "\\$(dirname "\\$MARKER")"
[ -f "\\$MARKER" ] && { rm -f "\\$HOME/.config/autostart/fleetctl-luks-change.desktop"; exit 0; }
DEVICE=\\$(sudo cryptsetup status "$CRYPT_NAME" 2>/dev/null | awk -F': *' '/device:/ {print \\$2}')
for term in x-terminal-emulator gnome-terminal konsole xfce4-terminal xterm; do
    if command -v "\\$term" >/dev/null 2>&1; then
        "\\$term" -e bash -c "echo 'This computer'\\''s disk is encrypted. Enter the TEMPORARY passphrase from your handoff card, then choose a new one only you know.'; sudo cryptsetup luksChangeKey \\"\\$DEVICE\\" && touch \\"\\$MARKER\\" && rm -f \\"\\$HOME/.config/autostart/fleetctl-luks-change.desktop\\"; read -p 'Press enter to close...'"
        break
    fi
done
EOS
    chmod +x /usr/local/bin/fleetctl-luks-change.sh
    echo "LUKS change wizard installed — will run at the buyer's first graphical login."
else
    echo "No /etc/crypttab entry found — is this disk actually LUKS-encrypted? Skipping LUKS wizard."
fi
"""


def _tpm_enroll_snippet() -> str:
    return """\
echo "==> TPM2 auto-unlock (replacing LUKS passphrase prompt) =="
CRYPT_NAME=$(awk '$1 !~ /^#/ {print $1; exit}' /etc/crypttab 2>/dev/null || true)
if [ -z "$CRYPT_NAME" ]; then
    echo "No /etc/crypttab entry found — skipping TPM enrollment."
elif ! command -v systemd-cryptenroll >/dev/null 2>&1; then
    echo "systemd-cryptenroll not available on this system — skipping TPM enrollment. Leaving the passphrase prompt in place."
else
    DEVICE=$(cryptsetup status "$CRYPT_NAME" 2>/dev/null | awk -F': *' '/device:/ {print $2}')
    OLD_SLOTS=$(cryptsetup luksDump "$DEVICE" 2>/dev/null | awk '/^ *[0-9]+: luks2$/ {gsub(/:/,"",$1); print $1}')
    OLD_SLOT_COUNT=$(echo "$OLD_SLOTS" | grep -c . || true)

    if systemd-cryptenroll --tpm2-device=list 2>/dev/null | grep -qi tpm; then
        if systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=0+7 "$DEVICE"; then
            echo "TPM2 enrolled for auto-unlock."
            if [ "$OLD_SLOT_COUNT" = "1" ]; then
                OLD_SLOT=$(echo "$OLD_SLOTS" | head -n1)
                echo "Removing the old temp passphrase (slot $OLD_SLOT) now that TPM unlock works — buyer never needs a LUKS passphrase."
                cryptsetup luksKillSlot "$DEVICE" "$OLD_SLOT" || \\
                    echo "Could not remove old passphrase slot automatically — remove it manually: cryptsetup luksKillSlot $DEVICE $OLD_SLOT"
            else
                echo "Found $OLD_SLOT_COUNT existing passphrase slots (expected 1) — leaving them all in place rather than guessing which to remove. Review manually with: cryptsetup luksDump $DEVICE"
            fi
        else
            echo "TPM2 enrollment failed — leaving the temp passphrase in place. Falling back to the passphrase-change wizard."
        fi
    else
        echo "No TPM2 device detected — leaving the temp passphrase in place. Falling back to the passphrase-change wizard."
    fi
fi
"""


def _section_wallpaper(opts: GenOptions) -> str:
    if not opts.oem_wallpaper:
        return ""
    b64 = base64.b64encode((ASSETS_DIR / "wallpaper.png").read_bytes()).decode()
    install_file = f"""\
mkdir -p /usr/share/backgrounds
base64 -d > /usr/share/backgrounds/obsidian-devices.png <<'EOS'
{b64}
EOS
"""
    desktop = OS_DESKTOP.get(opts.os_id)
    if desktop is None:
        return f"""\
echo "==> OEM wallpaper =="
{install_file}\
echo "No known default desktop for this OS target, so the wallpaper wasn't set automatically — image is at /usr/share/backgrounds/obsidian-devices.png, set it manually in your desktop's background settings."
"""
    # GNOME and Cinnamon both read their background settings via dconf/gsettings
    # under the same key names, just different schema paths — a system-wide
    # dconf default (rather than gsettings against a live session, which isn't
    # available yet during post-install) is the standard OEM approach here.
    dconf_path = "org/cinnamon/desktop/background" if desktop == "cinnamon" else "org/gnome/desktop/background"
    return f"""\
echo "==> OEM wallpaper =="
{install_file}\
echo "==> Setting as default desktop background ({desktop}) =="
mkdir -p /etc/dconf/db/local.d /etc/dconf/profile
cat > /etc/dconf/db/local.d/00-obsidian-wallpaper <<'EOS'
[{dconf_path}]
picture-uri='file:///usr/share/backgrounds/obsidian-devices.png'
picture-uri-dark='file:///usr/share/backgrounds/obsidian-devices.png'
picture-options='zoom'
EOS
cat > /etc/dconf/profile/user <<'EOS'
user-db:user
system-db:local
EOS
dconf update
"""


def _section_guide_folder(opts: GenOptions) -> str:
    if not opts.oem_guide_folder:
        return ""
    family = OS_TARGETS[opts.os_id]["family"]
    pkg = PKG_MANAGER_INFO[family]
    guide_dir = ASSETS_DIR / "guide"

    lines = [
        'echo "==> Getting-started guide (Desktop folder) =="',
        f'GUIDE_DIR="$TARGET_HOME/Desktop/{GUIDE_FOLDER_NAME}"',
        'mkdir -p "$GUIDE_DIR"',
    ]
    for src_name, dest_name in GUIDE_FILES:
        content = (guide_dir / src_name).read_text().format(
            os_name=OS_TARGETS[opts.os_id]["name"], pkg_manager_name=pkg["name"],
            install_cmd=pkg["install"], update_cmd=pkg["update"], search_cmd=pkg["search"],
        )
        lines.append(f"cat > \"$GUIDE_DIR/{dest_name}\" <<'EOS'\n{content}EOS")
    lines.append('chown -R "$TARGET_USER:$TARGET_USER" "$GUIDE_DIR"')
    return "\n".join(lines) + "\n"


def generate_script(opts: GenOptions) -> str:
    if opts.os_id not in OS_TARGETS:
        raise ValueError(f"Unknown OS target: {opts.os_id}")
    family = OS_TARGETS[opts.os_id]["family"]

    sections = [
        _section_header(opts),
        _section_package_manager(family),
    ]
    if opts.auto_updates:
        sections.append(_section_auto_updates(family))
    sections.append(_section_fwupd(family))
    if opts.firewall:
        sections.append(_section_firewall(family))
    if opts.zram_tlp:
        sections.append(_section_zram_tlp(family))
    if opts.printing_codecs:
        sections.append(_section_printing_codecs(family))
    resolved_apps = _resolve_apps(opts, family)
    if opts.apps:
        sections.append(_section_apps(resolved_apps, family))
    firefox_policy = _firefox_policies_snippet(
        resolved_apps["flatpak_ids"],
        ublock_requested="ublock_origin_firefox_policy" in resolved_apps["special_ids"],
        bookmarks_requested=opts.oem_bookmarks,
    )
    if firefox_policy:
        sections.append(firefox_policy)
    sections.append(_section_wallpaper(opts))
    sections.append(_section_guide_folder(opts))
    sections.append(_section_snapper(opts))
    sections.append(_section_grub_btrfs(opts))
    sections.append(_section_passwords(opts))
    sections.append('echo "==> Post-install complete. Reboot before handoff QA. =="\n')

    return "\n".join(s for s in sections if s)
