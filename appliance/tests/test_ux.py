"""Operator console UX regression invariants (no RF)."""

from __future__ import annotations

import json
import tempfile
import unittest
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
from appliance.tx import build_backend


STATIC = ROOT / "appliance" / "web" / "static"


class OperatorUxStaticTests(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
        self.css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
        self.broadcast = self.html.split('id="view-broadcast"')[1].split('id="view-music"')[0]
        self.music = self.html.split('id="view-music"')[1].split('id="view-station"')[0]

    def test_five_tab_navigation(self):
        for name in ("Broadcast", "Music", "Station", "System", "Help"):
            self.assertIn(name, self.html)
        self.assertNotIn("How to Broadcast", self.html)

    def test_stop_broadcast_always_in_markup(self):
        self.assertIn('id="btnStopBroadcast"', self.html)
        self.assertIn('id="btnStopBroadcastHeader"', self.html)
        self.assertIn("STOP BROADCAST", self.html)
        self.assertIn("LOWER THE BLACK FLAG", self.html)
        self.assertIn("RAISE THE BLACK FLAG", self.html)
        self.assertIn("never hide/disable Stop", self.js)
        self.assertIn("stopBtn.disabled = false", self.js)
        self.assertIn("stopBtn.hidden = false", self.js)

    def test_practice_mode_removed_from_operator_ui(self):
        self.assertNotIn("PRACTICE — NO FM SIGNAL", self.html)
        self.assertNotIn("PRACTICE — NO FM SIGNAL", self.js)
        self.assertNotIn("Practice mode is on", self.html)
        self.assertNotIn('id="txMode"', self.html)
        # Harness banner exists for mock engineering only; hidden unless mock.
        self.assertIn("dev-harness", self.html)
        self.assertIn("TEST HARNESS", self.html)

    def test_broadcast_deck_has_compact_player_not_legacy_panels(self):
        self.assertIn('data-testid="compact-player"', self.broadcast)
        self.assertIn("player-transport", self.broadcast)
        self.assertNotIn("MUSIC CONTROLS", self.broadcast)
        self.assertNotIn(">PROGRAM<", self.broadcast)
        self.assertNotIn('data-testid="stop-music"', self.broadcast)
        self.assertIn("RAISE THE BLACK FLAG", self.broadcast)
        self.assertIn("LOWER THE BLACK FLAG", self.broadcast)

    def test_program_reset_lives_on_music_page(self):
        self.assertIn('data-testid="stop-music"', self.music)
        self.assertIn("Reset program", self.music)
        self.assertIn(".stop-music", self.css)

    def test_rf_quiet_does_not_stop_broadcast_copy(self):
        self.assertIn("does not</strong> stop the FM broadcast", self.html)
        self.assertIn("Quiet network for interference test", self.html)

    def test_no_playlist_id_field_in_station(self):
        self.assertNotIn("Active playlist id", self.html)
        self.assertNotIn('id="cfgPl"', self.html)

    def test_no_engineering_jargon_on_broadcast_deck(self):
        for banned in ("PID", "pi_fm_rds", "backend", "watchdog", "reconcile", "PRACTICE", "mock"):
            self.assertNotIn(banned, self.broadcast)

    def test_content_agnostic_help(self):
        help_html = self.html.split('id="view-help"')[1]
        self.assertNotIn("Star Wars", help_html)
        self.assertIn("Choose your music", help_html)
        self.assertIn("Raise the Black Flag", help_html)
        self.assertNotIn("Practice Mode", help_html)
        self.assertNotIn("Sandbox", help_html)

    def test_program_labels_ready_now_playing_paused(self):
        self.assertIn("Ready to broadcast", self.js)
        self.assertIn('nowLabel.textContent = "READY"', self.js)
        self.assertIn('nowLabel.textContent = "NOW PLAYING"', self.js)
        self.assertIn('nowLabel.textContent = "PAUSED"', self.js)
        self.assertIn("THEN", self.js)
        self.assertIn("transport-play", self.js)
        self.assertNotIn("NOW PLAYING — PAUSED", self.js)

    def test_short_first_run_flow_exists_and_finishes_off_air(self):
        for label in ("WELCOME", "HARDWARE", "STATION", "MUSIC", "BROADCAST"):
            self.assertIn(label, self.html)
        self.assertIn('id="setupWizard"', self.html)
        self.assertIn("setup_completed: true", self.js)
        self.assertIn("You are OFF AIR", self.js)

    def test_music_workspace_and_multi_upload_controls(self):
        for label in ("Library", "Playlists", "Queue"):
            self.assertIn('data-music-tab="{}"'.format(label.lower()), self.html)
        self.assertIn('id="fileUpload" multiple', self.html)
        self.assertIn("data-upload-zone", self.html)
        self.assertIn("xhr.upload.onprogress", self.js)
        self.assertIn("/api/library/", self.js)
        self.assertIn('id="libraryPlaylistTargets"', self.html)
        self.assertIn("data-playlist-target", self.js)

    def test_hardware_override_is_advanced(self):
        self.assertIn("Advanced — change hardware profile", self.html)
        self.assertIn('value="auto">Detect automatically', self.html)
        self.assertIn("FM output: GPIO ", self.js)


def _make_ctrl(root: Path) -> Controller:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "library").mkdir(parents=True, exist_ok=True)
    (root / "data" / "playlists").mkdir(parents=True, exist_ok=True)
    cfg_path = root / "config" / "appliance.json"
    cfg_path.write_text(
        json.dumps(
            {
                "tx_backend": "mock",
                "library_dir": "data/library",
                "playlists_dir": "data/playlists",
                "active_playlist": "demo",
                "gpio_enabled": False,
                "rf_quiet_mode": "simulate",
                "software_version": "0.3.0",
                "rds_ps": "piFM",
                "rds_rt": "Local appliance",
            }
        )
    )
    audio = root / "data" / "library" / "Artist - Song.wav"
    audio.write_bytes(b"RIFF....WAVE")
    config = Config(cfg_path, root)
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
        "mock",
        str(config.get("pi_fm_rds_path")),
        log_dir=root / "data" / "logs",
        work_dir=root / "data" / "logs" / "wav",
        silence_wav=root / "data" / "audio" / "silence_30s.wav",
    )
    return ctrl


class OperatorBlockerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctrl = _make_ctrl(self.root)

    def tearDown(self):
        try:
            self.ctrl.tx_off()
        except Exception:
            pass
        self.tmp.cleanup()

    def test_blockers_are_actionable_objects(self):
        self.ctrl.config.update({"active_playlist": "", "rds_ps": ""})
        self.ctrl._queue = []
        bc = self.ctrl.broadcast_checklist()
        self.assertFalse(bc["ready"])
        self.assertTrue(bc["blockers"])
        first = bc["blockers"][0]
        self.assertIsInstance(first, dict)
        self.assertIn("message", first)

    def test_fault_blocker_points_to_system(self):
        self.ctrl.sm.enter_fault("unit test")
        bc = self.ctrl.broadcast_checklist()
        self.assertFalse(bc["ready"])
        msgs = " ".join(b["message"] for b in bc["blockers"])
        self.assertIn("System", msgs)

    def test_stop_available_flags_in_status(self):
        st = self.ctrl.status()
        self.assertTrue(st.get("show_stop_broadcast"))
        self.assertTrue(st.get("dev_harness"))
        self.ctrl.sm.enter_fault("ux")
        st2 = self.ctrl.status()
        self.assertEqual(st2.get("broadcast_ui"), "STATE UNKNOWN")
        self.assertTrue(st2.get("show_stop_broadcast", True))

    def test_on_air_requires_running_transmitter(self):
        st = self.ctrl.go_on_air()
        self.assertEqual(st["broadcast_ui"], "ON AIR")
        self.assertTrue(st["tx_running"])
        self.assertIsNotNone(st.get("now_playing"))
        self.ctrl.tx_off()
        st2 = self.ctrl.status()
        self.assertEqual(st2["broadcast_ui"], "OFF")
        self.assertIsNone(st2.get("now_playing"))

    def test_setup_completion_requires_ready_and_never_starts_tx(self):
        self.ctrl.update_config({"setup_completed": False})
        with self.assertRaisesRegex(StateError, "Finish setup"):
            self.ctrl.go_on_air()
        self.assertFalse(self.ctrl.tx.is_running())
        result = self.ctrl.update_setup({"setup_completed": True})
        self.assertTrue(result["setup_completed"])
        self.assertFalse(self.ctrl.tx.is_running())

        self.ctrl.update_config(
            {"setup_completed": False, "active_playlist": ""}
        )
        with self.assertRaises(StateError):
            self.ctrl.update_setup({"setup_completed": True})
        self.assertFalse(self.ctrl.config.get("setup_completed"))
        self.assertFalse(self.ctrl.tx.is_running())

    def test_incomplete_absolute_stop_stays_in_visible_fault(self):
        with patch(
            "appliance.controller.kill_all_transmitters",
            return_value={"clear": False, "actions": [], "remaining": [999]},
        ):
            status = self.ctrl.tx_off()
        self.assertEqual(status["state"], "FAULT")
        self.assertIn("STATE UNKNOWN", status["broadcast_ui"])
        self.assertTrue(status["show_stop_broadcast"])


if __name__ == "__main__":
    unittest.main()
