# fleetctl

Build/inventory tracker for refurbished Linux laptops and GrapheneOS Pixels.
Companion to the checklists and SOPs in `../../02 Operations/` — this is the
part that generates IDs, temp credentials, and a queryable record per unit
instead of a manually-typed markdown table.

CLI/TUI logic is stdlib Python 3 (secrets, argparse, curses) — but the
database is encrypted at rest with SQLCipher (see **Encryption at rest**
below), so `pip install -r requirements.txt` is required now, and
`FLEETCTL_DB_KEY` must be set before running anything, including `init`.

## Interactive menu (TUI)

For day-to-day use, just run it with no arguments:

```sh
cd Software/fleetctl
pip install -r requirements.txt   # one-time, needs sqlcipher3-binary
fleetctl gen-key                  # one-time, prints a new key — save it, then:
export FLEETCTL_DB_KEY=<the key it printed>
./fleetctl
```

That drops you into an arrow-key menu — up/down to move, enter to select, `q`
to back out a level. Everything (new unit, list units, update status, sell,
warranty, repurpose, hardware import, checklist save, handoff card, purge
secrets, manage builds) is there; you pick units and builds from a list
instead of typing serials, and free-text fields (make/model, paths, notes,
dates) prompt you one at a time instead of needing a full flag string.
It auto-creates the database on first run, so there's no separate init step.

`./fleetctl menu` does the same thing explicitly, if you want to be able to
tab-complete/alias it apart from bare `./fleetctl`.

The rest of this README documents the scriptable flag-based form of each
command — useful for one-offs you want in shell history, or scripting a batch
of units — but the menu covers the same ground for routine work.

## Web GUI (Docker)

There's also a browser GUI, for when a mouse/touchscreen is easier than a
terminal (e.g. a bench laptop next to you while you work on a unit).

```sh
cd Software/fleetctl
UID=$(id -u) GID=$(id -g) docker compose up --build
```

The `UID`/`GID` bit matters — see the comment in `docker-compose.yml`. Without
it, the container runs as root, and any file it writes through the bind
mount (the database, a checklist upload, a generated build script) ends up
root-owned on the host. The web UI keeps working fine since the container
is still root inside itself, but the **host-side `./fleetctl` CLI silently
loses write access** to anything the container touched — I hit this
(`sqlite3.OperationalError: attempt to write a readonly database`) testing
in a real container and fixed it by pinning the container to your own UID.
`docker compose` on Linux picks up bash's built-in `$UID` automatically;
`$GID` has no shell built-in, hence exporting both explicitly. Defaults to
1000:1000 if unset, which covers most single-user Linux installs.

Then open `http://localhost:4299`. It covers the same ground as the TUI —
units list with filters, unit detail page with all the actions (status,
sell, warranty, repurpose, hardware-import/checklist-save as file uploads,
handoff card, purge secrets), builds management, and a **Generate script**
page for the post-install script generator (see below) with checkboxes for
apps and policy toggles.

**This is one codebase, not two.** `fleetlib.py` holds every operation
(serial/passphrase generation, the DB schema, create/sell/warranty/etc.);
`fleetctl` (CLI + TUI) and `web/app.py` (Flask) are both thin front ends over
it. Fix a bug or add a field in `fleetlib.py` and all three interfaces get it.

**The container shares the same database as the CLI/TUI.** `docker-compose.yml`
bind-mounts this whole directory into the container, so `data/fleetctl.db`,
`checklists/`, and `postinstall/` are the literal same files whether you're
poking a unit with `./fleetctl` on the host or through the browser — there's
no sync step and no separate container-only data.

### Security — read before exposing this beyond your own machine

This UI has no authentication by default, and unit pages can show temporary
device passphrases in plaintext (same handoff-card content the CLI prints).
`docker-compose.yml` binds to `127.0.0.1:4299` by default specifically so it
isn't reachable from your LAN, only from the machine running Docker.

If you want it reachable from elsewhere (e.g. a bench laptop on the same
network), set both of these in `docker-compose.yml` before opening the port
up, so it at least requires a login:

```yaml
environment:
  - FLEETCTL_WEB_USER=admin
  - FLEETCTL_WEB_PASSWORD=some-long-random-string
```

