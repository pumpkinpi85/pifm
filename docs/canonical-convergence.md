# Canonical convergence (desk preparation)

This document prepares eventual replacement of any personal/reference deploy with
**this** canonical candidate. There is one product: the public/canonical tree.

**Do not** copy private configuration, media, host addresses, commissioning
bundles, or forensic archives into this repository.

Physical cutover and RF checks belong to the reference-station session and
require explicit founder authorization for any transmission.

---

## Deploy contract map

Canonical install defaults (see `scripts/install.sh`, `systemd/pifm-appliance.service`):

| Contract | Canonical value |
|----------|-----------------|
| Install root | `/opt/pifm` (`PIFM_PREFIX` / `PIFM_ROOT`) |
| Entry | `python3 -m appliance` |
| Config | `$PIFM_ROOT/config/appliance.json` (gitignored locally; seeded from example) |
| Production TX backend | `pi_fm_rds` at `/usr/local/bin/pi_fm_rds` (rebuild per board) |
| Test / clean-room lifecycle | `tx_backend=mock` or `fake` (no RF) |
| Boot TX state | OFF AIR — ON_AIR never persisted |
| systemd unit | `pifm-appliance.service`, `Restart=always`, stop timeout set |
| Optional panel GPIO | `gpio_enabled=false` unless operator enables |
| Hardware profile | `raspberry-pi-a-plus` (minimum / reference) |
| Network iface (RF Quiet) | `eth0` by default — override if the board uses another name |

Legacy personal layouts (home-directory trees, ad-hoc services, seasonal demo
playlists) are **not** a second product edition. They are superseded by the
contracts above.

---

## Station inventory checklist (report back — do not paste secrets here)

The reference-station session should report these fields so the canonical tree
can be proven as a drop-in software replacement. Record results outside this
repo (or in a private operator notebook).

| # | Item | Why it matters |
|---|------|----------------|
| 1 | Running `software_version` / git SHA if any | Detect private vs candidate delta |
| 2 | Actual `PIFM_ROOT` / working directory | Map to `/opt/pifm` |
| 3 | systemd unit name + `Restart=` policy | Ensure appliance recovers after clean exits |
| 4 | `tx_backend` and `pi_fm_rds` binary path/build | Production vs mock; rebuild on A+ |
| 5 | `gpio_enabled`, LED/switch BCM pins | Optional panel; defaults documented |
| 6 | Network iface name used for admin / RF Quiet | A+ USB-Ethernet naming |
| 7 | Any local patches not in candidate | Must be ported here or dropped deliberately |
| 8 | Legacy auto-start transmitter units still enabled? | Must be disabled before cutover |
| 9 | Operator library/playlist locations | Migrate data only; never commit media |
| 10 | OFF intent confirmed after service restart | Absolute STOP safety invariant |

---

## Generic migration notes (operator)

1. **Backup** operator config, library, and playlists on the device (outside git).
2. **Disable** any legacy auto-start FM services so only `pifm-appliance.service`
   owns broadcast control.
3. Install this candidate with `sudo ./scripts/install.sh` (default `/opt/pifm`).
4. Restore operator `appliance.json` keys carefully: frequency/RDS/library paths
   as needed; keep `tx_backend` intentional; never introduce persisted ON_AIR.
5. Point `pi_fm_rds_path` at an on-device rebuild under `/usr/local/bin/pi_fm_rds`
   (or set explicitly).
6. Re-copy music into `data/library` / playlists as the operator chooses.
7. Start service; confirm Deck loads **OFF AIR**.
8. Exercise Play/Pause/Next with TX off; only then consider authorized RF checks.

Clean-room Phase A on a spare Pi should complete before treating publication or
reference cutover as ready — see `docs/clean-room-validation.md` and
`docs/evidence/clean-room-phase-a.template.md`.

---

## Parity rule

If the station reports a product gap:

1. Reproduce or inspect against the private implementation source **read-only**
2. Port and sanitize into this repository
3. Re-run `scripts/run-validation.sh`
4. Deploy the fixed candidate — do not leave product fixes only on the Pi
