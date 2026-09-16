"""Read-only host prerequisite checks for hardware profiles."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


_CACHE = {}  # type: Dict[str, Any]
_CACHE_TIME = 0.0


def _active_boot_setting(lines: Iterable[str], key: str) -> Optional[str]:
    value = None  # type: Optional[str]
    prefix = key.lower() + "="
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith(prefix):
            value = line[len(prefix):].strip()
    return value


def _process_names(proc_root: Path) -> set:
    names = set()
    try:
        entries = proc_root.iterdir()
    except OSError:
        return names
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            names.add((entry / "comm").read_text().strip().lower())
        except OSError:
            continue
    return names


def check_host_prerequisites(
    profile: Optional[Dict[str, Any]],
    system_root: Path = Path("/"),
    use_cache: bool = True,
) -> Dict[str, Any]:
    """Check profile requirements without changing boot or service state."""
    global _CACHE, _CACHE_TIME
    system_root = Path(system_root)
    cache_key = "{}:{}".format(system_root, (profile or {}).get("id"))
    now = time.monotonic()
    if (
        system_root == Path("/")
        and use_cache
        and _CACHE.get("key") == cache_key
        and now - _CACHE_TIME < 15.0
    ):
        return dict(_CACHE["value"])

    requirements = dict((profile or {}).get("system_prerequisites") or {})
    boot_path = system_root / "boot" / "firmware" / "config.txt"
    if not boot_path.is_file():
        boot_path = system_root / "boot" / "config.txt"
    try:
        boot_lines = boot_path.read_text(errors="replace").splitlines()
    except OSError:
        boot_lines = []
    audio_setting = _active_boot_setting(boot_lines, "dtparam=audio")
    audio_disabled = audio_setting == "off"

    modules_path = system_root / "proc" / "modules"
    try:
        modules = modules_path.read_text(errors="replace")
    except OSError:
        modules = ""
    onboard_audio_loaded = any(
        line.startswith("snd_bcm2835 ") for line in modules.splitlines()
    )

    target_path = system_root / "etc" / "systemd" / "system" / "default.target"
    try:
        default_target = os.path.basename(os.path.realpath(str(target_path)))
    except OSError:
        default_target = None
    processes = _process_names(system_root / "proc")
    desktop_processes = sorted(
        name
        for name in processes
        if name in ("xorg", "wayland", "pulseaudio", "pipewire", "lightdm")
    )
    headless = default_target == "multi-user.target" and not desktop_processes

    checks = []
    if requirements.get("onboard_audio") == "disabled":
        checks.append(
            {
                "id": "onboard_audio",
                "ok": audio_disabled and not onboard_audio_loaded,
                "label": "Onboard audio disabled",
                "action": (
                    "Disable onboard audio to give PiFmRds exclusive PWM access."
                ),
            }
        )
    if requirements.get("boot_target") == "multi-user.target":
        checks.append(
            {
                "id": "headless",
                "ok": headless,
                "label": "Headless boot enabled",
                "action": "Use the headless boot target to avoid PWM clock contention.",
            }
        )
    ready = all(bool(check.get("ok")) for check in checks)
    result = {
        "ready": ready,
        "checks": checks,
        "onboard_audio_disabled": audio_disabled,
        "onboard_audio_module_loaded": onboard_audio_loaded,
        "default_target": default_target,
        "desktop_processes": desktop_processes,
        "headless": headless,
    }  # type: Dict[str, Any]
    if system_root == Path("/") and use_cache:
        _CACHE = {"key": cache_key, "value": dict(result)}
        _CACHE_TIME = now
    return result
