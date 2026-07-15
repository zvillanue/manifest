#!/usr/bin/env bash
#
# laptop-tier2-privacy.sh
# Standard Load-outs: Tier 2 — Privacy (Debian stable; for Fedora swap apt
# calls for dnf and adjust package names — signal/mullvad/veracrypt package
# sources differ, see comments below).
#
# Register this against a build id with:
#   fleetctl build register --id laptop-tier2-privacy-v1 --line laptop --tier 2 \
#       --script postinstall/laptop-tier2-privacy.sh --desc "Debian privacy load-out"

set -euo pipefail

echo "== Everything in Tier 1 first =="
"$(dirname "$0")/laptop-tier1-everyday.sh"

echo "== Signal Desktop (adds Signal's own apt repo + key) =="
wget -qO- https://updates.signal.org/desktop/apt/keys.asc | sudo gpg --dearmor -o /usr/share/keyrings/signal-desktop-keyring.gpg
echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/signal-desktop-keyring.gpg] https://updates.signal.org/desktop/apt xenial main' | sudo tee /etc/apt/sources.list.d/signal-xenial.list
sudo apt update
sudo apt install -y signal-desktop

echo "== KeePassXC, VeraCrypt (Debian backports/contrib may be needed) =="
sudo apt install -y keepassxc veracrypt

echo "== Tor Browser (via torbrowser-launcher) =="
sudo apt install -y torbrowser-launcher

echo "== Mullvad Browser: download+verify manually from mullvad.net/download — no apt package =="

echo "== uBlock Origin: install into Firefox profile manually or via policies.json =="

echo "== DNS-over-HTTPS + telemetry off + firewall =="
sudo apt install -y ufw
sudo ufw enable
sudo ufw default deny incoming
sudo ufw default allow outgoing

echo "== Done. Remaining manual steps: =="
echo "  - Load Mullvad Browser + uBlock Origin in Firefox by hand"
echo "  - Confirm DNS-over-HTTPS enabled in Firefox settings (network.trr)"
echo "  - Camera cover included in box"
