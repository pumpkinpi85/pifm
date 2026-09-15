"""Hardware profile loading and optional board detection hints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


PROFILES_REL = Path("hardware") / "profiles"
DEFAULT_PROFILE_ID = "raspberry-pi-a-plus"


def profiles_dir(root: Path) -> Path:
    return Path(root) / PROFILES_REL


def list_profile_ids(root: Path) -> List[str]:
    d = profiles_dir(root)
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        if path.name == "README.md":
            continue
        out.append(path.stem)
    return out


def load_profile(root: Path, profile_id: str) -> Dict[str, Any]:
    """Load a profile JSON. Raises FileNotFoundError if missing."""
    pid = (profile_id or DEFAULT_PROFILE_ID).strip() or DEFAULT_PROFILE_ID
    path = profiles_dir(root) / "{}.json".format(pid)
    if not path.is_file():
        raise FileNotFoundError("hardware profile not found: {}".format(pid))
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("hardware profile must be a JSON object")
    data = dict(data)
    data.setdefault("id", pid)
    return data


def detect_board_hints() -> Dict[str, Any]:
    """Best-effort read-only hints from /proc (empty on non-Pi hosts)."""
    hints = {
        "model": None,
        "hardware": None,
        "revision": None,
        "architecture": None,
    }  # type: Dict[str, Any]
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return hints
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if key == "Model":
            hints["model"] = val
        elif key == "Hardware":
            hints["hardware"] = val
        elif key == "Revision":
            hints["revision"] = val
    try:
        hints["architecture"] = Path("/proc/sys/kernel/osrelease").read_text().strip()
    except OSError:
        pass
    try:
        import os

        hints["uname_machine"] = os.uname().machine
    except (AttributeError, OSError):
        pass
    return hints


def suggest_profile_id(hints: Optional[Dict[str, Any]] = None) -> str:
    """Map detected hints to a known profile id (A+ only for now)."""
    hints = hints or detect_board_hints()
    model = (hints.get("model") or "").lower()
    rev = (hints.get("revision") or "").lower()
    if "a plus" in model or "a+" in model or rev.startswith("900021"):
        return DEFAULT_PROFILE_ID
    return DEFAULT_PROFILE_ID


def resolve_hardware_profile(
    root: Path,
    profile_id: Optional[str] = None,
    include_detection: bool = True,
) -> Dict[str, Any]:
    """Return profile document plus resolution metadata for API/status."""
    hints = detect_board_hints() if include_detection else {}
    requested = (profile_id or "").strip() or None
    effective = requested or suggest_profile_id(hints)
    try:
        profile = load_profile(root, effective)
        found = True
    except (OSError, ValueError, FileNotFoundError):
        profile = {
            "id": effective,
            "display_name": effective,
            "status": "UNKNOWN",
            "notes": "profile file missing",
        }
        found = False
    return {
        "hardware_profile": effective,
        "hardware_profile_found": found,
        "hardware_profile_doc": profile,
        "board_hints": hints,
        "suggested_hardware_profile": suggest_profile_id(hints) if hints else None,
    }
