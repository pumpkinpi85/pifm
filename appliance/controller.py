"""Central appliance controller — sole authority for TX child process."""

from __future__ import annotations

import random
import threading
import time
import uuid
from collections import Counter
from typing import Any, Dict, List, Optional

from .build_info import resolve_build_identity
from .config import (
    Config,
    frequency_band,
    frequency_is_grid_aligned,
    normalize_frequency_mhz,
)
from .events import EventLog
from .hardware_environment import check_host_prerequisites
from .hardware_profile import resolve_hardware_profile
from .library import Library
from .network import NetworkManager
from .recovery import BroadcastIntentStore, RecoveryStateError, current_boot_id
from .state import State, StateError, StateMachine  # StateError used by API callers
from .tx import TxBackend, build_backend, kill_all_transmitters, probe_real_tx_readiness
from .tx_process import (
    StartCancelled,
    TransmitterProcessScanError,
    count_transmitters,
    list_transmitter_processes,
)


class Controller:
    def __init__(
        self,
        config: Config,
        library: Library,
        events: EventLog,
        recovery: Optional[BroadcastIntentStore] = None,
    ) -> None:
        self.config = config
        self.library = library
        self.events = events
        self.recovery = recovery or BroadcastIntentStore(config.root)
        self.sm = StateMachine()
        self.tx = build_backend(
            str(config.get("tx_backend")),
            str(config.get("pi_fm_rds_path")),
            log_dir=config.root / "data" / "logs",
            work_dir=config.root / "data" / "logs" / "wav",
            silence_wav=config.root / "data" / "audio" / "silence_30s.wav",
            pi_fm_rds_ppm=float(config.get("pi_fm_rds_ppm", 0.0)),
        )  # type: TxBackend
        self.network = NetworkManager(
            events=events,
            mode=str(config.get("rf_quiet_mode", "simulate")),
            recovery_seconds=int(config.get("rf_quiet_seconds", 60)),
            iface=str(config.get("network_iface") or "eth0"),
        )
        self._lock = threading.RLock()
        self._broadcast_command_lock = threading.RLock()
        self._tx_lifecycle_lock = threading.Lock()
        self._status_revision = 0
        self._authority_id = "{}:{}".format(
            current_boot_id(),
            uuid.uuid4().hex,
        )
        self._closing = False
        self._queue = []  # type: List[str]  # track ids
        self._queue_index = -1
        self._playing = False
        self._paused = False
        # True while MP3→WAV runs outside the lock so status stays responsive.
        self._air_start_pending = False
        self._air_start_deadline = None  # type: Optional[float]
        self._air_start_generation = 0
        self._air_stop_pending = False
        self._stop_revision = 0
        # Program transport transitional intent (UI only until confirmed).
        # None | starting | pausing | resuming | changing
        self._program_pending = None  # type: Optional[str]
        self._program_generation = 0
        self._track_started_monotonic = None  # type: Optional[float]
        self._track_duration_s = None  # type: Optional[float]
        self._tx_start_timeout_s = float(config.get("tx_start_timeout_s", 90) or 90)
        self._recovery_stability_s = max(
            0.0, float(config.get("recovery_stability_s", 5.0))
        )
        self._started = time.time()
        self.events.emit("boot", "controller constructed; state=SAFE_OFF")
        # Evaluate READY vs SAFE_OFF from playlist contents; never ON_AIR
        with self._lock:
            self._refresh_ready_unlocked()

    # --- status ---

    def require_authority(self, expected_authority_id: str) -> None:
        with self._lock:
            if expected_authority_id != self._authority_id:
                raise StateError(
                    "Controller authority changed. Refresh authoritative state."
                )

    def broadcast_checklist(
        self, include_setup: bool = True
    ) -> Dict[str, Any]:
        """Operator-facing readiness for GO ON AIR (never starts TX).

        Read-only: must not reshuffle or otherwise mutate the queue.
        """
        with self._lock:
            cfg = self.config.as_dict()
            pl_id = str(cfg.get("active_playlist") or "")
            pl_name = pl_id
            track_count = len(self._queue)
            has_playable_track = False
            for track_id in self._queue:
                track = self.library.get_track(track_id)
                if not track:
                    continue
                try:
                    if self.library.absolute_path(track["path"]).is_file():
                        has_playable_track = True
                        break
                except (OSError, ValueError):
                    continue
            try:
                pl = self.library.load_playlist(pl_id)
                pl_name = str(pl.get("name") or pl_id)
            except FileNotFoundError:
                pl_name = pl_id or "(none)"

            items = []
            if include_setup:
                items.append(
                    {
                        "id": "setup",
                        "label": "Setup complete",
                        "ok": bool(cfg.get("setup_completed", True)),
                        "detail": (
                            "First-run setup complete"
                            if cfg.get("setup_completed", True)
                            else "First-run setup is not complete"
                        ),
                        "operator_hint": "Finish setup before going on air.",
                        "cta": "",
                        "cta_label": "",
                    }
                )
            items.append(
                {
                    "id": "playlist",
                    "label": "Playlist selected",
                    "ok": bool(pl_id) and track_count > 0,
                    "detail": "{} · {} track{}".format(
                        pl_name, track_count, "" if track_count == 1 else "s"
                    )
                    if pl_id
                    else "No playlist selected",
                    "operator_hint": "Choose a playlist with music first.",
                    "cta": "music",
                    "cta_label": "Choose music",
                }
            )
            freq = float(cfg["frequency_mhz"])
            frequency_valid = frequency_is_grid_aligned(freq)
            items.append(
                {
                    "id": "frequency",
                    "label": "Frequency selected",
                    "ok": frequency_valid,
                    "detail": "{:.1f} MHz".format(freq),
                    "operator_hint": (
                        "Set a frequency in 0.1 MHz increments on the Station page."
                    ),
                    "cta": "station",
                    "cta_label": "Open Station",
                }
            )
            items.append(
                {
                    "id": "music",
                    "label": "Music available",
                    "ok": has_playable_track,
                    "detail": "Program queue has music"
                    if has_playable_track
                    else "Active playlist has no playable files",
                    "operator_hint": "Add tracks to your playlist before going on air.",
                    "cta": "music",
                    "cta_label": "Choose music",
                }
            )
            items.append(
                {
                    "id": "rds",
                    "label": "Station name configured",
                    "ok": bool(str(cfg.get("rds_ps") or "").strip()),
                    "detail": 'Name "{}" · Text "{}"'.format(
                        cfg.get("rds_ps"), cfg.get("rds_rt")
                    ),
                    "operator_hint": "Set a station name on the Station page.",
                    "cta": "station",
                    "cta_label": "Open Station",
                }
            )

            backend = str(cfg.get("tx_backend") or "mock")
            real = probe_real_tx_readiness(str(cfg.get("pi_fm_rds_path") or ""))
            if backend in ("mock", "fake"):
                items.append(
                    {
                        "id": "transmitter",
                        "label": "Transmitter harness",
                        "ok": True,
                        "detail": "Internal non-RF harness (tests/dev only)",
                        "severity": False,
                        "operator_hint": "",
                        "cta": "",
                        "cta_label": "",
                    }
                )
            else:
                hw = resolve_hardware_profile(
                    self.config.root,
                    profile_id=(
                        str(cfg.get("hardware_profile") or "")
                        if cfg.get("hardware_profile_mode") == "manual"
                        else None
                    ),
                    include_detection=True,
                )
                profile = hw.get("hardware_profile_doc") or {}
                hardware_status = str(
                    hw.get("hardware_status") or "UNKNOWN"
                ).upper()
                hardware_ok = hardware_status in ("SUPPORTED", "EXPERIMENTAL")
                items.append(
                    {
                        "id": "hardware",
                        "label": "Hardware identified",
                        "ok": hardware_ok,
                        "detail": "{} · {}".format(
                            profile.get("display_name") or "Unknown hardware",
                            hardware_status,
                        ),
                        "severity": True,
                        "operator_hint": (
                            "This hardware is not ready for piFM. Open System for details."
                        ),
                        "cta": "system",
                        "cta_label": "Open System",
                    }
                )
                environment = check_host_prerequisites(profile)
                items.append(
                    {
                        "id": "hardware_environment",
                        "label": "Hardware environment ready",
                        "ok": bool(environment.get("ready")),
                        "detail": (
                            "Headless mode and onboard audio settings are ready"
                            if environment.get("ready")
                            else "A required hardware setting needs attention"
                        ),
                        "severity": True,
                        "operator_hint": (
                            "Prepare the Raspberry Pi hardware environment before going on air."
                        ),
                        "cta": "system",
                        "cta_label": "Open System",
                    }
                )
                items.append(
                    {
                        "id": "transmitter",
                        "label": "FM transmitter ready",
                        "ok": bool(real.get("ready")),
                        "detail": real.get("summary")
                        or "Transmitter software is not ready",
                        "severity": True,
                        "operator_hint": "The FM transmitter is not ready. Check System diagnostics.",
                        "cta": "system",
                        "cta_label": "Open System",
                    }
                )

            can_go = all(i["ok"] for i in items) and self.sm.state != State.FAULT
            blockers = []
            for i in items:
                if not i["ok"]:
                    blockers.append(
                        {
                            "id": i["id"],
                            "message": i.get("operator_hint") or i["detail"],
                            "cta": i.get("cta") or "",
                            "cta_label": i.get("cta_label") or "",
                        }
                    )
            if self.sm.state == State.FAULT:
                can_go = False
                blockers.append(
                    {
                        "id": "fault",
                        "message": "Clear the error on the System page before going on air.",
                        "cta": "system",
                        "cta_label": "Open System",
                    }
                )

            return {
                "ready": can_go,
                "items": items,
                "blockers": blockers,
                "playlist_id": pl_id,
                "playlist_name": pl_name,
                "track_count": track_count,
                "has_playable_track": has_playable_track,
                "frequency_mhz": freq,
                "rds_ps": cfg.get("rds_ps"),
                "rds_rt": cfg.get("rds_rt"),
                "rds_pi": cfg.get("rds_pi"),
                "broadcast_mode": (
                    "test_harness"
                    if backend in ("mock", "fake")
                    else "live"
                ),
                "on_air": self.sm.state == State.ON_AIR,
                "real_tx": real,
            }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            cfg = self.config.as_dict()
            cur = self._current_track()
            nxt = self._next_track_peek()
            first_up = self._first_up_unlocked()
            checklist = self.broadcast_checklist()
            pl_name = checklist.get("playlist_name") or cfg.get("active_playlist")
            if self.sm.state == State.ON_AIR:
                broadcast_state = "on_air"
            elif self.sm.state == State.FAULT:
                broadcast_state = "fault"
            else:
                broadcast_state = "off"
            if self._paused and (self.sm.state == State.ON_AIR or self._playing):
                program_state = "paused"
            elif self._playing and not self._paused:
                program_state = "playing"
            else:
                program_state = "stopped"
            pending = self._program_pending
            if pending == "pausing":
                program_ui = "PAUSING…"
            elif pending == "resuming":
                program_ui = "RESUMING…"
            elif pending == "starting":
                program_ui = "STARTING MUSIC…"
            elif pending == "changing":
                program_ui = "CHANGING TRACK…"
            elif program_state == "playing":
                program_ui = "PLAYING"
            elif program_state == "paused":
                program_ui = "PAUSED"
            else:
                program_ui = "READY"
            backend = str(cfg.get("tx_backend") or "mock")
            tracked_running = self.tx.is_running()
            # Always reconcile OS workers — independent of controller state/backend.
            system_tx = 0
            system_tx_list = []  # type: List[Dict[str, Any]]
            try:
                system_tx_list = list_transmitter_processes()
                system_tx = len(system_tx_list)
            except Exception:
                system_tx = -1
            # Orphan RF worker while software thinks OFF/READY/FAULT → POSSIBLE TX.
            disagree = False
            possible_tx = False
            if system_tx < 0:
                disagree = True
            elif system_tx > 1:
                disagree = True
                possible_tx = True
            elif system_tx > 0 and self.sm.state != State.ON_AIR and not self._air_start_pending:
                disagree = True
                possible_tx = True
            elif tracked_running and self.sm.state not in (State.ON_AIR, State.FAULT):
                disagree = True
            elif self.sm.state == State.ON_AIR and system_tx == 0 and not tracked_running:
                disagree = True
            # ON AIR only when transmitter is confirmed running (reconciled).
            if possible_tx:
                broadcast_ui = "STATE UNKNOWN / POSSIBLE TRANSMISSION"
            elif self.sm.state == State.FAULT or disagree:
                broadcast_ui = "STATE UNKNOWN"
            elif self._air_stop_pending:
                broadcast_ui = "STOPPING BROADCAST…"
            elif self._air_start_pending:
                broadcast_ui = "STARTING BROADCAST…"
            elif self.sm.state == State.ON_AIR and tracked_running:
                if backend == "pi_fm_rds" and system_tx != 1:
                    broadcast_ui = "STATE UNKNOWN"
                elif backend == "fake" and system_tx != 1:
                    broadcast_ui = "STATE UNKNOWN"
                else:
                    broadcast_ui = "ON AIR"
            elif self.sm.state == State.ON_AIR and not tracked_running:
                broadcast_ui = "STATE UNKNOWN"
            else:
                broadcast_ui = "OFF"
            now_playing = None  # type: Optional[Dict[str, Any]]
            up_next = None  # type: Optional[Dict[str, Any]]
            if program_state in ("playing", "paused"):
                now_playing = cur
                up_next = nxt
            emergency_stop = True
            build = resolve_build_identity(
                self.config.root,
                software_version=str(cfg.get("software_version") or ""),
                hardware_profile=str(cfg.get("hardware_profile") or ""),
            )
            hw = resolve_hardware_profile(
                self.config.root,
                profile_id=(
                    str(cfg.get("hardware_profile") or "")
                    if cfg.get("hardware_profile_mode") == "manual"
                    else None
                ),
                include_detection=True,
            )
            hardware_environment = check_host_prerequisites(
                hw.get("hardware_profile_doc")
            )
            self._status_revision += 1
            return {
                "authority_id": self._authority_id,
                "snapshot_revision": self._status_revision,
                "state": self.sm.state.value,
                "fault_reason": self.sm.fault_reason,
                "tx": self.tx.status(),
                "tx_running": tracked_running,
                "system_tx_count": system_tx if system_tx >= 0 else None,
                "system_tx_processes": system_tx_list,
                "frequency_mhz": cfg["frequency_mhz"],
                "frequency_band": frequency_band(),
                "rds_ps": cfg["rds_ps"],
                "rds_rt": cfg["rds_rt"],
                "rds_pi": cfg["rds_pi"],
                "active_playlist": cfg["active_playlist"],
                "selected_playlist_name": pl_name,
                "shuffle": cfg["shuffle"],
                "repeat": cfg["repeat"],
                "queue_index": self._queue_index,
                "queue_length": len(self._queue),
                # Legacy fields (queue cursor); UI should prefer now_playing/first_up.
                "current_track": cur,
                "next_track": nxt,
                "now_playing": now_playing,
                "up_next": up_next,
                "first_up": first_up if program_state == "stopped" else None,
                "playback_active": self._playing and not self._paused,
                "paused": self._paused,
                "program_state": program_state,
                "program_ui": program_ui,
                "program_pending": pending,
                "broadcast_state": broadcast_state,
                "broadcast_ui": broadcast_ui,
                "dev_harness": backend == "mock",
                "transmitter_mode": (
                    "LIVE"
                    if backend == "pi_fm_rds"
                    else ("FAKE" if backend == "fake" else "TEST_HARNESS")
                ),
                "emergency_stop_available": emergency_stop,
                "show_stop_broadcast": True,
                "uptime_s": time.time() - self._started,
                "tx_backend": cfg["tx_backend"],
                "pi_fm_rds_ppm": float(cfg.get("pi_fm_rds_ppm", 0.0)),
                "led_pin": cfg["led_pin"],
                "switch_pin": cfg["switch_pin"],
                "network": self.network.status(),
                "product_name": cfg.get("product_name", "piFM Pirate Radio"),
                "software_version": build["software_version"],
                "git_sha": build["git_sha"],
                "build_time": build["build_time"],
                "build_dirty": build["build_dirty"],
                "build_identified": build["build_identified"],
                "build_label": build["build_label"],
                "hardware_profile": hw["hardware_profile"],
                "hardware_profile_found": hw["hardware_profile_found"],
                "hardware_profile_doc": hw["hardware_profile_doc"],
                "board_hints": hw["board_hints"],
                "suggested_hardware_profile": hw.get(
                    "suggested_hardware_profile"
                ),
                "hardware_profile_source": hw.get("hardware_profile_source"),
                "hardware_profile_match": hw.get("hardware_profile_match"),
                "hardware_status": hw.get("hardware_status"),
                "hardware_environment": hardware_environment,
                "setup_completed": bool(cfg.get("setup_completed", True)),
                "setup_required": not bool(cfg.get("setup_completed", True)),
                "gpio_enabled": bool(cfg.get("gpio_enabled")),
                "broadcast_recovery": self.recovery.status(),
                "broadcast": checklist,
                "queue": self.queue_snapshot(),
                "queue_fingerprint": list(self._queue),
            }

    def _program_state_unlocked(self) -> str:
        if self._paused:
            return "paused"
        if self._playing:
            return "playing"
        return "stopped"

    def _recovery_snapshot_unlocked(
        self, program_state: Optional[str] = None
    ) -> Dict[str, Any]:
        current_track_id = None
        if 0 <= self._queue_index < len(self._queue):
            current_track_id = self._queue[self._queue_index]
        return {
            "program_state": program_state or self._program_state_unlocked(),
            "active_playlist": str(self.config.get("active_playlist") or ""),
            "queue": list(self._queue),
            "current_track_id": current_track_id,
        }

    def _update_recovery_program_unlocked(self) -> None:
        if not self.recovery.update_program(self._recovery_snapshot_unlocked()):
            status = self.recovery.status()
            if status.get("armed") and status.get("last_error"):
                self.events.emit(
                    "RECOVERY_PERSISTENCE_FAILED",
                    str(status["last_error"]),
                )

    def _set_track_timing_unlocked(self) -> None:
        meta = self.tx.status()
        duration = meta.get("wav_duration_s")
        try:
            parsed = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            parsed = None
        self._track_duration_s = parsed if parsed and parsed > 0 else None
        self._track_started_monotonic = (
            time.monotonic() if self._track_duration_s is not None else None
        )

    def _clear_track_timing_unlocked(self) -> None:
        self._track_started_monotonic = None
        self._track_duration_s = None

    def _current_track(self) -> Optional[Dict[str, Any]]:
        if self._queue_index < 0 or self._queue_index >= len(self._queue):
            return None
        return self.library.get_track(self._queue[self._queue_index])

    def _select_existing_track_unlocked(self) -> Optional[Dict[str, Any]]:
        if not self._queue:
            return None
        start = self._queue_index if self._queue_index >= 0 else 0
        for offset in range(len(self._queue)):
            index = (start + offset) % len(self._queue)
            track = self.library.get_track(self._queue[index])
            if not track:
                continue
            try:
                exists = self.library.absolute_path(track["path"]).is_file()
            except (OSError, ValueError):
                exists = False
            if exists:
                if index != start:
                    self.events.emit(
                        "MEDIA_TRACK_SKIPPED",
                        "missing track skipped before Broadcast start",
                        track_id=self._queue[start],
                        next_track=track,
                    )
                self._queue_index = index
                return track
        return None

    def _first_up_unlocked(self) -> Optional[Dict[str, Any]]:
        """Next/first track to play when program is stopped (does not mutate)."""
        if not self._queue:
            return None
        if self._queue_index < 0:
            return self.library.get_track(self._queue[0])
        if self._queue_index < len(self._queue):
            return self.library.get_track(self._queue[self._queue_index])
        return self.library.get_track(self._queue[0])

    def _next_track_peek(self) -> Optional[Dict[str, Any]]:
        if not self._queue:
            return None
        if self._queue_index < 0:
            idx = 0
        else:
            idx = self._queue_index + 1
            if idx >= len(self._queue):
                if self.config.get("repeat"):
                    idx = 0
                else:
                    return None
        return self.library.get_track(self._queue[idx])

    # --- config / library ---

    def _persist_off_intent_unlocked(self, source: str) -> Optional[str]:
        previous = self.recovery.status().get("desired_broadcast")
        try:
            self.recovery.disarm()
        except RecoveryStateError as exc:
            message = str(exc)
            self.events.emit(
                "RECOVERY_PERSISTENCE_FAILED",
                message,
                source=source,
                previous_intent=previous,
                next_intent="off",
            )
            return message
        self.events.emit(
            "BROADCAST_INTENT_CHANGED",
            "operator broadcast intent is OFF",
            source=source,
            previous_intent=previous,
            next_intent="off",
        )
        return None

    def _persist_on_intent_unlocked(self, source: str) -> str:
        previous = self.recovery.status().get("desired_broadcast")
        persisted = self.recovery.arm(
            self._recovery_snapshot_unlocked("playing"), source
        )
        self.events.emit(
            "BROADCAST_INTENT_CHANGED",
            "operator broadcast intent is ON",
            source=source,
            previous_intent=previous,
            next_intent="on",
        )
        return str(persisted["intent_revision"])

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._broadcast_command_lock:
            return self._update_config(patch, source="operator_config")

    def _update_config(
        self,
        patch: Dict[str, Any],
        source: str = "operator_config",
    ) -> Dict[str, Any]:
        before = self.config.as_dict()
        candidate = self.config.validate_update(patch)
        changes = {
            key: candidate[key]
            for key in patch
            if key in candidate and candidate[key] != before.get(key)
        }
        if not changes:
            return before
        with self._lock:
            must_stop = bool(
                self.sm.state == State.ON_AIR
                or self.tx.is_running()
                or self._air_start_pending
            )
        if must_stop:
            self.tx_off(
                persist_off=True,
                source="operator_config_change",
            )
        with self._lock:
            before_freq = before.get("frequency_mhz")
            data = self.config.update(changes)
            # Backend construction captures executable path and timing correction.
            # Rebuild for any such setting change; never auto-start TX.
            backend_keys = {
                "tx_backend",
                "pi_fm_rds_path",
                "pi_fm_rds_ppm",
            }
            backend_changed = bool(backend_keys.intersection(changes))
            if backend_changed:
                kill_all_transmitters()
                self.tx = build_backend(
                    str(self.config.get("tx_backend")),
                    str(self.config.get("pi_fm_rds_path")),
                    log_dir=self.config.root / "data" / "logs",
                    work_dir=self.config.root / "data" / "logs" / "wav",
                    silence_wav=self.config.root / "data" / "audio" / "silence_30s.wav",
                    pi_fm_rds_ppm=float(
                        self.config.get("pi_fm_rds_ppm", 0.0)
                    ),
                )
            if "pi_fm_rds_ppm" in changes:
                self.events.emit(
                    "tx_timing_changed",
                    "PiFmRds timing correction set to {} ppm".format(
                        data["pi_fm_rds_ppm"]
                    ),
                    source=source,
                )
            if "frequency_mhz" in changes and data["frequency_mhz"] != before_freq:
                self.events.emit(
                    "frequency_changed",
                    "frequency set to {}".format(data["frequency_mhz"]),
                    source=source,
                )
            if "active_playlist" in changes:
                self.events.emit(
                    "playlist_changed",
                    "active playlist {}".format(data["active_playlist"]),
                    source=source,
                )
            # Intentional queue rebuild boundaries only.
            if "active_playlist" in changes or "shuffle" in changes:
                self._load_queue(rebuild=True)
            self._refresh_ready_unlocked()
            return data

    def go_on_air_at_frequency(
        self, frequency_mhz: Any, wait: bool = False
    ) -> Dict[str, Any]:
        """Commit one operator tune-and-broadcast action through canonical paths."""
        frequency = normalize_frequency_mhz(frequency_mhz)
        with self._lock:
            operation_stop_revision = self._stop_revision
            if self._air_stop_pending:
                raise StateError("Broadcast is stopping. Wait until OFF AIR.")
        with self._broadcast_command_lock:
            with self._lock:
                if (
                    self._air_stop_pending
                    or self._stop_revision != operation_stop_revision
                ):
                    raise StateError("Broadcast is stopping. Wait until OFF AIR.")
                if self.sm.state == State.FAULT:
                    raise StateError(
                        "Clear the broadcast fault before raising the Black Flag."
                    )
                current = normalize_frequency_mhz(
                    self.config.get("frequency_mhz")
                )
                active = bool(
                    self.sm.state == State.ON_AIR or self.tx.is_running()
                )
                if self._air_start_pending:
                    if frequency == current:
                        return self.status()
                    raise StateError(
                        "Broadcast is already starting at another frequency."
                    )
            if active and frequency != current:
                self._tx_off(
                    persist_off=True,
                    source="operator_flagpole_retune",
                    clear_stop_pending=False,
                )
                with self._lock:
                    if self._stop_revision != operation_stop_revision:
                        raise StateError("Broadcast was stopped during tuning.")
                    self._air_stop_pending = False
            if frequency != current:
                self._update_config(
                    {"frequency_mhz": frequency},
                    source="operator_flagpole",
                )
            with self._lock:
                if (
                    self._air_stop_pending
                    or self._stop_revision != operation_stop_revision
                ):
                    raise StateError("Broadcast was stopped during tuning.")
            return self.go_on_air(
                wait=wait,
                persist_intent=True,
                source="operator_flagpole",
            )

    def update_setup(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Apply first-run choices; complete only when Broadcast is ready."""
        with self._lock:
            complete = patch.get("setup_completed") is True
            settings = dict(patch)
            settings.pop("setup_completed", None)
            if settings:
                self.update_config(settings)
            if complete:
                checklist = self.broadcast_checklist(include_setup=False)
                if not checklist.get("ready"):
                    blockers = checklist.get("blockers") or []
                    message = (
                        blockers[0].get("message")
                        if blockers and isinstance(blockers[0], dict)
                        else "Finish the required setup steps first."
                    )
                    raise StateError("Setup cannot finish: {}".format(message))
                data = self.update_config({"setup_completed": True})
                self.events.emit("setup_completed", "first-run setup completed")
                return data
            if "setup_completed" in patch:
                return self.update_config(
                    {"setup_completed": patch["setup_completed"]}
                )
            return self.config.as_dict()

    def _ensure_queue_loaded_unlocked(self) -> None:
        """Populate queue once if empty. Never reshuffles an existing queue."""
        if self._queue:
            return
        self._load_queue(rebuild=True)

    def _load_queue(self, rebuild: bool = False) -> None:
        """Load playlist into the in-memory queue.

        rebuild=False and queue already loaded: NO-OP (read-safe).
        rebuild=True: replace queue from playlist; shuffle once if enabled.
        """
        pid = str(self.config.get("active_playlist"))
        try:
            pl = self.library.load_playlist(pid)
        except FileNotFoundError:
            self._queue = []
            self._queue_index = -1
            return
        tracks = list(pl.get("tracks") or [])
        if not rebuild and self._queue:
            # READ-SAFE: polling / status / readiness must not mutate order.
            return
        if self.config.get("shuffle"):
            random.shuffle(tracks)
        self._queue = tracks
        if rebuild:
            self._queue_index = -1

    def _refresh_ready_unlocked(self) -> None:
        if self.sm.state in (State.ON_AIR, State.FAULT):
            return
        self._ensure_queue_loaded_unlocked()
        if self._queue:
            if self.sm.state == State.SAFE_OFF:
                self.sm.transition(State.READY, "playlist loaded")
        else:
            if self.sm.state == State.READY:
                self.sm.transition(State.SAFE_OFF, "empty playlist")

    # --- playback intent (distinct from TX) ---

    def play(self, wait: bool = True) -> Dict[str, Any]:
        """Start/resume program audio.

        wait=True (default, tests): block until ON_AIR audio restart finishes.
        wait=False (HTTP): return immediately with transitional program_ui.
        """
        audio_path = None  # type: Optional[str]
        generation = 0
        need_restart = False
        was_paused = False
        with self._lock:
            self._ensure_queue_loaded_unlocked()
            if not self._queue:
                raise StateError("no tracks in active playlist")
            if self._queue_index < 0:
                self._queue_index = 0
            if self._program_pending in ("starting", "resuming", "changing", "pausing"):
                raise StateError("Already changing music — wait a moment.")
            was_paused = self._paused
            self._playing = True
            self._paused = False
            self._update_recovery_program_unlocked()
            self._refresh_ready_unlocked()
            self.events.emit("track_changed", "play", track=self._current_track())
            if self.sm.state == State.ON_AIR:
                holding_silence = self.tx.status().get("program") == "silence"
                if was_paused or holding_silence or not self.tx.is_running():
                    need_restart = True
                    track = self._current_track()
                    if not track:
                        raise StateError("no current track")
                    audio_path = str(self.library.absolute_path(track["path"]))
                    self._program_generation += 1
                    generation = self._program_generation
                    self._program_pending = "resuming" if was_paused else "starting"
                    self.events.emit(
                        "program_pending",
                        "RESUMING…" if was_paused else "STARTING MUSIC…",
                        program_ui=self._program_pending,
                    )
                else:
                    self.events.emit(
                        "track_changed",
                        "play while ON_AIR ignored for TX (already broadcasting)",
                        track=self._current_track(),
                    )
                    return self.status()
            else:
                return self.status()

        def _finish() -> Dict[str, Any]:
            try:
                return self._complete_program_audio_restart(
                    generation=generation,
                    audio_path=audio_path or "",
                    pending_kind="resuming" if was_paused else "starting",
                    success_event=("program_resumed", "program resumed while ON_AIR")
                    if was_paused
                    else ("track_changed", "play audio started"),
                )
            except Exception:
                with self._lock:
                    if self._program_generation == generation:
                        self._program_pending = None
                raise

        if not wait:
            def _bg() -> None:
                try:
                    _finish()
                except Exception:
                    pass

            threading.Thread(
                target=_bg, name="pifm-program-play", daemon=True
            ).start()
            return self.status()
        return _finish()

    def pause(self) -> Dict[str, Any]:
        with self._lock:
            # Idempotent: already paused with silence hold — do not re-emit / restart.
            if self._paused:
                if self.sm.state == State.ON_AIR:
                    if self.tx.status().get("program") == "silence" or not self.tx.is_running():
                        return self.status()
                else:
                    return self.status()
            if self._program_pending in ("pausing", "resuming", "starting", "changing"):
                raise StateError("Already changing music — wait a moment.")
            self._paused = True
            if self.sm.state == State.ON_AIR:
                self._program_pending = "pausing"
                self.events.emit("program_pending", "PAUSING…", program_ui="PAUSING…")
                # Stay ON AIR. Hold carrier with looping seekable silence.
                self._hold_silence_unlocked()
                self._clear_track_timing_unlocked()
                self._update_recovery_program_unlocked()
                self._program_pending = None
                self.events.emit(
                    "program_paused",
                    "ON_AIR — program paused (broadcast remains on; silence loop)",
                    program_state="paused",
                    broadcast_state="on_air",
                    **{k: self.tx.status().get(k) for k in ("pid", "wav_path", "audio_mode")},
                )
            return self.status()

    def stop_playback(self) -> Dict[str, Any]:
        with self._lock:
            self._playing = False
            self._paused = False
            self._program_pending = None
            self._program_generation += 1
            self._queue_index = -1
            self._clear_track_timing_unlocked()
            if self.sm.state == State.ON_AIR:
                # Stop the program but keep the station on air (silence hold).
                # program_state becomes "stopped" (not paused).
                self._hold_silence_unlocked()
                self._update_recovery_program_unlocked()
                self.events.emit(
                    "program_stopped",
                    "program stopped — still ON_AIR (silence loop)",
                    program_state="stopped",
                    broadcast_state="on_air",
                )
            return self.status()

    def next_track(self, wait: bool = True) -> Dict[str, Any]:
        return self._skip_track(direction=1, wait=wait)

    def prev_track(self, wait: bool = True) -> Dict[str, Any]:
        return self._skip_track(direction=-1, wait=wait)

    def _skip_track(self, direction: int, wait: bool = True) -> Dict[str, Any]:
        audio_path = None  # type: Optional[str]
        generation = 0
        need_restart = False
        with self._lock:
            if self._program_pending in ("starting", "resuming", "changing", "pausing"):
                raise StateError("Already changing music — wait a moment.")
            if not self._queue:
                self._load_queue(rebuild=True)
            if not self._queue:
                raise StateError("empty queue")
            if direction > 0:
                self._queue_index += 1
                if self._queue_index >= len(self._queue):
                    if self.config.get("repeat"):
                        if self.config.get("shuffle"):
                            random.shuffle(self._queue)
                        self._queue_index = 0
                    else:
                        self._queue_index = len(self._queue) - 1
                        self._playing = False
                        self._paused = False
                        self._program_pending = None
                        if self.sm.state == State.ON_AIR:
                            self._hold_silence_unlocked()
                            self._clear_track_timing_unlocked()
                            self.events.emit(
                                "PROGRAM_ENDED",
                                "playlist ended; Broadcast remains ON with silence",
                            )
                        self._update_recovery_program_unlocked()
                        return self.status()
            else:
                self._queue_index = max(0, self._queue_index - 1)
            self._paused = False
            self._playing = True
            self._update_recovery_program_unlocked()
            label = "next" if direction > 0 else "previous"
            self.events.emit("track_changed", label, track=self._current_track())
            if self.sm.state == State.ON_AIR:
                need_restart = True
                track = self._current_track()
                if not track:
                    raise StateError("no current track")
                audio_path = str(self.library.absolute_path(track["path"]))
                self._program_generation += 1
                generation = self._program_generation
                self._program_pending = "changing"
                self.events.emit(
                    "tx_track_change",
                    "{} while ON AIR — controlled TX restart (brief carrier gap)".format(
                        "NEXT" if direction > 0 else "PREV"
                    ),
                    carrier_interrupt=True,
                    track=self._current_track(),
                )
                self.events.emit("program_pending", "CHANGING TRACK…", program_ui="CHANGING TRACK…")
            else:
                return self.status()

        def _finish() -> Dict[str, Any]:
            try:
                return self._complete_program_audio_restart(
                    generation=generation,
                    audio_path=audio_path or "",
                    pending_kind="changing",
                    success_event=("tx_audio", "track audio ready"),
                )
            except Exception:
                with self._lock:
                    if self._program_generation == generation:
                        self._program_pending = None
                raise

        if not wait:
            def _bg() -> None:
                try:
                    _finish()
                except Exception:
                    pass

            threading.Thread(
                target=_bg, name="pifm-program-skip", daemon=True
            ).start()
            return self.status()
        return _finish()

    def _complete_program_audio_restart(
        self,
        generation: int,
        audio_path: str,
        pending_kind: str,
        success_event: tuple,
    ) -> Dict[str, Any]:
        """Prefetch outside the lock, then restart TX under the lock."""
        prefetch_error = None  # type: Optional[BaseException]

        def _should_cancel() -> bool:
            with self._lock:
                if self._program_generation != generation:
                    return True
                if self.sm.state != State.ON_AIR:
                    return True
                return False

        try:
            prefetch = getattr(self.tx, "prefetch_audio", None)
            if callable(prefetch) and audio_path:
                prefetch(audio_path, should_cancel=_should_cancel)
        except StartCancelled as exc:
            prefetch_error = exc
        except Exception as exc:  # noqa: BLE001
            prefetch_error = exc

        with self._lock:
            if self._program_generation != generation:
                return self.status()
            if self.sm.state != State.ON_AIR:
                self._program_pending = None
                clear_pf = getattr(self.tx, "clear_prefetch", None)
                if callable(clear_pf):
                    clear_pf()
                return self.status()
            if prefetch_error is not None:
                clear_pf = getattr(self.tx, "clear_prefetch", None)
                if callable(clear_pf):
                    clear_pf()
                self._program_pending = None
                self.events.emit(
                    "MEDIA_TRACK_FAILED",
                    "track could not be prepared; Broadcast remains ON with silence",
                    error=str(prefetch_error),
                    track=self._current_track(),
                )
                try:
                    self._hold_silence_unlocked()
                except Exception as silence_error:  # noqa: BLE001
                    self.sm.enter_fault(
                        "failed track and silence hold failed: {}".format(
                            silence_error
                        )
                    )
                    self.events.emit("FAULT", str(silence_error))
                    raise StateError(
                        "Could not change music or hold Broadcast safely."
                    )
                self._playing = False
                self._paused = False
                self._clear_track_timing_unlocked()
                self._update_recovery_program_unlocked()
                return self.status()
            try:
                self._start_tx_current_unlocked()
                self._playing = True
                self._paused = False
                self._set_track_timing_unlocked()
                self._update_recovery_program_unlocked()
                self._program_pending = None
                kind, message = success_event
                self.events.emit(
                    kind,
                    message,
                    track=self._current_track(),
                    **{
                        k: self.tx.status().get(k)
                        for k in ("wav_path", "pid", "audio_mode", "wav_duration_s")
                    },
                )
                self._schedule_warm_up_next_unlocked()
            except Exception as exc:  # noqa: BLE001
                self._program_pending = None
                self.events.emit("tx_failure", str(exc))
                raise
            return self.status()

    def _schedule_warm_up_next_unlocked(self) -> None:
        """Best-effort: cache the Up Next track WAV when the Pi is idle enough."""
        nxt = self._next_track_peek()
        if not nxt:
            return
        try:
            path = str(self.library.absolute_path(nxt["path"]))
        except Exception:
            return
        warm = getattr(self.tx, "warm_cache", None)
        if not callable(warm):
            return

        def _work() -> None:
            try:
                warm(path)
                self.events.emit(
                    "tx_audio_prefetch",
                    "up-next audio cached",
                    source_path=path,
                    opportunistic=True,
                )
            except Exception as exc:  # noqa: BLE001
                self.events.emit(
                    "MEDIA_WARM_FAILED",
                    "up-next audio could not be cached",
                    error=str(exc),
                    source_path=path,
                )

        threading.Thread(target=_work, name="pifm-warm-next", daemon=True).start()

    # --- TX ---

    def go_on_air(
        self,
        wait: bool = True,
        persist_intent: bool = True,
        source: str = "operator",
        initial_program_state: str = "playing",
        expected_intent_revision: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._go_on_air(
            wait=wait,
            persist_intent=persist_intent,
            source=source,
            initial_program_state=initial_program_state,
            expected_intent_revision=expected_intent_revision,
        )

    def _go_on_air(
        self,
        wait: bool = True,
        persist_intent: bool = True,
        source: str = "operator",
        initial_program_state: str = "playing",
        expected_intent_revision: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Go On Air. Heavy WAV prep runs outside the controller lock on Pi A+.

        wait=True (default): block until ON AIR / fault (unit tests).
        wait=False (HTTP): arm STARTING and return immediately; completion runs
        on a background thread so the browser can show STARTING BROADCAST…
        without waiting for MP3→WAV.

        Bounded timeout + STOP-cancelable. Never holds the global lock during
        MP3→WAV conversion. Status/STOP remain responsive on other threads.
        """
        audio_path = None  # type: Optional[str]
        generation = 0
        intent_revision = expected_intent_revision
        if initial_program_state not in ("playing", "paused", "stopped"):
            raise ValueError("invalid initial program state")
        with self._lock:
            if self._closing:
                raise StateError("The appliance is shutting down.")
            if self._air_stop_pending:
                raise StateError("Broadcast is stopping. Wait until OFF AIR.")
            checklist = self.broadcast_checklist()
            if not checklist.get("ready"):
                blockers = checklist.get("blockers") or []
                message = (
                    blockers[0].get("message")
                    if blockers and isinstance(blockers[0], dict)
                    else "The station is not ready to go on air."
                )
                raise StateError(str(message))
            self._ensure_queue_loaded_unlocked()
            if self._queue_index < 0:
                self._queue_index = 0
            if not self._select_existing_track_unlocked():
                raise StateError("no playable track in active playlist")
            if self.sm.state == State.FAULT:
                raise StateError(
                    "The station needs attention. Press STOP BROADCAST first, then try again."
                )
            if self.sm.state == State.ON_AIR:
                if persist_intent and not self.recovery.status().get("armed"):
                    try:
                        intent_revision = self._persist_on_intent_unlocked(source)
                    except RecoveryStateError as exc:
                        raise StateError(
                            "Could not arm broadcast recovery: {}".format(exc)
                        )
                if not self.tx.is_running():
                    self.events.emit(
                        "TX_START_REQUEST",
                        "ON_AIR but TX child missing — restarting program audio",
                    )
                    self._start_tx_current_unlocked()
                    self._playing = True
                    self._paused = False
                    self.events.emit(
                        "TX_START",
                        "ON_AIR recovered",
                        **self.tx.status(),
                    )
                else:
                    self.events.emit(
                        "TX_START_IDEMPOTENT",
                        "Go On Air ignored — already ON_AIR",
                        pid=self.tx.status().get("worker_pid") or self.tx.status().get("pid"),
                    )
                return self.status()
            if self._air_start_pending:
                raise StateError(
                    "Already starting — wait for ON AIR or press STOP BROADCAST."
                )
            if persist_intent:
                try:
                    intent_revision = self._persist_on_intent_unlocked(source)
                except RecoveryStateError as exc:
                    raise StateError(
                        "Could not arm broadcast recovery: {}".format(exc)
                    )
            if self.sm.state == State.SAFE_OFF:
                self.sm.transition(State.READY, "arm")
            self.events.emit("TX_START_REQUEST", "operator requested ON_AIR")
            track = self._current_track()
            if not track:
                raise StateError("no current track")
            if initial_program_state == "playing":
                audio_path = str(self.library.absolute_path(track["path"]))
            self._air_start_generation += 1
            generation = self._air_start_generation
            self._air_start_pending = True
            self._air_start_deadline = time.time() + float(self._tx_start_timeout_s)
            self._air_stop_pending = False
            self._program_pending = "starting"
            self.events.emit(
                "program_pending",
                "STARTING BROADCAST…",
                broadcast_ui="STARTING BROADCAST…",
            )

        if not wait:
            def _bg() -> None:
                try:
                    self._complete_go_on_air(
                        generation,
                        audio_path,
                        initial_program_state,
                        intent_revision,
                        source,
                    )
                except Exception:
                    with self._lock:
                        if self._air_start_generation == generation:
                            self._air_start_pending = False
                            self._program_pending = None

            threading.Thread(
                target=_bg,
                name="pifm-go-on-air",
                daemon=True,
            ).start()
            return self.status()
        return self._complete_go_on_air(
            generation,
            audio_path,
            initial_program_state,
            intent_revision,
            source,
        )

    def _complete_go_on_air(
        self,
        generation: int,
        audio_path: Optional[str],
        initial_program_state: str = "playing",
        expected_intent_revision: Optional[str] = None,
        source: str = "operator",
    ) -> Dict[str, Any]:
        timed_out = False
        cancelled = False
        prefetch_error = None  # type: Optional[BaseException]
        box = {"done": False}  # type: Dict[str, Any]

        def _should_cancel() -> bool:
            with self._lock:
                if self._air_start_generation != generation:
                    return True
                if not self._air_start_pending:
                    return True
                if self._closing:
                    return True
                if not self.recovery.matches_on_revision(
                    expected_intent_revision
                ):
                    return True
                if self._air_start_deadline and time.time() > self._air_start_deadline:
                    return True
                return False

        def _prefetch_work() -> None:
            nonlocal prefetch_error
            try:
                prefetch = getattr(self.tx, "prefetch_audio", None)
                if callable(prefetch) and audio_path:
                    prefetch(audio_path, should_cancel=_should_cancel)
                    self.events.emit(
                        "tx_audio_prefetch",
                        "program audio prepared",
                        source_path=audio_path,
                    )
            except StartCancelled as exc:
                prefetch_error = exc
            except Exception as exc:  # noqa: BLE001
                prefetch_error = exc
            finally:
                box["done"] = True

        worker = threading.Thread(target=_prefetch_work, name="pifm-prefetch", daemon=True)
        worker.start()
        while not box["done"]:
            time.sleep(0.1)
            with self._lock:
                if self._air_start_generation != generation or not self._air_start_pending:
                    cancelled = True
                elif self._air_start_deadline and time.time() > self._air_start_deadline:
                    timed_out = True
                    self._air_start_pending = False
                    self.events.emit(
                        "TX_START_TIMEOUT",
                        "Go On Air timed out during audio prepare",
                        timeout_s=self._tx_start_timeout_s,
                    )
            if cancelled or timed_out:
                # Let prefetch observe cancel; do not wait forever.
                worker.join(timeout=5.0)
                break
        worker.join(timeout=5.0)

        with self._lock:
            if self._air_start_generation != generation:
                return self.status()
            still_pending = self._air_start_pending

            if timed_out or isinstance(prefetch_error, StartCancelled) or not still_pending:
                self._air_start_pending = False
                self._air_start_deadline = None
                clear_pf = getattr(self.tx, "clear_prefetch", None)
                if callable(clear_pf):
                    clear_pf()
                self._program_pending = None
                if timed_out:
                    self._stop_tx_unlocked("start timeout")
                    kill_all_transmitters()
                    self.sm.enter_fault("Go On Air timed out during audio prepare")
                    self.events.emit("FAULT", "TX start timeout")
                    raise StateError(
                        "Could not go on air — startup timed out. Press STOP BROADCAST, then try again."
                    )
                self.events.emit(
                    "TX_START_CANCELLED",
                    "Go On Air aborted — STOP BROADCAST during audio prepare",
                )
                return self.status()

            if prefetch_error is not None:
                self._air_start_pending = False
                self._air_start_deadline = None
                clear_pf = getattr(self.tx, "clear_prefetch", None)
                if callable(clear_pf):
                    clear_pf()
                self._program_pending = None
                self.events.emit(
                    "MEDIA_TRACK_FAILED",
                    "track could not be prepared for Broadcast",
                    error=str(prefetch_error),
                    track=self._current_track(),
                )
                self.events.emit("TX_START_REJECTED", str(prefetch_error))
                self._stop_tx_unlocked("start failed")
                self.sm.enter_fault("TX start failed: {}".format(prefetch_error))
                self.events.emit("FAULT", str(prefetch_error))
                raise StateError(
                    "Could not go on air — audio prepare failed. Press STOP BROADCAST, then try again."
                )

            # Keep _air_start_pending True until TX start finishes so concurrent
            # Go On Air calls cannot arm a second start mid-spawn.
            track = self._current_track()
            if not track:
                self._air_start_pending = False
                self._air_start_deadline = None
                self._program_pending = None
                raise StateError("no current track")
            audio = (
                str(self.library.absolute_path(track["path"]))
                if initial_program_state == "playing"
                else ""
            )
            freq = float(self.config.get("frequency_mhz"))
            rds_ps = str(self.config.get("rds_ps"))
            rds_rt = str(self.config.get("rds_rt"))
            rds_pi = str(self.config.get("rds_pi"))
            backend = str(self.config.get("tx_backend") or "mock")

        # The lifecycle barrier makes STOP a strict boundary: it cannot return
        # while an admitted spawn is still in progress.
        start_error, worker_count, start_cancelled = (
            self._spawn_broadcast_under_lifecycle_gate(
                generation=generation,
                expected_intent_revision=expected_intent_revision,
                initial_program_state=initial_program_state,
                frequency_mhz=freq,
                audio_path=audio,
                rds_ps=rds_ps,
                rds_rt=rds_rt,
                rds_pi=rds_pi,
                backend=backend,
            )
        )

        with self._lock:
            self._air_start_pending = False
            self._air_start_deadline = None
            if start_cancelled or self._air_start_generation != generation:
                try:
                    self.tx.stop()
                except Exception:
                    pass
                self._program_pending = None
                self.events.emit(
                    "TX_START_CANCELLED",
                    "Broadcast start invalidated before transmitter spawn",
                )
                return self.status()
            if start_error is not None:
                self._program_pending = None
                self.events.emit("TX_START_REJECTED", str(start_error))
                self._stop_tx_unlocked("start failed")
                self.sm.enter_fault("TX start failed: {}".format(start_error))
                self.events.emit("FAULT", str(start_error))
                raise StateError(
                    "Could not go on air — the transmitter did not start. Press STOP BROADCAST, then try again."
                ) from start_error
            try:
                if not self.tx.is_running():
                    self._stop_tx_unlocked("start unverified")
                    kill_all_transmitters()
                    self.sm.enter_fault("Transmitter did not start")
                    self.events.emit("FAULT", "Transmitter did not start")
                    self._program_pending = None
                    raise StateError(
                        "Could not go on air — the transmitter did not start. Press STOP BROADCAST, then try again."
                    )
                if backend in ("pi_fm_rds", "fake"):
                    n = worker_count if worker_count is not None else count_transmitters()
                    if n != 1:
                        self._stop_tx_unlocked("worker count {}".format(n))
                        kill_all_transmitters()
                        self.sm.enter_fault("Transmitter worker count {}".format(n))
                        self.events.emit(
                            "FAULT", "expected 1 TX worker, found {}".format(n)
                        )
                        self._program_pending = None
                        raise StateError(
                            "Could not go on air — transmitter state could not be confirmed. Press STOP BROADCAST."
                        )
                self.sm.transition(State.ON_AIR, "tx started")
                self._playing = initial_program_state == "playing"
                self._paused = initial_program_state == "paused"
                if self._playing:
                    self._set_track_timing_unlocked()
                else:
                    self._clear_track_timing_unlocked()
                self._update_recovery_program_unlocked()
                self._program_pending = None
                meta = self.tx.status()
                self.events.emit(
                    "TX_START",
                    "ON_AIR",
                    source=source,
                    pid=meta.get("worker_pid") or meta.get("pid"),
                    launcher_pid=meta.get("launcher_pid"),
                    **{k: meta.get(k) for k in ("wav_path", "audio_mode", "cmd")},
                )
                self.events.emit(
                    "TX_PID",
                    "worker tracked",
                    pid=meta.get("worker_pid") or meta.get("pid"),
                )
                self._schedule_warm_up_next_unlocked()
            except StateError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._program_pending = None
                self.events.emit("TX_START_REJECTED", str(exc))
                self._stop_tx_unlocked("start failed")
                self.sm.enter_fault("TX start failed: {}".format(exc))
                self.events.emit("FAULT", str(exc))
                raise
            return self.status()

    def _spawn_broadcast_under_lifecycle_gate(
        self,
        generation: int,
        expected_intent_revision: Optional[str],
        initial_program_state: str,
        frequency_mhz: float,
        audio_path: str,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
        backend: str,
    ) -> tuple:
        with self._tx_lifecycle_lock:
            with self._lock:
                admitted = bool(
                    not self._closing
                    and not self._air_stop_pending
                    and self._air_start_pending
                    and self._air_start_generation == generation
                    and self.recovery.matches_on_revision(
                        expected_intent_revision
                    )
                )
            if not admitted:
                return None, None, True

            start_error = None  # type: Optional[BaseException]
            try:
                if initial_program_state == "playing":
                    self.tx.start(
                        frequency_mhz=frequency_mhz,
                        audio_path=audio_path,
                        rds_ps=rds_ps,
                        rds_rt=rds_rt,
                        rds_pi=rds_pi,
                    )
                else:
                    self.tx.start_silence(
                        frequency_mhz=frequency_mhz,
                        rds_ps=rds_ps,
                        rds_rt=rds_rt,
                        rds_pi=rds_pi,
                    )
            except Exception as exc:  # noqa: BLE001
                start_error = exc

            worker_count = None  # type: Optional[int]
            if start_error is None and backend in ("pi_fm_rds", "fake"):
                try:
                    worker_count = count_transmitters()
                except Exception:
                    worker_count = -1
            return start_error, worker_count, False

    def tx_off(
        self,
        persist_off: bool = True,
        source: str = "operator",
    ) -> Dict[str, Any]:
        with self._lock:
            self._stop_revision += 1
            request_revision = self._stop_revision
            self._air_stop_pending = True
        with self._broadcast_command_lock:
            self._tx_off(
                persist_off=persist_off,
                source=source,
                clear_stop_pending=False,
            )
        with self._lock:
            if self._stop_revision == request_revision:
                self._air_stop_pending = False
            return self.status()

    def _tx_off(
        self,
        persist_off: bool = True,
        source: str = "operator",
        clear_stop_pending: bool = True,
    ) -> Dict[str, Any]:
        """Absolute STOP BROADCAST — independent of state machine validity."""
        with self._lock:
            persistence_error = (
                self._persist_off_intent_unlocked(source)
                if persist_off
                else None
            )
            self._air_stop_pending = True
            self._air_start_pending = False
            self._air_start_deadline = None
            self._air_start_generation += 1  # invalidate in-flight start
            self._program_pending = None
            self._program_generation += 1
            clear_pf = getattr(self.tx, "clear_prefetch", None)
            if callable(clear_pf):
                clear_pf()
            self.events.emit(
                "TX_STOP_REQUEST",
                "STOP BROADCAST",
                source=source,
            )
            prev = self.sm.state

        # Wait for an already-admitted spawn, then stop and sweep while holding
        # the same barrier. No transmitter can spawn after this barrier exits.
        with self._tx_lifecycle_lock:
            try:
                self.tx.stop()
                self.events.emit(
                    "TX_TERM",
                    "backend stop completed",
                    source=source,
                    previous_state=prev.value,
                )
            except Exception as exc:  # noqa: BLE001
                self.events.emit("tx_failure", "stop error: {}".format(exc))
            sweep = kill_all_transmitters()

        with self._lock:
            if sweep.get("actions") or sweep.get("cleaned"):
                self.events.emit("TX_KILL", "sweep completed", kill_sweep=sweep)
            if (
                persist_off
                and not persistence_error
                and self.recovery.status().get("armed")
            ):
                try:
                    self.recovery.disarm()
                    self.events.emit(
                        "BROADCAST_INTENT_RECONCILED",
                        "STOP BROADCAST removed a stale ON intent",
                        source=source,
                        next_intent="off",
                    )
                except RecoveryStateError as exc:
                    persistence_error = str(exc)
                    self.events.emit(
                        "RECOVERY_PERSISTENCE_FAILED",
                        persistence_error,
                        source=source,
                        next_intent="off",
                    )
            if not sweep.get("clear", True):
                self.events.emit("TX_DUPLICATE_DETECTED", "sweep incomplete", kill_sweep=sweep)
                self.sm.enter_fault(
                    "STOP could not prove the transmitter is off"
                )
            else:
                self.events.emit(
                    "TX_EXIT",
                    "no transmitter processes remain",
                    source=source,
                )
            self._playing = False
            self._paused = False
            self._clear_track_timing_unlocked()
            if sweep.get("clear", True) and not persistence_error:
                if self.sm.state == State.ON_AIR:
                    self.sm.transition(
                        State.READY if self._queue else State.SAFE_OFF, "tx off"
                    )
                elif self.sm.state == State.FAULT:
                    self.sm.reset_to_safe()
                    self._refresh_ready_unlocked()
            elif persistence_error:
                self.sm.enter_fault(persistence_error)
            if clear_stop_pending:
                self._air_stop_pending = False
            self.events.emit(
                "STATE_RECONCILE",
                (
                    "RF stopped but OFF intent could not be persisted"
                    if persistence_error
                    else (
                        "STOP BROADCAST reconciled to OFF"
                        if sweep.get("clear", True)
                        else "STOP BROADCAST could not prove RF is off"
                    )
                ),
                previous_state=prev.value,
                state=self.sm.state.value,
                source=source,
                kill_sweep=sweep,
            )
            return self.status()

    def clear_fault(self) -> Dict[str, Any]:
        return self.tx_off(
            persist_off=True,
            source="clear_fault",
        )

    def restore_persisted_broadcast_intent(
        self,
        wait: bool = False,
        boot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Restore deliberate ON intent through the canonical safety gates."""
        recovery_state, refusal = self.recovery.begin_restore(
            boot_id or current_boot_id()
        )
        if recovery_state is None:
            if refusal and "not armed" not in refusal:
                self.events.emit("RECOVERY_REFUSED", refusal)
            return self.status()

        with self._lock:
            expected_playlist = str(
                recovery_state.get("active_playlist") or ""
            )
            active_playlist = str(self.config.get("active_playlist") or "")
            self._ensure_queue_loaded_unlocked()
            if expected_playlist != active_playlist:
                reason = "active playlist changed since ON intent was recorded"
                self.recovery.finish_restore("refused", reason)
                self.events.emit("RECOVERY_REFUSED", reason)
                return self.status()
            stored_queue = list(recovery_state.get("queue") or [])
            if stored_queue and Counter(stored_queue) == Counter(self._queue):
                self._queue = stored_queue
            current_track_id = recovery_state.get("current_track_id")
            if current_track_id in self._queue:
                self._queue_index = self._queue.index(current_track_id)
            elif self._queue:
                self._queue_index = 0
            program_state = str(
                recovery_state.get("program_state") or "playing"
            )
            self.events.emit(
                "RECOVERY_REQUESTED",
                "persisted ON intent found; validating safe restoration",
                program_state=program_state,
                active_playlist=active_playlist,
            )

        def _restore() -> Dict[str, Any]:
            try:
                status = self.go_on_air(
                    wait=True,
                    persist_intent=False,
                    source="power_or_service_restore",
                    initial_program_state=program_state,
                    expected_intent_revision=str(
                        recovery_state.get("intent_revision") or ""
                    ),
                )
                if status.get("broadcast_state") != "on_air":
                    raise StateError("transmitter did not reach ON AIR")
                deadline = time.monotonic() + self._recovery_stability_s
                while time.monotonic() < deadline:
                    time.sleep(min(0.1, deadline - time.monotonic()))
                    with self._lock:
                        stable = bool(
                            not self._closing
                            and self.sm.state == State.ON_AIR
                            and self.tx.is_running()
                            and self.recovery.matches_on_revision(
                                str(
                                    recovery_state.get(
                                        "intent_revision"
                                    )
                                    or ""
                                )
                            )
                        )
                    if not stable:
                        raise StateError(
                            "restored transmitter did not remain stable"
                        )
                self.recovery.finish_restore("restored")
                self.events.emit(
                    "RECOVERY_COMPLETED",
                    "persisted ON intent restored safely",
                    program_state=program_state,
                )
                return self.status()
            except Exception as exc:  # noqa: BLE001
                reason = str(exc)
                try:
                    checklist_ready = bool(
                        self.broadcast_checklist().get("ready")
                    )
                except Exception:
                    checklist_ready = False
                result = "failed" if checklist_ready else "refused"
                self.recovery.finish_restore(result, reason)
                self.events.emit("RECOVERY_REFUSED", reason, result=result)
                return self.status()

        if wait:
            return _restore()
        threading.Thread(
            target=_restore,
            name="pifm-broadcast-recovery",
            daemon=True,
        ).start()
        return self.status()

    def service_shutdown(self) -> Dict[str, Any]:
        """Stop this process's RF worker without changing operator intent."""
        self.begin_shutdown()
        return self.tx_off(
            persist_off=False,
            source="service_shutdown",
        )

    def begin_shutdown(self) -> None:
        with self._lock:
            self._closing = True
            self._air_start_pending = False
            self._air_start_generation += 1
            self._program_generation += 1

    def rf_quiet(self, confirmed: bool = False) -> Dict[str, Any]:
        """Orthogonal to TX — never starts transmission."""
        with self._lock:
            # Refresh mode from config each call
            self.network.mode = str(self.config.get("rf_quiet_mode", "simulate"))
            self.network.recovery_seconds = int(self.config.get("rf_quiet_seconds", 60))
            self.network.enter_quiet(confirmed=confirmed)
            return self.status()

    def rf_quiet_restore(self) -> Dict[str, Any]:
        with self._lock:
            self.network.force_restore()
            return self.status()

    def queue_snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            tracks = self.library.get_tracks(self._queue)
            for i, tid in enumerate(self._queue):
                tr = tracks.get(tid)
                if tr:
                    row = dict(tr)
                    row["queue_pos"] = i
                    row["is_current"] = i == self._queue_index
                    out.append(row)
            return out

    def reload_active_playlist(self) -> None:
        """Refresh the derived queue after an explicit playlist mutation."""
        with self._lock:
            self._load_queue(rebuild=True)
            self._refresh_ready_unlocked()
            self.events.emit("queue_changed", "active playlist refreshed")

    def reorder_active_queue(self, track_ids: List[Any]) -> List[Dict[str, Any]]:
        """Persist a complete queue reorder back to the active playlist."""
        with self._lock:
            if self._playing or self.tx.is_running() or self.sm.state == State.ON_AIR:
                raise StateError(
                    "Pause or reset the music and stop Broadcast before reordering."
                )
            order = [str(track_id) for track_id in track_ids]
            self._ensure_queue_loaded_unlocked()
            if Counter(order) != Counter(self._queue):
                raise ValueError("queue reorder must contain every queued track once")
            playlist_id = str(self.config.get("active_playlist") or "")
            if not playlist_id:
                raise ValueError("select a playlist before reordering")
            playlist = self.library.load_playlist(playlist_id)
            playlist["tracks"] = order
            self.library.save_playlist(playlist_id, playlist)
            self._queue = list(order)
            self._queue_index = -1
            self._refresh_ready_unlocked()
            self.events.emit("queue_reordered", "active playlist order updated")
            return self.queue_snapshot()

    def _start_tx_current_unlocked(self) -> None:
        if self._air_stop_pending:
            raise StartCancelled("STOP BROADCAST is pending")
        track = self._current_track()
        if not track:
            raise StateError("no current track")
        audio = str(self.library.absolute_path(track["path"]))
        with self._tx_lifecycle_lock:
            if self._air_stop_pending:
                raise StartCancelled("STOP BROADCAST is pending")
            self.tx.start(
                frequency_mhz=float(self.config.get("frequency_mhz")),
                audio_path=audio,
                rds_ps=str(self.config.get("rds_ps")),
                rds_rt=str(self.config.get("rds_rt")),
                rds_pi=str(self.config.get("rds_pi")),
            )
        meta = self.tx.status()
        self.events.emit(
            "tx_audio",
            "TX audio pipeline ready",
            source_path=audio,
            wav_path=meta.get("wav_path"),
            wav_duration_s=meta.get("wav_duration_s"),
            wav_seekable=meta.get("wav_seekable"),
            wav_converted=meta.get("wav_converted"),
            audio_mode=meta.get("audio_mode"),
            pid=meta.get("pid"),
            cmd=meta.get("cmd"),
        )
        self._set_track_timing_unlocked()

    def _hold_silence_unlocked(self) -> None:
        if self._air_stop_pending:
            raise StartCancelled("STOP BROADCAST is pending")
        if hasattr(self.tx, "start_silence"):
            with self._tx_lifecycle_lock:
                if self._air_stop_pending:
                    raise StartCancelled("STOP BROADCAST is pending")
                self.tx.start_silence(
                    frequency_mhz=float(self.config.get("frequency_mhz")),
                    rds_ps=str(self.config.get("rds_ps")),
                    rds_rt=str(self.config.get("rds_rt")),
                    rds_pi=str(self.config.get("rds_pi")),
                )

    def _stop_tx_unlocked(self, reason: str) -> None:
        try:
            self.tx.stop()
        except Exception as exc:  # noqa: BLE001
            self.events.emit("tx_failure", "stop error: {}".format(exc))

    def watchdog(self) -> None:
        """Poll TX child. Never starts a second transmitter while one exists.

        Silence re-arm is the only restart path and always goes through stop→start
        under the controller lock (single-TX invariant inside OwnedTxProcess).
        """
        with self._broadcast_command_lock:
            self._watchdog_serialized()

    def _watchdog_serialized(self) -> None:
        advance_program = False
        with self._lock:
            if self.sm.state != State.ON_AIR or self._air_stop_pending:
                return
            # Duplicate discovery even if tracked child looks fine.
            backend = str(self.config.get("tx_backend") or "mock")
            if backend in ("pi_fm_rds", "fake"):
                try:
                    n = count_transmitters()
                except TransmitterProcessScanError as exc:
                    self._stop_tx_unlocked("process scan failed")
                    kill_all_transmitters()
                    self.sm.enter_fault(
                        "transmitter process state could not be inspected"
                    )
                    self.events.emit("FAULT", str(exc))
                    return
                if n > 1:
                    self.events.emit(
                        "TX_DUPLICATE_DETECTED",
                        "watchdog found {} TX processes".format(n),
                        processes=list_transmitter_processes(),
                    )
                    kill_all_transmitters()
                    self.sm.enter_fault("duplicate transmitter processes detected")
                    self.events.emit("FAULT", "duplicate TX")
                    return
            if self.tx.is_running():
                if (
                    self._playing
                    and not self._paused
                    and self._track_started_monotonic is not None
                    and self._track_duration_s is not None
                    and time.monotonic() - self._track_started_monotonic
                    >= self._track_duration_s
                ):
                    self._clear_track_timing_unlocked()
                    advance_program = True
                else:
                    return
            if advance_program:
                self.events.emit(
                    "PROGRAM_TRACK_ENDED",
                    "track duration reached; advancing locally",
                    track=self._current_track(),
                )
            else:
                holding = self._paused or (not self._playing)
                if holding:
                    try:
                        # stop/start inside start_silence enforces single TX.
                        self._hold_silence_unlocked()
                        self.events.emit(
                            "silence_hold_rearm",
                            "TX child exited during program pause/stop — silence re-armed; still ON AIR",
                        )
                        if not self.tx.is_running():
                            self.sm.enter_fault("silence hold failed to restart")
                            self.events.emit("FAULT", "silence hold restart failed")
                    except Exception as exc:  # noqa: BLE001
                        self.sm.enter_fault("silence hold failed: {}".format(exc))
                        self.events.emit("FAULT", "silence hold failed: {}".format(exc))
                    return
                self.sm.enter_fault("TX process exited unexpectedly")
                self.events.emit("FAULT", "TX child died; entered FAULT")
                return
        if advance_program:
            try:
                self.next_track(wait=False)
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self.events.emit(
                        "PROGRAM_ADVANCE_FAILED",
                        str(exc),
                        track=self._current_track(),
                    )

    def shutdown_request(self) -> None:
        self.events.emit("shutdown_request", "physical switch or API")
        self.tx_off(persist_off=True, source="physical_switch")
