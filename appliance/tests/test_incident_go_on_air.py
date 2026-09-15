"""Regression tests for the 2026-09-15 Go On Air deadlock / false-duplicate incident."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appliance.config import Config
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import Library
from appliance.state import State, StateError
from appliance.tx import FakeProcessTxBackend, build_backend
from appliance.tx_process import (
    OwnedTxProcess,
    cmdline_is_tx_worker,
    count_transmitters,
    ensure_no_transmitters,
    fake_tx_command,
    invalidate_tx_process_cache,
    list_transmitter_processes,
)


def _make_ctrl(root: Path, backend: str = "fake") -> Controller:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "library").mkdir(parents=True, exist_ok=True)
    (root / "data" / "playlists").mkdir(parents=True, exist_ok=True)
    cfg_backend = "mock" if backend == "fake" else backend
    cfg_path = root / "config" / "appliance.json"
    cfg_path.write_text(
        json.dumps(
            {
                "tx_backend": cfg_backend,
                "library_dir": "data/library",
                "playlists_dir": "data/playlists",
                "active_playlist": "demo",
                "gpio_enabled": False,
                "rf_quiet_mode": "simulate",
                "software_version": "0.2.9",
                "tx_start_timeout_s": 5,
            }
        )
    )
    audio = root / "data" / "library" / "Artist - Song.wav"
    audio.write_bytes(b"RIFF....WAVE")
    config = Config(cfg_path, root)
    if backend == "fake":
        config._data["tx_backend"] = "fake"
    events = EventLog(persist_path=root / "data" / "logs" / "ships-log.jsonl")
    library = Library(
        config.resolve("library_dir"),
        config.resolve("playlists_dir"),
        root / "data" / "library.sqlite3",
    )
    library.reindex()
    tracks = library.search()
    library.save_playlist("demo", {"name": "demo", "tracks": [tracks[0]["id"]]})
    ctrl = Controller(config, library, events)
    ctrl.tx = build_backend(
        backend,
        str(config.get("pi_fm_rds_path")),
        log_dir=root / "data" / "logs",
        work_dir=root / "data" / "logs" / "wav",
        silence_wav=root / "data" / "audio" / "silence_30s.wav",
    )
    ctrl._tx_start_timeout_s = 5.0
    return ctrl


class IncidentDiscoveryTests(unittest.TestCase):
    def test_01_sudo_launcher_plus_worker_is_one_tx(self):
        """sudo launcher + one worker ≠ duplicate TX."""
        self.assertFalse(
            cmdline_is_tx_worker(
                "sudo /usr/local/bin/pi_fm_rds -freq 89.9 -audio x.wav",
                "pi_fm_rds",
            )
        )
        self.assertTrue(
            cmdline_is_tx_worker(
                "/usr/local/bin/pi_fm_rds -freq 89.9 -audio x.wav",
                "pi_fm_rds",
            )
        )
        # Substring alone must not promote the launcher.
        self.assertFalse(
            cmdline_is_tx_worker(
                "sudo -n /usr/local/bin/pi_fm_rds -freq 1",
                "pi_fm_rds",
            )
        )

    def test_02_two_workers_are_duplicate(self):
        """Actual two workers = duplicate and safely killed."""
        ensure_no_transmitters(allow_clean=True, use_sudo=False)
        a = OwnedTxProcess(use_sudo_kill=False)
        a.spawn(fake_tx_command())
        rogue = subprocess.Popen(
            fake_tx_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.25)
            invalidate_tx_process_cache()
            self.assertGreaterEqual(count_transmitters(), 2)
            # Absolute cleanup must clear both workers.
            ensure_no_transmitters(allow_clean=True, use_sudo=False)
            invalidate_tx_process_cache()
            self.assertEqual(count_transmitters(), 0)
        finally:
            try:
                a.terminate()
            except Exception:
                pass
            try:
                rogue.kill()
                rogue.wait(timeout=2)
            except Exception:
                pass
            ensure_no_transmitters(allow_clean=True, use_sudo=False)


class IncidentStartCancelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctrl = _make_ctrl(self.root, backend="fake")

    def tearDown(self):
        try:
            self.ctrl.tx_off()
        except Exception:
            pass
        ensure_no_transmitters(allow_clean=True, use_sudo=False)
        self.tmp.cleanup()

    def test_03_go_on_air_during_slow_wav_remains_responsive(self):
        self.ctrl.tx.prefetch_delay_s = 1.2
        statuses = []

        def starter():
            self.ctrl.go_on_air()

        def poller():
            t0 = time.time()
            while time.time() - t0 < 3.0:
                statuses.append(self.ctrl.status()["broadcast_ui"])
                time.sleep(0.1)

        t1 = threading.Thread(target=starter)
        t2 = threading.Thread(target=poller)
        t1.start()
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        self.assertTrue(t1.is_alive() is False)
        self.assertIn("STARTING BROADCAST…", statuses)
        self.assertEqual(self.ctrl.sm.state, State.ON_AIR)
        self.ctrl.tx_off()

    def test_04_stop_during_conversion_cancels_startup(self):
        self.ctrl.tx.prefetch_delay_s = 2.0
        err = []

        def starter():
            try:
                self.ctrl.go_on_air()
            except Exception as exc:  # noqa: BLE001
                err.append(exc)

        t = threading.Thread(target=starter)
        t.start()
        time.sleep(0.2)
        st = self.ctrl.status()
        self.assertEqual(st["broadcast_ui"], "STARTING BROADCAST…")
        self.ctrl.tx_off()
        t.join(timeout=10)
        self.assertFalse(t.is_alive())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)
        self.assertEqual(count_transmitters(), 0)
        self.assertIn("OFF", self.ctrl.status()["broadcast_ui"])

    def test_05_stop_immediately_after_worker_spawn_kills_it(self):
        self.ctrl.go_on_air()
        self.assertEqual(count_transmitters(), 1)
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)

    def test_06_double_go_on_air_cannot_create_second_worker(self):
        self.ctrl.go_on_air()
        self.assertEqual(count_transmitters(), 1)
        for _ in range(20):
            self.ctrl.go_on_air()
        invalidate_tx_process_cache()
        self.assertEqual(count_transmitters(), 1)
        self.ctrl.tx_off()

    def test_07_api_remains_responsive_throughout_startup(self):
        self.ctrl.tx.prefetch_delay_s = 1.0
        results = []

        def starter():
            self.ctrl.go_on_air()

        def status_calls():
            for _ in range(8):
                t0 = time.time()
                st = self.ctrl.status()
                results.append((time.time() - t0, st["broadcast_ui"]))
                time.sleep(0.05)

        t1 = threading.Thread(target=starter)
        t2 = threading.Thread(target=status_calls)
        t1.start()
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)
        self.assertTrue(results)
        # Status must not block for the full convert window (~1s+).
        self.assertTrue(
            any(ui == "STARTING BROADCAST…" for _, ui in results),
            results,
        )
        self.assertTrue(
            all(dt < 2.5 for dt, _ in results),
            "status latency too high: {}".format(results),
        )
        self.ctrl.tx_off()

    def test_08_stale_off_plus_real_worker_possible_transmission(self):
        self.ctrl.go_on_air()
        self.assertEqual(self.ctrl.sm.state, State.ON_AIR)
        # Desync controller: pretend READY while OS worker still live.
        self.ctrl.sm.transition(State.READY, "test desync")
        self.ctrl._playing = False
        st = self.ctrl.status()
        self.assertEqual(st["broadcast_ui"], "STATE UNKNOWN / POSSIBLE TRANSMISSION")
        self.assertTrue(st["show_stop_broadcast"])
        self.assertTrue(st["emergency_stop_available"])
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)

    def test_09_no_recursive_lock_deadlock_on_duplicate_spawn(self):
        """spawn must not call terminate while holding the ownership lock."""
        lock = self.ctrl.tx._owned._lock
        self.assertTrue(lock.acquire(blocking=False))
        # Non-reentrant Lock cannot be re-acquired; RLock could.
        self.assertFalse(
            lock.acquire(blocking=False),
            "OwnedTxProcess must use non-reentrant Lock (not RLock)",
        )
        lock.release()
        owned = OwnedTxProcess(use_sudo_kill=False)
        owned.spawn(fake_tx_command())
        rogue = subprocess.Popen(
            fake_tx_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.15)
        invalidate_tx_process_cache()
        other = OwnedTxProcess(use_sudo_kill=False)
        done = {"ok": False}

        def attempt():
            try:
                other.spawn(fake_tx_command())
            except Exception:
                pass
            done["ok"] = True

        t = threading.Thread(target=attempt)
        t.start()
        t.join(timeout=5)
        self.assertFalse(t.is_alive(), "spawn/terminate deadlocked")
        self.assertTrue(done["ok"])
        try:
            other.terminate()
        except Exception:
            pass
        try:
            owned.terminate()
        except Exception:
            pass
        try:
            rogue.kill()
        except Exception:
            pass
        ensure_no_transmitters(allow_clean=True, use_sudo=False)

    def test_10_startup_exception_timeout_safe_recoverable(self):
        self.ctrl.tx.prefetch_fail = True
        with self.assertRaises(StateError):
            self.ctrl.go_on_air()
        self.assertEqual(self.ctrl.sm.state, State.FAULT)
        self.assertEqual(count_transmitters(), 0)
        # Absolute STOP recovers.
        self.ctrl.tx_off()
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)
        self.assertEqual(count_transmitters(), 0)

        # Timeout path
        self.ctrl.sm.reset_to_safe()
        self.ctrl._refresh_ready_unlocked()
        self.ctrl.tx.prefetch_fail = False
        self.ctrl.tx.prefetch_delay_s = 10.0
        self.ctrl._tx_start_timeout_s = 0.4
        with self.assertRaises(StateError):
            self.ctrl.go_on_air()
        self.assertEqual(self.ctrl.sm.state, State.FAULT)
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)


class ConcurrentStartStopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctrl = _make_ctrl(self.root, backend="fake")

    def tearDown(self):
        try:
            self.ctrl.tx_off()
        except Exception:
            pass
        ensure_no_transmitters(allow_clean=True, use_sudo=False)
        self.tmp.cleanup()

    def test_concurrent_status_during_start(self):
        self.ctrl.tx.prefetch_delay_s = 0.8
        with ThreadPoolExecutor(max_workers=4) as pool:
            fut_start = pool.submit(self.ctrl.go_on_air)
            futs = [pool.submit(self.ctrl.status) for _ in range(12)]
            for f in futs:
                st = f.result(timeout=5)
                self.assertIn("broadcast_ui", st)
            fut_start.result(timeout=10)
        self.assertLessEqual(count_transmitters(), 1)
        self.ctrl.tx_off()


if __name__ == "__main__":
    unittest.main()
