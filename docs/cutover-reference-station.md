# Reference station cutover (`/home/pi/pifm` → `/opt/pifm`)

**P1D executed with founder authorization** for the reference A+ cutover.
Legacy trees remain until retirement gates pass.

## Preconditions

- [ ] Canonical tip validated: `./scripts/run-validation.sh`
- [ ] P1C tools present on the Mac checkout
- [ ] `ssh pifm` works
- [ ] Station currently OFF AIR; `REAL TX PROCESS COUNT` is 0
- [ ] Founder authorization recorded for cutover (and separately for any RF)

## Procedure (future)

1. **Record baseline** (read-only):  
   `./scripts/validate-reference-hardware.sh --remote pifm --root /home/pi/pifm`

2. **Backup operator data on the Pi** (authorized write):  
   `./scripts/station-backup.sh --remote pifm --root /home/pi/pifm --dest /home/pi/backups`

3. **Build/install normalized `pi_fm_rds`** on the Pi to `/usr/local/bin/pi_fm_rds`
   using `scripts/install-pi-fm-rds.sh` from an on-device PiFmRds checkout
   (do not delete the historical binary until after PASS).

4. **Stage + deploy exact SHA** into `/opt/pifm` using `scripts/deploy-sha.sh`
   (after remote gate is intentionally enabled for authorized cutover).  
   Preserve:
   - `config/appliance.json` (merge `pi_fm_rds_path` → `/usr/local/bin/pi_fm_rds`,
     set `hardware_profile=raspberry-pi-a-plus`, never ON_AIR)
   - `data/library/`, `data/playlists/`, `data/library.sqlite3`

5. **Install systemd unit** from `/opt/pifm/systemd/pifm-appliance.service`
   (`PIFM_ROOT=/opt/pifm`, `Restart=always`). Disable any legacy auto-TX units.

6. **Restart service once** (authorized). Confirm:
   - Deck loads
   - API `broadcast` OFF / not ON_AIR
   - `build_meta.json` / API `git_sha` matches deployed SHA
   - OS `pgrep -x pi_fm_rds` and `fm_transmitter` → empty

7. **Non-RF validation**:  
   `./scripts/validate-reference-hardware.sh --remote pifm --root /opt/pifm`

8. **Rollback proof** (still non-RF): restore previous app snapshot via
   `scripts/rollback.sh`; confirm operator media/config intact; re-deploy forward.

9. **Authorized RF validation** only with explicit founder approval (Phase B).

10. **Legacy cleanup (later)**: leave `/home/pi/pifm` and `/home/pi/piFM` until
    retirement gates in `docs/legacy-retirement.md` pass.

## Failure handling

- If deploy fails mid-way: do not leave RF running; stop broadcast; restore
  previous application snapshot; keep operator data untouched.
- If TX processes are non-zero after restart: FAIL — investigate before any
  further RF work.
