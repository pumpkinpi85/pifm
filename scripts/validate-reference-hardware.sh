#!/usr/bin/env bash
# Non-radiating reference-hardware validation.
# Local install root:
#   ./scripts/validate-reference-hardware.sh --root /opt/pifm
# Remote read-only (does not restart services or key RF):
#   ./scripts/validate-reference-hardware.sh --remote pifm --root /opt/pifm
# (For pre-cutover stations, pass the station's current install root explicitly.)
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OPS="$ROOT_DIR/scripts/pifm_station_ops.py"
ROOT="/opt/pifm"
REMOTE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) ROOT="$2"; shift 2 ;;
    --remote) REMOTE="$2"; shift 2 ;;
    -h|--help) sed -n '1,8p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$REMOTE" ]]; then
  exec python3 "$OPS" validate --root "$ROOT"
fi

# Assemble remote evidence read-only.
ssh -o BatchMode=yes "$REMOTE" bash -s -- "$ROOT" <<'REMOTE'
set -euo pipefail
ROOT="$1"
python3 - <<'PY' "$ROOT"
import json, os, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])

def tx_count():
    n = 0
    for name in ("pi_fm_rds", "fm_transmitter"):
        try:
            out = subprocess.check_output(["pgrep", "-x", name], stderr=subprocess.DEVNULL)
            n += len([x for x in out.decode().splitlines() if x.strip()])
        except Exception:
            pass
    return n

api = None
try:
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:8080/api/status", timeout=5) as r:
        api = json.loads(r.read().decode())
except Exception as e:
    api = {"_error": str(e)}

svc = None
try:
    subprocess.check_call(["systemctl", "is-active", "--quiet", "pifm-appliance.service"])
    svc = True
except Exception:
    svc = False

dash = False
try:
    import urllib.request
    with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=5) as r:
        dash = r.status == 200
except Exception:
    dash = False

# Minimal inline report if ops module not installed on station yet.
meta = {}
mp = root / "build_meta.json"
if mp.is_file():
    meta = json.loads(mp.read_text())
cfg = {}
cp = root / "config" / "appliance.json"
if cp.is_file():
    cfg = json.loads(cp.read_text())

state = (api or {}).get("state")
tx_running = bool((api or {}).get("tx_running"))
broadcast = "OFF"
if state == "ON_AIR" or tx_running:
    broadcast = "NOT_OFF"
count = tx_count()
result = "PASS"
if count != 0 or broadcast != "OFF" or not (root / "data" / "library").is_dir():
    result = "FAIL"
if svc is False:
    result = "FAIL"

print("PI FM REFERENCE HARDWARE VALIDATION")
print("")
print("MODEL: (see /proc/cpuinfo on host)")
print("PROFILE:", cfg.get("hardware_profile") or meta.get("hardware_profile") or "—")
print("VERSION:", (api or {}).get("software_version") or meta.get("software_version") or cfg.get("software_version") or "—")
print("BUILD SHA:", (api or {}).get("git_sha") or meta.get("git_sha") or "unknown")
print("")
print("SERVICE:", svc)
print("API:", api is not None and "_error" not in (api or {}))
print("DASHBOARD:", dash)
print("")
print("CONFIG:", cp.is_file())
print("LIBRARY:", (root / "data" / "library").is_dir())
print("PLAYLISTS:", (root / "data" / "playlists").is_dir())
print("")
print("BACKEND:", cfg.get("tx_backend") or (api or {}).get("tx_backend"))
print("")
print("BROADCAST:")
print(broadcast)
print("")
print("REAL TX PROCESS COUNT:")
print(count)
print("")
print("GPIO:", cfg.get("gpio_enabled") if "gpio_enabled" in cfg else (api or {}).get("gpio_enabled"))
print("")
print("NETWORK:", (api or {}).get("network"))
print("")
print("RESULT:")
print(result)
sys.exit(0 if result == "PASS" else 2)
PY
REMOTE
