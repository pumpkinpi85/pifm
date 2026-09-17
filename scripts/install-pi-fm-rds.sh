#!/usr/bin/env bash
# Build/install pi_fm_rds from the pinned upstream ChristopheJacquet/PiFmRds
# revision into the canonical system path.
# Default destination: /usr/local/bin/pi_fm_rds
# Does NOT start RF. Does NOT vendor PiFmRds into the piFM tree.
# Does NOT use pumpkinpi85/PiFmRds.
#
#   # Clone (or reuse) the pinned upstream revision, build, install:
#   sudo ./scripts/install-pi-fm-rds.sh
#
#   # Build from an existing checkout (must contain the pinned SHA):
#   sudo ./scripts/install-pi-fm-rds.sh --src /path/to/PiFmRds
#
#   PI_FM_RDS_SRC=/path/to/PiFmRds sudo ./scripts/install-pi-fm-rds.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIN_FILE="${ROOT}/third_party/pifmrds.pin"
# shellcheck disable=SC1090
source "$PIN_FILE"

SRC="${PI_FM_RDS_SRC:-}"
DEST="${PI_FM_RDS_BIN:-$PIFMRDS_BINARY_PATH}"
REPO_URL="${PI_FM_RDS_REPO_URL:-$PIFMRDS_UPSTREAM_URL}"
PIN_SHA="${PI_FM_RDS_SHA:-$PIFMRDS_UPSTREAM_SHA}"
WORK_PARENT="${PI_FM_RDS_WORK_PARENT:-/usr/local/src}"
CLONE_DIR="${WORK_PARENT}/PiFmRds"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src) SRC="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --sha) PIN_SHA="$2"; shift 2 ;;
    -h|--help)
      sed -n '1,20p' "$0"
      echo
      echo "Pinned upstream: $PIFMRDS_UPSTREAM_URL @ $PIFMRDS_UPSTREAM_SHA"
      echo "Canonical binary: $PIFMRDS_BINARY_PATH"
      exit 0
      ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SRC" ]]; then
  mkdir -p "$WORK_PARENT"
  if [[ -d "$CLONE_DIR/.git" ]]; then
    echo "Reusing checkout $CLONE_DIR"
    git -C "$CLONE_DIR" fetch --quiet origin
  else
    echo "Cloning $REPO_URL into $CLONE_DIR"
    git clone "$REPO_URL" "$CLONE_DIR"
  fi
  git -C "$CLONE_DIR" checkout --quiet "$PIN_SHA"
  SRC="$CLONE_DIR"
fi

[[ -d "$SRC/src" ]] || { echo "PiFmRds src dir not found under $SRC" >&2; exit 2; }

if [[ -d "$SRC/.git" ]]; then
  HEAD_SHA="$(git -C "$SRC" rev-parse HEAD)"
  if [[ "$HEAD_SHA" != "$PIN_SHA" ]]; then
    echo "ERROR: PiFmRds checkout HEAD is $HEAD_SHA" >&2
    echo "Expected pinned upstream SHA $PIN_SHA" >&2
    echo "Run without --src to clone/check out the pin, or:" >&2
    echo "  git -C \"$SRC\" checkout $PIN_SHA" >&2
    exit 3
  fi
  REMOTE_URL="$(git -C "$SRC" remote get-url origin 2>/dev/null || true)"
  case "$REMOTE_URL" in
    *pumpkinpi85/PiFmRds*)
      echo "ERROR: refusing pumpkinpi85/PiFmRds. Use upstream ChristopheJacquet/PiFmRds." >&2
      exit 3
      ;;
  esac
else
  echo "WARNING: $SRC is not a git checkout; cannot verify pinned SHA $PIN_SHA" >&2
fi

echo "Building pi_fm_rds from $SRC @ $PIN_SHA"
make -C "$SRC/src" clean
make -C "$SRC/src"
echo "Installing to $DEST (requires write permission / sudo)"
install -m 755 "$SRC/src/pi_fm_rds" "$DEST"
echo "INSTALLED $DEST"
echo "PIFMRDS_UPSTREAM_SHA=$PIN_SHA"
file "$DEST" || true
echo "Rebuild on each board/architecture. A+ is ARMv6 — build on-device."
