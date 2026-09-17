"""Hardware profile loading and optional board detection hints."""

from __future__ import annotations

import json
import os
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


def detect_board_hints(system_root: Path = Path("/")) -> Dict[str, Any]:
    """Best-effort read-only Raspberry Pi identity without mutating the host."""
    system_root = Path(system_root)
    hints = {
        "model": None,
        "hardware": None,
        "revision": None,
        "soc_family": None,
        "uname_machine": None,
    }  # type: Dict[str, Any]
    model_path = system_root / "proc" / "device-tree" / "model"
    try:
        hints["model"] = model_path.read_bytes().rstrip(b"\x00").decode(
            "utf-8", errors="replace"
        )
    except OSError:
        pass
    try:
        text = (system_root / "proc" / "cpuinfo").read_text()
    except OSError:
        text = ""
    if text:
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            if key == "Model" and not hints["model"]:
                hints["model"] = val
            elif key == "Hardware":
                hints["hardware"] = val
            elif key == "Revision":
                hints["revision"] = val
    if system_root == Path("/"):
        try:
            hints["uname_machine"] = os.uname().machine
        except (AttributeError, OSError):
            pass
    hardware = str(hints.get("hardware") or "").upper()
    if hardware == "BCM2835":
        hints["soc_family"] = "BCM2835"
    elif hardware:
        hints["soc_family"] = hardware
    return hints


def suggest_profile_id(
    hints: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Map detected evidence to a profile; never guess A+ for unknown boards."""
    hints = hints or detect_board_hints()
    model = str(hints.get("model") or "").lower()
    rev = str(hints.get("revision") or "").lower()
    if "raspberry pi model a plus" in model or rev == "900021":
        return DEFAULT_PROFILE_ID
    return None


def detected_hardware_state(
    root: Path, hints: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Resolve detected hardware into evidence-based product language."""
    hints = hints or detect_board_hints()
    suggested = suggest_profile_id(hints)
    if not suggested:
        return {
            "detected": bool(hints.get("model") or hints.get("hardware")),
            "profile_id": None,
            "status": "UNKNOWN",
            "display_name": hints.get("model") or "Hardware not detected",
            "message": "This Raspberry Pi has not been validated for piFM.",
        }
    profile = load_profile(root, suggested)
    return {
        "detected": True,
        "profile_id": suggested,
        "status": str(profile.get("status") or "UNKNOWN").upper(),
        "display_name": profile.get("display_name") or hints.get("model"),
        "message": profile.get("notes") or "",
    }


def resolve_hardware_profile(
    root: Path,
    profile_id: Optional[str] = None,
    include_detection: bool = True,
) -> Dict[str, Any]:
    """Return profile document plus resolution metadata for API/status."""
    hints = detect_board_hints() if include_detection else {}
    requested = (profile_id or "").strip() or None
    suggested = suggest_profile_id(hints) if hints else None
    effective = requested or suggested
    if not effective:
        return {
            "hardware_profile": None,
            "hardware_profile_found": False,
            "hardware_profile_doc": {
                "id": None,
                "display_name": hints.get("model") or "Unknown hardware",
                "status": "UNKNOWN",
                "notes": "No matching hardware profile was detected.",
            },
            "board_hints": hints,
            "suggested_hardware_profile": None,
            "hardware_profile_source": "unknown",
            "hardware_profile_match": False,
            "hardware_status": "UNKNOWN",
        }
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
        "suggested_hardware_profile": suggested,
        "hardware_profile_source": "manual" if requested else "detected",
        "hardware_profile_match": (
            None
            if not include_detection
            else bool(suggested and suggested == effective)
        ),
        "hardware_status": (
            "EXPERIMENTAL"
            if requested and include_detection and suggested != effective
            else str(profile.get("status") or "UNKNOWN").upper()
        ),
    }
