# Migrating from a previous install

Use this when moving an older piFM install into the canonical `/opt/pifm`
layout. This is a maintainer procedure, not part of a first-time hobbyist
install. Prefer [INSTALL.md](INSTALL.md) for new stations.

## Goals

- Preserve operator music, playlists, SQLite index, and `appliance.json`
- Install the canonical application under `/opt/pifm`
- Leave the station **OFF AIR** after cutover
- Never copy private developer paths, host IPs, or commissioning evidence into
  the public product tree

## Checklist

1. Confirm you can SSH to the station as its normal admin user.
2. Take a backup:
   ```bash
   ./scripts/station-backup.sh --remote <user@host> --root <current-root> --dest /home/pi/backups
   ```
3. Validate the target hardware profile without starting RF:
   ```bash
   ./scripts/validate-reference-hardware.sh --remote <user@host> --root <current-root>
   ```
4. Deploy an exact product SHA to `/opt/pifm` with operator-data preservation
   (see `scripts/deploy-sha.sh` help). Require an explicit authorization flag
   for remote mutation scripts.
5. Restart the appliance service and confirm `broadcast_state` is `off`.
6. Open the Broadcast Deck and verify music/playlists/station settings.
7. Keep the previous install root as a read-only rollback artifact until a
   maintainer explicitly retires it.

Do not delete prior trees until backup, OFF AIR proof, and operator acceptance
are complete.
