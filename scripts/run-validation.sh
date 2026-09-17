#!/usr/bin/env bash
# P1A / release-gate: full non-RF appliance + publication sanitization suite.
# Never starts RF. Uses mock/fake backends inside tests only.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "piFM non-RF validation"
echo "  root: $ROOT"
echo "  backend: mock/fake (tests only)"
echo

python3 -m unittest discover -s appliance/tests -v

echo
echo "VALIDATION_OK: all non-RF unit/integration and sanitization tests passed."
