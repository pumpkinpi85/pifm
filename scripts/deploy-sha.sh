#!/usr/bin/env bash
# Deploy an exact Git SHA of this repository to a station install root.
# NEVER run against the live reference A+ until Nathan authorizes cutover.
#
# Local (lab / dry fixtures):
#   ./scripts/deploy-sha.sh --sha HEAD --target /tmp/pifm-target --dry-run
#
# Authorized remote cutover (future):
#   ./scripts/deploy-sha.sh --sha <fullsha> --remote pifm --target /opt/pifm
#
# Does not start RF. Restarts are explicit and must prove OFF AIR + zero TX procs.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OPS="$ROOT_DIR/scripts/pifm_station_ops.py"
SHA="HEAD"
TARGET=""
REMOTE=""
DRY=0
ALLOW_DIRTY=0
SKIP_VALIDATE=0
PROFILE="raspberry-pi-a-plus"
BACKUP_PARENT=""
PREV_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --backup-parent) BACKUP_PARENT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --skip-host-validate) SKIP_VALIDATE=1; shift ;;
    -h|--help) sed -n '1,16p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$TARGET" ]] || { echo "--target is required" >&2; exit 2; }

FULL_SHA="$(git -C "$ROOT_DIR" rev-parse "$SHA")"
VERSION="$(python3 -c "import sys; sys.path.insert(0,'$ROOT_DIR'); from appliance import __version__; print(__version__)")"
STAGE="$(mktemp -d /tmp/pifm-stage.XXXXXX)"
PREV_DIR=""
cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

STAGE_ARGS=(--repo "$ROOT_DIR" --stage "$STAGE" --sha "$FULL_SHA" --version "$VERSION" --profile "$PROFILE")
[[ "$ALLOW_DIRTY" -eq 1 ]] && STAGE_ARGS+=(--allow-dirty)

echo "Staging $FULL_SHA ($VERSION) → $STAGE"
python3 "$OPS" stage "${STAGE_ARGS[@]}"

if [[ "$DRY" -eq 1 ]]; then
  echo "DRY-RUN deploy to $TARGET (remote=${REMOTE:-local})"
  python3 "$OPS" deploy --stage "$STAGE" --target "$TARGET" --dry-run
  exit 0
fi

if [[ -n "$REMOTE" ]]; then
  echo "ERROR: remote deploy is intentionally gated." >&2
  echo "P1C ships the mechanism but will not mutate the A+ without separate authorization." >&2
  echo "Re-run locally against a lab --target, or obtain cutover authorization." >&2
  exit 3
fi

BACKUP_PARENT="${BACKUP_PARENT:-$(dirname "$TARGET")/pifm-backups}"
mkdir -p "$BACKUP_PARENT"
echo "Backing up operator data from $TARGET"
python3 "$OPS" backup --root "$TARGET" --dest "$BACKUP_PARENT" || {
  echo "Backup failed — aborting deploy" >&2
  exit 1
}

PREV_DIR="$BACKUP_PARENT/previous-app-$FULL_SHA-$(date -u +%Y%m%dT%H%M%SZ)"
echo "Deploying application (preserving operator data)"
python3 "$OPS" deploy --stage "$STAGE" --target "$TARGET" --previous "$PREV_DIR"

echo "Ensuring config cannot retain ON_AIR"
python3 - <<PY
from pathlib import Path
import importlib.util
spec = importlib.util.spec_from_file_location("pifm_station_ops", "$OPS")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.ensure_config_off_air(Path("$TARGET") / "config" / "appliance.json")
print("config_off_air_ok")
PY

if [[ "$SKIP_VALIDATE" -eq 0 ]]; then
  TX="$(python3 "$OPS" tx-count)"
  echo "REAL_TX_PROCESS_COUNT=$TX"
  if [[ "$TX" != "0" ]]; then
    echo "FAIL: transmitter processes running after deploy staging — investigate before service restart" >&2
    exit 1
  fi
  python3 "$OPS" validate --root "$TARGET" || true
fi

echo "DEPLOY_OK sha=$FULL_SHA target=$TARGET previous=$PREV_DIR"
echo "NOTE: service restart is an operator/cutover step; not performed by this script in P1C."
