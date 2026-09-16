#!/usr/bin/env bash
# Rollback application to a previous snapshot directory.
# Preserves operator config/media/playlists/DB by default.
# Never starts RF.
#
# Local:
#   ./scripts/rollback.sh --previous /path/to/previous-app --target /opt/pifm
# Authorized remote:
#   ./scripts/rollback.sh --previous /home/pi/backups/previous-app-... --target /opt/pifm \
#       --remote pifm --authorize-cutover [--restart]
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OPS="$ROOT_DIR/scripts/pifm_station_ops.py"
PREV=""
TARGET=""
DRY=0
REMOTE=""
AUTHORIZE=0
RESTART=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --previous) PREV="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --authorize-cutover) AUTHORIZE=1; shift ;;
    --restart) RESTART=1; shift ;;
    -h|--help) sed -n '1,12p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$PREV" && -n "$TARGET" ]] || { echo "--previous and --target required" >&2; exit 2; }

if [[ -n "$REMOTE" && "$AUTHORIZE" -ne 1 ]]; then
  echo "ERROR: remote rollback gated. Pass --authorize-cutover after founder authorization." >&2
  exit 3
fi

if [[ -z "$REMOTE" ]]; then
  ARGS=(rollback --previous "$PREV" --target "$TARGET")
  [[ "$DRY" -eq 1 ]] && ARGS+=(--dry-run)
  python3 "$OPS" "${ARGS[@]}"
  if [[ "$DRY" -eq 0 ]]; then
    TX="$(python3 "$OPS" tx-count)"
    echo "REAL_TX_PROCESS_COUNT=$TX"
    [[ "$TX" == "0" ]] || { echo "FAIL: TX processes present" >&2; exit 1; }
  fi
  echo "ROLLBACK_OK"
  exit 0
fi

if [[ "$DRY" -eq 1 ]]; then
  echo "DRY-RUN remote rollback $REMOTE:$PREV -> $TARGET"
  exit 0
fi

scp -q "$OPS" "$REMOTE:/tmp/pifm_station_ops.py"
# Prefer ops bundle so appliance helpers resolve on remote.
BUNDLE="$(mktemp /tmp/pifm-ops-bundle.XXXXXX.tgz)"
"$ROOT_DIR/scripts/pack-ops-bundle.sh" "$BUNDLE" >/dev/null
scp -q "$BUNDLE" "$REMOTE:/tmp/pifm-ops-bundle.tgz"
rm -f "$BUNDLE"
ssh -o BatchMode=yes "$REMOTE" bash -s <<EOF
set -euo pipefail
rm -rf /tmp/pifm_ops
mkdir -p /tmp/pifm_ops
tar -C /tmp/pifm_ops -xzf /tmp/pifm-ops-bundle.tgz
python3 /tmp/pifm_ops/pifm_station_ops.py rollback --previous "$PREV" --target "$TARGET"
TXC=\$(pgrep -x pi_fm_rds 2>/dev/null | wc -l | tr -d ' ')
FTC=\$(pgrep -x fm_transmitter 2>/dev/null | wc -l | tr -d ' ')
echo "REAL_TX_PROCESS_COUNT=\$((TXC+FTC))"
[[ "\$((TXC+FTC))" -eq 0 ]] || exit 1
EOF

if [[ "$RESTART" -eq 1 ]]; then
  ssh -o BatchMode=yes "$REMOTE" bash -s <<EOF
set -euo pipefail
sudo cp "$TARGET/systemd/pifm-appliance.service" /etc/systemd/system/pifm-appliance.service
sudo systemctl daemon-reload
sudo systemctl restart pifm-appliance.service
sleep 3
systemctl is-active pifm-appliance.service
TXC=\$(pgrep -x pi_fm_rds 2>/dev/null | wc -l | tr -d ' ')
FTC=\$(pgrep -x fm_transmitter 2>/dev/null | wc -l | tr -d ' ')
echo "REAL_TX_PROCESS_COUNT=\$((TXC+FTC))"
[[ "\$((TXC+FTC))" -eq 0 ]] || { sudo systemctl stop pifm-appliance.service; sudo pkill -x pi_fm_rds || true; sudo pkill -x fm_transmitter || true; exit 1; }
curl -sf http://127.0.0.1:8080/api/status | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("state")!="ON_AIR"; assert not d.get("tx_running"); print("STATUS_OK", d.get("software_version"), d.get("git_sha"))'
EOF
fi
echo "ROLLBACK_OK"