then change the port line to `"4299:4299"`. Without both vars set, the
container logs a warning on startup as a reminder. There's still no HTTPS
here — this is meant for a trusted home/workshop network, not the internet.

### Using it from your phone

The UI itself is mobile-responsive (single-column layout, full-width tap
targets, scrollable tables below ~640px) — but your phone is a *separate
device* on your network, and the default `127.0.0.1:4299` binding means only
the machine running Docker can reach it at all. To open it from your phone:

1. Set `FLEETCTL_WEB_USER`/`FLEETCTL_WEB_PASSWORD` in `docker-compose.yml`
   (see above) — do this first, since the next step exposes the port to
   your whole LAN.
2. Change the ports line to `"4299:4299"` and `docker compose up --build`
   again.
3. Find the machine's LAN IP (`ip addr` / `ifconfig` on the Docker host),
   and visit `http://<that-ip>:4299` from your phone, on the same Wi-Fi.

Still no HTTPS — fine for glancing at inventory on your own home/workshop
network, not something to leave open on a network you don't trust.

## Branding

Reskinned with the real Obsidian Devices brand assets and palette from
`../../05 Brand/Brand Style Guide.md` — not placeholder colors. The header
uses `logo-primary-dark.svg` (copied into `web/static/brand/`), including
its blinking-cursor animation, which the style guide specifically calls out
as intended for website use. Favicon is `icon-app.svg`/`.png` (the
self-contained app-icon tile), also from that same brand folder.

`web/static/style.css`'s `:root` variables are named after the guide's own
color names (Obsidian Black, Charcoal, Graphite, Steel, Terminal Green,
Off-White) so the mapping back to the style guide stays obvious. Body/UI
font stack is Inter → Space Grotesk → system sans, monospace is JetBrains
Mono → Menlo/Consolas — matching the guide's typography section, but as
fallback stacks rather than a Google Fonts `<link>`, so the page makes no
outbound font-loading request (consistent with this being a privacy-focused
business's own internal tool). If you want pixel-exact Inter/JetBrains Mono
rendering rather than whatever's already on your system, the real fix is
self-hosting the actual font files under `web/static/fonts/` — happy to wire
that up if it matters to you.

One deliberate deviation from the guide: the temp-passphrase box (`pre.secret`)
uses the functional warning red, not Terminal Green — green is this theme's
accent/success color everywhere else (buttons, links, "ok" flash messages),
so using it for "here's a sensitive secret" would undercut the warning.

## Quick start (scriptable / CLI form)

```sh
cd Software/fleetctl
./fleetctl init                                    # creates data/fleetctl.db

# Register each post-install build once (hashes the script for an audit trail)
./fleetctl build register --id laptop-tier1-mint-v1 --line laptop --tier 1 \
    --script postinstall/laptop-tier1-everyday.sh --desc "Mint Cinnamon everyday load-out"
./fleetctl build register --id pixel-grapheneos-v1 --line pixel \
    --script postinstall/pixel-grapheneos-loadout.md --desc "Default GrapheneOS load-out"

# Create a unit — generates serial + 3 temp passphrases
./fleetctl new --line laptop --tier 1 --make Lenovo --model "ThinkPad T480" \
    --build laptop-tier1-mint-v1

# On the physical machine during refurb (needs sudo for full detail):
sudo ./scripts/hardware-inventory.sh > /tmp/unit.json
./fleetctl hardware-import LT1-260713-D05-5 /tmp/unit.json

# After filling out the checklist note for this unit:
./fleetctl checklist-save LT1-260713-D05-5 "/path/to/filled/checklist.md"

./fleetctl status LT1-260713-D05-5 Refurb
./fleetctl status LT1-260713-D05-5 QA
./fleetctl status LT1-260713-D05-5 Listed
./fleetctl sell LT1-260713-D05-5 --price 280
./fleetctl status LT1-260713-D05-5 Delivered

./fleetctl list
./fleetctl show LT1-260713-D05-5
./fleetctl handoff-card LT1-260713-D05-5
```

Run `./fleetctl <command> --help` for any subcommand's flags.

## Serial number format

`PREFIX[TIER]-YYMMDD-RAND3-CHECK`, e.g. `LT2-260713-9F2-K` (laptop, tier 2,
made 2026-07-13) or `PX-260713-K7M-9` (pixel, no tier).

- **Prefix**: `LT` (laptop) or `PX` (pixel)
- **Date**: manufacture date, so it's readable at a glance and matches
  `date_of_manufacture` in the record
- **3 random chars**: from Crockford's base32 alphabet (excludes `I`, `L`,
  `O`, `U` — characters easily confused with `1`, `0`, `V` when handwritten
  or read aloud)
