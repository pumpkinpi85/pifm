#!/usr/bin/env bash
# Generic piFM station backup (never starts RF).
# Local:
#   ./scripts/station-backup.sh --root /opt/pifm --dest ./backups
#   ./scripts/station-backup.sh --root /opt/pifm --dest ./backups --dry-run
# Remote (runs on host; dest is a path ON the remote host):
#   ./scripts/station-backup.sh --remote pifm --root /opt/pifm --dest /home/pi/backups --dry-run
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OPS="$ROOT_DIR/scripts/pifm_station_ops.py"
REMOTE=""
ROOT=""
DEST=""
DRY=0
MEDIA=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    --root) ROOT="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --no-media) MEDIA=0; shift ;;
    -h|--help) sed -n '1,10p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$DEST" ]] || { echo "--dest is required" >&2; exit 2; }
[[ -n "$ROOT" ]] || ROOT="/opt/pifm"

EXTRA=()
[[ "$MEDIA" -eq 0 ]] && EXTRA+=(--no-media)
[[ "$DRY" -eq 1 ]] && EXTRA+=(--dry-run)

if [[ -n "$REMOTE" ]]; then
  TMP_REMOTE="/tmp/pifm_station_ops.py"
  scp -q "$OPS" "$REMOTE:$TMP_REMOTE"
  ssh -o BatchMode=yes "$REMOTE" \
    "python3 '$TMP_REMOTE' backup --root '$ROOT' --dest '$DEST' ${EXTRA[*]+${EXTRA[*]}}"
else
  python3 "$OPS" backup --root "$ROOT" --dest "$DEST" "${EXTRA[@]}"
fi
