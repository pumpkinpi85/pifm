# Clean-room validation

**Do not use the personal reference A+ station for this procedure.**
Use a spare Pi or a fresh SD card image.

Evidence form: [evidence/clean-room-phase-a.template.md](evidence/clean-room-phase-a.template.md)  
Convergence prep: [canonical-convergence.md](canonical-convergence.md)

## Host software gate (before or beside on-Pi Phase A)

From a development checkout of this candidate (no RF):

```bash
./scripts/run-validation.sh
```

This runs the full mock/fake unit/integration suite and publication sanitization
tests. It must pass on the candidate SHA before claiming P1A software readiness.

## Phase A — non-RF (required before publication)

May install with `tx_backend=pi_fm_rds` **or** temporarily `mock` for lifecycle-only.
For mock-oriented installs:

```bash
sudo PIFM_TX_BACKEND=mock ./scripts/install.sh
```

If `pi_fm_rds` is installed, keep the station **OFF AIR**; do not Raise the Black
Flag unless Phase B is authorized. Prefer disconnecting any antenna / using a
shielded load if the binary is present.

Checklist:

1. Flash Raspberry Pi OS Lite; boot; network/SSH
2. Clone the **public candidate** repository (not the private restoration tree)
3. Install packages; build `pi_fm_rds` if needed; run `scripts/install.sh`
4. Confirm service active; Deck loads
5. Grep install tree for private artifacts (LAN IPs, legacy home directory
   transmitter trees, commissioning bundles, personal hostnames — see
   `appliance/tests/test_publication_sanitize.py` for the current token list)
6. Upload ordinary MP3s; create/select playlist
7. Configure frequency/RDS
8. Play / Pause / Next with TX still OFF (program preview / mock as applicable)
9. If using mock: Raise/Lower Black Flag exercises lifecycle without RF
10. Absolute STOP while STARTING (inject slow prepare in test harness or use mock delay)
11. With recovery disarmed/OFF intent, reboot; confirm OFF AIR and zero TX
12. Record board model, OS version, piFM commit, `pi_fm_rds` commit on the
    evidence template

## Phase B — explicit RF validation (optional, authorized only)

Founder/maintainer authorization required. Prefer shielded feed to a receiver.
Document spectrum caution; no compliance claims without measurement.
