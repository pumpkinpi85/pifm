"""Central appliance controller — sole authority for TX child process."""

from __future__ import annotations

import random
import threading
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from .build_info import resolve_build_identity
from .config import Config
from .events import EventLog
from .hardware_environment import check_host_prerequisites
from .hardware_profile import resolve_hardware_profile
from .library import Library
from .network import NetworkManager
from .state import State, StateError, StateMachine  # StateError used by API callers
from .tx import TxBackend, build_backend, kill_all_transmitters, probe_real_tx_readiness
from .tx_process import StartCancelled, count_transmitters, list_transmitter_processes


class Controller:
    def __init__(self, config: Config, library: Library, events: EventLog) -> None:
        self.config = config
        self.library = library
        self.events = events
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
        self._queue = []  # type: List[str]  # track ids
        self._queue_index = -1
        self._playing = False
        self._paused = False
        # True while MP3→WAV runs outside the lock so status stays responsive.
        self._air_start_pending = False
        self._air_start_deadline = None  # type: Optional[float]
        self._air_start_generation = 0
        self._air_stop_pending = False
        # Program transport transitional intent (UI only until confirmed).
        # None | starting | pausing | resuming | changing
        self._program_pending = None  # type: Optional[str]
        self._program_generation = 0
        self._tx_start_timeout_s = float(config.get("tx_start_timeout_s", 90) or 90)
        self._started = time.time()
        self.events.emit("boot", "controller constructed; state=SAFE_OFF")
        # Evaluate READY vs SAFE_OFF from playlist contents; never ON_AIR
        with self._lock:
            self._refresh_ready_unlocked()

    # --- status ---

    def broadcast_checklist(self) -> Dict[str, Any]:
        """Operator-facing readiness for GO ON AIR (never starts TX).

        Read-only: must not reshuffle or otherwise mutate the queue.
        """
        with self._lock:
            self._ensure_queue_loaded_unlocked()
            cfg = self.config.as_dict()
            pl_id = str(cfg.get("active_playlist") or "")
            pl_name = pl_id
            track_count = len(self._queue)
            try:
                pl = self.library.load_playlist(pl_id)
                pl_name = str(pl.get("name") or pl_id)
            except FileNotFoundError:
                pl_name = pl_id or "(none)"

            items = []
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
            items.append(
                {
                    "id": "frequency",
                    "label": "Frequency selected",
                    "ok": 87.1 <= freq <= 108.2,
                    "detail": "{:.1f} MHz".format(freq),
                    "operator_hint": "Set a valid FM frequency on the Station page.",
                    "cta": "station",
                    "cta_label": "Open Station",
                }
            )
            items.append(
                {
                    "id": "music",
                    "label": "Music available",
                    "ok": track_count > 0,
                    "detail": "Program queue has music"
                    if track_count
                    else "Active playlist has no tracks",
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
            if backend == "mock":
                items.append(
                    {
                        "id": "transmitter",
                        "label": "Transmitter harness",
                        "ok": True,
                        "detail": "Internal mock harness (tests/dev only)",
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
                "frequency_mhz": freq,
                "rds_ps": cfg.get("rds_ps"),
                "rds_rt": cfg.get("rds_rt"),
                "rds_pi": cfg.get("rds_pi"),
                "broadcast_mode": "test_harness" if backend == "mock" else "live",
                "on_air": self.sm.state == State.ON_AIR,
                "real_tx": real,
            }

    def status(self) -> Dict[str, Any]:
        with self._lock:
            cfg = self.config.as_dict()
            self._ensure_queue_loaded_unlocked()
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
            return {
                "state": self.sm.state.value,
                "fault_reason": self.sm.fault_reason,
                "tx": self.tx.status(),
                "tx_running": tracked_running,
                "system_tx_count": system_tx if system_tx >= 0 else None,
                "system_tx_processes": system_tx_list,
                "frequency_mhz": cfg["frequency_mhz"],
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
                "broadcast": checklist,
                "queue_fingerprint": list(self._queue),
            }

    def _current_track(self) -> Optional[Dict[str, Any]]:
        if self._queue_index < 0 or self._queue_index >= len(self._queue):
            return None
        return self.library.get_track(self._queue[self._queue_index])

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

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            before_freq = self.config.get("frequency_mhz")
            data = self.config.update(patch)
            # Backend construction captures executable path and timing correction.
            # Rebuild for any such setting change; never auto-start TX.
            backend_keys = {
                "tx_backend",
                "pi_fm_rds_path",
                "pi_fm_rds_ppm",
            }
            backend_changed = bool(backend_keys.intersection(patch))
            if self.sm.state == State.ON_AIR or self.tx.is_running():
                # Changing freq/RDS/backend while on air: emergency stop first
                self._stop_tx_unlocked("config changed while on air")
                kill_all_transmitters()
                if self.sm.state == State.ON_AIR:
                    self.sm.transition(
                        State.READY if self._queue else State.SAFE_OFF, "config"
                    )
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
            if "pi_fm_rds_ppm" in patch:
                self.events.emit(
                    "tx_timing_changed",
                    "PiFmRds timing correction set to {} ppm".format(
                        data["pi_fm_rds_ppm"]
                    ),
                )
            if "frequency_mhz" in patch and patch["frequency_mhz"] != before_freq:
                self.events.emit(
                    "frequency_changed",
                    "frequency set to {}".format(data["frequency_mhz"]),
                )
            if "active_playlist" in patch:
                self.events.emit(
                    "playlist_changed",
                    "active playlist {}".format(data["active_playlist"]),
                )
            # Intentional queue rebuild boundaries only.
            if "active_playlist" in patch or "shuffle" in patch:
                self._load_queue(rebuild=True)
            self._refresh_ready_unlocked()
            return data

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
                if hasattr(self.tx, "start_silence"):
                    self.tx.start_silence(
                        frequency_mhz=float(self.config.get("frequency_mhz")),
                        rds_ps=str(self.config.get("rds_ps")),
                        rds_rt=str(self.config.get("rds_rt")),
                        rds_pi=str(self.config.get("rds_pi")),
                    )
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
            if self.sm.state == State.ON_AIR:
                # Stop the program but keep the station on air (silence hold).
                # program_state becomes "stopped" (not paused).
                if hasattr(self.tx, "start_silence"):
                    self.tx.start_silence(
                        frequency_mhz=float(self.config.get("frequency_mhz")),
                        rds_ps=str(self.config.get("rds_ps")),
                        rds_rt=str(self.config.get("rds_rt")),
                        rds_pi=str(self.config.get("rds_pi")),
                    )
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
                        self._program_pending = None
                        if self.sm.state == State.ON_AIR:
                            self._stop_tx_unlocked("end of playlist")
                            self.sm.transition(State.READY, "end")
                        return self.status()
            else:
                self._queue_index = max(0, self._queue_index - 1)
            self._paused = False
            self._playing = True
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
                self.events.emit("tx_failure", str(prefetch_error))
                raise StateError("Could not change music audio: {}".format(prefetch_error))
            try:
                self._start_tx_current_unlocked()
                self._playing = True
                self._paused = False
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
            except Exception:
                pass

        threading.Thread(target=_work, name="pifm-warm-next", daemon=True).start()

    # --- TX ---

    def go_on_air(self, wait: bool = True) -> Dict[str, Any]:
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
        with self._lock:
            self._ensure_queue_loaded_unlocked()
            if not self._queue:
                raise StateError(
                    "Cannot go on air yet — choose a playlist that has music first."
                )
            if self._queue_index < 0:
                self._queue_index = 0
            if self.sm.state == State.FAULT:
                raise StateError(
                    "The station needs attention. Press STOP BROADCAST first, then try again."
                )
            if self.sm.state == State.ON_AIR:
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
            if self.sm.state == State.SAFE_OFF:
                self.sm.transition(State.READY, "arm")
            self.events.emit("TX_START_REQUEST", "operator requested ON_AIR")
            track = self._current_track()
            if not track:
                raise StateError("no current track")
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
                    self._complete_go_on_air(generation, audio_path)
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
        return self._complete_go_on_air(generation, audio_path)

    def _complete_go_on_air(
        self, generation: int, audio_path: Optional[str]
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
            audio = str(self.library.absolute_path(track["path"]))
            freq = float(self.config.get("frequency_mhz"))
            rds_ps = str(self.config.get("rds_ps"))
            rds_rt = str(self.config.get("rds_rt"))
            rds_pi = str(self.config.get("rds_pi"))
            backend = str(self.config.get("tx_backend") or "mock")

        # TX start + process reconcile outside the controller lock so /api/status
        # and STOP remain responsive on Pi A+.
        start_error = None  # type: Optional[BaseException]
        try:
            self.tx.start(
                frequency_mhz=freq,
                audio_path=audio,
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

        with self._lock:
            self._air_start_pending = False
            self._air_start_deadline = None
            if self._air_start_generation != generation:
                try:
                    self.tx.stop()
                except Exception:
                    pass
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
                self._playing = True
                self._paused = False
                self._program_pending = None
                meta = self.tx.status()
                self.events.emit(
                    "TX_START",
                    "ON_AIR",
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

    def tx_off(self) -> Dict[str, Any]:
        """Absolute STOP BROADCAST — independent of state machine validity."""
        with self._lock:
            self._air_stop_pending = True
            self._air_start_pending = False
            self._air_start_deadline = None
            self._air_start_generation += 1  # invalidate in-flight start
            self._program_pending = None
            self._program_generation += 1
            clear_pf = getattr(self.tx, "clear_prefetch", None)
            if callable(clear_pf):
                clear_pf()
            self.events.emit("TX_STOP_REQUEST", "STOP BROADCAST")
            prev = self.sm.state
            try:
                self.tx.stop()
                self.events.emit("TX_TERM", "backend stop completed", previous_state=prev.value)
            except Exception as exc:  # noqa: BLE001
                self.events.emit("tx_failure", "stop error: {}".format(exc))
            sweep = kill_all_transmitters()
            if sweep.get("actions") or sweep.get("cleaned"):
                self.events.emit("TX_KILL", "sweep completed", kill_sweep=sweep)
            if not sweep.get("clear", True):
                self.events.emit("TX_DUPLICATE_DETECTED", "sweep incomplete", kill_sweep=sweep)
            else:
                self.events.emit("TX_EXIT", "no transmitter processes remain")
            self._playing = False
            self._paused = False
            if self.sm.state == State.ON_AIR:
                self.sm.transition(
                    State.READY if self._queue else State.SAFE_OFF, "tx off"
                )
            elif self.sm.state == State.FAULT:
                self.sm.reset_to_safe()
                self._refresh_ready_unlocked()
            self._air_stop_pending = False
            self.events.emit(
                "STATE_RECONCILE",
                "STOP BROADCAST reconciled to OFF",
                previous_state=prev.value,
                state=self.sm.state.value,
                kill_sweep=sweep,
            )
            return self.status()

    def clear_fault(self) -> Dict[str, Any]:
        with self._lock:
            self._stop_tx_unlocked("clear fault")
            kill_all_transmitters()
            self.sm.reset_to_safe()
            self._refresh_ready_unlocked()
            return self.status()

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
            for i, tid in enumerate(self._queue):
                tr = self.library.get_track(tid)
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
        track = self._current_track()
        if not track:
            raise StateError("no current track")
        audio = str(self.library.absolute_path(track["path"]))
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

    def _hold_silence_unlocked(self) -> None:
        if hasattr(self.tx, "start_silence"):
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
        with self._lock:
            if self.sm.state != State.ON_AIR:
                return
            # Duplicate discovery even if tracked child looks fine.
            backend = str(self.config.get("tx_backend") or "mock")
            if backend in ("pi_fm_rds", "fake"):
                n = count_transmitters()
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
                return
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

    def shutdown_request(self) -> None:
        with self._lock:
            self.events.emit("shutdown_request", "physical switch or API")
            self._stop_tx_unlocked("shutdown")
            self.sm.reset_to_safe()
