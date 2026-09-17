"""Deployed build identity — no Git required on the Pi at runtime.

Prefer ``build_meta.json`` stamped at deploy time under ``$PIFM_ROOT``.
Missing or incomplete stamps report as unidentified / dirty rather than
pretending to be a clean release.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__ as PACKAGE_VERSION

BUILD_META_NAME = "build_meta.json"
UNKNOWN_SHA = "unknown"
DIRTY_SUFFIX = "-dirty"


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, TypeError):
        return None
    return None


def load_build_meta(root: Path) -> Dict[str, Any]:
    """Load stamped build metadata from an install root."""
    meta = _read_json(Path(root) / BUILD_META_NAME)
    if meta is None:
        meta = _read_json(Path(root) / "appliance" / BUILD_META_NAME)
    return meta or {}


def write_build_meta(root: Path, meta: Dict[str, Any]) -> Path:
    """Atomically write build_meta.json at install root."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / BUILD_META_NAME
    tmp = path.with_suffix(".json.tmp")
    payload = dict(meta)
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def resolve_build_identity(
    root: Path,
    software_version: Optional[str] = None,
    hardware_profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Return operator-visible build identity fields.

    Keys: software_version, git_sha, build_time, hardware_profile,
    build_dirty, build_identified, build_label.
    """
    meta = load_build_meta(root)
    version = (
        str(meta.get("software_version") or software_version or PACKAGE_VERSION).strip()
        or PACKAGE_VERSION
    )
    sha_raw = str(meta.get("git_sha") or "").strip()
    dirty_flag = bool(meta.get("dirty"))
    identified = bool(sha_raw) and sha_raw.lower() not in ("", UNKNOWN_SHA, "none")

    if not identified:
        git_sha = UNKNOWN_SHA
        dirty_flag = True
    else:
        git_sha = sha_raw
        if git_sha.endswith(DIRTY_SUFFIX):
            dirty_flag = True
            git_sha = git_sha[: -len(DIRTY_SUFFIX)] + DIRTY_SUFFIX
        elif dirty_flag and not git_sha.endswith(DIRTY_SUFFIX):
            git_sha = git_sha + DIRTY_SUFFIX

    build_time = meta.get("build_time")
    if build_time is not None:
        build_time = str(build_time)

    profile = (
        str(
            meta.get("hardware_profile")
            or hardware_profile
            or "raspberry-pi-a-plus"
        ).strip()
        or "raspberry-pi-a-plus"
    )

    if not identified:
        label = "{} (unidentified build)".format(version)
    elif dirty_flag:
        label = "{} @ {} (dirty)".format(version, git_sha)
    else:
        label = "{} @ {}".format(version, git_sha)

    return {
        "software_version": version,
        "git_sha": git_sha,
        "build_time": build_time,
        "hardware_profile": profile,
        "build_dirty": dirty_flag,
        "build_identified": identified,
        "build_label": label,
    }
