#!/usr/bin/env bash
# Reversibly establish known profile prerequisites. Never starts pi_fm_rds.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APPLY="${1:-}"
MODEL_FILE="/proc/device-tree/model"
BOOT_CONFIG="/boot/config.txt"
if [[ -f /boot/firmware/config.txt ]]; then
  BOOT_CONFIG="/boot/firmware/config.txt"
fi
export BOOT_CONFIG
MODEL=""
if [[ -r "$MODEL_FILE" ]]; then
  MODEL="$(tr -d '\0' < "$MODEL_FILE")"
fi

if [[ "$MODEL" != "Raspberry Pi Model A Plus Rev 1.1" ]]; then
  echo "No automatic hardware changes made."
  echo "Detected: ${MODEL:-unknown hardware}"
  echo "Only the physically proven Raspberry Pi Model A+ profile is configurable."
  exit 2
fi

if [[ "$APPLY" != "--apply" ]]; then
  echo "Detected: $MODEL"
  echo "Proposed reversible changes:"
  echo "  - set dtparam=audio=off in /boot/config.txt"
  echo "  - set the default boot target to multi-user.target"
  echo "Run with --apply to create a backup and apply these settings."
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Re-run with sudo to apply hardware settings." >&2
  exit 1
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/var/lib/pifm/hardware-backups/$STAMP"
mkdir -p "$BACKUP_DIR"
cp -a "$BOOT_CONFIG" "$BACKUP_DIR/config.txt"
readlink /etc/systemd/system/default.target > "$BACKUP_DIR/default-target.txt" || true

python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["BOOT_CONFIG"])
lines = path.read_text().splitlines()
out = []
found = False
for line in lines:
    stripped = line.strip().lower()
    if stripped.startswith("dtparam=audio=") and not stripped.startswith("#"):
        if not found:
            out.append("dtparam=audio=off")
            found = True
        continue
    out.append(line)
if not found:
    out.append("dtparam=audio=off")
path.write_text("\n".join(out) + "\n")
PY

systemctl set-default multi-user.target
cat > "$BACKUP_DIR/rollback.sh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cp -a "$BACKUP_DIR/config.txt" "$BOOT_CONFIG"
TARGET="\$(cat "$BACKUP_DIR/default-target.txt")"
if [[ -n "\$TARGET" ]]; then
  ln -sfn "\$TARGET" /etc/systemd/system/default.target
fi
echo "Hardware settings restored. Reboot when safe."
EOF
chmod 700 "$BACKUP_DIR/rollback.sh"

echo "A+ hardware prerequisites applied."
echo "Backup and rollback: $BACKUP_DIR"
echo "Reboot is required. piFM remains OFF AIR."
"$ROOT/scripts/hardware-readiness.py" --profile raspberry-pi-a-plus || true
