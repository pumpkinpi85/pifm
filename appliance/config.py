"""Persistent configuration. TX ON_AIR is never persisted."""

from __future__ import annotations

import json
import math
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

# Production default: real transmitter target, always OFF AIR until operator action.
# Automated tests override tx_backend to mock/fake in their own config files.
DEFAULTS = {
    "frequency_mhz": 90.0,
    "rds_ps": "piFM",
    "rds_rt": "piFM Pirate Radio",
    "rds_pi": "FFFF",
    "active_playlist": "default",
    "shuffle": False,
    "repeat": True,
    "library_dir": "data/library",
    "playlists_dir": "data/playlists",
    "led_pin": 18,
    "switch_pin": 5,
    "tx_pin": 4,
    "web_host": "0.0.0.0",
    "web_port": 8080,
    "tx_backend": "pi_fm_rds",
    "pi_fm_rds_path": "/usr/local/bin/pi_fm_rds",
    "pi_fm_rds_ppm": 0.0,
    "network_iface": "eth0",
    "hardware_profile": "raspberry-pi-a-plus",
    "hardware_profile_mode": "auto",
    "setup_completed": True,
    "gpio_enabled": False,
    "local_monitor": False,
    "rf_quiet_mode": "simulate",
    "rf_quiet_seconds": 60,
    "product_name": "piFM Pirate Radio",
    "software_version": "0.5.0",
}

FREQ_MIN = 87.1
FREQ_MAX = 108.2


class ConfigError(ValueError):
    pass


class Config:
    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self._data = deepcopy(DEFAULTS)  # type: Dict[str, Any]
        self.load()

    def load(self) -> None:
        data = deepcopy(DEFAULTS)
        if self.path.exists():
            loaded = json.loads(self.path.read_text())
            if not isinstance(loaded, dict):
                raise ConfigError("config root must be an object")
            # Never accept persisted TX state
            loaded.pop("tx_on_air", None)
            loaded.pop("state", None)
            loaded.pop("on_air", None)
            data.update(loaded)
        self._validate(data)
        self._data = data

    def _validate(self, data: Dict[str, Any]) -> None:
        freq = float(data["frequency_mhz"])
        if not (FREQ_MIN <= freq <= FREQ_MAX):
            raise ConfigError(
                "frequency_mhz {} out of range {}-{}".format(freq, FREQ_MIN, FREQ_MAX)
            )
        data["frequency_mhz"] = freq
        ps = str(data.get("rds_ps", "piFM"))[:8]
        data["rds_ps"] = ps
        data["rds_rt"] = str(data.get("rds_rt", ""))[:64]
        pi = str(data.get("rds_pi", "FFFF")).upper()
        if len(pi) != 4 or any(c not in "0123456789ABCDEF" for c in pi):
            raise ConfigError("rds_pi must be 4 hex digits")
        data["rds_pi"] = pi
        data["led_pin"] = int(data["led_pin"])
        data["switch_pin"] = int(data["switch_pin"])
        data["tx_pin"] = int(data["tx_pin"])
        data["web_port"] = int(data["web_port"])
        backend = str(data.get("tx_backend", "pi_fm_rds"))
        if backend not in ("mock", "pi_fm_rds", "fake"):
            raise ConfigError("tx_backend must be mock, pi_fm_rds, or fake")
        data["tx_backend"] = backend
        if isinstance(data.get("pi_fm_rds_ppm"), bool):
            raise ConfigError("pi_fm_rds_ppm must be a finite number")
        try:
            ppm = float(data.get("pi_fm_rds_ppm", 0.0))
        except (OverflowError, TypeError, ValueError):
            raise ConfigError("pi_fm_rds_ppm must be a finite number")
        if not math.isfinite(ppm):
            raise ConfigError("pi_fm_rds_ppm must be a finite number")
        # PiFmRds divides by (1 + ppm / 1e6); -1,000,000 or lower is invalid.
        # Keep an upper sanity bound while allowing the large corrections
        # reported by some PiFmRds hardware combinations.
        if not (-999999.0 <= ppm <= 10000000.0):
            raise ConfigError(
                "pi_fm_rds_ppm must be between -999999 and 10000000"
            )
        data["pi_fm_rds_ppm"] = ppm
        rq = str(data.get("rf_quiet_mode", "simulate"))
        if rq not in ("simulate", "timed"):
            raise ConfigError("rf_quiet_mode must be simulate or timed")
        data["rf_quiet_mode"] = rq
        data["rf_quiet_seconds"] = max(15, int(data.get("rf_quiet_seconds", 60)))
        iface = str(data.get("network_iface") or "eth0").strip() or "eth0"
        data["network_iface"] = iface
        profile = str(data.get("hardware_profile") or "raspberry-pi-a-plus").strip()
        data["hardware_profile"] = profile or "raspberry-pi-a-plus"
        profile_mode = str(data.get("hardware_profile_mode") or "auto").strip()
        if profile_mode not in ("auto", "manual"):
            raise ConfigError("hardware_profile_mode must be auto or manual")
        data["hardware_profile_mode"] = profile_mode
        setup_completed = data.get("setup_completed", True)
        if not isinstance(setup_completed, bool):
            raise ConfigError("setup_completed must be true or false")
        data["setup_completed"] = setup_completed
        # Resolve relative transmitter paths against install root.
        tx_path = str(data.get("pi_fm_rds_path") or "")
        if tx_path and not os.path.isabs(tx_path):
            data["pi_fm_rds_path"] = str((self.root / tx_path).resolve())

    def save(self) -> None:
        # Atomic write; never include ON_AIR
        payload = deepcopy(self._data)
        payload.pop("tx_on_air", None)
        payload.pop("state", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".config.", suffix=".json", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, str(self.path))
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def as_dict(self) -> Dict[str, Any]:
        return deepcopy(self._data)

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        forbidden = {"tx_on_air", "state", "on_air"}
        clean = {k: v for k, v in patch.items() if k not in forbidden}
        merged = deepcopy(self._data)
        merged.update(clean)
        self._validate(merged)
        self._data = merged
        self.save()
        return self.as_dict()

    def resolve(self, key: str) -> Path:
        raw = Path(str(self._data[key]))
        if raw.is_absolute():
            return raw
        return (self.root / raw).resolve()


def discover_root() -> Path:
    env = os.environ.get("PIFM_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def default_config_path(root: Path) -> Path:
    return root / "config" / "appliance.json"
