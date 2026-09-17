# Architecture

Engineering overview for contributors. Beginners should start with the
[README](../README.md) and [USER-MANUAL.md](USER-MANUAL.md).

## Runtime shape

```
Browser Broadcast Deck
    │  HTTP JSON + SSE
    ▼
appliance.webapp (ThreadingHTTPServer)
    ▼
Controller (state machine, queue, STOP)
    ├── Library (files + SQLite)
    ├── NetworkManager (RF Quiet; never starts TX)
    └── TxBackend
            ├── pi_fm_rds (production)
            ├── mock / fake (tests)
            └── OwnedTxProcess (PID ownership)
```

Audio path: source → FFprobe validate → (optional) owned FFmpeg → seekable WAV
cache → `pi_fm_rds -audio <wav>` → GPIO 4.

![Audio pipeline](diagrams/audio-pipeline.svg)

![Broadcast lifecycle](diagrams/broadcast-lifecycle.svg)

## One authoritative station frequency

`frequency_mhz` is a single config value. Station precision editing and the
Broadcast brass handle read/write that same setting. The UI does not keep a
second competing frequency.

## Authoritative appliance state

The controller owns broadcast, program, fault, queue, and recovery state.
Browsers display snapshots; they are not the source of truth. Status responses
and SSE payloads carry monotonic revisions and an authority ID so stale clients
cannot mutate a restarted process.

## Transmitter ownership / lifecycle

- Production backend supervises `pi_fm_rds` with explicit worker PID ownership.
- Single-transmitter invariant: duplicate starts are rejected.
- Absolute Stop Broadcast cancels in-flight starts, stops the owned worker, and
  verifies process-table cleanup.
- Startup clears orphan transmitter processes before any recovery attempt.

## Recovery intent

- ON intent is recorded atomically under `data/recovery/` **before** TX start.
- OFF / Stop renames the ON marker to an OFF tombstone before RF shutdown.
- No valid ON marker means OFF (fresh install default).
- Restoration after service/power events repeats readiness and single-worker
  checks; failed recovery latches for the boot to avoid restart loops.

## SSE / reconnection authority

- Live updates use Server-Sent Events with periodic full-status heartbeats.
- On disconnect, the browser discards local confirmation and blocks mutations.
- On reconnect, one complete `GET /api/status` must succeed before SSE resumes.
- Reconciliation never issues play/stop/tune commands.

## Hardware profiles

Profiles under `hardware/profiles/` describe detection hints, GPIO defaults,
prerequisites, and support classification. They adapt behaviour; they do not
create separate product editions. Unknown boards fail closed for live TX
readiness rather than guessing.

## Media validation / transcoding

- Six filename families: MP3, WAV, FLAC, M4A, AAC, OGG.
- FFprobe/FFmpeg remain authoritative for decodability.
- Suitable seekable PCM16 WAV may bypass conversion.
- Otherwise FFmpeg produces seekable 44.1 kHz stereo PCM16 cache entries.
- Conversions are owned child processes with bounded cancel/reap/cleanup.

See [music.md](music.md) for the operator-facing contract.

## WAV cache limits / eviction

- `wav_cache_max_mb` and `wav_cache_min_free_mb` bound disposable cache growth.
- Eviction is deterministic and never deletes source media, playlists, config,
  the library DB, active conversion output, or TX-owned audio.
- Speculative cache warming defaults off and never runs while ON AIR.

## Persistent operator-data boundary

| Preserved | Replaced on upgrade |
|-----------|---------------------|
| `config/appliance.json` | `appliance/`, `scripts/`, `docs/`, … |
| `data/library/`, `data/playlists/` | `config/default.json` templates |
| `data/library.sqlite3` | stamped `build_meta.json` |
| `data/recovery/` | |

Logs and WAV cache are disposable runtime data. Live operator data must never
be committed to Git.

## Deployment / rollback

- `scripts/install.sh` — first install / refresh
- `scripts/deploy-sha.sh` — exact Git SHA application deploy
- `scripts/rollback.sh` — previous application snapshot
- `scripts/station-backup.sh` — operator-data backup

Invariants: preserve operator data by default; never persist transient ON_AIR
PIDs in `appliance.json`; post-deploy expectation is OFF AIR unless valid ON
intent deliberately remains.

## A+ performance constraints

Design for single-core ARMv6 and ~430–512 MiB RAM:

- No Docker / Node production runtime / heavy frameworks
- Streaming uploads; incremental library indexing
- Reject expensive library mutations while ON AIR
- Bound FFmpeg and WAV cache aggressively

The A+ floor is a product feature that keeps the architecture honest.
