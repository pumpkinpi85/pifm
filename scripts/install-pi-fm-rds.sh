#!/usr/bin/env bash
# Build/install pi_fm_rds to the canonical system path.
# Default destination: /usr/local/bin/pi_fm_rds
# Does NOT start RF. Does NOT modify any existing transmitter binary unless
# that path is explicitly passed as the source checkout or --dest.
#
#   ./scripts/install-pi-fm-rds.sh --src /path/to/PiFmRds
#   PI_FM_RDS_SRC=/path/to/PiFmRds ./scripts/install-pi-fm-rds.sh
set -euo pipefail
SRC="${PI_FM_RDS_SRC:-}"
DEST="${PI_FM_RDS_BIN:-/usr/local/bin/pi_fm_rds}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --src) SRC="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    -h|--help) sed -n '1,12p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$SRC" ]] || { echo "--src or PI_FM_RDS_SRC required" >&2; exit 2; }
[[ -d "$SRC/src" ]] || { echo "PiFmRds src dir not found under $SRC" >&2; exit 2; }

echo "Building pi_fm_rds from $SRC"
make -C "$SRC/src" clean
make -C "$SRC/src"
echo "Installing to $DEST (requires write permission / sudo)"
install -m 755 "$SRC/src/pi_fm_rds" "$DEST"
echo "INSTALLED $DEST"
file "$DEST" || true
echo "Rebuild on each board/architecture. A+ is ARMv6 — build on-device."
