#!/usr/bin/env bash
# Deploy an exact Git SHA of this repository to a station install root.
#
# Local:
#   ./scripts/deploy-sha.sh --sha HEAD --target /tmp/pifm-target --dry-run
#
# Authorized remote cutover (requires explicit flag):
#   ./scripts/deploy-sha.sh --sha HEAD --remote pifm --target /opt/pifm \
#       --authorize-cutover [--legacy-root <current-install-root>]
#
# Does not start RF. Service restart is separate unless --restart is passed
# with --authorize-cutover.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OPS="$ROOT_DIR/scripts/pifm_station_ops.py"
SHA="HEAD"
TARGET=""
REMOTE=""
DRY=0
ALLOW_DIRTY=0
SKIP_VALIDATE=0
AUTHORIZE=0
RESTART=0
PROFILE="raspberry-pi-a-plus"
BACKUP_PARENT=""
LEGACY_ROOT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --backup-parent) BACKUP_PARENT="$2"; shift 2 ;;
    --legacy-root) LEGACY_ROOT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --allow-dirty) ALLOW_DIRTY=1; shift ;;
    --skip-host-validate) SKIP_VALIDATE=1; shift ;;
    --authorize-cutover) AUTHORIZE=1; shift ;;
    --restart) RESTART=1; shift ;;
    -h|--help) sed -n '1,18p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

[[ -n "$TARGET" ]] || { echo "--target is required" >&2; exit 2; }

FULL_SHA="$(git -C "$ROOT_DIR" rev-parse --verify "${SHA}^{commit}")"
HEAD_SHA="$(git -C "$ROOT_DIR" rev-parse --verify "HEAD^{commit}")"
if [[ "$FULL_SHA" != "$HEAD_SHA" ]]; then
  echo "ERROR: requested SHA is not the checked-out HEAD; check out $FULL_SHA before deploying" >&2
  exit 2
fi
VERSION="$(python3 -c "import sys; sys.path.insert(0,'$ROOT_DIR'); from appliance import __version__; print(__version__)")"
STAGE="$(mktemp -d /tmp/pifm-stage.XXXXXX)"
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

if [[ -n "$REMOTE" && "$AUTHORIZE" -ne 1 ]]; then
  echo "ERROR: remote deploy is gated. Pass --authorize-cutover after founder authorization." >&2
  exit 3
fi

if [[ -z "$REMOTE" ]]; then
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
    [[ "$TX" == "0" ]] || { echo "FAIL: TX processes present" >&2; exit 1; }
  fi
  echo "DEPLOY_OK sha=$FULL_SHA target=$TARGET previous=$PREV_DIR"
  exit 0
fi

# ---- Authorized remote deploy ----
BACKUP_PARENT="${BACKUP_PARENT:-/home/pi/backups}"
REMOTE_STAGE="/tmp/pifm-stage-$FULL_SHA"
REMOTE_PREV="$BACKUP_PARENT/previous-app-$FULL_SHA-$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE_TARBALL="/tmp/pifm-stage-$FULL_SHA.tgz"

echo "Packaging stage"
LOCAL_TGZ="/tmp/pifm-stage-$FULL_SHA.tgz"
tar -C "$STAGE" -czf "$LOCAL_TGZ" .

echo "Uploading stage + ops bundle to $REMOTE"
BUNDLE="$(mktemp /tmp/pifm-ops-bundle.XXXXXX)"
"$ROOT_DIR/scripts/pack-ops-bundle.sh" "$BUNDLE.tgz" >/dev/null
scp -q "$LOCAL_TGZ" "$REMOTE:/tmp/pifm-stage-$FULL_SHA.tgz"
scp -q "$BUNDLE.tgz" "$REMOTE:/tmp/pifm-ops-bundle.tgz"
rm -f "$BUNDLE" "$BUNDLE.tgz"
ssh -o BatchMode=yes "$REMOTE" bash -s <<REMOTE_SCRIPT
set -euo pipefail
STAGE="$REMOTE_STAGE"
TARGET="$TARGET"
LEGACY="${LEGACY_ROOT}"
BACKUP_PARENT="$BACKUP_PARENT"
PREV="$REMOTE_PREV"
rm -rf /tmp/pifm_ops
mkdir -p /tmp/pifm_ops
tar -C /tmp/pifm_ops -xzf /tmp/pifm-ops-bundle.tgz
OPS=/tmp/pifm_ops/pifm_station_ops.py
TGZ=/tmp/pifm-stage-$FULL_SHA.tgz

sudo mkdir -p "\$TARGET" "\$BACKUP_PARENT"
sudo chown -R pi:pi "\$TARGET" "\$BACKUP_PARENT" || true

rm -rf "\$STAGE"
mkdir -p "\$STAGE"
tar -C "\$STAGE" -xzf "\$TGZ"

