#!/usr/bin/env python3
"""Report piFM hardware identity and non-RF host prerequisites."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from appliance.hardware_environment import check_host_prerequisites
from appliance.hardware_profile import resolve_hardware_profile


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        default="auto",
        help="auto or a profile id such as raspberry-pi-a-plus",
    )
    args = parser.parse_args()
    requested = None if args.profile == "auto" else args.profile
    hardware = resolve_hardware_profile(ROOT, requested, include_detection=True)
    environment = check_host_prerequisites(
        hardware.get("hardware_profile_doc"), use_cache=False
    )
    report = dict(hardware)
    report["environment"] = environment
    print(json.dumps(report, indent=2, sort_keys=True))
    status = str(hardware.get("hardware_status") or "UNKNOWN").upper()
    return 0 if status in ("SUPPORTED", "EXPERIMENTAL") and environment["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
