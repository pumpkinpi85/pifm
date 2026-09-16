"""P1G standalone-appliance resilience tests. All transmitter paths are non-RF."""

from __future__ import annotations

import errno
import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from appliance.__main__ import acquire_controller_lease
from appliance.config import Config
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import Library
from appliance.media_import import MediaImportError, import_media_stream
from appliance.recovery import BroadcastIntentStore, RecoveryStateError
from appliance.state import State
from appliance.tx import prepare_seekable_wav


def make_controller(root: Path, track_count: int = 2) -> Controller:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data/library").mkdir(parents=True, exist_ok=True)
    (root / "data/playlists").mkdir(parents=True, exist_ok=True)
    config_path = root / "config/appliance.json"
    config_path.write_text(
        json.dumps(
            {
                "tx_backend": "mock",
                "library_dir": "data/library",
                "playlists_dir": "data/playlists",
                "active_playlist": "demo",
                "setup_completed": True,
                "repeat": True,
                "recovery_stability_s": 0,
            }
        )
    )
    for index in range(track_count):
        (root / "data/library/Track-{}.wav".format(index)).write_bytes(
            b"RIFF....WAVE"
        )
    config = Config(config_path, root)
    library = Library(
        config.resolve("library_dir"),
        config.resolve("playlists_dir"),
        root / "data/library.sqlite3",
    )
    library.reindex()
    tracks = library.search()
    library.save_playlist(
        "demo",
        {"name": "Demo", "tracks": [track["id"] for track in tracks]},
    )
    return Controller(
        config,
        library,
        EventLog(persist_path=root / "data/logs/ships-log.jsonl"),
    )


def recovery_snapshot(program_state: str = "playing"):
    return {
        "program_state": program_state,
        "active_playlist": "demo",
        "queue": ["track-a", "track-b"],
        "current_track_id": "track-a",
    }


