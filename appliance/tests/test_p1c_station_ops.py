"""P1C station ops: backup, deploy, rollback, validation (non-RF, temp roots)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_ops():
    path = ROOT / "scripts" / "pifm_station_ops.py"
    spec = importlib.util.spec_from_file_location("pifm_station_ops", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class StationOpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ops = _load_ops()

    def _seed_target(self, target: Path) -> None:
        (target / "config").mkdir(parents=True)
        (target / "data" / "library").mkdir(parents=True)
        (target / "data" / "playlists").mkdir(parents=True)
        (target / "data" / "recovery").mkdir(parents=True)
        (target / "appliance").mkdir(parents=True)
        (target / "appliance" / "marker_old.txt").write_text("old-app\n")
        cfg = {
            "frequency_mhz": 91.5,
            "rds_ps": "TEST",
            "rds_rt": "Operator",
            "rds_pi": "ABCD",
            "active_playlist": "default",
            "tx_backend": "mock",
            "pi_fm_rds_path": "/usr/local/bin/pi_fm_rds",
            "state": "ON_AIR",
            "on_air": True,
            "software_version": "0.3.1",
            "hardware_profile": "raspberry-pi-a-plus",
            "gpio_enabled": False,
            "library_dir": "data/library",
            "playlists_dir": "data/playlists",
            "led_pin": 18,
            "switch_pin": 5,
            "tx_pin": 4,
            "web_host": "0.0.0.0",
            "web_port": 8080,
            "shuffle": False,
            "repeat": True,
            "rf_quiet_mode": "simulate",
            "rf_quiet_seconds": 60,
            "product_name": "piFM Pirate Radio",
        }
        (target / "config" / "appliance.json").write_text(json.dumps(cfg, indent=2) + "\n")
        (target / "data" / "library" / "song.mp3").write_text("fake-mp3")
        (target / "data" / "playlists" / "default.json").write_text(
            json.dumps({"name": "default", "tracks": ["a"], "updated": 1}) + "\n"
        )
        (target / "data" / "library.sqlite3").write_text("sqlite-bytes")
        (target / "data" / "recovery" / "broadcast-on.json").write_text(
            '{"version":1,"desired_broadcast":"on"}\n'
        )

    def test_backup_manifest_dry_run_and_real(self):
        ops = self.ops
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            target = td_path / "opt" / "pifm"
            dest = td_path / "backups"
            self._seed_target(target)
            dry = ops.backup_station(target, dest, include_media=True, dry_run=True)
            self.assertTrue(dry["dry_run"])
            self.assertTrue(any(i["path"] == "config/appliance.json" for i in dry["items"]))
            man = ops.backup_station(target, dest, include_media=True, dry_run=False)
            backup_root = Path(man["backup_root"])
            self.assertTrue((backup_root / "MANIFEST.json").is_file())
            self.assertTrue((backup_root / "config" / "appliance.json").is_file())
            self.assertTrue((backup_root / "data" / "library" / "song.mp3").is_file())
            self.assertTrue(
                (backup_root / "data/recovery/broadcast-on.json").is_file()
            )
            self.assertIn("build", man)

    def test_seed_demo_media_copies_once_and_preserves_existing(self):
        ops = self.ops
        demo_src = ROOT / "examples" / "demo" / "Brynja Vinter - The Sky Belongs to No King.wav"
        if not demo_src.is_file():
            self.skipTest("bundled demo track not present")
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "opt" / "pifm"
            (target / "examples" / "demo").mkdir(parents=True)
            shutil.copy2(demo_src, target / "examples" / "demo" / demo_src.name)
            first = ops.seed_demo_media(target)
            self.assertTrue(first["copied_track"])
            self.assertTrue(first["created_playlist"])
            dest = target / "data" / "library" / "demo" / demo_src.name
            self.assertTrue(dest.is_file())
            original = dest.read_bytes()
            dest.write_bytes(b"operator-owned")
            second = ops.seed_demo_media(target)
            self.assertFalse(second["copied_track"])
            self.assertFalse(second["created_playlist"])
            self.assertEqual(dest.read_bytes(), b"operator-owned")
            self.assertEqual(original[:4], b"RIFF")

    def test_stage_deploy_preserves_operator_data_and_strips_on_air(self):
        ops = self.ops
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            target = td_path / "opt" / "pifm"
            stage = td_path / "stage"
            prev = td_path / "previous"
            self._seed_target(target)
            ops.stage_application(
                source_repo=ROOT,
                stage_dir=stage,
                git_sha="cafebabe01234567",
                software_version="0.4.2",
                hardware_profile="raspberry-pi-a-plus",
                dirty=False,
            )
            self.assertTrue((stage / "build_meta.json").is_file())
            self.assertFalse((stage / "config" / "appliance.json").exists())

            result = ops.deploy_application(
                stage_dir=stage,
                target_root=target,
                previous_app_dir=prev,
                dry_run=False,
            )
            self.assertEqual(result["action"], "deployed")
            # Operator media preserved
            self.assertEqual((target / "data" / "library" / "song.mp3").read_text(), "fake-mp3")
            self.assertTrue((target / "data" / "playlists" / "default.json").is_file())
            self.assertEqual((target / "data" / "library.sqlite3").read_text(), "sqlite-bytes")
            self.assertTrue(
                (target / "data/recovery/broadcast-on.json").is_file()
            )
            # Config preserved but ON_AIR keys stripped
            cfg = json.loads((target / "config" / "appliance.json").read_text())
            self.assertEqual(cfg["frequency_mhz"], 91.5)
            self.assertNotIn("state", cfg)
            self.assertNotIn("on_air", cfg)
            # App replaced / stamped
            self.assertTrue((target / "appliance" / "build_info.py").is_file())
            self.assertFalse((target / "appliance" / "marker_old.txt").exists())
            build = ops.resolve_build_identity(target) if hasattr(ops, "resolve_build_identity") else None
            from appliance.build_info import resolve_build_identity

            info = resolve_build_identity(target)
            self.assertEqual(info["git_sha"], "cafebabe01234567")
            self.assertTrue((prev / "appliance" / "marker_old.txt").is_file())

    def test_deploy_dry_run_does_not_mutate(self):
        ops = self.ops
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            target = td_path / "opt" / "pifm"
            stage = td_path / "stage"
            self._seed_target(target)
            before = (target / "appliance" / "marker_old.txt").read_text()
            ops.stage_application(
                ROOT, stage, "abc", "0.4.2", dirty=False
            )
            ops.deploy_application(stage, target, dry_run=True)
            self.assertEqual((target / "appliance" / "marker_old.txt").read_text(), before)

    def test_rollback_restores_app_keeps_newer_media(self):
        ops = self.ops
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            target = td_path / "opt" / "pifm"
            stage_a = td_path / "stage_a"
            stage_b = td_path / "stage_b"
            prev = td_path / "previous"
            self._seed_target(target)
            ops.stage_application(ROOT, stage_a, "11111111", "0.4.1", dirty=False)
            ops.deploy_application(stage_a, target, previous_app_dir=None)
            # Operator adds new media after deploy A
            (target / "data" / "library" / "new.mp3").write_text("new")
            ops.stage_application(ROOT, stage_b, "22222222", "0.4.2", dirty=False)
            ops.deploy_application(stage_b, target, previous_app_dir=prev)
            self.assertEqual(
                json.loads((target / "build_meta.json").read_text())["git_sha"],
                "22222222",
            )
            self.assertEqual(
                json.loads((prev / "build_meta.json").read_text())["git_sha"],
                "11111111",
            )
            ops.rollback_application(prev, target)
            info = json.loads((target / "build_meta.json").read_text())
            self.assertEqual(info["git_sha"], "11111111")
            self.assertEqual((target / "data" / "library" / "new.mp3").read_text(), "new")
            self.assertEqual((target / "data" / "library" / "song.mp3").read_text(), "fake-mp3")
            self.assertTrue(
                (target / "data/recovery/broadcast-on.json").is_file()
            )

    def test_validate_requires_zero_tx_and_off_air(self):
        ops = self.ops
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "opt" / "pifm"
            self._seed_target(target)
            from appliance.build_info import write_build_meta

            write_build_meta(
                target,
                {
                    "git_sha": "abcd",
                    "software_version": "0.4.2",
                    "dirty": False,
                    "hardware_profile": "raspberry-pi-a-plus",
                },
            )
            # Copy hardware profiles for resolver
            shutil.copytree(ROOT / "hardware", target / "hardware")
            report = ops.validate_reference_station(
                target,
                api_status={
                    "state": "READY",
                    "tx_running": False,
                    "broadcast_state": "off",
                    "tx_backend": "mock",
                    "gpio_enabled": False,
                    "network": {"iface": "eth0"},
                },
                service_active=True,
                dashboard_ok=True,
            )
            self.assertEqual(report["broadcast"], "OFF")
            self.assertEqual(report["real_tx_process_count"], 0)
            self.assertEqual(report["result"], "PASS")
            text = ops.format_validation_report(report)
            self.assertIn("PI FM REFERENCE HARDWARE VALIDATION", text)
            self.assertIn("RESULT:", text)

            bad = ops.validate_reference_station(
                target,
                api_status={"state": "ON_AIR", "tx_running": True, "broadcast_state": "on"},
            )
            self.assertEqual(bad["result"], "FAIL")


if __name__ == "__main__":
    unittest.main()
