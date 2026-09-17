#!/usr/bin/env bash
# Generic piFM installer for a local Raspberry Pi checkout.
# Does NOT start broadcasting. Does NOT require a remote host IP.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PIFM_PREFIX:-/opt/pifm}"
SERVICE_USER="${PIFM_USER:-pi}"
PI_FM_RDS_SRC="${PI_FM_RDS_SRC:-}"
PI_FM_RDS_BIN="${PI_FM_RDS_BIN:-/usr/local/bin/pi_fm_rds}"
# Production default is pi_fm_rds. Use PIFM_TX_BACKEND=mock for clean-room
# lifecycle-only installs (no RF). Never use this script to start transmitting.
TX_BACKEND="${PIFM_TX_BACKEND:-pi_fm_rds}"
CONFIGURE_HARDWARE="${PIFM_CONFIGURE_HARDWARE:-0}"

echo "piFM install"
echo "  source:  $ROOT"
echo "  prefix:  $PREFIX"
echo "  user:    $SERVICE_USER"
echo "  tx:      $TX_BACKEND"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo so systemd and /opt can be configured." >&2
  exit 1
fi

apt-get update
apt-get install -y python3 ffmpeg libsndfile1-dev git build-essential

mkdir -p "$PREFIX"
rsync -a \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude 'data/library/*' \
  --exclude 'data/playlists/*' \
  --exclude 'data/logs/*' \
  --exclude 'data/recovery' \
  --exclude 'data/backups' \
  --exclude 'data/library.sqlite3' \
  --exclude 'data/*.sqlite3' \
  --exclude 'config/appliance.json' \
  --exclude '.env' \
  --exclude '.env.*' \
  "$ROOT"/ "$PREFIX"/

mkdir -p \
  "$PREFIX/data/library" \
  "$PREFIX/data/playlists" \
  "$PREFIX/data/logs/wav/cache" \
  "$PREFIX/data/audio" \
  "$PREFIX/data/recovery" \
  "$PREFIX/config"

if [[ ! -f "$PREFIX/config/appliance.json" ]]; then
  cp "$PREFIX/examples/config.example.json" "$PREFIX/config/appliance.json"
  # Point transmitter path at the installed binary location.
  python3 - <<PY
import json
from pathlib import Path
p = Path("$PREFIX/config/appliance.json")
cfg = json.loads(p.read_text())
cfg["pi_fm_rds_path"] = "$PI_FM_RDS_BIN"
cfg["tx_backend"] = "$TX_BACKEND"
if cfg["tx_backend"] not in ("mock", "pi_fm_rds", "fake"):
    raise SystemExit("PIFM_TX_BACKEND must be mock, pi_fm_rds, or fake")
p.write_text(json.dumps(cfg, indent=2) + "\n")
PY
fi

if [[ "$TX_BACKEND" == "pi_fm_rds" && ! -x "$PI_FM_RDS_BIN" ]]; then
  if [[ -z "$PI_FM_RDS_SRC" ]]; then
    echo "WARNING: $PI_FM_RDS_BIN not found."
    echo "Build PiFmRds (see docs/INSTALL.md), install the binary to $PI_FM_RDS_BIN,"
    echo "or set PI_FM_RDS_SRC to a checkout and re-run."
    echo "For non-RF clean-room lifecycle only: PIFM_TX_BACKEND=mock ./scripts/install.sh"
  else
    echo "Building pi_fm_rds from $PI_FM_RDS_SRC"
    make -C "$PI_FM_RDS_SRC/src" clean
    make -C "$PI_FM_RDS_SRC/src"
    install -m 755 "$PI_FM_RDS_SRC/src/pi_fm_rds" "$PI_FM_RDS_BIN"
  fi
fi

if [[ "$CONFIGURE_HARDWARE" == "1" ]]; then
  "$ROOT/scripts/configure-hardware.sh" --apply
else
  echo
  echo "Hardware readiness (read-only):"
  "$ROOT/scripts/hardware-readiness.py" || true
  echo "For a detected supported A+, re-run with PIFM_CONFIGURE_HARDWARE=1"
  echo "to apply the reversible headless/onboard-audio prerequisites."
fi

chown -R "$SERVICE_USER:$SERVICE_USER" "$PREFIX"

UNIT_SRC="$PREFIX/systemd/pifm-appliance.service"
UNIT_DST="/etc/systemd/system/pifm-appliance.service"
sed \
  -e "s|/opt/pifm|$PREFIX|g" \
  -e "s|User=pi|User=$SERVICE_USER|g" \
  -e "s|Group=pi|Group=$SERVICE_USER|g" \
  "$UNIT_SRC" > "$UNIT_DST"

systemctl daemon-reload
systemctl enable pifm-appliance.service
systemctl restart pifm-appliance.service

echo
echo "Installed. Service enabled. Broadcast state should be OFF AIR."
echo "Open: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8080/"
echo "Configure music/station before moving the brass handle out of OFF AIR."
echo "Legal responsibility for RF remains with the operator — see docs/rf-and-law.md"
