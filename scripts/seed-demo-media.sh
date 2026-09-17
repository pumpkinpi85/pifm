#!/usr/bin/env bash
# Seed the bundled demo track into an install prefix without clobbering
# existing operator media or playlists. Never starts RF.
set -euo pipefail

PREFIX="${1:-}"
if [[ -z "$PREFIX" ]]; then
  echo "usage: $0 /path/to/pifm-prefix" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_NAME="Brynja Vinter - The Sky Belongs to No King.wav"
DEMO_SRC="$ROOT/examples/demo/$DEMO_NAME"
# After rsync install, the demo also lives under the prefix examples tree.
if [[ ! -f "$DEMO_SRC" && -f "$PREFIX/examples/demo/$DEMO_NAME" ]]; then
  DEMO_SRC="$PREFIX/examples/demo/$DEMO_NAME"
fi
if [[ ! -f "$DEMO_SRC" ]]; then
  echo "Demo media not found; skipping seed." >&2
  exit 0
fi

DEST_DIR="$PREFIX/data/library/demo"
DEST="$DEST_DIR/$DEMO_NAME"
PLAYLIST="$PREFIX/data/playlists/default.json"
REL_PATH="demo/$DEMO_NAME"

mkdir -p "$DEST_DIR" "$PREFIX/data/playlists"

if [[ ! -f "$DEST" ]]; then
  cp "$DEMO_SRC" "$DEST"
  echo "Seeded demo track: $DEST"
else
  echo "Demo track already present; leaving operator copy unchanged."
fi

python3 - <<PY
import json
import uuid
from pathlib import Path

playlist = Path("$PLAYLIST")
rel = "$REL_PATH"
track_id = uuid.uuid5(uuid.NAMESPACE_URL, rel).hex
if playlist.exists():
    print("Playlist already present; leaving unchanged:", playlist)
else:
    playlist.write_text(
        json.dumps({"name": "default", "tracks": [track_id]}, indent=2) + "\n"
    )
    print("Seeded default playlist with demo track id", track_id)
PY
