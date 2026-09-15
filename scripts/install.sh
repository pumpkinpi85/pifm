#!/usr/bin/env bash
# Generic piFM installer for a local Raspberry Pi checkout.
# Does NOT start broadcasting. Does NOT require a remote host IP.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${PIFM_PREFIX:-/opt/pifm}"
SERVICE_USER="${PIFM_USER:-pi}"
PI_FM_RDS_SRC="${PI_FM_RDS_SRC:-}"
PI_FM_RDS_BIN="${PI_FM_RDS_BIN:-/usr/local/bin/pi_fm_rds}"

echo "piFM install"
echo "  source:  $ROOT"
echo "  prefix:  $PREFIX"
echo "  user:    $SERVICE_USER"

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
  --exclude 'data/logs/*' \
  --exclude 'config/appliance.json' \
  "$ROOT"/ "$PREFIX"/

mkdir -p \
  "$PREFIX/data/library" \
  "$PREFIX/data/playlists" \
  "$PREFIX/data/logs/wav/cache" \
  "$PREFIX/data/audio" \
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
cfg["tx_backend"] = "pi_fm_rds"
p.write_text(json.dumps(cfg, indent=2) + "\n")
PY
fi

if [[ ! -x "$PI_FM_RDS_BIN" ]]; then
  if [[ -z "$PI_FM_RDS_SRC" ]]; then
    echo "WARNING: $PI_FM_RDS_BIN not found."
    echo "Build PiFmRds (see docs/installation.md), install the binary to $PI_FM_RDS_BIN,"
    echo "or set PI_FM_RDS_SRC to a checkout and re-run."
  else
    echo "Building pi_fm_rds from $PI_FM_RDS_SRC"
    make -C "$PI_FM_RDS_SRC/src" clean
    make -C "$PI_FM_RDS_SRC/src"
    install -m 755 "$PI_FM_RDS_SRC/src/pi_fm_rds" "$PI_FM_RDS_BIN"
  fi
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
echo "Configure music/station before Raise the Black Flag."
echo "Legal responsibility for RF remains with the operator — see docs/rf-and-law.md"
