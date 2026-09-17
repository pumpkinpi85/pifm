# Clean-room Phase A evidence template

Copy this file (do not commit filled copies with private host data).
Use a spare Pi or fresh SD image — **not** the personal reference A+ station.
**No RF** unless Phase B is separately authorized.

## Identity

| Field | Value |
|-------|-------|
| Date (UTC) | |
| Operator | |
| Board model / revision | |
| Architecture (`uname -m`) | |
| OS / release | |
| Kernel | |
| RAM (approx) | |
| piFM git commit (SHA) | |
| piFM version (`software_version`) | |
| `pi_fm_rds` build commit / path | |
| Install prefix (`PIFM_ROOT`) | `/opt/pifm` (or record override) |
| `tx_backend` used for Phase A | `mock` / `pi_fm_rds` (OFF AIR) |
| Antenna / load state | disconnected / shielded load / N/A |

## Checklist results

Mark each item pass / fail / N/A. Do not move the brass Broadcast handle out of
OFF AIR on a live antenna unless Phase B is authorized.

| # | Step | Result | Notes |
|---|------|--------|-------|
| 1 | Fresh OS boot + network/SSH | | |
| 2 | Candidate tree obtained (not private restoration archive) | | |
| 3 | Packages + `pi_fm_rds` build (if used) + `scripts/install.sh` | | |
| 4 | Service active; Broadcast Deck loads | | |
| 5 | Host non-RF suite: `scripts/run-validation.sh` (or equivalent) | | |
| 6 | Install-tree private-artifact grep (see sanitization token list) | | |
| 7 | Upload ordinary MP3s; create/select playlist | | |
| 8 | Configure frequency / RDS (lawful local values) | | |
| 9 | Play / Pause / Next with TX OFF | | |
| 10 | Test harness (no FM): Start Broadcasting / Stop Broadcast lifecycle | | |
| 11 | Absolute STOP during STARTING | | |
| 12 | OFF intent + reboot → OFF AIR; zero TX | | |

## Safety attestations

- [ ] No unintended transmission observed during Phase A
- [ ] Absolute STOP remained available
- [ ] OFF intent/service restart left station OFF AIR
- [ ] No private LAN IPs, personal paths, media, or commissioning bundles were copied into the candidate tree

## Disposition

| Field | Value |
|-------|-------|
| Phase A overall | PASS / FAIL |
| Follow-ups for canonical repo | |
| Phase B RF authorized? | NO (default) / YES (founder only) |
