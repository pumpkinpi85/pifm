# piFM — PROJECT MASTER

**Durable project authority.** A new contributor or Cursor session should be able
to understand piFM from this file without the private reference-station history.

Version of this document: **0.4.3 P1E (PiFmRds timing calibration capability)**

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
- Operator UX is a physical radio deck metaphor (Black Flag / transport).
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
- Config: JSON under `config/appliance.json` (never persists ON_AIR)
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

## 9. ABSOLUTE STOP / SAFETY INVARIANTS

- Fresh boot / service start: **TX OFF**, state not ON_AIR
- Config changes, uploads, playlist selection: **must not** auto-TX
- Lower the Black Flag / STOP BROADCAST: always available; cancels in-flight start
- Pause keeps carrier (silence hold) when ON AIR; STOP ends transmission
- Reboot returns OFF AIR
- Fake/mock backends are for automated tests — not the normal operator product mode

## 10. OPERATOR UX PRINCIPLES

- Centre PLAY/PAUSE primary; PREV/NEXT secondary
- Raise the Black Flag = go on air; Lower = emergency/safety stop
- Ship’s Log: meaningful operator transitions (idempotent pause)
- Diagnostics under System, not the first viewport

## 11. HARDWARE ABSTRACTION STRATEGY

Minimal profiles under `hardware/profiles/` document board evidence and defaults.
P0 ships the A+ reference profile only as SUPPORTED. Do not invent validated
profiles for untested boards.

## 12. SUPPORTED-HARDWARE EVIDENCE POLICY

| Label | Meaning |
|-------|---------|
| SUPPORTED | Physically validated with this software + compatible `pi_fm_rds` |
| EXPERIMENTAL | Upstream/docs suggest viability; piFM validation pending |
| UNSUPPORTED | Known incompatible |
| UNKNOWN | Insufficient evidence |

Promotion to SUPPORTED requires a recorded clean-room or lab validation note
(non-RF lifecycle at minimum; RF only with explicit authorization).

## 13. RF DOCUMENTATION POLICY

Distinguish SOFTWARE-PROVEN / HARDWARE-PROVEN / ASSUMED / REQUIRES MEASUREMENT.
Do not market raw GPIO as a finished compliant transmitter. Operators own local
lawful use. Prefer shielded loads for lab work.

## 14. OPEN-SOURCE / GPL-3.0 POLICY

This project is licensed under **GNU GPL version 3**. Users may run, study,
share, and modify under GPL-3.0. That is not write access to the canonical repo.
Upstream PiFmRds is also GPL-3.0 — see `third_party/NOTICE.md`.

## 15. REPOSITORY GOVERNANCE

**Canonical GitHub owner:** [pumpkinpi85](https://github.com/pumpkinpi85)
(intended repo: `pumpkinpi85/pifm`). Not any other organization or Industries account.

Intended eventual GitHub posture: protected `main`, PR-required, maintainer
merges, maintainer releases/tags under that account. P0 creates a **local
candidate only** — no public push until separately authorized.

## 16. TESTING STRATEGY

- Unit/integration tests with `mock`/`fake` TX (no RF)
- Publication sanitization scan for private artifacts
- Clean-room non-RF validation on a fresh Pi/SD (not the reference A+ station)
- Explicit RF validation only with founder authorization

## 17. RELEASE STRATEGY

- Pre-1.0 public candidates: `0.4.x`
- `1.0.0` only after clean-room Phase A, docs, license, and publication checklist
- Do not pretend public history includes private commissioning chronology

## 18. CURRENT PROJECT STATUS

**P1D complete (reference A+ cutover):** the physical A+ reference station runs
canonical piFM from `/opt/pifm` with stamped build identity. Operator media and
station settings were preserved. Legacy personal trees remain on-disk as
read-only rollback artifacts pending authorized RF validation and retirement
gates. Still no public GitHub push.

## 19. KNOWN LIMITATIONS

- Only A+ is SUPPORTED in-repo; other Pis are EXPERIMENTAL/UNKNOWN/UNSUPPORTED
- Pi 5 not supported without a proven RP1-capable backend
- Optional panel LED/switch not required for core product
- RF filtering/matching not measured by this software project
- Authorized real-radio validation has not yet been performed
- Legacy `/home/pi/pifm` and private Mac tree not deleted (retirement gated)

## 20. NEXT APPROVED PHASE

**Separately authorized real-radio validation** on the reference A+ (shielded
load preferred), then publication readiness review for
**github.com/pumpkinpi85/pifm**. Do not delete legacy trees until retirement
gates in `docs/legacy-retirement.md` pass with founder authorization.
