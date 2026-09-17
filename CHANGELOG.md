# Changelog

## Unreleased — Canonical Broadcast Control artwork & sanitation

- Freeze approved Broadcast Control assets as canonical production names:
  `broadcast-flagpole.png`, `broadcast-flag-off.png`, `broadcast-flag-on.png`,
  and `broadcast-tuner-handle{,-active,-pressed}.png`.
- Remove superseded flagpole/flag/handle experiments, ropes overlay, and
  temporary visual-fit evidence dumps.
- Update operator docs for permanently raised PumpkinPi flag (black/white =
  OFF AIR; orange = authoritative ON AIR) with a separate brass handle and
  small OFF AIR detent.

## 0.6.2 — Public release documentation & sanitation

- Reorganize public manuals around README, INSTALL, HARDWARE, USER-MANUAL,
  TROUBLESHOOTING, and ARCHITECTURE while preserving the validated media
  contract in `docs/music.md`.
- Harden `.gitignore` and installer excludes so live operator configuration,
  media, playlists, SQLite, recovery markers, and secrets are not install or
  commit candidates.
- Sanitize founder-only path/alias examples from docs and script comments;
  keep publication sanitization tests as a release gate.
- Remove compatibility stub manuals, founder-only planning notes, and the
  unused `pirate-flag-waving.png` asset after the public manual hierarchy landed.
- Bundle `examples/demo/Brynja Vinter - The Sky Belongs to No King.wav` as the
  distributable demo track (artist credit: Brynja Vinter) and seed it into
  fresh installs without overwriting operator media.
  No other appliance behaviour change versus the A+-validated 0.6.1 line.
- Document honest operator guidance for SD/library storage sizing, FFmpeg
  prepare-when-needed behaviour, and auto-detect limits (only A+ maps to
  SUPPORTED; recognition does not invent support for newer boards).
- Pin public `pi_fm_rds` installs to upstream ChristopheJacquet/PiFmRds
  `777f8e52648b88156483d067e24e0b8682abe8c1` via `third_party/pifmrds.pin`
  (not vendored; not `pumpkinpi85/PiFmRds`).

## 0.6.1 — A+ stress-audit remediation

- Reassert authoritative status on the SSE heartbeat so an open browser
  reconciles a fault/possible-transmission display to confirmed OFF AIR without
  relying on a refresh or optimistic local state.
- Derive browser media-picker capabilities from the backend's six-format
  contract: MP3, WAV, FLAC, M4A, AAC, and OGG.
- Predict decoded PCM size before conversion, enforce configurable cache and
  free-disk bounds, and evict only deterministic disposable WAV-cache entries
  while protecting transmitter-owned audio.
- Own each FFmpeg conversion by exact child process, with bounded
  TERM/wait/KILL cancellation, reaping, and partial-output cleanup.
- Disable speculative cache warming by default and prevent it while ON AIR;
  reject library upload, deletion, and full reindex while transmission may be
  active.
- Replace normal import/delete full-library rebuilds with incremental SQLite
  updates while preserving deliberate full reindex for repair.
- Cache static build/board identity while retaining fresh TX/process
  reconciliation in every authoritative status snapshot.
- Normalize operator-facing Broadcast, Stop Broadcast, OFF AIR, brass handle,
  pirate flag, track, playlist, queue, music program, and test-harness wording.

## 0.6.0 — Standalone appliance resilience

- Persists deliberate operator ON intent in an atomic, fsync-backed marker;
  no valid ON marker (including a retained OFF tombstone) means OFF.
- Disarms recovery before absolute STOP and preserves ON intent across service
  or machine shutdown.
- Restores only through canonical hardware, backend, media, and single-worker
  gates, with same-boot failure latching to prevent restart loops.
- Adds per-command intent revisions, a strict start/STOP lifecycle barrier, a
  single-controller process lease, restoration stability observation, and
  systemd restart throttling.
- Removes network-online ordering from the appliance service and keeps browser,
  LAN, DNS, NTP, Internet, and cloud state outside the playback lifecycle.
- Makes connection loss explicitly unverified in the browser, rejects stale
  responses, blocks disconnected mutations, and requires a complete read-only
  appliance snapshot before reopening SSE.
- Persists useful playlist/current-track/program state and adds local,
  duration-based playlist progression.
- Defines repeat-disabled completion as Broadcast ON with a stopped program and
  silence hold.
- Hardens disk-full uploads, failed WAV preparation, atomic configuration
  writes, and unwritable Ship's Log behavior without corrupting operator data.
- Preserves recovery intent in backup, deployment, and rollback tooling.
- Adds the Broadcast brass-handle tuner: a full 10% OFF decision detent, a
  lowest-frequency snap at its upper boundary, full-range tuning above it, and
  an authoritative flag that is hidden while OFF AIR/previewing and appears
  after confirmed start.
- Refines the tuner with a smaller handle and reference-style scale, makes the
  selected frequency a confirmed start action, and immediately reconciles
  Station saves into the authoritative Broadcast display.

## 0.5.0 — Hardware-first setup and Music workspace

- Detects Raspberry Pi identity without guessing unknown boards and maps the
  physically proven Model A+ to its canonical hardware profile.
- Checks the A+ headless/onboard-audio prerequisites discovered during P1E and
  provides an explicit, backed-up, reversible configuration command.
- Adds a short Welcome → Hardware → Station → Music → Broadcast first run while
  treating pre-P1F operator configurations as already complete.
- Adds streaming multi-file browser import with progress, safe filenames,
  FFmpeg/FFprobe decode validation, atomic writes, duplicate handling, and a
  128 MiB per-file A+ limit.
- Organizes Music into Library, Playlists, and Queue with playlist rename,
  deletion/reference cleanup, drag-and-drop ordering, and persisted idle queue
  reorder.
- Adds original header, RF-path, audio-pipeline, and broadcast-lifecycle
  diagrams.
- Preserves OFF AIR defaults, absolute STOP, single-transmitter enforcement,
  operator data, and Python 3.7 / ARMv6 compatibility.

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
