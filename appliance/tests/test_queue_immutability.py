"""Read APIs must never mutate the operator queue (shuffle bug regression)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appliance.config import Config
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import Library
from appliance.state import StateError
from appliance.tx import build_backend


def _make_ctrl(root: Path, shuffle: bool = True) -> Controller:
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
                "shuffle": shuffle,
                "repeat": True,
                "gpio_enabled": False,
                "rf_quiet_mode": "simulate",
                "software_version": "0.2.7",
            }
        )
    )
    for name in ("A - One.wav", "B - Two.wav", "C - Three.wav"):
        (root / "data" / "library" / name).write_bytes(b"RIFF....WAVE")
    config = Config(cfg_path, root)
    events = EventLog()
    library = Library(
        config.resolve("library_dir"),
        config.resolve("playlists_dir"),
        root / "data" / "library.sqlite3",
    )
    library.reindex()
    tracks = library.search()
    ids = [t["id"] for t in tracks]
    library.save_playlist("demo", {"name": "Demo Mix", "tracks": ids})
    ctrl = Controller(config, library, events)
    ctrl.tx = build_backend(
        "mock",
        str(config.get("pi_fm_rds_path")),
        log_dir=root / "data" / "logs",
        work_dir=root / "data" / "logs" / "wav",
        silence_wav=root / "data" / "audio" / "silence_30s.wav",
    )
    return ctrl


class QueueImmutabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctrl = _make_ctrl(self.root, shuffle=True)

    def tearDown(self):
        try:
            self.ctrl.tx_off()
        except Exception:
            pass
        self.tmp.cleanup()

    def _fp(self):
        st = self.ctrl.status()
        return (
            tuple(st["queue_fingerprint"]),
            st["queue_index"],
            (st.get("current_track") or {}).get("id"),
            (st.get("next_track") or {}).get("id"),
            (st.get("first_up") or {}).get("id"),
            (st.get("now_playing") or {}).get("id") if st.get("now_playing") else None,
            st["program_state"],
        )

    def test_status_polling_does_not_reshuffle(self):
        baseline = self._fp()
        self.assertTrue(baseline[0], "queue should be loaded")
        for _ in range(40):
            self.ctrl.status()
            self.ctrl.broadcast_checklist()
            self.ctrl.queue_snapshot()
            self.assertEqual(self._fp(), baseline)

    def test_concurrent_reads_do_not_mutate_queue(self):
        baseline = self._fp()

        def reader(_i):
            for _ in range(20):
                self.ctrl.status()
                self.ctrl.broadcast_checklist()
                self.ctrl.queue_snapshot()
            return self._fp()

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = [pool.submit(reader, i) for i in range(8)]
            for f in as_completed(futs):
                self.assertEqual(f.result(), baseline)
        self.assertEqual(self._fp(), baseline)

    def test_shuffle_builds_stable_queue_until_rebuild(self):
        a = self.ctrl.status()["queue_fingerprint"]
        b = self.ctrl.status()["queue_fingerprint"]
        self.assertEqual(a, b)
        # Intentional rebuild via shuffle toggle
        self.ctrl.update_config({"shuffle": True})
        c = self.ctrl.status()["queue_fingerprint"]
        # May or may not equal previous order, but must be stable afterward
        d = self.ctrl.status()["queue_fingerprint"]
        self.assertEqual(c, d)
        self.assertEqual(len(c), 3)

    def test_next_advances_exactly_once(self):
        self.ctrl.update_config({"shuffle": False})
        self.ctrl.play()
        st0 = self.ctrl.status()
        id0 = (st0.get("now_playing") or st0.get("current_track") or {}).get("id")
        st1 = self.ctrl.next_track()
        id1 = (st1.get("now_playing") or st1.get("current_track") or {}).get("id")
        self.assertNotEqual(id0, id1)
        self.assertEqual(st1["queue_index"], st0["queue_index"] + 1)
        fp = tuple(st1["queue_fingerprint"])
        st2 = self.ctrl.status()
        self.assertEqual(tuple(st2["queue_fingerprint"]), fp)
        self.assertEqual(st2["queue_index"], st1["queue_index"])

    def test_previous_deterministic(self):
        self.ctrl.update_config({"shuffle": False})
        self.ctrl.play()
        self.ctrl.next_track()
        mid = self.ctrl.status()["queue_index"]
        self.ctrl.prev_track()
        self.assertEqual(self.ctrl.status()["queue_index"], mid - 1)

    def test_stopped_never_exposes_now_playing(self):
        st = self.ctrl.status()
        self.assertEqual(st["program_state"], "stopped")
        self.assertIsNone(st.get("now_playing"))
        self.assertIsNotNone(st.get("first_up"))

    def test_paused_exposes_now_playing(self):
        self.ctrl.go_on_air()
        st = self.ctrl.pause()
        self.assertEqual(st["program_state"], "paused")
        self.assertEqual(st["broadcast_ui"], "ON AIR")
        self.assertIsNotNone(st.get("now_playing"))
        self.ctrl.tx_off()

    def test_idle_queue_reorder_persists_to_playlist(self):
        self.ctrl.update_config({"shuffle": False})
        original = list(self.ctrl.status()["queue_fingerprint"])
        reordered = list(reversed(original))
        snapshot = self.ctrl.reorder_active_queue(reordered)
        self.assertEqual([track["id"] for track in snapshot], reordered)
        self.assertEqual(
            self.ctrl.library.load_playlist("demo")["tracks"], reordered
        )
        for _ in range(10):
            self.assertEqual(
                self.ctrl.status()["queue_fingerprint"], reordered
            )

    def test_queue_reorder_rejects_missing_tracks_and_on_air(self):
        original = list(self.ctrl.status()["queue_fingerprint"])
        with self.assertRaises(ValueError):
            self.ctrl.reorder_active_queue(original[:-1])
        self.ctrl.go_on_air()
        with self.assertRaises(StateError):
            self.ctrl.reorder_active_queue(list(reversed(original)))
        self.ctrl.tx_off()


if __name__ == "__main__":
    unittest.main()
