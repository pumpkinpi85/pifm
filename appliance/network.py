"""Network / RF Quiet control. Orthogonal to TX. Never starts TX.

RF Quiet phases:
  simulate — UI/state only
  timed    — briefly downs eth0 with independent at/systemd recovery
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .events import EventLog


class NetState(str, Enum):
    NETWORK_CONNECTED = "NETWORK_CONNECTED"
    RF_QUIET = "RF_QUIET"
    NETWORK_RECOVERING = "NETWORK_RECOVERING"
    SIMULATED_RF_QUIET = "SIMULATED_RF_QUIET"


class NetworkManager:
    def __init__(
        self,
        events: "EventLog",
        mode: str = "simulate",
        recovery_seconds: int = 60,
        iface: str = "eth0",
    ) -> None:
        self.events = events
        self.mode = mode  # simulate | timed
        self.recovery_seconds = max(15, int(recovery_seconds))
        self.iface = iface
        self._state = NetState.NETWORK_CONNECTED
        self._lock = threading.RLock()
        self._timer = None  # type: Optional[threading.Timer]
        self._quiet_until = None  # type: Optional[float]

    @property
    def state(self) -> NetState:
        return self._state

    def status(self) -> Dict[str, Any]:
        with self._lock:
            oper = None
            ip = None
            try:
                oper = open("/sys/class/net/{}/operstate".format(self.iface)).read().strip()
            except OSError:
                oper = "missing"
            try:
                out = subprocess.check_output(
                    ["ip", "-4", "-o", "addr", "show", "dev", self.iface],
                    stderr=subprocess.DEVNULL,
                ).decode("utf-8", errors="replace")
                # parse inet addr
                for part in out.split():
                    if "/" in part and part[0].isdigit():
                        ip = part.split("/")[0]
                        break
            except (OSError, subprocess.CalledProcessError):
                pass
            remaining = None
            if self._quiet_until:
                remaining = max(0, int(self._quiet_until - time.time()))
            return {
                "network_state": self._state.value,
                "mode": self.mode,
                "iface": self.iface,
                "operstate": oper,
                "ip": ip,
                "recovery_seconds": self.recovery_seconds,
                "quiet_remaining_s": remaining,
                "rf_quiet_active": self._state
                in (NetState.RF_QUIET, NetState.SIMULATED_RF_QUIET),
            }

    def enter_quiet(self, confirmed: bool = False) -> Dict[str, Any]:
        if not confirmed:
            raise ValueError("RF Quiet requires confirmed=true")
        with self._lock:
            if self._state in (NetState.RF_QUIET, NetState.SIMULATED_RF_QUIET):
                return self.status()
            self.events.emit(
                "rf_quiet_requested",
                "RF Quiet requested mode={} recovery={}s".format(
                    self.mode, self.recovery_seconds
                ),
            )
            if self.mode == "simulate":
                self._state = NetState.SIMULATED_RF_QUIET
                self._quiet_until = time.time() + self.recovery_seconds
                self._arm_timer(self.exit_quiet_simulated)
                self.events.emit("rf_quiet", "SIMULATED_RF_QUIET active")
                return self.status()

            # timed real mode — schedule independent OS recovery BEFORE downing link
            self._schedule_os_recovery()
            self._quiet_until = time.time() + self.recovery_seconds
            self._down_iface()
            self._state = NetState.RF_QUIET
            self._arm_timer(self.exit_quiet_real)
            self.events.emit("network_disabled", "eth0 down for RF Quiet")
            return self.status()

    def exit_quiet_simulated(self) -> None:
        with self._lock:
            self._cancel_timer()
            self._quiet_until = None
            self._state = NetState.NETWORK_CONNECTED
            self.events.emit("network_restored", "simulated RF Quiet ended")

    def exit_quiet_real(self) -> None:
        with self._lock:
            self._cancel_timer()
            self._state = NetState.NETWORK_RECOVERING
            self.events.emit("network_restored", "recovering eth0 after RF Quiet")
            self._up_iface()
            self._quiet_until = None
            self._state = NetState.NETWORK_CONNECTED
            self.events.emit("network_restored", "eth0 up")

    def force_restore(self) -> Dict[str, Any]:
        with self._lock:
            self._cancel_timer()
            if self.mode != "simulate" or self._state == NetState.RF_QUIET:
                self._up_iface()
            self._quiet_until = None
            self._state = NetState.NETWORK_CONNECTED
            self.events.emit("network_restored", "force restore")
            return self.status()

    def _arm_timer(self, fn: Callable[[], None]) -> None:
        self._cancel_timer()
        self._timer = threading.Timer(self.recovery_seconds, fn)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_os_recovery(self) -> None:
        """Independent of the web app: `at` or shell background sleep+ip."""
        sec = self.recovery_seconds
        iface = self.iface
        # Prefer `at` if available; always also spawn detached bash backup.
        cmd = "ip link set {iface} up; dhclient -1 {iface} >/dev/null 2>&1 || true".format(
            iface=iface
        )
        # Detached recovery process (survives parent if we only stop web, not whole OS)
        subprocess.Popen(
            ["bash", "-c", "sleep {sec}; {cmd}".format(sec=sec, cmd=cmd)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid if os.name != "nt" else None,
            start_new_session=True,
        )
        self.events.emit(
            "rf_quiet",
            "independent recovery armed for {}s".format(sec),
        )

    def _down_iface(self) -> None:
        subprocess.check_call(
            ["sudo", "ip", "link", "set", self.iface, "down"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _up_iface(self) -> None:
        try:
            subprocess.check_call(
                ["sudo", "ip", "link", "set", self.iface, "up"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # dhcpcd usually reclaims; nudge
            subprocess.Popen(
                ["sudo", "dhclient", "-1", self.iface],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:  # noqa: BLE001
            self.events.emit("network_restored", "up failed: {}".format(exc))