class BroadcastIntentStoreTests(unittest.TestCase):
    def test_first_boot_is_off(self):
        with tempfile.TemporaryDirectory() as td:
            store = BroadcastIntentStore(Path(td))
            self.assertFalse(store.status()["armed"])
            self.assertEqual(store.status()["desired_broadcast"], "off")
            state, reason = store.begin_restore("boot-a")
            self.assertIsNone(state)
            self.assertIn("not armed", reason)

    def test_on_intent_survives_new_store_instance(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            BroadcastIntentStore(root).arm(recovery_snapshot(), "operator")
            restored = BroadcastIntentStore(root)
            self.assertTrue(restored.status()["armed"])
            self.assertEqual(restored.snapshot()["current_track_id"], "track-a")

    def test_stop_removes_on_marker_before_shutdown(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = BroadcastIntentStore(root)
            store.arm(recovery_snapshot(), "operator")
            self.assertTrue(store.disarm())
            self.assertFalse((root / "data/recovery/broadcast-on.json").exists())
            self.assertFalse(BroadcastIntentStore(root).status()["armed"])

    def test_corrupt_marker_fails_off(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "data/recovery/broadcast-on.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{not-json")
            store = BroadcastIntentStore(root)
            self.assertFalse(store.status()["armed"])
            self.assertFalse(store.status()["valid"])
            state, reason = store.begin_restore("boot-a")
            self.assertIsNone(state)
            self.assertIn("invalid persisted", reason)

    def test_failed_restore_is_latched_for_same_boot_only(self):
        with tempfile.TemporaryDirectory() as td:
            store = BroadcastIntentStore(Path(td))
            store.arm(recovery_snapshot(), "operator")
            state, reason = store.begin_restore("boot-a")
            self.assertIsNotNone(state)
            self.assertIsNone(reason)
            store.finish_restore("refused", "missing media")
            state, reason = store.begin_restore("boot-a")
            self.assertIsNone(state)
            self.assertIn("already attempted", reason)
            state, reason = store.begin_restore("boot-b")
            self.assertIsNotNone(state)
            self.assertIsNone(reason)

    def test_successful_service_restart_can_restore_again_same_boot(self):
        with tempfile.TemporaryDirectory() as td:
            store = BroadcastIntentStore(Path(td))
            store.arm(recovery_snapshot(), "operator")
            store.begin_restore("boot-a")
            store.finish_restore("restored")
            state, reason = store.begin_restore("boot-a")
            self.assertIsNotNone(state)
            self.assertIsNone(reason)

    def test_disk_full_cannot_partially_replace_valid_on_marker(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = BroadcastIntentStore(root)
            original = store.arm(recovery_snapshot(), "operator")
            marker = root / "data/recovery/broadcast-on.json"
            before = marker.read_bytes()
            with patch(
                "appliance.recovery.os.replace",
                side_effect=OSError(errno.ENOSPC, "disk full"),
            ):
                self.assertFalse(
                    store.update_program(recovery_snapshot("paused"))
                )
            self.assertEqual(marker.read_bytes(), before)
            self.assertEqual(
                BroadcastIntentStore(root).snapshot()["program_state"],
                original["program_state"],
            )

    def test_directory_fsync_failure_after_replace_fails_off(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = BroadcastIntentStore(root)
            with patch.object(
                store,
                "_fsync_directory",
                side_effect=OSError(errno.EIO, "fsync failed"),
            ):
                with self.assertRaises(RecoveryStateError):
                    store.arm(recovery_snapshot(), "operator")
            self.assertFalse(
                (root / "data/recovery/broadcast-on.json").exists()
            )
            self.assertFalse(store.status()["armed"])

    def test_unwritable_stop_reports_failure_without_hiding_on_intent(self):
        with tempfile.TemporaryDirectory() as td:
            store = BroadcastIntentStore(Path(td))
            store.arm(recovery_snapshot(), "operator")
            with patch.object(
                Path, "unlink", side_effect=OSError(errno.EROFS, "read only")
            ):
                with self.assertRaises(RecoveryStateError):
                    store.disarm()
            self.assertTrue(store.status()["armed"])


class ControllerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.controller = make_controller(self.root)

    def tearDown(self):
        try:
            self.controller.tx_off()
        except Exception:
            pass
        self.temp.cleanup()

    def test_first_boot_restore_remains_off(self):
        status = self.controller.restore_persisted_broadcast_intent(
            wait=True, boot_id="boot-a"
        )
        self.assertEqual(status["broadcast_state"], "off")
        self.assertFalse(status["tx_running"])

    def test_on_intent_restores_after_service_restart(self):
        self.controller.go_on_air()
        self.assertTrue(self.controller.recovery.status()["armed"])
        self.controller.service_shutdown()
        self.assertTrue(self.controller.recovery.status()["armed"])
        replacement = make_controller(self.root)
        self.controller = replacement
        status = replacement.restore_persisted_broadcast_intent(
            wait=True, boot_id="boot-a"
        )
        self.assertEqual(status["broadcast_state"], "on_air")
        self.assertTrue(status["tx_running"])
        self.assertEqual(
            status["broadcast_recovery"]["last_restore_result"], "restored"
        )

    def test_explicit_stop_persists_off_across_restart(self):
        self.controller.go_on_air()
        self.controller.tx_off()
        self.assertFalse(self.controller.recovery.status()["armed"])
        replacement = make_controller(self.root)
        self.controller = replacement
        status = replacement.restore_persisted_broadcast_intent(
            wait=True, boot_id="boot-a"
        )
        self.assertEqual(status["broadcast_state"], "off")
        self.assertFalse(status["tx_running"])

    def test_explicit_stop_disarms_before_backend_stop(self):
        self.controller.go_on_air()
        marker = self.root / "data/recovery/broadcast-on.json"
        original_stop = self.controller.tx.stop

        def assert_disarmed_then_stop():
            self.assertFalse(marker.exists())
            original_stop()

        with patch.object(
            self.controller.tx,
            "stop",
            side_effect=assert_disarmed_then_stop,
        ):
            self.controller.tx_off()
        self.assertFalse(marker.exists())

    def test_missing_media_refuses_restore_and_latches_attempt(self):
        self.controller.go_on_air()
        self.controller.service_shutdown()
        for path in (self.root / "data/library").glob("*.wav"):
            path.unlink()
        replacement = make_controller(self.root, track_count=0)
        self.controller = replacement
        status = replacement.restore_persisted_broadcast_intent(
            wait=True, boot_id="boot-a"
        )
        self.assertNotEqual(status["broadcast_state"], "on_air")
        self.assertFalse(status["tx_running"])
        self.assertEqual(
            status["broadcast_recovery"]["last_restore_result"], "refused"
        )
        second = replacement.restore_persisted_broadcast_intent(
            wait=True, boot_id="boot-a"
        )
        self.assertFalse(second["tx_running"])

    def test_paused_program_restores_with_broadcast_silence(self):
        self.controller.go_on_air()
        self.controller.pause()
        self.controller.service_shutdown()
        replacement = make_controller(self.root)
        self.controller = replacement
        status = replacement.restore_persisted_broadcast_intent(
            wait=True, boot_id="boot-a"
        )
        self.assertEqual(status["broadcast_state"], "on_air")
        self.assertEqual(status["program_state"], "paused")
        self.assertEqual(status["tx"]["program"], "silence")

    def test_repeat_disabled_ends_program_but_keeps_broadcast_intent(self):
        self.controller.update_config({"repeat": False})
        self.controller.go_on_air()
        self.controller.next_track()
        status = self.controller.next_track()
        self.assertEqual(status["broadcast_state"], "on_air")
        self.assertEqual(status["program_state"], "stopped")
        self.assertTrue(status["tx_running"])
        self.assertTrue(status["broadcast_recovery"]["armed"])
        self.assertEqual(status["tx"]["program"], "silence")

    def test_watchdog_advances_completed_track_without_network(self):
        self.controller.go_on_air()
        original = self.controller.status()["queue_index"]
        self.controller._track_started_monotonic = time.monotonic() - 2
        self.controller._track_duration_s = 1
        with patch.object(
            self.controller.network,
            "status",
            side_effect=AssertionError("network must not drive playback"),
        ):
            self.controller.watchdog()
        deadline = time.time() + 2
        while (
            time.time() < deadline
            and self.controller._program_pending is not None
        ):
            time.sleep(0.01)
        self.assertNotEqual(self.controller._queue_index, original)
        self.assertEqual(self.controller.sm.state, State.ON_AIR)

    def test_bad_next_track_holds_silence_without_retry_loop(self):
        self.controller.go_on_air()
        self.controller.tx.prefetch_fail = True
        status = self.controller.next_track()
        self.assertEqual(status["broadcast_state"], "on_air")
        self.assertEqual(status["program_state"], "stopped")
        self.assertEqual(status["tx"]["program"], "silence")
        failures = [
            event
            for event in self.controller.events.recent(50)
            if event["kind"] == "MEDIA_TRACK_FAILED"
        ]
        self.assertEqual(len(failures), 1)

    def test_duplicate_restore_requests_remain_single_start(self):
        self.controller.go_on_air()
        self.controller.service_shutdown()
        replacement = make_controller(self.root)
        self.controller = replacement
        original_start = replacement.tx.start
        starts = []

        def counted_start(*args, **kwargs):
            starts.append(1)
            return original_start(*args, **kwargs)

        with patch.object(replacement.tx, "start", side_effect=counted_start):
            replacement.restore_persisted_broadcast_intent(
                wait=True, boot_id="boot-a"
            )
            replacement.restore_persisted_broadcast_intent(
                wait=True, boot_id="boot-a"
            )
        self.assertEqual(len(starts), 1)
        self.assertTrue(replacement.tx.is_running())

    def test_stop_is_barrier_against_admitted_start(self):
        start_entered = threading.Event()
        allow_start = threading.Event()
        stop_returned = threading.Event()
        original_start = self.controller.tx.start

        def blocked_start(*args, **kwargs):
            start_entered.set()
            self.assertTrue(allow_start.wait(3))
            return original_start(*args, **kwargs)

        with patch.object(
            self.controller.tx, "start", side_effect=blocked_start
        ):
            starter = threading.Thread(target=self.controller.go_on_air)
            starter.start()
            self.assertTrue(start_entered.wait(3))

            def stop():
                self.controller.tx_off()
                stop_returned.set()

            stopper = threading.Thread(target=stop)
            stopper.start()
            time.sleep(0.1)
            self.assertFalse(
                stop_returned.is_set(),
                "STOP returned while an admitted start was unresolved",
            )
            allow_start.set()
            starter.join(3)
            stopper.join(3)
        self.assertTrue(stop_returned.is_set())
        self.assertFalse(self.controller.tx.is_running())
        self.assertFalse(self.controller.recovery.status()["armed"])

    def test_restore_snapshot_cannot_start_after_operator_stop(self):
        self.controller.go_on_air()
        self.controller.service_shutdown()
        replacement = make_controller(self.root)
        self.controller = replacement
        restore_entered = threading.Event()
        continue_restore = threading.Event()
        restore_finished = threading.Event()
        original_go_on_air = replacement.go_on_air

        def delayed_restore(*args, **kwargs):
            restore_entered.set()
            self.assertTrue(continue_restore.wait(3))
            try:
                return original_go_on_air(*args, **kwargs)
            finally:
                restore_finished.set()

        with patch.object(
            replacement, "go_on_air", side_effect=delayed_restore
        ):
            replacement.restore_persisted_broadcast_intent(
                wait=False, boot_id="boot-a"
            )
            self.assertTrue(restore_entered.wait(3))
            stopped = replacement.tx_off()
            self.assertEqual(stopped["broadcast_state"], "off")
            continue_restore.set()
            self.assertTrue(restore_finished.wait(3))
        self.assertFalse(replacement.tx.is_running())
        self.assertFalse(replacement.recovery.status()["armed"])

    def test_unstable_restore_is_latched_for_same_boot(self):
        self.controller.go_on_air()
        self.controller.service_shutdown()
        replacement = make_controller(self.root)
        self.controller = replacement
        replacement._recovery_stability_s = 0.3
        replacement.restore_persisted_broadcast_intent(
            wait=False, boot_id="boot-a"
        )
        deadline = time.time() + 2
        while time.time() < deadline and replacement.sm.state != State.ON_AIR:
            time.sleep(0.01)
        self.assertEqual(replacement.sm.state, State.ON_AIR)
        replacement.begin_shutdown()
        deadline = time.time() + 2
        while (
            time.time() < deadline
            and replacement.recovery.status()["last_restore_result"]
            == "pending"
        ):
            time.sleep(0.01)
        self.assertEqual(
            replacement.recovery.status()["last_restore_result"], "failed"
        )
        replacement.service_shutdown()
        second = make_controller(self.root)
        self.controller = second
        status = second.restore_persisted_broadcast_intent(
            wait=True, boot_id="boot-a"
        )
        self.assertEqual(status["broadcast_state"], "off")
        self.assertFalse(status["tx_running"])

    def test_systemd_service_does_not_wait_for_network(self):
        service = (
            Path(__file__).resolve().parents[2]
            / "systemd/pifm-appliance.service"
        ).read_text()
        self.assertNotIn("network-online.target", service)
        self.assertIn("After=local-fs.target", service)
        self.assertIn("StartLimitBurst=5", service)


class StorageFailureTests(unittest.TestCase):
    def test_disk_full_upload_preserves_existing_media(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            library = Library(
                root / "library",
                root / "playlists",
                root / "library.sqlite3",
            )
            library.library_dir.mkdir(parents=True, exist_ok=True)
            existing = library.library_dir / "Existing.wav"
            existing.write_bytes(b"existing")
            with patch.object(
                Path,
                "open",
                side_effect=OSError(errno.ENOSPC, "disk full"),
            ):
                with self.assertRaisesRegex(MediaImportError, "Storage is full"):
                    import_media_stream(
                        library,
                        io.BytesIO(b"new-audio"),
                        "New.wav",
                        9,
                    )
            self.assertEqual(existing.read_bytes(), b"existing")

    def test_failed_ffmpeg_conversion_preserves_source_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.wav"
            source.write_bytes(b"not-a-valid-wav")
            work = root / "work"
            with patch(
                "appliance.tx.subprocess.check_call",
                side_effect=OSError(errno.ENOSPC, "disk full"),
            ):
                with self.assertRaisesRegex(RuntimeError, "ffmpeg failed"):
                    prepare_seekable_wav(str(source), work)
            self.assertEqual(source.read_bytes(), b"not-a-valid-wav")
            self.assertEqual(list(work.glob("pifm-*.wav")), [])

    def test_unwritable_event_log_degrades_to_memory(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "blocked/log.jsonl"
            with patch.object(
                Path,
                "mkdir",
                side_effect=OSError(errno.EROFS, "read only"),
            ):
                events = EventLog(persist_path=path)
            events.emit("test", "still running")
            self.assertEqual(events.recent(1)[0]["kind"], "test")
            self.assertIsNotNone(events.persistence_error)

    def test_failed_atomic_config_write_restores_in_memory_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "appliance.json"
            path.write_text(json.dumps({"tx_backend": "mock"}))
            config = Config(path, root)
            previous = config.get("frequency_mhz")
            with patch(
                "appliance.config.os.replace",
                side_effect=OSError(errno.ENOSPC, "disk full"),
            ):
                with self.assertRaises(OSError):
                    config.update({"frequency_mhz": 99.1})
            self.assertEqual(config.get("frequency_mhz"), previous)
            self.assertNotIn("99.1", path.read_text())


class ControllerLeaseTests(unittest.TestCase):
    def test_second_controller_process_lease_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = acquire_controller_lease(root)
            try:
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    acquire_controller_lease(root)
            finally:
                first.close()


if __name__ == "__main__":
    unittest.main()
