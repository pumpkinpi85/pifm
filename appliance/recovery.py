"""Atomic persisted operator broadcast intent.

The presence of a valid marker means the operator deliberately requested ON.
Marker absence means OFF.  This makes STOP a small unlink operation that is
reliable even when the filesystem has no free data blocks.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


STATE_VERSION = 1
PROGRAM_STATES = ("playing", "paused", "stopped")


class RecoveryStateError(RuntimeError):
    """Persisted recovery state could not be safely changed."""


def current_boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        value = ""
    return value or "boot-id-unavailable"


class BroadcastIntentStore:
    """Own the durable ON marker and fail closed on malformed state."""

    def __init__(self, root: Path) -> None:
        self.directory = Path(root) / "data" / "recovery"
        self.path = self.directory / "broadcast-on.json"
        self._lock = threading.Lock()
        self._state = None  # type: Optional[Dict[str, Any]]
        self._invalid_reason = None  # type: Optional[str]
        self._last_error = None  # type: Optional[str]
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text())
            self._state = self._validate(payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._state = None
            self._invalid_reason = "invalid persisted recovery state: {}".format(exc)

    @staticmethod
    def _validate(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("state root must be an object")
        if payload.get("version") != STATE_VERSION:
            raise ValueError("unsupported state version")
        if payload.get("desired_broadcast") != "on":
            raise ValueError("marker does not contain ON intent")
        program_state = str(payload.get("program_state") or "")
        if program_state not in PROGRAM_STATES:
            raise ValueError("invalid program state")
        playlist_id = str(payload.get("active_playlist") or "").strip()
        if not playlist_id:
            raise ValueError("active playlist is required")
        queue = payload.get("queue") or []
        if not isinstance(queue, list) or any(
            not isinstance(track_id, str) or not track_id for track_id in queue
        ):
            raise ValueError("queue must contain track ids")
        current_track_id = payload.get("current_track_id")
        if current_track_id is not None and not isinstance(current_track_id, str):
            raise ValueError("current track id must be a string")
        clean = dict(payload)
        clean["program_state"] = program_state
        clean["active_playlist"] = playlist_id
        clean["queue"] = list(queue)
        clean["current_track_id"] = current_track_id
        return clean

    def _fsync_directory(self) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        fd = os.open(str(self.directory), flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write(self, payload: Dict[str, Any]) -> None:
        clean = self._validate(payload)
        self.directory.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=".broadcast-on.", suffix=".json", dir=str(self.directory)
        )
        try:
            with os.fdopen(fd, "w") as handle:
                json.dump(clean, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, str(self.path))
            self._fsync_directory()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self._state = clean
        self._invalid_reason = None
        self._last_error = None

    def arm(self, snapshot: Dict[str, Any], source: str) -> Dict[str, Any]:
        payload = {
            "version": STATE_VERSION,
            "desired_broadcast": "on",
            "program_state": snapshot.get("program_state") or "playing",
            "active_playlist": snapshot.get("active_playlist"),
            "queue": list(snapshot.get("queue") or []),
            "current_track_id": snapshot.get("current_track_id"),
            "source": str(source or "operator"),
            "updated_at": time.time(),
            "last_restore_boot_id": None,
            "last_restore_result": None,
            "last_restore_reason": None,
        }
        with self._lock:
            try:
                self._write(payload)
            except OSError as exc:
                self._last_error = "could not persist ON intent: {}".format(exc)
                raise RecoveryStateError(self._last_error)
            return deepcopy(payload)

    def disarm(self) -> bool:
        """Persist OFF by removing the ON marker before transmitter shutdown."""
        with self._lock:
            existed = self.path.exists()
            removed = False
            try:
                if existed:
                    self.path.unlink()
                    removed = True
                    self._fsync_directory()
            except OSError as exc:
                if removed:
                    self._state = None
                    self._invalid_reason = None
                self._last_error = "could not persist OFF intent: {}".format(exc)
                raise RecoveryStateError(self._last_error)
            self._state = None
            self._invalid_reason = None
            self._last_error = None
            return existed

    def update_program(self, snapshot: Dict[str, Any]) -> bool:
        with self._lock:
            if self._state is None:
                return False
            payload = dict(self._state)
            payload.update(
                {
                    "program_state": snapshot.get("program_state") or "playing",
                    "active_playlist": snapshot.get("active_playlist"),
                    "queue": list(snapshot.get("queue") or []),
                    "current_track_id": snapshot.get("current_track_id"),
                    "updated_at": time.time(),
                }
            )
            try:
                self._write(payload)
            except OSError as exc:
                self._last_error = "could not update recovery state: {}".format(exc)
                return False
            return True

    def begin_restore(
        self, boot_id: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        with self._lock:
            if self._invalid_reason:
                return None, self._invalid_reason
            if self._state is None:
                return None, "broadcast recovery is not armed"
            active_boot = boot_id or current_boot_id()
            if (
                self._state.get("last_restore_boot_id") == active_boot
                and self._state.get("last_restore_result")
                in ("pending", "refused", "failed")
            ):
                return None, "recovery already attempted during this boot"
            payload = dict(self._state)
            payload["last_restore_boot_id"] = active_boot
            payload["last_restore_result"] = "pending"
            payload["last_restore_reason"] = None
            payload["updated_at"] = time.time()
            try:
                self._write(payload)
            except OSError as exc:
                self._last_error = "could not mark recovery attempt: {}".format(exc)
                return None, self._last_error
            return deepcopy(payload), None

    def finish_restore(self, result: str, reason: str = "") -> None:
        if result not in ("restored", "refused", "failed"):
            raise ValueError("invalid restore result")
        with self._lock:
            if self._state is None:
                return
            payload = dict(self._state)
            payload["last_restore_result"] = result
            payload["last_restore_reason"] = str(reason or "")
            payload["updated_at"] = time.time()
            try:
                self._write(payload)
            except OSError as exc:
                self._last_error = "could not record recovery result: {}".format(exc)

    def snapshot(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return deepcopy(self._state)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            state = deepcopy(self._state)
            return {
                "armed": state is not None and self._invalid_reason is None,
                "desired_broadcast": "on" if state is not None else "off",
                "valid": self._invalid_reason is None,
                "invalid_reason": self._invalid_reason,
                "last_error": self._last_error,
                "program_state": state.get("program_state") if state else None,
                "last_restore_result": (
                    state.get("last_restore_result") if state else None
                ),
                "last_restore_reason": (
                    state.get("last_restore_reason") if state else None
                ),
            }
