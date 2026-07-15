# Tier 3 — Journalist / hardened

Qubes OS setup isn't a post-install *script* the way Tiers 1/2 are (dom0 +
per-qube config, not a single apt run), so this file is the reference
checklist for that build. `fleetctl build register` will hash this file the
same way it hashes a `.sh`, so you still get an audit trail of what version
of the steps a given build id used.

Register with:
```
fleetctl build register --id laptop-tier3-qubes-v1 --line laptop --tier 3 \
    --script postinstall/laptop-tier3-journalist.md --desc "Qubes OS journalist load-out"
```

## If hardware supports Qubes (16GB RAM min, 32 preferred)

- Install Qubes OS from verified ISO (check signature against qubes-os.org key)
- Create qubes per Standard Load-outs Tier 3: work, personal, vault (offline), disp-untrusted
- Install in relevant qubes: Signal Desktop, KeePassXC, VeraCrypt, Tor Browser,
  Mullvad Browser, uBlock Origin (Tier 2 app set)
- Install OnionShare, Dangerzone in a dedicated qube
- Include a Tails USB in the box (separately imaged, not part of this install)
- LUKS: set with the **buyer's own passphrase at handoff**, done together —
  do NOT use fleetctl's generated temp LUKS passphrase for this tier; note
  that in the unit record instead of storing a temp one

## If hardware can't take Qubes: Debian hardened fallback

- Run `laptop-tier2-privacy.sh` first
- Add: Heads or coreboot **only if this exact model has been validated** —
  don't experiment on a sellable unit
- Harden further: AppArmor enforcing, disable unused kernel modules,
  full-disk LUKS (buyer-set passphrase at handoff, same as Qubes path above)

## Handoff (both paths)

- Always in person or verified video call
- 30-minute orientation included in price
- Printed threat-model one-pager
