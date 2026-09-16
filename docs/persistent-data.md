# Persistent data contract

Canonical application install root: **`/opt/pifm`** (`PIFM_ROOT`).

piFM separates **application** (replaced on upgrade) from **operator data**
(preserved across upgrades). Nathan’s personal values must never be baked into
the product defaults committed to Git.

## Preserved across upgrades (operator data)

| Path | Class |
|------|--------|
| `config/appliance.json` | CONFIGURATION |
| `data/library/` | MEDIA |
| `data/playlists/` | PLAYLIST / OPERATOR DATA |
| `data/library.sqlite3` | PLAYLIST / OPERATOR DATA |
| `data/recovery/` | OPERATOR BROADCAST INTENT |

Deploy and rollback tools must not overwrite these unless an explicit
destructive option is used (none is enabled by default).

## Replaced on upgrade (application)

| Path | Class |
|------|--------|
| `appliance/` | APPLICATION |
| `scripts/`, `systemd/`, `hardware/`, `docs/`, `examples/` | APPLICATION |
| `config/default.json` | APPLICATION template (not live config) |
| `build_meta.json` | BUILD IDENTITY (stamped per deploy) |
| Top-level license/readme/governance docs | APPLICATION |

## Persistent but disposable / runtime

| Path | Class | Notes |
|------|--------|-------|
| `data/logs/` | LOG / RUNTIME | Ship’s log, TX stderr, incident dirs |
| `data/logs/wav/cache/` | CACHE | Regenerable WAV cache |
| `data/audio/` | CACHE / RUNTIME | Generated silence / helpers |

These may be cleared during recovery without losing the station’s music library.

## Never enter Git

- Live `config/appliance.json`
- Operator media and playlists
- SQLite library DB contents
- Logs, caches, backups, commissioning evidence
- Host IPs, personal paths, private keys

## Broadcast-intent invariant

ON_AIR state and transmitter PIDs are **never** persisted in
`appliance.json`. A separate atomic marker under `data/recovery/` records only
the operator's deliberate ON intent. Marker absence means OFF.

Fresh installs therefore boot OFF AIR. A service or machine restart may
restore a valid ON intent only through the complete readiness and
single-transmitter checks. Absolute STOP removes the marker before terminating
RF.
