"""Ring-buffer event log for appliance — optional JSONL persistence + push fanout."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional


class EventLog:
    def __init__(
        self,
        maxlen: int = 500,
        persist_path: Optional[Path] = None,
    ) -> None:
        self._events = deque(maxlen=maxlen)  # type: Deque[Dict[str, Any]]
        self._lock = threading.Lock()
        self._persist_path = Path(persist_path) if persist_path else None
        self._seq = 0
        self._listeners = []  # type: List[Callable[[Dict[str, Any]], None]]
        self._cond = threading.Condition(self._lock)
        if self._persist_path is not None:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)

    def subscribe(self, callback: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        """Register a listener. Returns an unsubscribe function."""
        with self._lock:
            self._listeners.append(callback)

        def _unsub() -> None:
            with self._lock:
                try:
                    self._listeners.remove(callback)
                except ValueError:
                    pass

        return _unsub

    def wait(self, after_seq: int = 0, timeout: float = 25.0) -> int:
        """Block until a newer event seq exists (for SSE long-poll style wakeups)."""
        deadline = time.time() + max(0.1, float(timeout))
        with self._cond:
            while self._seq <= after_seq:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=remaining)
            return self._seq

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def emit(self, kind: str, message: str, **extra: Any) -> None:
        row = {
            "ts": time.time(),
            "kind": kind,
            "message": message,
        }
        row.update(extra)
        listeners = []  # type: List[Callable[[Dict[str, Any]], None]]
        with self._cond:
            self._seq += 1
            row["seq"] = self._seq
            self._events.append(row)
            listeners = list(self._listeners)
            self._cond.notify_all()
            if self._persist_path is not None:
                try:
                    with open(str(self._persist_path), "a") as fh:
                        fh.write(json.dumps(row, default=str) + "\n")
                except OSError:
                    pass
        for cb in listeners:
            try:
                cb(row)
            except Exception:
                pass

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._events)
        return items[-limit:]
