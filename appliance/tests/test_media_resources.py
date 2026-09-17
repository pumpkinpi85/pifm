"""Bounded WAV cache and specifically-owned FFmpeg lifecycle regressions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from appliance.config import Config, ConfigError
from appliance.tx import (
    ConversionResourceError,
    OwnedFfmpegProcess,
    PiFmRdsBackend,
    enforce_wav_cache_budget,
    prepare_seekable_wav,
    probe_audio_resources,
)
from appliance.tx_process import StartCancelled


class WavResourcePredictionTests(unittest.TestCase):
    def test_cache_resource_defaults_and_validation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = Config(root / "config.json", root)
            self.assertEqual(config.get("wav_cache_max_mb"), 1024)
            self.assertEqual(config.get("wav_cache_min_free_mb"), 256)
            self.assertFalse(config.get("cache_warming_enabled"))
            for patch_value in (
                {"wav_cache_max_mb": -1},
                {"wav_cache_min_free_mb": True},
                {"cache_warming_enabled": "yes"},
            ):
                with self.assertRaises(ConfigError):
                    config.validate_update(patch_value)

    def test_probe_projects_fixed_pcm16_stereo_output(self):
        payload = {
            "streams": [
                {"sample_rate": "48000", "channels": 1, "duration": "12.5"}
            ],
            "format": {"duration": "12.5"},
        }
        completed = subprocess.CompletedProcess(
            ["ffprobe"], 0, stdout=json.dumps(payload).encode("utf-8"), stderr=b""
        )
        with patch("appliance.tx.shutil.which", return_value="/usr/bin/ffprobe"), patch(
            "appliance.tx.subprocess.run", return_value=completed
        ):
            resources = probe_audio_resources("/music/test.m4a")
        self.assertEqual(resources["duration_s"], 12.5)
        self.assertEqual(resources["sample_rate"], 48000)
        self.assertEqual(resources["channels"], 1)
        self.assertEqual(
            resources["projected_pcm_bytes"],
            int(12.5 * 44100 * 2 * 2) + 4096,
        )

    def test_projected_output_larger_than_budget_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            with self.assertRaisesRegex(ConversionResourceError, "cache budget"):
                enforce_wav_cache_budget(
                    cache,
                    projected_bytes=11 * 1024 * 1024,
                    cache_budget_bytes=10 * 1024 * 1024,
                    min_free_bytes=0,
                )

    def test_lru_eviction_is_deterministic_and_preserves_active_wav(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td)
            oldest = cache / "oldest.wav"
            newer = cache / "newer.wav"
            active = cache / "active.wav"
            for path in (oldest, newer, active):
                path.write_bytes(b"x" * 100)
            os.utime(str(oldest), (10, 10))
            os.utime(str(newer), (20, 20))
            os.utime(str(active), (1, 1))
            result = enforce_wav_cache_budget(
                cache,
                projected_bytes=150,
                cache_budget_bytes=350,
                min_free_bytes=0,
                protected_paths=[str(active)],
            )
            self.assertFalse(oldest.exists())
            self.assertTrue(newer.exists())
            self.assertTrue(active.exists())
            self.assertEqual(
                [Path(row["path"]).name for row in result["evicted"]],
                ["oldest.wav"],
            )

    def test_low_disk_rejects_when_disposable_cache_cannot_make_room(self):
        usage = type("DiskUsage", (), {"free": 50, "used": 0, "total": 50})()
        with tempfile.TemporaryDirectory() as td, patch(
            "appliance.tx.shutil.disk_usage", return_value=usage
        ):
            with self.assertRaisesRegex(ConversionResourceError, "free disk"):
                enforce_wav_cache_budget(
                    Path(td),
                    projected_bytes=100,
                    cache_budget_bytes=1000,
                    min_free_bytes=100,
                )


class OwnedFfmpegLifecycleTests(unittest.TestCase):
    def test_backend_stop_cancels_owned_conversion_process(self):
        with tempfile.TemporaryDirectory() as td:
            backend = PiFmRdsBackend(
                "/unused/pi_fm_rds",
                work_dir=Path(td) / "wav",
            )
            errors = []

            def run() -> None:
                try:
                    backend._conversion.run(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        timeout_s=10,
                    )
                except BaseException as exc:  # test captures worker exception
                    errors.append(exc)

            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.time() + 3
            while backend._conversion.pid is None and time.time() < deadline:
                time.sleep(0.02)
            owned_pid = backend._conversion.pid
            self.assertIsNotNone(owned_pid)
            backend.stop()
            worker.join(timeout=3)

            self.assertFalse(worker.is_alive())
            self.assertEqual(backend._conversion.pid, None)
            self.assertTrue(errors)
            with self.assertRaises(OSError):
                os.kill(int(owned_pid), 0)

    def test_cancel_terminates_and_reaps_only_owned_child(self):
        owner = OwnedFfmpegProcess(term_wait_s=0.2, kill_wait_s=0.2)
        cancel = threading.Event()
        errors = []

        def run() -> None:
            try:
                owner.run(
                    [sys.executable, "-c", "import time; time.sleep(30)"],
                    should_cancel=cancel.is_set,
                    timeout_s=10,
                )
            except BaseException as exc:  # test captures worker exception
                errors.append(exc)

        unrelated = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"]
        )
        worker = threading.Thread(target=run)
        worker.start()
        deadline = time.time() + 3
        while owner.pid is None and time.time() < deadline:
            time.sleep(0.02)
        owned_pid = owner.pid
        self.assertIsNotNone(owned_pid)
        cancel.set()
        worker.join(timeout=3)
        try:
            self.assertFalse(worker.is_alive())
            self.assertEqual(owner.pid, None)
            self.assertTrue(any(isinstance(exc, StartCancelled) for exc in errors))
            with self.assertRaises(OSError):
                os.kill(int(owned_pid), 0)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=3)

    def test_cancelled_conversion_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.mp3"
            source.write_bytes(b"source-is-preserved")
            work = root / "work"
            resources = {
                "duration_s": 1.0,
                "sample_rate": 44100,
                "channels": 2,
                "output_sample_rate": 44100,
                "output_channels": 2,
                "output_sample_width": 2,
                "projected_pcm_bytes": 180000,
            }

            def cancel_after_partial(command, should_cancel=None, timeout_s=600):
                Path(command[-1]).write_bytes(b"partial")
                raise StartCancelled("cancelled by test")

            owner = OwnedFfmpegProcess()
            with patch("appliance.tx.probe_audio_resources", return_value=resources), patch.object(
                owner, "run", side_effect=cancel_after_partial
            ):
                with self.assertRaises(StartCancelled):
                    prepare_seekable_wav(
                        str(source),
                        work,
                        cache_budget_bytes=1024 * 1024,
                        min_free_bytes=0,
                        process_owner=owner,
                    )
            self.assertEqual(source.read_bytes(), b"source-is-preserved")
            self.assertEqual(list(work.glob("pifm-*.wav")), [])


if __name__ == "__main__":
    unittest.main()