# First-cutover: seed operator data from legacy root if target has none.
if [[ -n "\$LEGACY" && -d "\$LEGACY" ]]; then
  if [[ ! -f "\$TARGET/config/appliance.json" && -f "\$LEGACY/config/appliance.json" ]]; then
    echo "Seeding operator config from legacy root"
    mkdir -p "\$TARGET/config"
    cp -a "\$LEGACY/config/appliance.json" "\$TARGET/config/appliance.json"
  fi
  if [[ ! -d "\$TARGET/data/library" || -z "\$(ls -A "\$TARGET/data/library" 2>/dev/null || true)" ]]; then
    if [[ -d "\$LEGACY/data/library" ]]; then
      echo "Seeding library from legacy root"
      mkdir -p "\$TARGET/data"
      rm -rf "\$TARGET/data/library"
      cp -a "\$LEGACY/data/library" "\$TARGET/data/library"
    fi
  fi
  if [[ ! -d "\$TARGET/data/playlists" || -z "\$(ls -A "\$TARGET/data/playlists" 2>/dev/null || true)" ]]; then
    if [[ -d "\$LEGACY/data/playlists" ]]; then
      echo "Seeding playlists from legacy root"
      mkdir -p "\$TARGET/data"
      rm -rf "\$TARGET/data/playlists"
      cp -a "\$LEGACY/data/playlists" "\$TARGET/data/playlists"
    fi
  fi
  if [[ ! -f "\$TARGET/data/library.sqlite3" && -f "\$LEGACY/data/library.sqlite3" ]]; then
    echo "Seeding library DB from legacy root"
    mkdir -p "\$TARGET/data"
    cp -a "\$LEGACY/data/library.sqlite3" "\$TARGET/data/library.sqlite3"
  fi
fi

echo "Backup target before deploy"
python3 "\$OPS" backup --root "\$TARGET" --dest "\$BACKUP_PARENT"

echo "Deploy application"
python3 "\$OPS" deploy --stage "\$STAGE" --target "\$TARGET" --previous "\$PREV"

python3 -c "
from pathlib import Path
import importlib.util, json
spec = importlib.util.spec_from_file_location('pifm_station_ops', '/tmp/pifm_ops/pifm_station_ops.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
cfg_path = Path('$TARGET') / 'config' / 'appliance.json'
mod.ensure_config_off_air(cfg_path)
if cfg_path.is_file():
    cfg = json.loads(cfg_path.read_text())
    cfg['pi_fm_rds_path'] = '/usr/local/bin/pi_fm_rds'
    cfg['hardware_profile'] = cfg.get('hardware_profile') or 'raspberry-pi-a-plus'
    cfg['network_iface'] = cfg.get('network_iface') or 'eth0'
    cfg.pop('state', None)
    cfg.pop('on_air', None)
    cfg.pop('tx_on_air', None)
    cfg_path.write_text(json.dumps(cfg, indent=2, sort_keys=True) + '\n')
print('config_normalized_ok')
"

TXC=\$( (pgrep -x pi_fm_rds 2>/dev/null || true) | wc -l | tr -d ' ')
FTC=\$( (pgrep -x fm_transmitter 2>/dev/null || true) | wc -l | tr -d ' ')
echo "REAL_TX_PROCESS_COUNT=\$((TXC+FTC))"
if [[ "\$((TXC+FTC))" -ne 0 ]]; then
  echo "FAIL: transmitter processes present after deploy" >&2
  exit 1
fi
echo "REMOTE_DEPLOY_OK previous=$REMOTE_PREV"
REMOTE_SCRIPT

rm -f "$LOCAL_TGZ"
echo "DEPLOY_OK sha=$FULL_SHA remote=$REMOTE target=$TARGET"

if [[ "$RESTART" -eq 1 ]]; then
  echo "Installing systemd unit and restarting service (authorized)"
  ssh -o BatchMode=yes "$REMOTE" bash -s <<EOF
set -euo pipefail
TARGET="$TARGET"
sudo cp "\$TARGET/systemd/pifm-appliance.service" /etc/systemd/system/pifm-appliance.service
sudo systemctl daemon-reload
sudo systemctl enable pifm-appliance.service
sudo systemctl restart pifm-appliance.service
sleep 3
systemctl is-active pifm-appliance.service
# emergency TX check
TXC=\$( (pgrep -x pi_fm_rds 2>/dev/null || true) | wc -l | tr -d ' ')
FTC=\$( (pgrep -x fm_transmitter 2>/dev/null || true) | wc -l | tr -d ' ')
echo "REAL_TX_PROCESS_COUNT=\$((TXC+FTC))"
if [[ "\$((TXC+FTC))" -ne 0 ]]; then
  echo "EMERGENCY: TX process after restart — stopping service"
  sudo systemctl stop pifm-appliance.service || true
  sudo pkill -x pi_fm_rds || true
  sudo pkill -x fm_transmitter || true
  exit 1
fi
STATUS_FILE=\$(mktemp)
STATUS_READY=0
for _attempt in \$(seq 1 20); do
  if curl -sf http://127.0.0.1:8080/api/status > "\$STATUS_FILE"; then
    STATUS_READY=1
    break
  fi
  sleep 0.5
done
if [[ "\$STATUS_READY" -ne 1 ]]; then
  echo "FAIL: status API did not become ready after service restart" >&2
  rm -f "\$STATUS_FILE"
  exit 1
fi
python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("state")!="ON_AIR"; assert not d.get("tx_running"); print("STATUS", d.get("state"), d.get("software_version"), d.get("git_sha"))' < "\$STATUS_FILE"
rm -f "\$STATUS_FILE"
EOF
fi
