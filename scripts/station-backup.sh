#!/usr/bin/env bash
# Generic piFM station backup (never starts RF).
# Local:
#   ./scripts/station-backup.sh --root /opt/pifm --dest ./backups
# Remote (dest is a path ON the remote host):
#   ./scripts/station-backup.sh --remote pifm --root /opt/pifm --dest /home/pi/backups
# Pre-cutover stations: pass the live install root explicitly as --root.
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
  BUNDLE="$(mktemp /tmp/pifm-ops-bundle.XXXXXX.tgz)"
  "$ROOT_DIR/scripts/pack-ops-bundle.sh" "$BUNDLE" >/dev/null
  scp -q "$BUNDLE" "$REMOTE:/tmp/pifm-ops-bundle.tgz"
  rm -f "$BUNDLE"
  ssh -o BatchMode=yes "$REMOTE" bash -s <<EOF
set -euo pipefail
rm -rf /tmp/pifm_ops
mkdir -p /tmp/pifm_ops
tar -C /tmp/pifm_ops -xzf /tmp/pifm-ops-bundle.tgz
python3 /tmp/pifm_ops/pifm_station_ops.py backup --root '$ROOT' --dest '$DEST' ${EXTRA[*]+${EXTRA[*]}}
EOF
else
  python3 "$OPS" backup --root "$ROOT" --dest "$DEST" "${EXTRA[@]}"
fi
