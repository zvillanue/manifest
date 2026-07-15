#!/usr/bin/env bash
#
# hardware-inventory.sh
#
# Collects hardware config from the machine it's run on and prints a single
# JSON object to stdout. Meant to be run on the unit during refurb, then
# imported into fleetctl:
#
#   sudo ./hardware-inventory.sh > unit.json
#   fleetctl hardware-import <serial> unit.json
#
# Run with sudo for full detail — dmidecode (system manufacturer/model/serial/
# UUID, BIOS info) needs root. Without root you still get CPU/RAM/storage/PCI
# info, just no dmidecode fields (they'll show as null).
#
# Requires: python3 (for JSON assembly), and standard util-linux/pciutils
# tools. dmidecode/upower are optional — missing tools degrade gracefully
# rather than failing the whole script.

set -uo pipefail

SCRATCH="$(mktemp -d)"
trap 'rm -rf "$SCRATCH"' EXIT

run() {
    # Run a command, capture stdout, never fail the script if it errors
    # or doesn't exist.
    "$@" 2>/dev/null || true
}

have() { command -v "$1" >/dev/null 2>&1; }

if [[ $EUID -ne 0 ]]; then
    echo "note: not running as root — dmidecode fields (make/model/serial/UUID/BIOS) will be null. Re-run with sudo for full detail." >&2
fi

# --- simple key/value fields, one "key<TAB>value" per line ---
{
    if have dmidecode; then
        printf 'system.manufacturer\t%s\n' "$(run dmidecode -s system-manufacturer)"
        printf 'system.product_name\t%s\n' "$(run dmidecode -s system-product-name)"
        printf 'system.serial_number\t%s\n' "$(run dmidecode -s system-serial-number)"
        printf 'system.uuid\t%s\n' "$(run dmidecode -s system-uuid)"
        printf 'bios.vendor\t%s\n' "$(run dmidecode -s bios-vendor)"
        printf 'bios.version\t%s\n' "$(run dmidecode -s bios-version)"
        printf 'bios.release_date\t%s\n' "$(run dmidecode -s bios-release-date)"
    fi

    if have lscpu; then
        printf 'cpu.model\t%s\n' "$(run lscpu | awk -F: '/Model name/ {print $2}' | sed 's/^ *//')"
        printf 'cpu.cores\t%s\n' "$(run nproc)"
    fi

    if have free; then
        printf 'memory.total\t%s\n' "$(run free -h | awk '/^Mem:/ {print $2}')"
    fi

    if have upower; then
        bat="$(run upower -e | grep -i BAT | head -n1)"
        if [[ -n "$bat" ]]; then
            info="$(run upower -i "$bat")"
            printf 'battery.model\t%s\n' "$(echo "$info" | awk -F: '/model/ {print $2}' | sed 's/^ *//')"
            printf 'battery.state\t%s\n' "$(echo "$info" | awk -F: '/state/ {print $2}' | sed 's/^ *//' | head -n1)"
            printf 'battery.energy_full\t%s\n' "$(echo "$info" | awk -F: '/energy-full:/ {print $2}' | sed 's/^ *//')"
            printf 'battery.energy_full_design\t%s\n' "$(echo "$info" | awk -F: '/energy-full-design/ {print $2}' | sed 's/^ *//')"
            printf 'battery.capacity_pct\t%s\n' "$(echo "$info" | awk -F: '/capacity/ {print $2}' | sed 's/^ *//')"
        fi
    fi
} > "$SCRATCH/fields.tsv"

# --- structured blobs, captured separately so we don't hand-roll JSON escaping ---
if have lsblk; then
    run lsblk -J -o NAME,MODEL,SERIAL,SIZE,ROTA,TYPE > "$SCRATCH/lsblk.json"
else
    echo '{}' > "$SCRATCH/lsblk.json"
fi

if have lspci; then
    run lspci | grep -iE 'vga|3d controller' > "$SCRATCH/gpu.txt" || true
    run lspci | grep -iE 'network|ethernet|wireless' > "$SCRATCH/network.txt" || true
else
    : > "$SCRATCH/gpu.txt"
    : > "$SCRATCH/network.txt"
fi

python3 - "$SCRATCH" <<'PYEOF'
import json
import sys
from pathlib import Path

scratch = Path(sys.argv[1])
out = {}

fields_file = scratch / "fields.tsv"
if fields_file.exists():
    for line in fields_file.read_text().splitlines():
        if "\t" not in line:
            continue
        key, _, value = line.partition("\t")
        value = value.strip() or None
        node = out
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

try:
    out["storage"] = json.loads((scratch / "lsblk.json").read_text() or "{}")
except json.JSONDecodeError:
    out["storage"] = {}

out["gpu"] = [l.strip() for l in (scratch / "gpu.txt").read_text().splitlines() if l.strip()]
out["network_devices"] = [l.strip() for l in (scratch / "network.txt").read_text().splitlines() if l.strip()]

print(json.dumps(out, indent=2))
PYEOF
