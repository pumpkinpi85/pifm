#!/usr/bin/env bash
# Rollback application to a previous snapshot directory.
# Preserves operator config/media/playlists/DB by default.
# Never starts RF.
#
#   ./scripts/rollback.sh --previous /path/to/previous-app --target /opt/pifm
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OPS="$ROOT_DIR/scripts/pifm_station_ops.py"
PREV=""
TARGET=""
DRY=0
REMOTE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --previous) PREV="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '1,8p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$PREV" && -n "$TARGET" ]] || { echo "--previous and --target required" >&2; exit 2; }

if [[ -n "$REMOTE" ]]; then
  echo "ERROR: remote rollback gated until cutover authorization." >&2
  exit 3
fi

ARGS=(rollback --previous "$PREV" --target "$TARGET")
[[ "$DRY" -eq 1 ]] && ARGS+=(--dry-run)
python3 "$OPS" "${ARGS[@]}"

if [[ "$DRY" -eq 0 ]]; then
  TX="$(python3 "$OPS" tx-count)"
  echo "REAL_TX_PROCESS_COUNT=$TX"
  [[ "$TX" == "0" ]] || { echo "FAIL: TX processes present" >&2; exit 1; }
  python3 "$OPS" validate --root "$TARGET" || true
fi
echo "ROLLBACK_OK"
