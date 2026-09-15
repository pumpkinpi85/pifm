"""State-sync / latency / SSE / cache / idempotent pause regressions (non-RF)."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from appliance.config import Config
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import Library
from appliance.state import State
from appliance.tx import prepare_seekable_wav, wav_cache_dir
from appliance.tx_process import ensure_no_transmitters
from appliance.webapp import serve


def _make_ctrl(root: Path, backend: str = "mock") -> Controller:
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
                "software_version": "0.3.1",
                "tx_start_timeout_s": 8,
            }
        )
    )
    # Minimal valid-ish WAV header so inspect_wav may fail; mock never needs real audio.
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
    if backend == "fake":
        from appliance.tx import build_backend

        ctrl.tx = build_backend(
            "fake",
            str(config.get("pi_fm_rds_path")),
            log_dir=root / "data" / "logs",
            work_dir=root / "data" / "logs" / "wav",
            silence_wav=root / "data" / "audio" / "silence_30s.wav",
        )
    return ctrl


class AsyncGoOnAirTests(unittest.TestCase):
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

    def test_go_on_air_wait_false_returns_starting_immediately(self):
        self.ctrl.tx.prefetch_delay_s = 1.0
        t0 = time.time()
        st = self.ctrl.go_on_air(wait=False)
        dt = time.time() - t0
        self.assertLess(dt, 0.75, "HTTP-style go_on_air must not wait for convert")
        self.assertEqual(st["broadcast_ui"], "STARTING BROADCAST…")
        deadline = time.time() + 5
        while time.time() < deadline and self.ctrl.sm.state != State.ON_AIR:
            time.sleep(0.05)
        self.assertEqual(self.ctrl.sm.state, State.ON_AIR)
        self.assertEqual(self.ctrl.status()["broadcast_ui"], "ON AIR")

    def test_stop_during_async_start(self):
        self.ctrl.tx.prefetch_delay_s = 2.0
        self.ctrl.go_on_air(wait=False)
        time.sleep(0.15)
        self.assertEqual(self.ctrl.status()["broadcast_ui"], "STARTING BROADCAST…")
        t0 = time.time()
        self.ctrl.tx_off()
        self.assertLess(time.time() - t0, 5.0)
        self.assertNotEqual(self.ctrl.sm.state, State.ON_AIR)
        self.assertIn("OFF", self.ctrl.status()["broadcast_ui"])


class IdempotentPauseTests(unittest.TestCase):
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

    def test_second_pause_does_not_emit_again(self):
        self.ctrl.go_on_air(wait=True)
        self.ctrl.pause()
        before = [
            e for e in self.ctrl.events.recent(50) if e.get("kind") == "program_paused"
        ]
        self.assertEqual(len(before), 1)
        self.ctrl.pause()
        self.ctrl.pause()
        after = [
            e for e in self.ctrl.events.recent(50) if e.get("kind") == "program_paused"
        ]
        self.assertEqual(len(after), 1)


class EventLogPushTests(unittest.TestCase):
    def test_wait_wakes_on_emit(self):
        ev = EventLog()
        box = {"seq": 0}

        def waiter():
            box["seq"] = ev.wait(after_seq=0, timeout=2.0)

        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)
        ev.emit("ping", "hello")
        t.join(timeout=2)
        self.assertGreater(box["seq"], 0)


class SseEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctrl = _make_ctrl(self.root, backend="mock")
        self.httpd = serve("127.0.0.1", 0, self.ctrl, self.ctrl.library, self.ctrl.events)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.tmp.cleanup()

    def test_sse_sends_initial_status(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/events/stream")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.getheader("Content-Type", "").startswith("text/event-stream"))
        chunk = resp.read(400).decode("utf-8", errors="replace")
        self.assertIn("data:", chunk)
        self.assertIn("status", chunk)
        conn.close()

    def test_tx_on_http_returns_quickly(self):
        # Force slow mock prefetch if available
        if hasattr(self.ctrl.tx, "prefetch_delay_s"):
            self.ctrl.tx.prefetch_delay_s = 1.2
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        t0 = time.time()
        conn.request("POST", "/api/tx/on", body=b"{}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        dt = time.time() - t0
        self.assertEqual(resp.status, 200)
        self.assertLess(dt, 0.75)
        self.assertEqual(body.get("broadcast_ui"), "STARTING BROADCAST…")
        conn.close()
        # Absolute stop must work during start
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/tx/off", body=b"{}", headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        body = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 200)
        self.assertIn("OFF", body.get("broadcast_ui", ""))
        conn.close()


class WavCacheTests(unittest.TestCase):
    def test_second_prepare_is_cache_hit(self):
        import shutil
        import wave
        import struct

        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg not available")
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        work = root / "wav"
        work.mkdir()
        # Build a tiny real WAV then pretend it needs convert by using mp3 path —
        # if no mp3 encoder, create a non-ok wav that still converts via ffmpeg from raw.
        src = root / "tone.wav"
        with wave.open(str(src), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(struct.pack("<" + "h" * 2205, *([0] * 2205)))
        # Force convert path: mono 22050 is not accepted as-is by inspect ok rules
        # (ok requires 44100/48000 stereo) — prepare_seekable_wav will convert.
        a = prepare_seekable_wav(str(src), work)
        self.assertTrue(a.get("wav_path"))
        b = prepare_seekable_wav(str(src), work)
        self.assertTrue(b.get("cache_hit"))
        self.assertEqual(Path(a["wav_path"]).resolve(), Path(b["wav_path"]).resolve())
        self.assertTrue(wav_cache_dir(work).is_dir())
        tmp.cleanup()


class MultiTabStatusTests(unittest.TestCase):
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
        results = []

        def starter():
            self.ctrl.go_on_air(wait=False)

        def poller():
            for _ in range(10):
                t0 = time.time()
                st = self.ctrl.status()
                results.append((time.time() - t0, st["broadcast_ui"]))
                time.sleep(0.05)

        t1 = threading.Thread(target=starter)
        t2 = threading.Thread(target=poller)
        t1.start()
        time.sleep(0.02)
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertTrue(any(ui == "STARTING BROADCAST…" for _, ui in results))
        # Pi A+ ps/reconcile can briefly spike during spawn; keep under 1.5s.
        self.assertTrue(
            all(dt < 1.5 for dt, _ in results),
            "status latency too high: {}".format(results),
        )
        deadline = time.time() + 4
        while time.time() < deadline and self.ctrl.sm.state != State.ON_AIR:
            time.sleep(0.05)
        # Two "tabs" reading status
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.map(lambda _: self.ctrl.status()["broadcast_ui"], range(2))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
