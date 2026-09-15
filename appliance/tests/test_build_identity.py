"""P1C build identity and hardware profile tests (non-RF)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from appliance.build_info import resolve_build_identity, write_build_meta
from appliance.hardware_profile import load_profile, resolve_hardware_profile, suggest_profile_id


ROOT = Path(__file__).resolve().parents[2]


class BuildIdentityTests(unittest.TestCase):
    def test_unknown_without_meta_is_dirty_and_visible(self):
        with tempfile.TemporaryDirectory() as td:
            info = resolve_build_identity(Path(td), software_version="0.4.2")
            self.assertEqual(info["git_sha"], "unknown")
            self.assertTrue(info["build_dirty"])
            self.assertFalse(info["build_identified"])
            self.assertIn("unidentified", info["build_label"])

    def test_stamped_clean_sha(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_build_meta(
                root,
                {
                    "git_sha": "abc123def",
                    "software_version": "0.4.2",
                    "build_time": "20260101T000000Z",
                    "hardware_profile": "raspberry-pi-a-plus",
                    "dirty": False,
                },
            )
            info = resolve_build_identity(root)
            self.assertEqual(info["git_sha"], "abc123def")
            self.assertTrue(info["build_identified"])
            self.assertFalse(info["build_dirty"])
            self.assertEqual(info["hardware_profile"], "raspberry-pi-a-plus")
            self.assertIn("abc123def", info["build_label"])

    def test_dirty_flag_appends_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_build_meta(
                root,
                {"git_sha": "deadbeef", "software_version": "0.4.2", "dirty": True},
            )
            info = resolve_build_identity(root)
            self.assertTrue(info["build_dirty"])
            self.assertTrue(info["git_sha"].endswith("-dirty"))


class HardwareProfileTests(unittest.TestCase):
    def test_load_a_plus_profile(self):
        doc = load_profile(ROOT, "raspberry-pi-a-plus")
        self.assertEqual(doc["status"], "SUPPORTED")
        self.assertEqual(doc["rf_gpio_bcm"], 4)
        self.assertEqual(doc["rf_header_pin"], 7)

    def test_resolve_includes_doc(self):
        resolved = resolve_hardware_profile(ROOT, "raspberry-pi-a-plus", include_detection=False)
        self.assertTrue(resolved["hardware_profile_found"])
        self.assertEqual(resolved["hardware_profile"], "raspberry-pi-a-plus")

    def test_suggest_defaults_to_a_plus(self):
        self.assertEqual(suggest_profile_id({"model": "Raspberry Pi Model A Plus Rev 1.1"}), "raspberry-pi-a-plus")


if __name__ == "__main__":
    unittest.main()
