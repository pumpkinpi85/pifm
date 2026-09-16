# Changelog

## 0.4.3 — Canonical PiFmRds timing calibration

- Add validated `pi_fm_rds_ppm` station configuration, defaulting to zero.
- Pass the correction to PiFmRds as `-ppm <value>` for audio/DMA timing.
- Expose the configured/applied value in status, System diagnostics, TX metadata,
  and transmitter diagnostic logs.
- Preserve OFF AIR behavior when timing configuration changes and add non-RF
  regression coverage for zero, positive, negative, invalid, and STOP paths.

## 0.4.2 — P1C/P1D canonical deployment & reference A+ cutover

P1C machinery plus authorized P1D physical cutover of the reference A+:

- Deployed build identity (`build_meta.json`, API `git_sha` / System Build line)
- Persistent data contract (`docs/persistent-data.md`)
- Station backup / exact-SHA stage-deploy / rollback tools (`scripts/`)
- Authorized remote cutover via `--authorize-cutover`
- `pi_fm_rds` normalization helper → `/usr/local/bin/pi_fm_rds`
- Hardware profile runtime resolution
- Non-RF reference hardware validator
- Cutover + legacy retirement procedures
- Reference A+ now runs `/opt/pifm` (legacy trees retained pending RF + retirement)

## 0.4.1 — P1A clean-room software validation & convergence prep

Desk-side P1A on the public candidate (no RF, no public GitHub push):

- Host validation gate: `scripts/run-validation.sh` (full mock/fake + sanitization suite)
- Clean-room Phase A evidence template under `docs/evidence/`
- Canonical convergence / generic migration notes (`docs/canonical-convergence.md`)
- Installer supports `PIFM_TX_BACKEND=mock` for non-RF clean-room lifecycle installs
- systemd `Restart=always` (port sanitized from private reference deploy fix)
- Hardened publication sanitization token list
- Version bump to 0.4.1

Physical clean-room Phase A on a spare Pi, reference-station cutover, and
publication to github.com/pumpkinpi85/pifm remain later authorized steps.

## 0.4.0 — public candidate (P0)

First sanitized public-repository candidate extracted from a privately developed
reference appliance (internal line reached v0.3.1 on Raspberry Pi Model A+).

### Included

- Lightweight Python appliance + Broadcast Deck
- `pi_fm_rds` production backend target with OFF AIR default
- FFmpeg seekable WAV cache, SSE state updates, hardened TX lifecycle
- GPL-3.0 licensing and public documentation skeleton
- Generic installer and example configuration
- Publication sanitization tests

### Not included

- Private commissioning evidence, backups, personal playlists, or host-specific paths
- Claims of support for unvalidated Raspberry Pi models

### Ownership

Canonical public home (when authorized): **github.com/pumpkinpi85/pifm**
under the **pumpkinpi85** GitHub account.
