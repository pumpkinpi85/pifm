"""Unit tests for state machine, TX-off invariants, and RF Quiet (no RF)."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appliance.config import (
    FREQ_MAX,
    FREQ_MIN,
    Config,
    ConfigError,
    frequency_units,
    normalize_frequency_mhz,
)
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import Library
from appliance.network import NetworkManager, NetState
from appliance.state import State, StateError, StateMachine
from appliance.tx import PiFmRdsBackend, build_pi_fm_command, prepare_seekable_wav


class StateTests(unittest.TestCase):
    def test_boot_safe(self):
        sm = StateMachine()
        self.assertEqual(sm.state, State.SAFE_OFF)

    def test_illegal_on_air_from_safe_direct(self):
        sm = StateMachine()
        with self.assertRaises(StateError):
            sm.transition(State.ON_AIR, "nope")

    def test_on_air_not_in_defaults_serialization(self):
        # Config never stores ON_AIR
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            path = root / "config" / "appliance.json"
            path.write_text(json.dumps({"tx_backend": "mock", "state": "ON_AIR", "on_air": True}))
            cfg = Config(path, root)
            self.assertNotIn("state", cfg.as_dict())
            self.assertNotIn("on_air", cfg.as_dict())
            self.assertNotEqual(cfg.get("tx_backend"), "ON_AIR")

    def test_pi_fm_rds_ppm_defaults_to_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "config" / "appliance.json"
            path.parent.mkdir()
            path.write_text("{}")
            self.assertEqual(Config(path, root).get("pi_fm_rds_ppm"), 0.0)

    def test_pi_fm_rds_ppm_accepts_positive_and_negative(self):
        for value in (125000, -250.5):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / "config" / "appliance.json"
                path.parent.mkdir()
                path.write_text(json.dumps({"pi_fm_rds_ppm": value}))
                self.assertEqual(Config(path, root).get("pi_fm_rds_ppm"), float(value))

    def test_pi_fm_rds_ppm_rejects_invalid_values(self):
        for value in (
            "not-a-number",
            None,
            True,
            float("inf"),
            10**400,
            -1000000,
            10000001,
        ):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / "config" / "appliance.json"
                path.parent.mkdir()
                path.write_text(json.dumps({"pi_fm_rds_ppm": value}))
                with self.assertRaises(ConfigError):
                    Config(path, root)


class ConfigFrequencyTests(unittest.TestCase):
    def test_frequency_accepts_exact_boundaries_and_integer_tenths(self):
        self.assertEqual(normalize_frequency_mhz(FREQ_MIN), 87.1)
        self.assertEqual(normalize_frequency_mhz(FREQ_MAX), 108.2)
        self.assertEqual(frequency_units("99.9"), 999)

    def test_frequency_rejects_invalid_range_precision_and_non_finite(self):
        for value in (87.0, 108.3, 99.95, True, None, "nan", "inf"):
            with self.subTest(value=value):
                with self.assertRaises(ConfigError):
                    normalize_frequency_mhz(value)

    def test_legacy_off_grid_value_loads_but_new_write_requires_correction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "appliance.json"
            path.write_text(json.dumps({"frequency_mhz": 99.95}))
            config = Config(path, root)
            self.assertEqual(config.get("frequency_mhz"), 99.95)
            with self.assertRaises(ConfigError):
                config.update({"rds_ps": "TEST"})
            config.update({"frequency_mhz": 99.9})
            self.assertEqual(config.get("frequency_mhz"), 99.9)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "data" / "library").mkdir(parents=True)
        (self.root / "data" / "playlists").mkdir(parents=True)
        cfg_path = self.root / "config" / "appliance.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "tx_backend": "mock",
                    "library_dir": "data/library",
                    "playlists_dir": "data/playlists",
                    "active_playlist": "demo",
                    "gpio_enabled": False,
                    "rf_quiet_mode": "simulate",
                    "rf_quiet_seconds": 15,
                }
            )
        )
        audio = self.root / "data" / "library" / "Artist - Song.wav"
        audio.write_bytes(b"RIFF....")
        self.config = Config(cfg_path, self.root)
        self.events = EventLog()
        self.library = Library(
            self.config.resolve("library_dir"),
            self.config.resolve("playlists_dir"),
            self.root / "data" / "library.sqlite3",
        )
        self.library.reindex()
        tracks = self.library.search()
        self.assertTrue(tracks)
        self.library.save_playlist(
            "demo", {"name": "demo", "tracks": [tracks[0]["id"]]}
        )
        self.ctrl = Controller(self.config, self.library, self.events)

    def tearDown(self):
        try:
            self.ctrl.tx_off()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_boot_tx_off(self):
        self.assertIn(self.ctrl.sm.state, (State.SAFE_OFF, State.READY))
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)

    def test_config_change_does_not_start_tx(self):
        self.ctrl.update_config({"frequency_mhz": 95.5})
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)

    def test_invalid_or_noop_config_does_not_stop_on_air(self):
        self.ctrl.go_on_air()
        started_at = self.ctrl.tx._started_at
        with self.assertRaises(ConfigError):
            self.ctrl.update_config({"frequency_mhz": 95.55})
        self.assertTrue(self.ctrl.tx.is_running())
        self.assertEqual(self.ctrl.tx._started_at, started_at)
        self.ctrl.update_config({"frequency_mhz": self.config.get("frequency_mhz")})
        self.assertTrue(self.ctrl.tx.is_running())
        self.assertEqual(self.ctrl.tx._started_at, started_at)

    def test_flagpole_frequency_commit_retunes_through_canonical_lifecycle(self):
        self.ctrl.go_on_air()
        before_revision = self.ctrl.recovery.snapshot()["intent_revision"]
        status = self.ctrl.go_on_air_at_frequency(95.5, wait=True)
        self.assertEqual(status["broadcast_ui"], "ON AIR")
        self.assertEqual(status["frequency_mhz"], 95.5)
        self.assertEqual(status["tx"]["frequency_mhz"], 95.5)
        self.assertTrue(status["broadcast_recovery"]["armed"])
        self.assertNotEqual(
            self.ctrl.recovery.snapshot()["intent_revision"], before_revision
        )
        events = self.events.recent(50)
        kinds = [event["kind"] for event in events]
        self.assertIn("TX_STOP_REQUEST", kinds)
        self.assertIn("frequency_changed", kinds)
        stop_index = kinds.index("TX_STOP_REQUEST")
        frequency_index = kinds.index("frequency_changed")
        restarted_index = kinds.index("TX_START", frequency_index)
        self.assertLess(stop_index, frequency_index)
        self.assertLess(frequency_index, restarted_index)
        self.assertEqual(
            events[stop_index]["source"], "operator_flagpole_retune"
        )
        self.assertEqual(
            events[frequency_index]["source"], "operator_flagpole"
        )
        self.assertEqual(events[restarted_index]["source"], "operator_flagpole")

    def test_ppm_change_does_not_start_tx_and_is_visible(self):
        status = self.ctrl.update_config({"pi_fm_rds_ppm": 125000})
        self.assertEqual(status["pi_fm_rds_ppm"], 125000.0)
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)
        self.assertEqual(self.ctrl.status()["pi_fm_rds_ppm"], 125000.0)

    def test_rds_change_does_not_start_tx(self):
        self.ctrl.update_config({"rds_ps": "PIRATE", "rds_rt": "Test"})
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)

    def test_playlist_change_does_not_start_tx(self):
        self.ctrl.update_config({"active_playlist": "demo"})
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)

    def test_play_pause_next_do_not_start_tx(self):
        st = self.ctrl.play()
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)
        self.assertEqual(st["broadcast_state"], "off")
        self.assertEqual(st["broadcast_ui"], "OFF")
        self.assertEqual(st["program_state"], "playing")
        self.ctrl.next_track()
        self.assertFalse(self.ctrl.tx.is_running())
        st = self.ctrl.pause()
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertEqual(st["broadcast_state"], "off")
        self.assertEqual(st["program_state"], "paused")
        st = self.ctrl.stop_playback()
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertEqual(st["program_state"], "stopped")
        self.assertEqual(st["broadcast_state"], "off")

    def test_explicit_on_air_and_off(self):
        st = self.ctrl.go_on_air()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertTrue(self.ctrl.tx.is_running())
        st = self.ctrl.tx_off()
        self.assertNotEqual(st["state"], "ON_AIR")
        self.assertFalse(self.ctrl.tx.is_running())

    def test_watchdog_fault_no_autorestart(self):
        self.ctrl.go_on_air()
        self.ctrl.tx.stop()
        self.ctrl.watchdog()
        self.assertEqual(self.ctrl.sm.state, State.FAULT)
        self.assertFalse(self.ctrl.tx.is_running())
        # clear does not go on air
        st = self.ctrl.clear_fault()
        self.assertNotEqual(st["state"], "ON_AIR")
        self.assertFalse(self.ctrl.tx.is_running())

    def test_rf_quiet_simulate_never_starts_tx(self):
        with self.assertRaises(ValueError):
            self.ctrl.rf_quiet(confirmed=False)
        st = self.ctrl.rf_quiet(confirmed=True)
        self.assertTrue(st["network"]["rf_quiet_active"])
        self.assertEqual(st["network"]["network_state"], "SIMULATED_RF_QUIET")
        self.assertFalse(self.ctrl.tx.is_running())
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)
        st = self.ctrl.rf_quiet_restore()
        self.assertFalse(st["network"]["rf_quiet_active"])
        self.assertEqual(st["network"]["network_state"], "NETWORK_CONNECTED")
        self.assertFalse(self.ctrl.tx.is_running())

    def test_rf_quiet_orthogonal_to_on_air(self):
        self.ctrl.go_on_air()
        self.assertEqual(self.ctrl.sm.state, State.ON_AIR)
        st = self.ctrl.rf_quiet(confirmed=True)
        self.assertEqual(st["state"], "ON_AIR")
        self.assertTrue(st["network"]["rf_quiet_active"])
        self.ctrl.rf_quiet_restore()
        self.ctrl.tx_off()

    def test_broadcast_checklist_ready_for_playlist_shape(self):
        bc = self.ctrl.broadcast_checklist()
        self.assertTrue(bc["ready"])
        self.assertEqual(bc["broadcast_mode"], "test_harness")
        self.assertTrue(bc["track_count"] >= 1)
        labels = [i["label"] for i in bc["items"]]
        self.assertIn("Playlist selected", labels)

    def test_play_while_on_air_does_not_restart_tx(self):
        self.ctrl.go_on_air()
        self.assertTrue(self.ctrl.tx.is_running())
        meta_before = dict(self.ctrl.tx.status())
        st = self.ctrl.play()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertTrue(self.ctrl.tx.is_running())
        # Mock backend start() would clear/reset uptime if restarted; ensure still running
        self.assertTrue(st["tx_running"])
        self.ctrl.tx_off()

    def test_pause_while_on_air_keeps_broadcast(self):
        self.ctrl.go_on_air()
        st = self.ctrl.pause()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertTrue(self.ctrl.tx.is_running())
        self.assertEqual(st["program_state"], "paused")
        self.assertEqual(st["broadcast_state"], "on_air")
        st = self.ctrl.play()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertEqual(st["program_state"], "playing")
        self.ctrl.tx_off()

    def test_stop_music_while_on_air_keeps_broadcast(self):
        self.ctrl.go_on_air()
        st = self.ctrl.stop_playback()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertTrue(self.ctrl.tx.is_running())
        self.assertEqual(st["program_state"], "stopped")
        self.assertEqual(st["broadcast_state"], "on_air")
        st = self.ctrl.play()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertEqual(st["program_state"], "playing")
        self.assertEqual(self.ctrl.tx.status().get("program"), "audio")
        self.ctrl.tx_off()

    def test_go_on_air_idempotent(self):
        self.ctrl.go_on_air()
        st = self.ctrl.go_on_air()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertTrue(self.ctrl.tx.is_running())
        kinds = [e["kind"] for e in self.events.recent(30)]
        self.assertTrue(
            any(e.get("kind") == "TX_START_IDEMPOTENT" for e in self.events.recent(30))
        )
        self.ctrl.tx_off()
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)

    def test_stop_broadcast_clears_fault(self):
        self.ctrl.go_on_air()
        self.ctrl.sm.enter_fault("simulated")
        st = self.ctrl.tx_off()
        self.assertNotEqual(st["state"], "FAULT")
        self.assertFalse(st["tx_running"])
        self.ctrl.go_on_air()
        self.ctrl.pause()
        time.sleep(2.0)
        for _ in range(4):
            self.ctrl.watchdog()
            time.sleep(0.2)
        st = self.ctrl.status()
        self.assertEqual(st["broadcast_state"], "on_air")
        self.assertEqual(st["program_state"], "paused")
        self.assertTrue(st["tx_running"])
        self.ctrl.tx_off()

    def test_next_prev_on_air_controlled_restart(self):
        # Need two tracks
        tracks = self.library.search()
        audio2 = self.root / "data" / "library" / "Artist - Song2.wav"
        audio2.write_bytes(b"RIFF....")
        self.library.reindex()
        tracks = self.library.search()
        self.library.save_playlist(
            "demo",
            {"name": "demo", "tracks": [t["id"] for t in tracks[:2]]},
        )
        self.ctrl.update_config({"active_playlist": "demo"})
        self.ctrl.go_on_air()
        st = self.ctrl.next_track()
        self.assertEqual(st["state"], "ON_AIR")
        self.assertTrue(st["tx_running"])
        kinds = [e["kind"] for e in self.events.recent(20)]
        self.assertIn("tx_track_change", kinds)
        st = self.ctrl.prev_track()
        self.assertEqual(st["state"], "ON_AIR")
        self.ctrl.tx_off()


class TxPipelineTests(unittest.TestCase):
    def test_build_command_rejects_stdin(self):
        with self.assertRaises(ValueError):
            build_pi_fm_command("/bin/true", 89.9, "-", "piFM", "RT", "1234")

    def test_build_command_always_passes_default_zero_ppm(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            wav.write_bytes(b"RIFF")
            cmd = build_pi_fm_command(
                "/bin/true", 89.9, str(wav), "piFM", "RT", "1234"
            )
            self.assertEqual(cmd[cmd.index("-ppm") + 1], "0.0")
            self.assertLess(cmd.index("-ppm"), cmd.index("-audio"))

    def test_build_command_passes_positive_and_negative_ppm(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "audio.wav"
            wav.write_bytes(b"RIFF")
            for ppm in (1000000.0, -125.25):
                with self.subTest(ppm=ppm):
                    cmd = build_pi_fm_command(
                        "/bin/true",
                        89.9,
                        str(wav),
                        "piFM",
                        "RT",
                        "1234",
                        ppm,
                    )
                    self.assertEqual(cmd[cmd.index("-ppm") + 1], str(ppm))

    def test_pi_fm_rds_status_exposes_applied_ppm_before_start(self):
        backend = PiFmRdsBackend("/bin/true", ppm=-125.25)
        status = backend.status()
        self.assertEqual(status["pi_fm_rds_ppm"], -125.25)
        self.assertFalse(status["running"])

    def test_prepare_failure_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "wav"
            work.mkdir()
            missing = Path(tmp) / "no such file with spaces.mp3"
            with self.assertRaises((FileNotFoundError, RuntimeError)):
                prepare_seekable_wav(str(missing), work)
            self.assertEqual(list(work.glob("pifm-*.wav")), [])

    def test_event_log_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ships-log.jsonl"
            ev = EventLog(persist_path=path)
            ev.emit("test", "hello", n=1)
            lines = path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 1)
            row = json.loads(lines[0])
            self.assertEqual(row["kind"], "test")
            self.assertEqual(row["message"], "hello")


class NetworkManagerTests(unittest.TestCase):
    def test_simulate_requires_confirm(self):
        ev = EventLog()
        nm = NetworkManager(ev, mode="simulate", recovery_seconds=15)
        with self.assertRaises(ValueError):
            nm.enter_quiet(confirmed=False)

    def test_simulate_enter_exit(self):
        ev = EventLog()
        nm = NetworkManager(ev, mode="simulate", recovery_seconds=15)
        st = nm.enter_quiet(confirmed=True)
        self.assertEqual(st["network_state"], NetState.SIMULATED_RF_QUIET.value)
        nm.force_restore()
        self.assertEqual(nm.state, NetState.NETWORK_CONNECTED)


class LibraryTests(unittest.TestCase):
    def test_empty_and_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "lib"
            pls = root / "pl"
            lib.mkdir()
            pls.mkdir()
            (lib / "junk.txt").write_text("nope")
            (lib / "ok.wav").write_bytes(b"RIFF")
            L = Library(lib, pls, root / "db.sqlite3")
            n = L.reindex()
            self.assertEqual(n, 1)
            self.assertEqual(len(L.search()), 1)
            L.save_playlist("empty", {"name": "empty", "tracks": []})
            self.assertEqual(L.load_playlist("empty")["tracks"], [])


if __name__ == "__main__":
    unittest.main()
