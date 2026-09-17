"""Hardware profile loading and read-only board detection.

Detected physical identity and the configured piFM hardware profile are
separate concerns. Detection never invents SUPPORTED status for unvalidated
boards; a manual profile never rewrites detected identity.
"""

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


def list_profiles(root: Path) -> List[Dict[str, Any]]:
    """Return implemented profile summaries (id, display_name, status)."""
    summaries = []  # type: List[Dict[str, Any]]
    for profile_id in list_profile_ids(root):
        try:
            profile = load_profile(root, profile_id)
        except (OSError, ValueError, FileNotFoundError):
            continue
        summaries.append(
            {
                "id": str(profile.get("id") or profile_id),
                "display_name": str(
                    profile.get("display_name") or profile_id
                ),
                "status": str(profile.get("status") or "UNKNOWN").upper(),
            }
        )
    return summaries


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
    """Factual/read-only physical board identity and support classification.

    display_name comes from the host model string (device-tree / cpuinfo), never
    from a manually selected operating profile. SUPPORTED only when detection
    maps to a physically validated profile.
    """
    hints = hints or detect_board_hints()
    model = hints.get("model")
    detected = bool(model or hints.get("hardware") or hints.get("revision"))
    suggested = suggest_profile_id(hints) if detected else None
    rf_gpio_bcm = None  # type: Optional[int]
    rf_header_pin = None  # type: Optional[int]
    message = "This Raspberry Pi has not been validated for piFM."
    status = "UNKNOWN"
    if suggested:
        try:
            profile = load_profile(root, suggested)
        except (OSError, ValueError, FileNotFoundError):
            profile = {}
        status = str(profile.get("status") or "UNKNOWN").upper()
        message = str(profile.get("notes") or message)
        if profile.get("rf_gpio_bcm") is not None:
            rf_gpio_bcm = int(profile["rf_gpio_bcm"])
        if profile.get("rf_header_pin") is not None:
            rf_header_pin = int(profile["rf_header_pin"])
    return {
        "detected": detected,
        "model": model,
        "revision": hints.get("revision"),
        "hardware": hints.get("hardware"),
        "soc_family": hints.get("soc_family"),
        "uname_machine": hints.get("uname_machine"),
        "display_name": model or "Hardware not detected",
        "status": status if detected else "UNKNOWN",
        "suggested_profile": suggested,
        "profile_id": suggested,
        "rf_gpio_bcm": rf_gpio_bcm,
        "rf_header_pin": rf_header_pin,
        "message": message if detected else "Hardware not detected.",
    }


def resolve_hardware_profile(
    root: Path,
    profile_id: Optional[str] = None,
    include_detection: bool = True,
    profile_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Return profile document plus resolution metadata for API/status.

    profile_mode:
      - auto: effective profile is the detection suggestion (if any)
      - manual: effective profile is the requested profile_id

    When profile_mode is omitted, an explicit profile_id is treated as a
    manual selection for backward compatibility with callers/tests.
    """
    requested = (profile_id or "").strip() or None
    if profile_mode is None:
        mode = "manual" if requested else "auto"
    else:
        mode = str(profile_mode or "auto").strip().lower()
        if mode not in ("auto", "manual"):
            mode = "auto"
    hints = detect_board_hints() if include_detection else {}
    if include_detection:
        detected = detected_hardware_state(root, hints)
    else:
        detected = {
            "detected": False,
            "model": None,
            "revision": None,
            "hardware": None,
            "soc_family": None,
            "uname_machine": None,
            "display_name": "Hardware not detected",
            "status": "UNKNOWN",
            "suggested_profile": None,
            "profile_id": None,
            "rf_gpio_bcm": None,
            "rf_header_pin": None,
            "message": "Hardware detection skipped.",
        }
    suggested = detected.get("suggested_profile")
    if mode == "manual":
        effective = requested
        source = "manual"
    else:
        effective = suggested
        source = "detected" if suggested else "unknown"
    if not effective:
        return {
            "hardware_profile": None,
            "hardware_profile_found": False,
            "hardware_profile_doc": {
                "id": None,
                "display_name": detected.get("display_name")
                or "Unknown hardware",
                "status": "UNKNOWN",
                "notes": "No matching hardware profile was detected.",
            },
            "board_hints": hints,
            "detected_hardware": detected,
            "suggested_hardware_profile": suggested,
            "hardware_profile_mode": mode,
            "hardware_profile_source": source,
            "hardware_profile_match": False,
            "hardware_status": str(detected.get("status") or "UNKNOWN"),
            "available_hardware_profiles": list_profiles(root),
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
    match = bool(suggested and suggested == effective)
    # Manual selection on a board that does not match the profile is never
    # SUPPORTED — keep physical support on detected_hardware.status only.
    if mode == "manual" and include_detection and not match:
        hardware_status = "EXPERIMENTAL"
    elif mode == "manual" and include_detection and match:
        hardware_status = str(detected.get("status") or "UNKNOWN").upper()
    else:
        hardware_status = str(
            detected.get("status")
            or profile.get("status")
            or "UNKNOWN"
        ).upper()
    return {
        "hardware_profile": effective,
        "hardware_profile_found": found,
        "hardware_profile_doc": profile,
        "board_hints": hints,
        "detected_hardware": detected,
        "suggested_hardware_profile": suggested,
        "hardware_profile_mode": mode,
        "hardware_profile_source": source,
        "hardware_profile_match": (
            None if not include_detection else match
        ),
        "hardware_status": hardware_status,
        "available_hardware_profiles": list_profiles(root),
    }
