"""Fail-safe / race / ownership hardening tests (no RF)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appliance.config import Config
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import Library
from appliance.state import State, StateError
from appliance.tx import FakeProcessTxBackend, MockTxBackend, build_backend
from appliance.tx_process import (
    OwnedTxProcess,
    TransmitterProcessScanError,
    cmdline_is_tx_worker,
    count_transmitters,
    ensure_no_transmitters,
    fake_tx_command,
    invalidate_tx_process_cache,
    list_transmitter_processes,
)


def _make_ctrl(root: Path, backend: str = "mock") -> Controller:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "library").mkdir(parents=True, exist_ok=True)
    (root / "data" / "playlists").mkdir(parents=True, exist_ok=True)
    # Config only allows mock at load; swap backend after construct for fake.
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
                "software_version": "0.2.6",
            }
        )
    )
    audio = root / "data" / "library" / "Artist - Song.wav"
    audio.write_bytes(b"RIFF....WAVE")
    config = Config(cfg_path, root)
    if backend == "fake":
        # Bypass validator for test harness by writing after load.
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
    return ctrl


class OwnershipTests(unittest.TestCase):
    def tearDown(self):
        ensure_no_transmitters(allow_clean=True, use_sudo=False)

    def test_sudo_launcher_not_counted_as_worker(self):
        """Regression: sudo + pi_fm_rds must count as one TX, not two."""
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
        self.assertTrue(
            cmdline_is_tx_worker(
                "python3 -c code pifm-fake-tx-hold",
                "pifm-fake-tx-hold",
            )
        )

    def test_process_table_failure_is_unknown_not_zero(self):
        invalidate_tx_process_cache()
        with patch(
            "appliance.tx_process.subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, ["ps"]),
        ):
            with self.assertRaises(TransmitterProcessScanError):
                list_transmitter_processes()

    def test_owned_spawn_single_and_stop(self):
        owned = OwnedTxProcess(use_sudo_kill=False)
        owned.spawn(fake_tx_command())
        self.assertTrue(owned.is_running())
        self.assertEqual(count_transmitters(), 1)
        audit = owned.terminate()
        self.assertTrue(audit["clear"])
        self.assertEqual(count_transmitters(), 0)

    def test_second_spawn_cleans_first(self):
        a = OwnedTxProcess(use_sudo_kill=False)
        a.spawn(fake_tx_command())
        pid1 = a.worker_pid
        b = OwnedTxProcess(use_sudo_kill=False)
        # spawn() cleans existing before start
        b.spawn(fake_tx_command())
        if a.proc is not None:
            a.proc.wait(timeout=2)
        self.assertEqual(count_transmitters(), 1)
        self.assertNotEqual(b.worker_pid, pid1)
        b.terminate()
        self.assertEqual(count_transmitters(), 0)


class FakeBackendControllerTests(unittest.TestCase):
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

    def test_100_go_on_air_idempotent(self):
        self.ctrl.go_on_air()
        self.assertEqual(count_transmitters(), 1)
        for _ in range(100):
            st = self.ctrl.go_on_air()
            self.assertEqual(st["state"], "ON_AIR")
            self.assertEqual(count_transmitters(), 1)
            self.assertNotEqual(st["state"], "FAULT")
        kinds = [e["kind"] for e in self.ctrl.events.recent(200)]
        self.assertIn("TX_START_IDEMPOTENT", kinds)
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)

    def test_concurrent_go_on_air(self):
        errors = []
        max_seen = [0]

        def hit():
            try:
                self.ctrl.go_on_air()
                max_seen[0] = max(max_seen[0], count_transmitters())
            except StateError as exc:
                # Concurrent starters may be rejected while STARTING — safe.
                if "Already starting" not in str(exc):
                    errors.append(exc)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(hit) for _ in range(20)]
            for f in as_completed(futs):
                f.result()
        self.assertEqual(errors, [])
        self.assertLessEqual(max_seen[0], 1)
        # One starter must win; wait briefly if still finishing prefetch.
        deadline = time.time() + 5
        while time.time() < deadline and self.ctrl.sm.state != State.ON_AIR:
            time.sleep(0.05)
        self.assertEqual(count_transmitters(), 1)
        self.assertEqual(self.ctrl.sm.state, State.ON_AIR)
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)

    def test_repeated_stop(self):
        self.ctrl.go_on_air()
        for _ in range(20):
            st = self.ctrl.tx_off()
            self.assertNotEqual(st["state"], "ON_AIR")
            self.assertEqual(count_transmitters(), 0)

    def test_stop_from_fault(self):
        self.ctrl.go_on_air()
        self.ctrl.sm.enter_fault("injected")
        st = self.ctrl.tx_off()
        self.assertNotEqual(st["state"], "FAULT")
        self.assertEqual(count_transmitters(), 0)
        self.assertEqual(st["broadcast_ui"], "OFF")

    def test_stop_from_paused_and_stopped_program(self):
        self.ctrl.go_on_air()
        self.ctrl.pause()
        self.assertEqual(self.ctrl.status()["program_state"], "paused")
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)
        self.ctrl.go_on_air()
        self.ctrl.stop_playback()
        self.assertEqual(self.ctrl.status()["program_state"], "stopped")
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)

    def test_go_stop_race(self):
        def go():
            for _ in range(15):
                try:
                    self.ctrl.go_on_air()
                except Exception:
                    pass

        def stop():
            for _ in range(15):
                self.ctrl.tx_off()

        t1 = threading.Thread(target=go)
        t2 = threading.Thread(target=stop)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)

    def test_pause_resume_race(self):
        self.ctrl.go_on_air()

        def pause_loop():
            for _ in range(12):
                try:
                    self.ctrl.pause()
                except StateError:
                    # A concurrent transition may deliberately reject overlap.
                    pass

        def resume_loop():
            for _ in range(12):
                try:
                    self.ctrl.play()
                except StateError:
                    # A concurrent transition may deliberately reject overlap.
                    pass

        t1 = threading.Thread(target=pause_loop)
        t2 = threading.Thread(target=resume_loop)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        self.assertLessEqual(count_transmitters(), 1)
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)

    def test_watchdog_no_duplicate(self):
        self.ctrl.go_on_air()
        self.ctrl.pause()
        # Simulate child death while paused.
        self.ctrl.tx.stop()
        # Leave process cleared; watchdog should re-arm silence once, still <=1.
        for _ in range(5):
            self.ctrl.watchdog()
            self.assertLessEqual(count_transmitters(), 1)
        self.assertEqual(self.ctrl.sm.state, State.ON_AIR)
        self.ctrl.tx_off()

    def test_watchdog_detects_duplicate(self):
        self.ctrl.go_on_air()
        # Inject a second fake TX outside OwnedTxProcess (raw Popen) so cleanup
        # does not run — simulates Test #3 orphan/duplicate condition.
        rogue = subprocess.Popen(
            fake_tx_command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.2)
        invalidate_tx_process_cache()
        self.assertGreaterEqual(count_transmitters(), 2)
        self.ctrl.watchdog()
        self.assertEqual(self.ctrl.sm.state, State.FAULT)
        self.ctrl.tx_off()
        self.assertEqual(count_transmitters(), 0)
        try:
            rogue.kill()
        except Exception:
            pass

    def test_watchdog_process_scan_failure_enters_fault(self):
        self.ctrl.go_on_air()
        with patch(
            "appliance.controller.count_transmitters",
            side_effect=TransmitterProcessScanError("ps unavailable"),
        ):
            self.ctrl.watchdog()
        self.assertEqual(self.ctrl.sm.state, State.FAULT)
        self.assertIn("STATE UNKNOWN", self.ctrl.status()["broadcast_ui"])
        self.assertFalse(self.ctrl.tx.is_running())

    def test_watchdog_fault_cannot_interleave_with_flagpole_retune(self):
        self.ctrl.go_on_air()
        update_entered = threading.Event()
        allow_update = threading.Event()
        original_update = self.ctrl._update_config
        errors = []

        def blocked_update(*args, **kwargs):
            update_entered.set()
            self.assertTrue(allow_update.wait(3))
            return original_update(*args, **kwargs)

        def retune():
            try:
                self.ctrl.go_on_air_at_frequency(95.5, wait=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        def scan_for_current_thread():
            if threading.current_thread().name == "scan-failure-watchdog":
                raise TransmitterProcessScanError("ps unavailable")
            return count_transmitters()

        with patch.object(
            self.ctrl, "_update_config", side_effect=blocked_update
        ), patch(
            "appliance.controller.count_transmitters",
            side_effect=scan_for_current_thread,
        ):
            tuner = threading.Thread(target=retune)
            tuner.start()
            self.assertTrue(update_entered.wait(3))
            watcher = threading.Thread(
                target=self.ctrl.watchdog,
                name="scan-failure-watchdog",
            )
            watcher.start()
            time.sleep(0.1)
            self.assertTrue(watcher.is_alive())
            self.assertNotEqual(self.ctrl.sm.state, State.FAULT)
            allow_update.set()
            tuner.join(5)
            watcher.join(5)
        self.assertFalse(tuner.is_alive())
        self.assertFalse(watcher.is_alive())
        self.assertFalse(errors)
        self.assertEqual(self.ctrl.sm.state, State.FAULT)
        self.assertFalse(self.ctrl.tx.is_running())

    def test_stale_pid_and_missing_pid_stop(self):
        self.ctrl.go_on_air()
        # Stale: pretend tracking lost while process still up — stop must sweep.
        lost_process = None
        if hasattr(self.ctrl.tx, "_owned"):
            lost_process = self.ctrl.tx._owned.proc
            self.ctrl.tx._owned.proc = None
            self.ctrl.tx._owned.worker_pid = 999999
            self.ctrl.tx._owned.launcher_pid = 999998
        self.ctrl.tx_off()
        if lost_process is not None:
            lost_process.wait(timeout=2)
        self.assertEqual(count_transmitters(), 0)
        # Missing PID: stop with nothing running.
        st = self.ctrl.tx_off()
        self.assertEqual(st["broadcast_ui"], "OFF")

    def test_ships_log_lifecycle_kinds(self):
        self.ctrl.go_on_air()
        self.ctrl.go_on_air()
        self.ctrl.tx_off()
        kinds = {e["kind"] for e in self.ctrl.events.recent(100)}
        for need in (
            "TX_START_REQUEST",
            "TX_START",
            "TX_START_IDEMPOTENT",
            "TX_STOP_REQUEST",
            "TX_EXIT",
            "STATE_RECONCILE",
        ):
            self.assertIn(need, kinds)
        log = self.root / "data" / "logs" / "ships-log.jsonl"
        self.assertTrue(log.is_file())
        self.assertGreater(log.stat().st_size, 0)

    def test_unknown_when_disagree(self):
        self.ctrl.go_on_air()
        # Force state OFF while process still running → POSSIBLE TRANSMISSION
        self.ctrl.sm._state = State.READY
        st = self.ctrl.status()
        self.assertIn("STATE UNKNOWN", st["broadcast_ui"])
        self.assertIn("POSSIBLE TRANSMISSION", st["broadcast_ui"])
        self.ctrl.tx_off()
        self.assertEqual(self.ctrl.status()["broadcast_ui"], "OFF")


class MockIdempotentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctrl = _make_ctrl(self.root, backend="mock")

    def tearDown(self):
        try:
            self.ctrl.tx_off()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_mock_100_go_on_air(self):
        self.ctrl.go_on_air()
        for _ in range(100):
            st = self.ctrl.go_on_air()
            self.assertEqual(st["state"], "ON_AIR")
            self.assertNotEqual(st["state"], "FAULT")
        self.ctrl.tx_off()


if __name__ == "__main__":
    unittest.main()
