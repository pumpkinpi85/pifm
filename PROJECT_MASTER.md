# piFM — PROJECT MASTER

**Durable project authority.** A new contributor or Cursor session should be able
to understand piFM from this file without the private reference-station history.

Version of this document: **0.6.1 P1G (A+ stress-audit remediation)**

---

## 1. PROJECT PURPOSE

piFM is a **lightweight Raspberry Pi FM broadcast appliance**:

Raspberry Pi → piFM software → `pi_fm_rds` → GPIO/clock RF output →
**user-supplied** RF hardware (filter/load/antenna as the operator chooses).

It provides a browser **Broadcast Deck** for music, station/RDS settings, and
explicit on-air / off-air control.

## 2. PRODUCT PHILOSOPHY

- Prefer simple, inspectable systems over frameworks.
- Real FM capability with **strong safety defaults** (OFF AIR).
- The A+ class machine is a feature, not a limitation to outgrow casually.
- Operator UX is a physical radio deck metaphor (pirate flag, brass handle,
  and music-program transport).
- Engineering diagnostics stay out of the everyday operator path.

## 3. REFERENCE HARDWARE

**Raspberry Pi Model A+ Rev 1.1** — reference and minimum target.

The personal A+ station that matured the software is a **REFERENCE
IMPLEMENTATION**, not the deployment environment of this repository.

Public code must **never** depend on that station’s paths, IP address, library,
enclosure, network, or commissioning history.

## 4. MINIMUM-HARDWARE POLICY

piFM is deliberately designed to remain useful on low-resource Raspberry Pi
hardware. The Raspberry Pi Model A+ Rev 1.1 is the reference minimum target.
Changes should preserve usable operation on that reference class unless a
documented architectural decision explicitly changes the minimum hardware
requirement.

Avoid without compelling justification: Docker, Node runtime, frontend
frameworks, database servers, cloud dependencies, large daemon stacks,
unnecessary polling, excessive logging, repeated transcoding, excessive SD writes.

## 5. CURRENT SOFTWARE ARCHITECTURE

- Python 3 package `appliance/`
- Entry: `python3 -m appliance` (`PIFM_ROOT` supported)
- Config: JSON under `config/appliance.json` (never persists transient ON_AIR/PIDs)
- Broadcast intent: atomic ON marker under `data/recovery/`; no valid ON marker
  (including a retained OFF tombstone) means OFF
- Library: filesystem + SQLite index
- Web: stdlib `ThreadingHTTPServer`, static HTML/CSS/JS
- Live updates: SSE (`/api/events/stream`) + light polling fallback
- Optional GPIO LED/switch via `RPi.GPIO` (disabled by default)

## 6. TRANSMITTER ARCHITECTURE

- Backends: `pi_fm_rds` (production), `mock` / `fake` (**tests/dev only**)
- Production spawn typically: `sudo … pi_fm_rds … -audio <seekable WAV>`
- `OwnedTxProcess` tracks worker PID; absolute STOP kills worker + sweep
- Single-transmitter invariant; duplicate-start protection; start cancel via STOP
- RF leaves **BCM GPIO 4** (header pin 7) under upstream PiFmRds assumptions

## 7. AUDIO / TRANSCODING ARCHITECTURE

- Upload/index MP3 (and other ffmpeg-readable) music
- Convert to seekable 44.1 kHz stereo PCM WAV for `pi_fm_rds`
- Durable cache keyed by source identity (path + mtime + size)
- Heavy convert runs outside the controller lock; status/STOP stay responsive
- Opportunistic Up Next warm-cache when idle enough

## 8. BROADCAST STATE MODEL

Software states include SAFE_OFF / READY / ON_AIR / FAULT with UI labels:

- OFF / STARTING BROADCAST… / ON AIR / STOPPING… / STATE UNKNOWN / POSSIBLE TRANSMISSION

Program UI: READY / STARTING MUSIC… / PLAYING / PAUSING… / PAUSED / RESUMING… /
CHANGING TRACK…

**Never claim ON AIR or PAUSED before backend confirmation.** Optimistic UI may
show transitional “starting/pausing” only.

## 9. STANDALONE APPLIANCE / RECOVERY MODEL

The Raspberry Pi runs the station. The web interface is a remote control, not
part of the broadcast path. Normal broadcasting and local program progression
do not depend on a browser, LAN, Internet, DNS, NTP, or cloud service.

Persist only the operator's deliberate broadcast intent:

- Moving the brass handle into the FM tuner atomically arms ON recovery before
  transmitter startup.
- Absolute STOP atomically disarms recovery before transmitter shutdown.
- Fresh install / absent or corrupt marker means OFF.
- Service and power restoration reuse the canonical readiness, media, hardware,
  and single-transmitter gates.
- Failed recovery is latched for the current machine boot to prevent systemd
  restart loops.
- Service shutdown stops RF without rewriting deliberate operator intent.
- A disconnected browser discards its local station authority and blocks
  mutations. Reconnection must replace it with one complete read-only status
  snapshot before SSE resumes.
- Status snapshots carry a per-controller `authority_id`; a browser must accept
  a new controller even when its process-local revision restarts at a lower
  number, then reject delayed snapshots from the replaced controller.
- Browser reconnection must have zero effect on RF or playback.

Founder-operated A+ network-disconnect and power-restoration behavior was
physically validated on pre-flagpole P1G product SHA
`f57cab0583f70f431e478eda784233117b4bd56d`. Every later RF-lifecycle or
control-surface change requires its own exact-tip founder gate; historical
evidence must not be presented as validation of a newer SHA.

