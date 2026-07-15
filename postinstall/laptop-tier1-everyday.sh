#!/usr/bin/env bash
#
# laptop-tier1-everyday.sh
# Standard Load-outs: Tier 1 — Everyday (Linux Mint Cinnamon LTS)
# Run on a fresh Mint install, as the user, with sudo available.
#
# Register this against a build id with:
#   fleetctl build register --id laptop-tier1-mint-v1 --line laptop --tier 1 \
#       --script postinstall/laptop-tier1-everyday.sh --desc "Mint Cinnamon everyday load-out"

set -euo pipefail

echo "== Updating base system =="
sudo apt update
sudo apt full-upgrade -y

echo "== Firmware updates =="
sudo apt install -y fwupd
sudo fwupdmgr refresh --force || true
sudo fwupdmgr update -y || true

echo "== Base apps (Firefox, LibreOffice, Thunderbird, VLC, GIMP already ship with Mint; fill any gaps) =="
sudo apt install -y firefox libreoffice thunderbird vlc gimp timeshift

echo "== Timeshift: schedule an initial snapshot =="
sudo timeshift --create --comments "post-install baseline" --tags D || true

echo "== Flatpak =="
sudo apt install -y flatpak gnome-software-plugin-flatpak
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo

echo "== Unattended security updates =="
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -f noninteractive unattended-upgrades

echo "== Printer stack =="
sudo apt install -y cups system-config-printer

echo "== Done. Remaining manual steps per checklist: =="
echo "  - Verify LUKS temp passphrase (set at install time by fleetctl 'new')"
echo "  - Test Wi-Fi/BT/suspend-resume/webcam/mic/audio/ports/function keys"
echo "  - Battery burn-in: full charge -> 1hr video playback test"
