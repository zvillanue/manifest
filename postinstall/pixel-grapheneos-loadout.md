# GrapheneOS load-out

Not a shell script — GrapheneOS is flashed via the official web installer, not
apt/dnf. This file is the reference steps for a given build id; register it
the same way as the Tier 3 doc so builds stay auditable:

```
fleetctl build register --id pixel-grapheneos-v1 --line pixel \
    --script postinstall/pixel-grapheneos-loadout.md --desc "Default GrapheneOS load-out"
```

## Flash

- Factory reset, confirm no FRP lock
- Enable OEM unlocking (Settings -> Developer options)
- Use the official web installer at grapheneos.org/install — verify the domain
- Flash, then **re-lock the bootloader** (critical — verified boot)
- Confirm boot, updates working

## Configure (default — ask before deviating)

- Owner profile basics
- Sandboxed Google Play **only if buyer wants it** (ask — many don't); if yes,
  offer it in a separate profile, not the owner profile
- Preinstall from Accrescent/F-Droid Basic: Signal, Organic Maps on request
- Browser stays Vanadium — never replace as default

## Test

- Cameras, speakers, mics, GPS, NFC, both SIMs/eSIM

## Handoff

- Auditor app verification together with the buyer
- 15-minute orientation for local buyers
- Never: preinstall paid apps, log into any account for the buyer, skip re-locking the bootloader
