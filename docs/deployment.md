# Deployment & rollback (canonical)

Local tools (this repository):

| Script | Role |
|--------|------|
| `scripts/run-validation.sh` | Host non-RF unit/integration + sanitization |
| `scripts/station-backup.sh` | Timestamped operator-data backup |
| `scripts/deploy-sha.sh` | Stage exact SHA, stamp `build_meta.json`, deploy app |
| `scripts/rollback.sh` | Restore previous **application** snapshot |
| `scripts/validate-reference-hardware.sh` | Non-RF reference proof |
| `scripts/install-pi-fm-rds.sh` | Build/install `pi_fm_rds` → `/usr/local/bin` |
| `scripts/pifm_station_ops.py` | Shared Python 3.7-compatible implementation |

## Exact-SHA deploy invariants

1. Operator data paths are preserved (see `persistent-data.md`).  
2. ON_AIR is never persisted or restored.  
3. Post-restart expectation: OFF AIR.  
4. Success checks OS process counts for `pi_fm_rds` / `fm_transmitter`.  
5. Deployed `build_meta.json` carries `git_sha` + `software_version`.  
6. Remote mutation of the reference A+ remains **gated** until founder
   authorizes cutover (`docs/cutover-reference-station.md`).

## Rollback invariants

- Restores previous application only.  
- Does not overwrite newer music/playlists/DB/config unless explicitly forced
  (no force flag shipped by default).  
- Requires real TX process count 0 before declaring success.
