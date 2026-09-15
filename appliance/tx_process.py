"""Deterministic transmitter process ownership (no RF).

Test #3 / 2026-09-15 Go On Air incident:
- Popen tracked the ``sudo`` parent; SIGTERM on that parent did not reliably
  kill the privileged ``pi_fm_rds`` worker.
- Counting ``sudo … pi_fm_rds`` + worker as two independent transmitters
  triggered a false duplicate path; ``spawn()`` then called ``terminate()``
  while holding a non-reentrant lock → API deadlock with RF still live.

Design:
- Count only TX *workers* (never launcher wrappers).
- Before any spawn: discover existing workers and clean to zero.
- After spawn: resolve the worker PID (child of sudo, or the direct process).
- On stop: ``sudo kill`` the worker PID, then launcher, then a bounded sweep.
- ``spawn()`` never calls ``terminate()`` while holding ``_lock``.
- FakeProcessTxBackend uses the same ownership path with a harmless sleeper
  labeled ``pifm-fake-tx-hold`` (no GPIO / no RF).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Patterns that identify a transmitter *worker* binary basename (not launchers).
TX_WORKER_PATTERNS = (
    "pi_fm_rds",
    "fm_transmitter",
    "pifm-fake-tx-hold",
)

# argv0 basenames that only launch a TX worker (must not inflate the worker count).
_TX_LAUNCHER_BASENAMES = frozenset({"sudo", "sudoedit", "env", "nice", "ionice"})


class DuplicateTransmitterError(RuntimeError):
    """Raised when a second transmitter would violate the single-TX invariant."""


class StartCancelled(Exception):
    """Raised when Go On Air preparation is cancelled (STOP or timeout)."""


def cmdline_is_tx_worker(cmdline: str, pattern: str) -> bool:
    """True only for the real TX worker binary, not sudo/env wrappers.

    Production spawn is ``sudo …/pi_fm_rds …`` → two ps rows share the
    ``pi_fm_rds`` token. The row whose argv0 basename is a launcher is never
    counted. The worker row's argv0 basename must match the worker pattern.
    """
    if not cmdline or not pattern:
        return False
    if "pgrep" in cmdline or "ps -ax" in cmdline:
        return False
    tokens = cmdline.split()
    if not tokens:
        return False
    first = os.path.basename(tokens[0])
    # This PID is a launcher process (sudo holding the worker as a child).
    if first in _TX_LAUNCHER_BASENAMES:
        return False
    if pattern == "pifm-fake-tx-hold":
        # Fake workers rewrite argv0 in-process; ps still shows python -c …token…
        return pattern in cmdline
    # Worker identity = executable basename (not loose whole-line substring alone).
    return first == pattern


def _ps_ax_lines() -> List[str]:
    """One process table snapshot (pid + command)."""
    try:
        out = subprocess.check_output(
            ["ps", "-ax", "-o", "pid=,command="],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return []
    lines = []  # type: List[str]
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if "pgrep" in ln or "ps -ax" in ln:
            continue
        lines.append(ln)
    return lines


def _pgrep_lines(pattern: str) -> List[str]:
    """Portable process listing — macOS pgrep -a ≠ Linux pgrep -a."""
    lines = []  # type: List[str]
    for ln in _ps_ax_lines():
        if pattern in ln:
            lines.append(ln)
    if lines:
        return lines
    try:
        out = subprocess.check_output(
            ["pgrep", "-af", pattern],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
    except (subprocess.CalledProcessError, OSError, FileNotFoundError):
        return []
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln or "pgrep" in ln:
            continue
        lines.append(ln)
    return lines


_list_cache = {"t": 0.0, "patterns": None, "rows": []}  # type: Dict[str, Any]
_LIST_CACHE_TTL_S = 1.0


def list_transmitter_processes(
    patterns: Sequence[str] = TX_WORKER_PATTERNS,
) -> List[Dict[str, Any]]:
    """Return live TX *worker* processes: [{pid, cmdline}, ...].

    Launcher wrappers (``sudo … pi_fm_rds``) are excluded so one RF tree
    counts as one transmitter.
    """
    now = time.time()
    if (
        _list_cache["patterns"] == tuple(patterns)
        and now - float(_list_cache["t"]) < _LIST_CACHE_TTL_S
    ):
        return list(_list_cache["rows"])  # type: ignore[arg-type]

    # One ps snapshot for all patterns — Pi A+ cannot afford 3× full scans.
    snapshot = _ps_ax_lines()
    found = {}  # type: Dict[int, str]
    for ln in snapshot:
        parts = ln.split(None, 1)
        try:
            pid = int(parts[0])
        except (ValueError, IndexError):
            continue
        if pid == os.getpid():
            continue
        cmdline = parts[1] if len(parts) > 1 else ""
        for pat in patterns:
            if pat not in (cmdline or ln):
                continue
            if not cmdline_is_tx_worker(cmdline, pat):
                continue
            found[pid] = cmdline or ln
            break
    rows = [{"pid": pid, "cmdline": cmd} for pid, cmd in sorted(found.items())]
    _list_cache["t"] = now
    _list_cache["patterns"] = tuple(patterns)
    _list_cache["rows"] = rows
    return list(rows)


def invalidate_tx_process_cache() -> None:
    _list_cache["t"] = 0.0
    _list_cache["rows"] = []


def count_transmitters(patterns: Sequence[str] = TX_WORKER_PATTERNS) -> int:
    return len(list_transmitter_processes(patterns))


def children_of(pid: int) -> List[int]:
    """Best-effort children via pgrep -P."""
    try:
        out = subprocess.check_output(
            ["pgrep", "-P", str(pid)],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8", errors="replace")
    except (subprocess.CalledProcessError, OSError, FileNotFoundError):
        return []
    kids = []
    for ln in out.splitlines():
        ln = ln.strip()
        if ln.isdigit():
            kids.append(int(ln))
    return kids


def resolve_worker_pid(launcher_pid: int, deadline_s: float = 0.8) -> Optional[int]:
    """Find the actual TX worker under a sudo/launcher PID."""
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        kids = children_of(launcher_pid)
        if kids:
            procs = list_transmitter_processes()
            kid_set = set(kids)
            for p in procs:
                if p["pid"] in kid_set:
                    return int(p["pid"])
            return kids[0]
        for p in list_transmitter_processes():
            if p["pid"] == launcher_pid:
                return launcher_pid
        time.sleep(0.05)
    for p in list_transmitter_processes():
        if p["pid"] == launcher_pid:
            return launcher_pid
    return None


def _kill_pid(pid: int, sig: int, use_sudo: bool) -> None:
    if pid <= 1:
        return
    try:
        if use_sudo:
            subprocess.call(
                ["sudo", "-n", "kill", "-{}".format(sig), str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        else:
            os.kill(pid, sig)
    except (OSError, ProcessLookupError, subprocess.TimeoutExpired):
        pass


def terminate_pids(
    pids: Sequence[int],
    use_sudo: bool = True,
    wait_s: float = 0.4,
) -> Dict[str, Any]:
    """TERM then KILL exact PIDs. Returns audit dict."""
    invalidate_tx_process_cache()
    unique = sorted({int(p) for p in pids if int(p) > 1})
    actions = []  # type: List[Dict[str, Any]]
    for pid in unique:
        _kill_pid(pid, signal.SIGTERM, use_sudo=False)
        if use_sudo:
            _kill_pid(pid, signal.SIGTERM, use_sudo=True)
        actions.append({"pid": pid, "signal": "TERM"})
    t0 = time.time()
    while time.time() - t0 < wait_s:
        alive = []
        for pid in unique:
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except OSError:
                pass
        if not alive:
            break
        time.sleep(0.05)
    for pid in unique:
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        _kill_pid(pid, signal.SIGKILL, use_sudo=False)
        if use_sudo:
            _kill_pid(pid, signal.SIGKILL, use_sudo=True)
        actions.append({"pid": pid, "signal": "KILL"})
    invalidate_tx_process_cache()
    return {"actions": actions, "requested": unique}


def ensure_no_transmitters(
    allow_clean: bool = True,
    use_sudo: bool = True,
) -> Dict[str, Any]:
    """Ensure zero TX workers. Optionally clean; else raise if any exist."""
    invalidate_tx_process_cache()
    existing = list_transmitter_processes()
    result = {
        "had": existing,
        "cleaned": False,
        "clear": len(existing) == 0,
        "remaining": existing,
        "actions": [],
    }  # type: Dict[str, Any]
    if not existing:
        return result
    if not allow_clean:
        raise DuplicateTransmitterError(
            "transmitters already running: {}".format(existing)
        )
    pids = [int(p["pid"]) for p in existing]
    # Kill launcher parents (sudo) of workers — never the appliance/test runner.
    for p in existing:
        try:
            ppid_s = (
                subprocess.check_output(
                    ["ps", "-o", "ppid=", "-p", str(p["pid"])],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            ppid = int(ppid_s or "0")
            if ppid <= 1:
                continue
            parent_cmd = (
                subprocess.check_output(
                    ["ps", "-o", "command=", "-p", str(ppid)],
                    stderr=subprocess.DEVNULL,
                )
                .decode()
                .strip()
            )
            parent_base = os.path.basename(parent_cmd.split()[0]) if parent_cmd else ""
            if parent_base in _TX_LAUNCHER_BASENAMES:
                pids.append(ppid)
        except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
            pass
    audit = terminate_pids(pids, use_sudo=use_sudo)
    result["actions"] = audit.get("actions") or []
    # Optional RF pattern sweep — only when sudo kill is enabled (appliance host).
    # Never pkill from unit tests: -f matching is too easy to hit the runner.
    if use_sudo:
        rf_patterns = ("pi_fm_rds", "fm_transmitter")
        for _ in range(2):
            for pat in rf_patterns:
                for sig in ("-TERM", "-KILL"):
                    try:
                        subprocess.call(
                            ["pkill", sig, "-f", pat],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    try:
                        subprocess.call(
                            ["sudo", "-n", "pkill", sig, "-f", pat],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=2,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            time.sleep(0.15)
            invalidate_tx_process_cache()
            result["remaining"] = list_transmitter_processes()
            result["clear"] = len(result["remaining"]) == 0
            if result["clear"]:
                break
    for _ in range(3):
        invalidate_tx_process_cache()
        result["remaining"] = list_transmitter_processes()
        result["clear"] = len(result["remaining"]) == 0
        if result["clear"]:
            break
        more = [int(p["pid"]) for p in result["remaining"]]
        if more:
            terminate_pids(more, use_sudo=use_sudo)
        time.sleep(0.1)
    if not result["clear"]:
        raise DuplicateTransmitterError(
            "failed to clear transmitters: {}".format(result["remaining"])
        )
    result["cleaned"] = True
    result["had"] = existing
    return result


class OwnedTxProcess(object):
    """Spawn + track launcher and worker PIDs under a non-reentrant lock.

    ``spawn()`` must never call ``terminate()`` while holding ``_lock``.
    Duplicate abort releases the lock first, then terminates.
    """

    def __init__(self, use_sudo_kill: bool = True) -> None:
        self.use_sudo_kill = use_sudo_kill
        self.proc = None  # type: Optional[subprocess.Popen]
        self.launcher_pid = None  # type: Optional[int]
        self.worker_pid = None  # type: Optional[int]
        self._lock = threading.Lock()

    def spawn(
        self,
        cmd: List[str],
        stderr_path: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        abort_live = None  # type: Optional[List[Dict[str, Any]]]
        result = None  # type: Optional[Dict[str, Any]]
        with self._lock:
            cleaned = ensure_no_transmitters(
                allow_clean=True, use_sudo=self.use_sudo_kill
            )
            err_fh = open(str(stderr_path), "ab") if stderr_path else subprocess.DEVNULL
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=err_fh,
                    env=env,
                    preexec_fn=None,
                )
            finally:
                if stderr_path is not None:
                    try:
                        err_fh.close()  # type: ignore[union-attr]
                    except Exception:
                        pass
            self.launcher_pid = self.proc.pid
            self.worker_pid = resolve_worker_pid(self.launcher_pid) or self.launcher_pid
            invalidate_tx_process_cache()
            live = list_transmitter_processes()
            result = {
                "launcher_pid": self.launcher_pid,
                "worker_pid": self.worker_pid,
                "cleaned_before": cleaned,
                "live_after": live,
            }
            if len(live) > 1:
                # Do NOT call terminate() while holding _lock (incident deadlock).
                abort_live = live
        if abort_live is not None:
            self.terminate()
            raise DuplicateTransmitterError(
                "spawn created duplicate transmitters: {}".format(abort_live)
            )
        assert result is not None
        return result

    def is_running(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        if self.worker_pid:
            try:
                os.kill(int(self.worker_pid), 0)
                return True
            except OSError:
                return False
        return False

    def terminate(self) -> Dict[str, Any]:
        with self._lock:
            return self._terminate_holding_lock()

    def _terminate_holding_lock(self) -> Dict[str, Any]:
        pids = []  # type: List[int]
        if self.worker_pid:
            pids.append(int(self.worker_pid))
        if self.launcher_pid and self.launcher_pid != self.worker_pid:
            pids.append(int(self.launcher_pid))
        if self.proc is not None and self.proc.pid:
            pids.append(int(self.proc.pid))
        for p in list_transmitter_processes():
            pids.append(int(p["pid"]))
        audit = terminate_pids(pids, use_sudo=self.use_sudo_kill)
        if self.proc is not None:
            try:
                self.proc.wait(timeout=1)
            except Exception:
                pass
        self.proc = None
        self.launcher_pid = None
        self.worker_pid = None
        try:
            ensure_no_transmitters(allow_clean=True, use_sudo=self.use_sudo_kill)
        except DuplicateTransmitterError as exc:
            audit["final_error"] = str(exc)
        audit["clear"] = count_transmitters() == 0
        return audit


def fake_tx_command(marker_file: Optional[str] = None) -> List[str]:
    """Build a non-RF fake transmitter command with recognizable argv0 token."""
    marker = marker_file or ""
    code = (
        "import os,sys,time\n"
        "sys.argv[0]='pifm-fake-tx-hold'\n"
        "m=sys.argv[1] if len(sys.argv)>1 else ''\n"
        "if m:\n"
        "  open(m,'w').write(str(os.getpid()))\n"
        "while True:\n"
        "  time.sleep(0.25)\n"
    )
    cmd = [sys.executable, "-c", code]
    if marker:
        cmd.append(marker)
    return cmd
