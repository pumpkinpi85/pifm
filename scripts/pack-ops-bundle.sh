#!/usr/bin/env bash
# Build a minimal remote-ops bundle (appliance helpers + station ops).
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-/tmp/pifm-ops-bundle.tgz}"
STAGE="$(mktemp -d /tmp/pifm-ops-pack.XXXXXX)"
mkdir -p "$STAGE/appliance"
cp "$ROOT_DIR/appliance/__init__.py" "$STAGE/appliance/"
cp "$ROOT_DIR/appliance/build_info.py" "$STAGE/appliance/"
cp "$ROOT_DIR/appliance/hardware_profile.py" "$STAGE/appliance/"
# hardware profiles needed for validate/resolve on remote
mkdir -p "$STAGE/hardware/profiles"
cp "$ROOT_DIR/hardware/profiles/"*.json "$STAGE/hardware/profiles/" 2>/dev/null || true
cp "$ROOT_DIR/scripts/pifm_station_ops.py" "$STAGE/"
tar -C "$STAGE" -czf "$OUT" .
rm -rf "$STAGE"
echo "$OUT"