- **Check digit**: a weighted checksum over the preceding characters. Run
  `./fleetctl verify-serial <serial>` any time — e.g. when a buyer reads
  their serial back to you for a warranty claim over email/phone — to catch
  a mistyped character before you go looking up the wrong unit.

This isn't cryptographic, it's a typo-catcher. It doesn't encode total units
built/sold the way a plain sequential ID would (`L0001`, `L0002`, ...),
which also means you don't leak your volume to anyone who sees two serials
side by side (e.g. on a resale marketplace).

## Temp passphrases

`fleetctl new` generates three independent diceware passphrases (login,
UEFI, LUKS) using the EFF long wordlist (`wordlist/eff_large_wordlist.txt`,
7776 words, ~12.9 bits/word entropy) via Python's `secrets` module — words
are picked with a cryptographically secure RNG, not `random`. Default is 6
words/passphrase (~77 bits); override with `--words N`.

Words were chosen over random-character strings because these get **read
off a printed handoff card and typed by the buyer** — a passphrase like
`dynamic-geriatric-squatter-joystick-ground-haphazard` is slower to type but
far less error-prone than a mixed-case/symbol string of similar strength.

**Exception — Tier 3 / journalist builds**: per
[`postinstall/laptop-tier3-journalist.md`](postinstall/laptop-tier3-journalist.md),
LUKS on that tier should be set to a passphrase **the buyer chooses at
handoff**, not a temp one. `fleetctl new` will still generate a temp LUKS
passphrase for a tier-3 unit (so the field isn't null) — just don't put it
on the handoff card for that tier; overwrite the disk's LUKS passphrase with
the buyer's own at handoff time as the checklist describes.

### Security note: purge temp secrets once they're no longer temp

Temp passphrases sit in the DB (encrypted at rest — see below) so you can
reprint a handoff card any time before delivery. Once you've confirmed a
buyer changed all three credentials (end of the "Handoff" step in the
checklists), run:

```sh
./fleetctl purge-secrets <serial>
```

This nulls the three passphrase columns and stamps `secrets_purged_at`.
`handoff-card` refuses to print anything for a purged unit. There's no
automatic purge-on-Delivered — that's a deliberate manual step so you're not
forced to purge before actually confirming the buyer completed the change.
Purging is still worth doing even with encryption at rest — it shrinks how
much a leaked `FLEETCTL_DB_KEY` would actually expose.

## Encryption at rest

`data/fleetctl.db` is encrypted at rest with [SQLCipher](https://www.zetetic.net/sqlcipher/)
(AES-256), via the `sqlcipher3-binary` package — a drop-in replacement for
Python's stdlib `sqlite3` module, so nothing else in the codebase changed
shape. **Why this matters more than "my laptop has full-disk encryption"**:
this whole `Software/fleetctl/` directory lives inside a Nextcloud-synced
vault. The database syncs to your Nextcloud server and every other device
syncing this vault — full-disk encryption on any one machine doesn't cover
that. Encrypting the file itself does.

### Getting a key

```sh
./fleetctl gen-key
```

prints a fresh 256-bit key (64 hex characters) and instructions. **fleetctl
does not store this key anywhere** — that's deliberate, since anywhere it
could default to storing a key would itself be inside this synced vault,
defeating the point. Save the printed key in a password manager, then set it
before running fleetctl:

```sh
export FLEETCTL_DB_KEY=<the key>          # put this in your shell profile
```

or point at a file containing it instead (e.g. a path outside the vault):

```sh
export FLEETCTL_DB_KEY_FILE=/path/outside/this/vault/fleetctl.key
```

**There is no recovery if the key is lost** — that's what "encrypted"
means. `fleetctl` refuses to run without one (clear error, not a crash), and
refuses a key that isn't a valid 64-character hex string — this is meant to
be a high-entropy generated key from a password manager, not something typed
from memory.

### Docker

`docker-compose.yml` requires `FLEETCTL_DB_KEY` in the environment running
`docker compose up` — it'll refuse to start with a clear error otherwise
(compose's `${VAR:?message}` syntax). Don't put the actual key value in
`docker-compose.yml` itself (that file is meant to be safe to commit):

```sh
FLEETCTL_DB_KEY=<the key> UID=$(id -u) GID=$(id -g) docker compose up --build
```

### What this does and doesn't cover

Covers: the DB file at rest, wherever it ends up (Nextcloud, a backup, a
stolen laptop, a compromised sync provider). Doesn't cover: the process's
own memory while it's running, or the web GUI's HTTP responses — those are
addressed separately (see the web GUI's auth/localhost-binding section, and
the QR section's note on tokens).

## QR labels (web GUI only)

Every unit gets a random opaque token (`secrets.token_urlsafe(16)`, 128 bits)
the moment it's created — set in `op_create_unit` in `fleetlib.py`, so this
happens for units created via the CLI, TUI, or web equally. It has **no
relation** to the serial, hardware UUID, or anything else; it exists purely
so you can print a scannable label on a unit and have scanning it reveal
nothing. If someone other than you scans it, they get a meaningless random
string — not a serial, not a model, not anything that identifies the device
or your business.

The QR *image* itself is generated only by the web GUI (unit detail page,
and right after creating a unit) — `qrgen.py` wraps the `qrcode` package
using its pure-Python PNG backend (`qrcode.image.pure.PyPNGImage`), so it
needs no Pillow/native imaging library, just `qrcode` + `pypng` (already in
`web/requirements.txt`). The CLI/TUI don't import `qrgen.py` at all and stay
fully dependency-free — they just have the token sitting in the DB, ready
whenever you open the web GUI for that unit.

To use it: print/download the PNG from a unit's detail page, stick it on
the unit. To look a unit up from a scanned label, scan it with any ordinary
QR reader (it'll just show you the token text, not open anything — there's
no URL in the code), then paste that text into **QR lookup** in the web
nav. Units created before this feature existed get a token generated
automatically the first time their QR image or a lookup touches them
(`op_ensure_qr_token`) — nothing to backfill by hand.

## Hardware config script

`scripts/hardware-inventory.sh` is a separate script you run **on the unit
itself** during refurb (`sudo ./scripts/hardware-inventory.sh > unit.json`).
It shells out to `dmidecode` (system manufacturer/model/serial/UUID, BIOS
info — needs root), `lscpu`, `free`, `upower`, `lsblk -J`, and `lspci`, and
prints one JSON object. Any tool that's missing or fails is skipped rather
than failing the whole script, so it still produces partial output without
root or on a minimal live-USB environment.

`fleetctl hardware-import <serial> unit.json` attaches that JSON to the
unit's record, and backfills `oem_make`/`oem_model` from the dmidecode
system fields if you didn't already set them in `fleetctl new`.

## Post-install script generator

Generates the actual post-install script instead of you hand-writing one per
OS — pick a target OS, which apps to install, and toggle password/admin
policies, and it produces a single self-contained `.sh` file. Lives in
`scriptgen.py` (shared core) + `app_catalog.py` (the app list); both the CLI
and the web UI's **Generate script** page call the same generator, so
there's one implementation to maintain, not two.

```sh
./fleetctl script oses                              # list supported OS targets
./fleetctl script apps                               # list the app catalog
./fleetctl script generate --os mint \
    --apps firefox,libreoffice,signal,ublock-origin \
    --register-build laptop-tier1-mint-v2 --line laptop --tier 1 \
    --desc "Mint tier1, generated"
```

Or use the web UI at `/generate`: same options as checkboxes, with a
"register this as a build" option that saves the file under
`postinstall/generated/<build_id>.sh` and calls `fleetctl build register`
for you, or just downloads the `.sh` if you leave that unchecked.

**Supported OS targets:** Linux Mint Cinnamon, Ubuntu, Debian stable, Fedora
Workstation, Arch Linux (the load-outs from `Standard Load-outs.md`, plus the
two you asked for). Qubes isn't in this list — it isn't a single-script
install the way the others are; `postinstall/laptop-tier3-journalist.md`
covers it as a reference checklist instead.

### Adding a new app

Edit `app_catalog.py` — add an entry to the `APPS` dict with the package
name for whichever of apt/dnf/pacman actually carry it (`None` if it
doesn't), and/or a Flatpak id. No other file needs to change. See the
module's docstring for the field meanings; `veracrypt`/`mullvad-browser` in
there are examples of the "no reliable unattended install path — print a
note instead" case, and `ublock-origin` is an example of the "needs special
handling beyond installing a package" case (it installs via Firefox's
enterprise policy mechanism instead).

### What it configures beyond apps

- **Flatpak + Flathub** on every target, alongside each OS's native package
  manager — apps are installed via whichever the catalog prefers per-app
  (native where it integrates better, Flatpak where it's more consistent
  across five different distros).
- **Automatic updates**: unattended-upgrades (apt) / dnf-automatic (dnf) /
  a notify-only systemd timer on Arch that never runs `pacman -Syu`
  unattended — a broken unattended Arch upgrade is a worse first-Linux
  experience for a Windows switcher than just not auto-updating.
- **fwupd** firmware updates on every target (was Tier-1-only before; now
  everywhere, low-risk).
- **Firewall** — `ufw` (apt/Arch) or verifying Fedora's already-enabled
  `firewalld` (not fighting the distro default).
- **zram swap + TLP** power management, with the per-distro quirks handled
  (Fedora already ships zram by default since F33; Arch needs a
  `zram-generator` config dropped in).
- **Printing + codecs** — CUPS + Avahi, GStreamer good/bad/ugly/libav +
  ffmpeg. Fedora needs RPM Fusion for the non-free codec plugins, which the
  script enables — that's Fedora's own standard, wiki-documented path for
  this, not a random third-party repo.
- **Snapper**, if the root filesystem is btrfs at runtime (checked with
  `findmnt`, skipped with a clear message otherwise) — timeline + cleanup
  timers enabled, retention trimmed to something sane for a buyer who won't
  manage it themselves (5 hourly / 7 daily / 4 weekly / 2 monthly).
- **grub-btrfs** (boot-menu snapshot rollback), but **only actually
  installed on Arch** where it's in the official repos. On apt/Fedora it'd
  need a PPA/COPR, which this generator won't add unattended — it prints
  where to get it manually instead. Worth revisiting if you end up wanting
  this badly enough on Mint/Fedora to accept a third-party repo there.

### OEM branding checkboxes

Three checkboxes (all checked by default), separate from the app catalog —
these are about the buyer's first-boot experience, not software:

- **Obsidian Devices wallpaper as default background** — `assets/wallpaper.png`
  (built from the real brand assets in `05 Brand/`, not a placeholder) gets
  base64-embedded into the generated script, written to
  `/usr/share/backgrounds/obsidian-devices.png`, and set as the system-wide
  default via a `dconf` database default (`/etc/dconf/db/local.d/`) — the
  standard OEM mechanism for this, since there's no live desktop session
  during post-install to run `gsettings` against directly. Applied on
  Mint (Cinnamon) and Ubuntu/Debian/Fedora (GNOME) — see `OS_DESKTOP` in
  `scriptgen.py`. Arch has no assumed default desktop, so the wallpaper file
  is still installed there, just not set automatically (a note is printed
  instead).
- **"Stay Safe Online" default Firefox bookmarks** — EFF, Privacy Guides,
  Tor Project, ToS;DR, and Have I Been Pwned, added via Firefox's own
  enterprise policy mechanism (`policies.json`), the same mechanism
  `ublock-origin` already used — see `OEM_BOOKMARKS` in `scriptgen.py` to
  change the list. Both this and uBlock Origin write into the *same*
  `policies.json`, merged into one JSON object by `_firefox_policies_snippet()`
  rather than each clobbering the other's write — worth knowing if you add a
  third Firefox-policy-based feature later.
- **Beginner Linux guide folder on the Desktop** — copies
  `assets/guide/*.txt` (six short, OS-agnostic docs: installing software,
  finding files, staying secure, the terminal, getting help) into
  `~/Desktop/Getting Started with Linux/` for the buyer, owned by the target
  user. Package-manager commands in the text are filled in per-family at
  generation time (`{install_cmd}`/`{update_cmd}`/`{search_cmd}` placeholders,
  see `PKG_MANAGER_INFO`) — the same guide files work across all five OS
  targets without duplicating a copy per distro.

### Password policy checkboxes

- **Require LUKS passphrase change at first boot** (checked by default): a
  first-login autostart entry opens a terminal and runs `cryptsetup
  luksChangeKey` interactively — the buyer types the temp passphrase from
  the handoff card once (same as any password change), then their own new
  one. Nothing is baked into the script, so the same generated file works
  for every unit of a build, not just one serial.
- **Unchecked** (you said you'll use TPM for general PCs instead): the
  script enrolls `systemd-cryptenroll --tpm2-device=auto` with a PCR 0+7
  policy (bound to firmware/boot-chain state), and — only if it can
  positively identify exactly one pre-existing passphrase slot — removes
  the old temp passphrase afterward so the buyer never has a LUKS
  passphrase to manage at all. If TPM enrollment fails, or there's more
  than one existing slot (unexpected — the script won't guess which to
  remove), it leaves everything alone and tells you what to check manually.
  I tested both the success path and this safety-guard path with mocked
  `cryptsetup`/`systemd-cryptenroll` before shipping this.
- **Force Unix password change at next login** (checked by default):
  `passwd --expire`, standard PAM behavior, no custom wizard needed.
- **UEFI/BIOS password reminder**: there's no cross-vendor way to set a
  firmware password from inside Linux, so this checkbox only adds a printed
  reminder for you during refurb — it doesn't attempt anything. (Dell's
  `cctk` tool can actually do this on Dell hardware specifically, if you
  end up wanting real automation for your Dell volume — ask and I can add
  it as a Dell-specific path.)

### Privacy & power checkboxes

- **Hibernate on lid close** (checked by default) — suspend-to-disk, not
  suspend-to-RAM. This matters specifically because plain suspend keeps the
  LUKS decryption key sitting in RAM the whole time, which is vulnerable to
  a cold-boot/DMA attack against a suspended machine; hibernate clears RAM
  and re-requires the full LUKS passphrase on resume, which is the actual
  security property "encrypt on lid close" is asking for.

  This only activates if a real (non-`zram`) swap **partition** at least as
  large as installed RAM is already present — checked at runtime via
  `swapon --show=NAME,TYPE,SIZE`. If found, it adds `resume=UUID=...` to the
  kernel command line (GRUB) if missing, regenerates the initramfs
  (`update-initramfs`/`dracut`/`mkinitcpio`, with the `resume` hook added to
  `mkinitcpio.conf` on Arch if it isn't already there), and sets
  `HandleLidSwitch=hibernate` via `/etc/systemd/logind.conf.d/`.

  If no adequate swap partition exists, it **does not** attempt to create or
  resize one — auto-provisioning correct hibernate-ready swap (especially a
  swapfile's `resume_offset`, which differs by filesystem and is genuinely
  one of the more failure-prone corners of Linux setup) risks leaving a
  machine that fails to resume or fails to boot. Instead it falls back to
  `HandleLidSwitch=suspend` and prints exactly what's missing and why,
  matching this generator's existing rule elsewhere (grub-btrfs, TPM
  enrollment): if it can't be done with confidence, detect that and say so
  rather than guess. Verified the swap-detection logic (zram-only, adequate
  partition, undersized partition, swapfile-not-partization, mixed) and the
  GRUB/mkinitcpio `sed` edits against realistic sample files before shipping
  this — no physical hardware needed to check that part.

- **Wi-Fi MAC address randomization** (checked by default) — a
  `NetworkManager` conf.d drop-in (`wifi.cloned-mac-address=random`)
  generates a new random MAC for every connection, so this laptop can't be
  tracked across networks by its hardware address. Skipped with a note if
  NetworkManager isn't present.
- **Generic hostname** (checked by default) — replaces whatever the OS
  installer picked (often the account name) with `laptop-<4 random hex
  bytes>`, so the buyer's identity isn't visible to anyone else on a shared
  network. Random per unit rather than fixed, so multiple units refurbished
  together don't collide on the same hostname on the workshop LAN — same
  "don't leak identity, don't collide" reasoning as fleetctl's own serial
  format.
- **Idle screen lock** (checked by default, 5 minutes) — same `dconf`
  system-default mechanism as the wallpaper, separate file
  (`/etc/dconf/db/local.d/01-obsidian-lockscreen`) so it doesn't need to
  coordinate with it. Same GNOME/Cinnamon-only, Arch-prints-a-note caveat.
- **Firefox privacy hardening** (checked by default) — `HttpsOnlyMode:
  "enabled"` (buyer can still turn it off for a specific broken HTTP-only
  site — not `force_enabled`), plus `DisableTelemetry`, `DisableFirefoxStudies`,
  and `DisablePocket`. Merges into the same `policies.json` as bookmarks and
  uBlock Origin — see `_firefox_policies_snippet()`.

## Builds vs. units

Two separate concepts, matching the checklists' "one defined image per
tier" discipline:

- **`builds`**: a `build_id` (e.g. `laptop-tier1-mint-v1`) mapped to the
  exact post-install script/doc used, plus a sha256 of that file at
  registration time. `fleetctl build verify <id>` tells you if the
  registered script has since been edited — useful if you tweak a script
  and want to know whether existing build ids are still accurate, or if you
  should bump the version suffix and register a new build id instead.
- **`units`**: one row per physical device — serial, which build it used,
  OEM make/model, hardware config, dates, status, warranty/repurpose
  flags, checklist copy, temp credentials.

## Acquisition info

Where, when, and how much a unit was acquired for — three fields on the
`units` row (`acquisition_date`, `acquisition_source`, `acquisition_cost`).
Set them at creation time:

```sh
./fleetctl new --line laptop --tier 1 --make Lenovo --model "ThinkPad T480" \
    --acquisition-date 2026-06-01 --acquisition-source eBay --acquisition-cost 110
```

or any time after, since the purchase often isn't finalized (or logged)
the moment you create the unit record:

```sh
./fleetctl acquisition <serial> --date 2026-06-01 --source eBay --cost 110
```

Same fields, same command, in the TUI ("Set acquisition info") and web GUI
(on the "New unit" form, and as an action card on the unit detail page —
resubmitting just overwrites the current values, there's no history kept
here unlike part replacements below).

## Selling a unit, and syncing the sale to Dolibarr

`fleetctl sell <serial>` takes an optional `--buyer-name` and `--buyer-email`
in addition to `--date`/`--price` (same fields in the TUI's "Mark sold" flow
and the web GUI's sell form). fleetctl itself has no CRM/invoicing —
buyer name/email here exist only to identify the customer if you use the
optional Dolibarr sync described below; they aren't used for anything else.

```sh
./fleetctl sell LT1-260713-D05-5 --price 280 \
    --buyer-name "Jane Doe" --buyer-email jane@example.com
```

**Optional: push the sale into [Dolibarr](https://www.dolibarr.org/)** (an
open-source CRM/invoicing/accounting suite) rather than building any of that
into fleetctl itself. Set both `DOLIBARR_API_URL` (Dolibarr's REST API base,
e.g. `https://your-dolibarr-host/api/index.php`) and `DOLIBARR_API_KEY`
(Setup > API in Dolibarr, per-user key) before running fleetctl — CLI, TUI,
and the web GUI (via `docker-compose.yml`) all read the same two env vars.
Left unset, this is a silent no-op; fleetctl's own database stays the source
of truth for inventory regardless of whether Dolibarr is configured or even
reachable.

When both are set, `sell` will (best-effort, in `dolibarr_sync.py`):

1. Find an existing Dolibarr customer by `--buyer-email`, or create one from
   `--buyer-name`/`--buyer-email` if none matches (or none is found — a
   fresh customer is created from the name alone if no email was given).
2. Create and validate an invoice for the sale price, one line item
   describing the unit (make/model/serial).

If `--buyer-name` is omitted, the sync is skipped for that sale (a warning
is printed — Dolibarr needs at least a name to create a customer). If
Dolibarr is unreachable or the API call fails for any other reason, that's
also just a warning on stderr — it never blocks recording the sale in
fleetctl's own database. The invoice is left **unpaid** in Dolibarr (validated,
not marked paid) — record payment there once you've actually collected it.

## Part replacements

A running log of hardware swapped during refurb — battery died and got
replaced, RAM upgraded, that kind of thing. Lives in its own table
(`part_replacements`), one row per swap, **not** columns on `units` — a unit
can rack up several of these over its life, and each one records both
sides of the swap: the part that came out and the part that went in, each
with make/model/model number/serial number/date of manufacture (all
optional — a dead battery's manufacture date is often illegible or just
unknown, so nothing here is required except the part type and the unit).

```sh
./fleetctl part add <serial> --type Battery --replaced-at 2026-07-10 \
    --old-make Lenovo --old-model 45N1 --old-serial OLDBAT123 --old-mfg-date 2018-03-01 \
    --new-make Lenovo --new-model 45N1775 --new-serial NEWBAT456 --new-mfg-date 2025-01-01 \
    --notes "Swelling, replaced under safety concern"

./fleetctl part list <serial>
```

`--type` takes any text — there's a suggested list (Battery, RAM, Storage,
Screen/Display, Keyboard, Trackpad, Fan/Cooling, Motherboard, Charger/PSU,
Camera, Speaker) offered as autocomplete in the web GUI's `<datalist>` and
as a pick-list in the TUI, but nothing stops you from typing something
else. There's no delete/edit for a part replacement record once added —
same append-only philosophy as the rest of fleetctl (units aren't deleted
either); if you logged something wrong, add a corrected record and note
the mistake, don't try to erase it.

`fleetctl show <serial>` and the TUI's "Show unit details" both include the
full part replacement history inline. The web GUI shows it as its own table
on the unit detail page, with a "+ Record a part replacement" link to a
dedicated form (old part / new part side by side).

## Repurposing

`fleetctl repurpose <serial>` marks the original unit `Repurposed` and
creates a **new** unit row (new serial, new build, new temp passphrases)
linked back via `repurposed_from`, since a repurposed unit is effectively
rebuilt from the same hardware. The original row is kept for history rather
than mutated in place.

## Status pipeline

Matches `SOP — End-to-End Workflow.md`:

```
Acquired -> Refurb -> QA -> Listed -> Sold -> Delivered
                                    -> Warrantied
                                    -> Repurposed
                                    -> Parted
```

`fleetctl status <serial> <NewStatus>` sets it directly; `sell` and
`warranty` also update status as a side effect (to `Sold` / `Warrantied`
respectively).

## Layout

```
fleetlib.py             shared core: DB schema (encrypted via SQLCipher), serial/passphrase gen, every op_* function
requirements.txt        sqlcipher3-binary — needed by fleetctl (CLI/TUI), not just the web GUI
scriptgen.py            post-install script generator core, shared by CLI + web
app_catalog.py          the app list scriptgen.py installs from — edit to add an app
qrgen.py                QR PNG rendering — web-only dependency, not imported by fleetctl
dolibarr_sync.py         optional Dolibarr customer/invoice push on sale — stdlib-only, see "Selling a unit" above
assets/wallpaper.png     OEM default wallpaper, embedded into generated scripts (see "OEM branding checkboxes")
assets/wallpaper.svg     source for wallpaper.png — built from 05 Brand/Logo/logo-stacked-dark.svg
assets/guide/            beginner Linux guide text files, copied to the buyer's Desktop by the generator
fleetctl                executable CLI + TUI, thin wrapper over fleetlib.py/scriptgen.py
web/app.py              Flask GUI, thin wrapper over fleetlib.py/scriptgen.py/qrgen.py
web/templates/          Jinja2 templates for the web GUI
web/static/style.css    web GUI styling — Obsidian Devices brand palette + mobile media queries
web/static/brand/       logo/favicon SVGs+PNG copied from ../../05 Brand/Logo/
Dockerfile              image for web/app.py only — the CLI/TUI need no container
docker-compose.yml      runs the web GUI on port 4299, bind-mounts this directory
data/fleetctl.db        SQLite database (created automatically on first run)
wordlist/               EFF large wordlist for passphrase generation
scripts/                hardware-inventory.sh
postinstall/            one script/doc per build id (tier 1/2/3 laptop, GrapheneOS)
postinstall/generated/  scripts produced by the generator with "save as build" checked
checklists/             completed checklist copies land here, named <serial>.md
```

## Extending

Adding a new product line or field is a schema + function change in
`fleetlib.py`, plus argparse/curses wiring in `fleetctl` and a route/template
in `web/app.py` if the new field needs UI. There's no migration framework, so
for now edit `SCHEMA` and delete `data/fleetctl.db` to rebuild during
development, or write an `ALTER TABLE` by hand once you have real data you
don't want to lose.
