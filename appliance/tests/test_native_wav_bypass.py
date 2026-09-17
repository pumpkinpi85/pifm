"""Native WAV bypass vs FFmpeg conversion — docs/UI contract regressions."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from appliance.tx import inspect_wav, prepare_seekable_wav

ROOT = Path(__file__).resolve().parents[2]
DOCS_MUSIC = ROOT / "docs" / "music.md"
INDEX_HTML = ROOT / "appliance" / "web" / "static" / "index.html"


def write_wav(
    path: Path,
    *,
    channels: int,
    sample_rate: int,
    sample_width: int,
    frames: int = 128,
) -> None:
    """Write a tiny real WAV using the stdlib wave module."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * (frames * channels * sample_width))


class NativeWavBypassTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.work = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _wav(self, name: str, **kwargs) -> Path:
        path = self.work / name
        write_wav(path, **kwargs)
        return path

    def test_inspect_wav_acceptance_matrix(self):
        cases = (
            ("stereo_44100_16.wav", dict(channels=2, sample_rate=44100, sample_width=2), True),
            ("mono_44100_16.wav", dict(channels=1, sample_rate=44100, sample_width=2), True),
            ("stereo_48000_16.wav", dict(channels=2, sample_rate=48000, sample_width=2), True),
            ("mono_48000_16.wav", dict(channels=1, sample_rate=48000, sample_width=2), True),
            ("stereo_22050_16.wav", dict(channels=2, sample_rate=22050, sample_width=2), False),
            ("stereo_44100_24.wav", dict(channels=2, sample_rate=44100, sample_width=3), False),
            ("quad_44100_16.wav", dict(channels=4, sample_rate=44100, sample_width=2), False),
        )
        for name, params, expected_ok in cases:
            with self.subTest(name=name, **params):
                path = self._wav(name, **params)
                info = inspect_wav(str(path))
                self.assertEqual(info["ok"], expected_ok, info)

    def test_prepare_seekable_wav_bypasses_qualifying_stereo_44100(self):
        source = self._wav(
            "bypass.wav",
            channels=2,
            sample_rate=44100,
            sample_width=2,
        )
        result = prepare_seekable_wav(str(source), self.work / "cache-work")
        self.assertFalse(result["converted"])
        self.assertEqual(result["wav_path"], str(source))

    def test_prepare_seekable_wav_converts_non_qualifying_with_ffmpeg_targets(self):
        source = self._wav(
            "needs_convert.wav",
            channels=2,
            sample_rate=22050,
            sample_width=2,
        )
        resources = {
            "duration_s": 0.01,
            "sample_rate": 22050,
            "channels": 2,
            "output_sample_rate": 44100,
            "output_channels": 2,
            "output_sample_width": 2,
            "projected_pcm_bytes": 4096,
        }
        captured = {}

        def fake_run(command, should_cancel=None, timeout_s=600):
            captured["command"] = list(command)
            out_path = command[-1]
            write_wav(
                Path(out_path),
                channels=2,
                sample_rate=44100,
                sample_width=2,
            )

        with patch("appliance.tx.shutil.which", return_value="/usr/bin/ffmpeg"), patch(
            "appliance.tx.probe_audio_resources", return_value=resources
        ), patch("appliance.tx.OwnedFfmpegProcess.run", side_effect=fake_run):
            result = prepare_seekable_wav(
                str(source),
                self.work / "convert-work",
                cache_budget_bytes=1024 * 1024,
                min_free_bytes=0,
            )

        cmd = captured["command"]
        self.assertTrue(result["converted"])
        self.assertIn("44100", cmd)
        self.assertIn("2", cmd)
        self.assertIn("pcm_s16le", cmd)

    def test_music_md_documents_recommended_and_accepted_formats(self):
        text = DOCS_MUSIC.read_text(encoding="utf-8")
        self.assertIn("### Avoid transcoding", text)
        self.assertIn("44.1 kHz", text)
        self.assertIn("PCM", text)
        self.assertIn("Stereo", text)
        self.assertIn("48 kHz", text)
        self.assertIn("mono", text)

    def test_upload_tip_present_in_index_html(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        tip = "Tip: WAV · PCM 16-bit · 44.1 kHz · Stereo skips conversion on this Pi."
        self.assertIn(tip, html)
        self.assertGreaterEqual(html.count(tip), 2)


if __name__ == "__main__":
    unittest.main()
