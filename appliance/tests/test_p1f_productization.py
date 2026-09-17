"""P1F hardware, onboarding, media, and persistence regressions (no RF)."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from appliance.__main__ import ensure_startup_rf_off
from appliance.config import Config, ConfigError
from appliance.hardware_environment import check_host_prerequisites
from appliance.hardware_profile import (
    detect_board_hints,
    resolve_hardware_profile,
    suggest_profile_id,
)
from appliance.library import Library
from appliance.media_import import (
    MediaImportError,
    import_media_stream,
    safe_media_filename,
)


ROOT = Path(__file__).resolve().parents[2]


def make_library(root: Path) -> Library:
    return Library(
        root / "data" / "library",
        root / "data" / "playlists",
        root / "data" / "library.sqlite3",
    )


class HardwareDetectionTests(unittest.TestCase):
    def test_a_plus_device_tree_detection(self):
        with tempfile.TemporaryDirectory() as td:
            system_root = Path(td)
            model_path = system_root / "proc" / "device-tree" / "model"
            model_path.parent.mkdir(parents=True)
            model_path.write_bytes(b"Raspberry Pi Model A Plus Rev 1.1\x00")
            (system_root / "proc" / "cpuinfo").write_text(
                "Hardware\t: BCM2835\nRevision\t: 900021\n"
            )
            hints = detect_board_hints(system_root)
            self.assertEqual(suggest_profile_id(hints), "raspberry-pi-a-plus")
            self.assertEqual(hints["soc_family"], "BCM2835")

    def test_unknown_hardware_does_not_guess_a_plus(self):
        hints = {"model": "Raspberry Pi 5 Model B Rev 1.0", "revision": "d04170"}
        self.assertIsNone(suggest_profile_id(hints))
        resolved = resolve_hardware_profile(ROOT, None, include_detection=False)
        self.assertIsNone(resolved["hardware_profile"])
        self.assertEqual(resolved["hardware_profile_doc"]["status"], "UNKNOWN")

    def test_manual_profile_does_not_claim_unknown_board_supported(self):
        with patch(
            "appliance.hardware_profile.detect_board_hints",
            return_value={
                "model": "Raspberry Pi 5 Model B Rev 1.0",
                "revision": "d04170",
            },
        ):
            resolved = resolve_hardware_profile(
                ROOT, "raspberry-pi-a-plus", include_detection=True
            )
        self.assertEqual(resolved["hardware_status"], "EXPERIMENTAL")
        self.assertFalse(resolved["hardware_profile_match"])

    def test_a_plus_prerequisites_are_read_only_and_detected(self):
        with tempfile.TemporaryDirectory() as td:
            system_root = Path(td)
            (system_root / "boot").mkdir(parents=True)
            (system_root / "boot" / "config.txt").write_text(
                "dtparam=audio=off\n"
            )
            (system_root / "proc").mkdir()
            (system_root / "proc" / "modules").write_text("")
            target = system_root / "etc" / "systemd" / "system" / "default.target"
            target.parent.mkdir(parents=True)
            target.symlink_to("/lib/systemd/system/multi-user.target")
            profile = resolve_hardware_profile(
                ROOT, "raspberry-pi-a-plus", include_detection=False
            )["hardware_profile_doc"]
            result = check_host_prerequisites(
                profile, system_root=system_root, use_cache=False
            )
            self.assertTrue(result["ready"])
            self.assertTrue(result["onboard_audio_disabled"])
            self.assertTrue(result["headless"])

    def test_a_plus_audio_or_desktop_conflict_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as td:
            system_root = Path(td)
            (system_root / "boot").mkdir(parents=True)
            (system_root / "boot" / "config.txt").write_text(
                "dtparam=audio=on\n"
            )
            (system_root / "proc" / "42").mkdir(parents=True)
            (system_root / "proc" / "42" / "comm").write_text("Xorg\n")
            (system_root / "proc" / "modules").write_text(
                "snd_bcm2835 123 0 - Live 0x0\n"
            )
            target = system_root / "etc" / "systemd" / "system" / "default.target"
            target.parent.mkdir(parents=True)
            target.symlink_to("/lib/systemd/system/graphical.target")
            profile = resolve_hardware_profile(
                ROOT, "raspberry-pi-a-plus", include_detection=False
            )["hardware_profile_doc"]
            result = check_host_prerequisites(
                profile, system_root=system_root, use_cache=False
            )
            self.assertFalse(result["ready"])
            self.assertFalse(result["onboard_audio_disabled"])
            self.assertFalse(result["headless"])


class FirstRunConfigTests(unittest.TestCase):
    def test_existing_config_without_setup_key_is_complete(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(json.dumps({"tx_backend": "mock"}))
            config = Config(path, root)
            self.assertTrue(config.get("setup_completed"))

    def test_fresh_config_can_require_setup(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(
                json.dumps({"tx_backend": "mock", "setup_completed": False})
            )
            config = Config(path, root)
            self.assertFalse(config.get("setup_completed"))
            config.update({"setup_completed": True})
            self.assertTrue(config.get("setup_completed"))

    def test_setup_state_rejects_non_boolean(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "config.json"
            path.write_text(json.dumps({"tx_backend": "mock"}))
            config = Config(path, root)
            with self.assertRaises(ConfigError):
                config.update({"setup_completed": "true"})

    @patch(
        "appliance.__main__.kill_all_transmitters",
        return_value={"clear": True, "actions": []},
    )
    def test_startup_requires_zero_transmitters(self, _kill):
        ensure_startup_rf_off()

    @patch(
        "appliance.__main__.kill_all_transmitters",
        return_value={"clear": False, "remaining": [999]},
    )
    def test_startup_refuses_when_zero_cannot_be_proven(self, _kill):
        with self.assertRaisesRegex(RuntimeError, "Startup refused"):
            ensure_startup_rf_off()


class MediaImportTests(unittest.TestCase):
    def test_filename_safety_and_formats(self):
        self.assertEqual(safe_media_filename("../../Song.mp3"), "Song.mp3")
        self.assertEqual(safe_media_filename("..\\..\\Song.flac"), "Song.flac")
        with self.assertRaises(MediaImportError):
            safe_media_filename("../../.hidden")
        with self.assertRaises(MediaImportError):
            safe_media_filename("movie.exe")

    @patch(
        "appliance.media_import._probe_media",
        return_value={"codec": "pcm_s16le", "duration": 1.0},
    )
    def test_duplicate_and_collision_handling(self, _probe):
        with tempfile.TemporaryDirectory() as td:
            library = make_library(Path(td))
            first = import_media_stream(
                library, io.BytesIO(b"audio-one"), "Song.wav", 9
            )
            duplicate = import_media_stream(
                library, io.BytesIO(b"audio-one"), "Song.wav", 9
            )
            collision = import_media_stream(
                library, io.BytesIO(b"audio-two"), "Song.wav", 9
            )
            self.assertFalse(first["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(collision["filename"], "Song (2).wav")
            self.assertEqual(len(library.search()), 2)

    @patch(
        "appliance.media_import._probe_media",
        return_value={"codec": "pcm_s16le", "duration": 1.0},
    )
    def test_duplicate_detection_covers_nested_library(self, _probe):
        with tempfile.TemporaryDirectory() as td:
            library = make_library(Path(td))
            nested = library.library_dir / "testing" / "Song.wav"
            nested.parent.mkdir(parents=True)
            nested.write_bytes(b"audio-one")
            library.reindex()

            result = import_media_stream(
                library, io.BytesIO(b"audio-one"), "Song.wav", 9
            )

            self.assertTrue(result["duplicate"])
            self.assertEqual(result["track"]["path"], "testing/Song.wav")
            self.assertFalse((library.library_dir / "Song.wav").exists())
            self.assertEqual(len(library.search()), 1)

    @patch(
        "appliance.media_import._probe_media",
        return_value={"codec": "mp3", "duration": 1.0},
    )
    def test_incomplete_upload_is_removed(self, _probe):
        with tempfile.TemporaryDirectory() as td:
            library = make_library(Path(td))
            with self.assertRaises(MediaImportError):
                import_media_stream(
                    library, io.BytesIO(b"short"), "Song.mp3", 100
                )
            self.assertEqual(list(library.library_dir.iterdir()), [])

    def test_undecodable_media_fails_cleanly(self):
        if not shutil.which("ffprobe"):
            self.skipTest("ffprobe not installed")
        with tempfile.TemporaryDirectory() as td:
            library = make_library(Path(td))
            with self.assertRaisesRegex(
                MediaImportError, "damaged, encrypted, or DRM-protected"
            ):
                import_media_stream(
                    library, io.BytesIO(b"not audio"), "Broken.mp3", 9
                )
            self.assertEqual(library.search(), [])

    @patch(
        "appliance.media_import._probe_media",
        return_value={"codec": "pcm_s16le", "duration": 1.0},
    )
    def test_delete_removes_playlist_references(self, _probe):
        with tempfile.TemporaryDirectory() as td:
            library = make_library(Path(td))
            imported = import_media_stream(
                library, io.BytesIO(b"audio"), "Song.wav", 5
            )
            track_id = imported["track"]["id"]
            library.save_playlist(
                "default", {"name": "Default", "tracks": [track_id]}
            )
            result = library.delete_track(track_id)
            self.assertEqual(result["removed_from_playlists"], ["default"])
            self.assertEqual(library.load_playlist("default")["tracks"], [])
            self.assertEqual(library.search(), [])


if __name__ == "__main__":
    unittest.main()