## 10. ABSOLUTE STOP / SAFETY INVARIANTS

- Fresh install / OFF intent: **TX OFF**, state not ON_AIR
- Config changes, uploads, playlist selection: **must not** auto-TX
- OFF AIR detent / STOP BROADCAST: cancels in-flight start; the independent
  masthead STOP remains available when handle position is unknown
- STOP persists OFF before transmitter termination and blocks later restoration
- Pause keeps carrier (silence hold) when ON AIR; STOP ends transmission
- Reboot restores only valid deliberate ON intent after safety validation
- Frequency uses one canonical `87.1–108.2 MHz` value on an enforced `0.1 MHz`
  grid. Invalid/off-grid legacy values block readiness rather than being rounded.
- Fake/mock backends are for automated tests — not the normal operator product mode

## 11. OPERATOR UX PRINCIPLES

- Centre PLAY/PAUSE primary; PREV/NEXT secondary
- Broadcast uses one vertical brass handle: the bottom is a small OFF AIR
  detent, `87.1 MHz` begins at 10% travel, and `108.2 MHz` is at the top
- Pointer movement is preview-only; release makes one canonical backend command
- The pirate flag is hidden while OFF AIR and during preview, then appears only
  after the confirmed start boundary. OFF AIR renders the handle at the bottom
  while an actionable marker shows the Station frequency; selecting that marker
  enters the same confirmed start path as the handle.
  ON AIR and recovered intent render only from authoritative status.
- FAULT, possible transmission, or disconnection never displays a definitive
  handle position; the masthead STOP remains independent
- Ship’s Log: meaningful operator transitions (idempotent pause)
- Diagnostics under System, not the first viewport

## 12. HARDWARE ABSTRACTION STRATEGY

Minimal profiles under `hardware/profiles/` document board evidence and defaults.
P0 ships the A+ reference profile only as SUPPORTED. Do not invent validated
profiles for untested boards.

## 13. SUPPORTED-HARDWARE EVIDENCE POLICY

| Label | Meaning |
|-------|---------|
| SUPPORTED | Physically validated with this software + compatible `pi_fm_rds` |
| EXPERIMENTAL | Upstream/docs suggest viability; piFM validation pending |
| UNSUPPORTED | Known incompatible |
| UNKNOWN | Insufficient evidence |

Promotion to SUPPORTED requires a recorded clean-room or lab validation note
(non-RF lifecycle at minimum; RF only with explicit authorization).

## 14. RF DOCUMENTATION POLICY

Distinguish SOFTWARE-PROVEN / HARDWARE-PROVEN / ASSUMED / REQUIRES MEASUREMENT.
Do not market raw GPIO as a finished compliant transmitter. Operators own local
lawful use. Prefer shielded loads for lab work.

## 15. OPEN-SOURCE / GPL-3.0 POLICY

This project is licensed under **GNU GPL version 3**. Users may run, study,
share, and modify under GPL-3.0. That is not write access to the canonical repo.
Upstream PiFmRds is also GPL-3.0 — see `third_party/NOTICE.md`.

## 16. REPOSITORY GOVERNANCE

**Canonical GitHub owner:** [pumpkinpi85](https://github.com/pumpkinpi85)
(intended repo: `pumpkinpi85/pifm`). Not any other organization or Industries account.

Intended eventual GitHub posture: protected `main`, PR-required, maintainer
merges, maintainer releases/tags under that account. P0 creates a **local
candidate only** — no public push until separately authorized.

## 17. TESTING STRATEGY

- Unit/integration tests with `mock`/`fake` TX (no RF)
- Publication sanitization scan for private artifacts
- Clean-room non-RF validation on a fresh Pi/SD (not the reference A+ station)
- Explicit RF validation only with founder authorization

## 18. RELEASE STRATEGY

- Pre-1.0 public candidates: `0.x`
- `1.0.0` only after clean-room Phase A, docs, license, and publication checklist
- Do not pretend public history includes private commissioning chronology

## 19. CURRENT PROJECT STATUS

**P1G candidate:** canonical P1G runs on the physical A+ from `/opt/pifm`.
P1G implements atomic operator broadcast intent, network-independent service
startup, local program progression, and safety-gated restoration. Founder
physical network-loss and power-restoration scenarios passed on the historical
pre-flagpole product-validation SHA. The current flagpole change remains a new
candidate until exact-tip non-RF, A+ OFF AIR, and founder RF validation finish.
Legacy personal trees remain read-only rollback artifacts. No public GitHub
push has occurred.

## 20. KNOWN LIMITATIONS

- Only A+ is SUPPORTED in-repo; other Pis are EXPERIMENTAL/UNKNOWN/UNSUPPORTED
- Pi 5 not supported without a proven RP1-capable backend
- Optional panel LED/switch not required for core product
- RF filtering/matching not measured by this software project
- P1E audio timing and the pre-flagpole P1G network/power restoration behavior
  were physically validated; later product SHAs require fresh exact-tip evidence
- LAN API authentication remains future security-hardening work
- Legacy `/home/pi/pifm` and private Mac tree not deleted (retirement gated)

## 21. NEXT APPROVED PHASE

**Exact-tip flagpole validation:** complete non-RF validation, deploy to the
reference A+ while OFF AIR with preserved operator data, then stop before the
founder-operated real-RF flag interaction. Do not publish GitHub or delete
legacy trees until their separate gates pass.
