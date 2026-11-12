"""Audit fixes: quiet-network honesty, playlist seed, copy, favicon, installer user."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

from appliance import __version__
from appliance.config import DEFAULTS, Config
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import STARTER_PLAYLIST_NAMES, Library
from appliance.network import NetworkManager
from appliance.webapp import serve


STATIC = ROOT / "appliance" / "web" / "static"
COPY_JS = STATIC / "js" / "copy.js"


def _run_node(source: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "-e", source],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )


def _library(root: Path) -> Library:
    return Library(
        root / "library",
        root / "playlists",
        root / "library.sqlite3",
    )


def _write_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "library").mkdir(parents=True, exist_ok=True)
    (root / "data" / "playlists").mkdir(parents=True, exist_ok=True)
    (root / "config" / "appliance.json").write_text(
        json.dumps(
            {
                "tx_backend": "mock",
                "library_dir": "data/library",
                "playlists_dir": "data/playlists",
                "active_playlist": "default",
                "gpio_enabled": False,
                "rf_quiet_mode": "simulate",
                "rf_quiet_seconds": 60,
                "setup_completed": True,
            }
        )
    )


def _boot(root: Path) -> Controller:
    config = Config(root / "config" / "appliance.json", root)
    events = EventLog(persist_path=root / "data" / "logs" / "ships-log.jsonl")
    library = Library(
        config.resolve("library_dir"),
        config.resolve("playlists_dir"),
        root / "data" / "library.sqlite3",
    )
    library.ensure_default_playlists()
    return Controller(config, library, events)


class QuietNetworkDefaultTests(unittest.TestCase):
    def test_shipped_default_is_simulate_and_docs_do_not_claim_it_drops_the_link(self):
        for rel in ("config/default.json", "examples/config.example.json"):
            data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
            self.assertEqual(data["rf_quiet_mode"], "simulate")
            self.assertEqual(data["rf_quiet_seconds"], 60)
        self.assertEqual(DEFAULTS["rf_quiet_mode"], "simulate")
        self.assertEqual(DEFAULTS["rf_quiet_seconds"], 60)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = Config(root / "missing.json", root)
            self.assertEqual(config.get("rf_quiet_mode"), "simulate")

        html = (STATIC / "index.html").read_text(encoding="utf-8")
        warning = html.split('data-testid="rf-quiet-warning"', 1)[1].split("</p>", 1)[0]
        self.assertIn("simulate", warning)
        self.assertIn("does not</strong> turn the network connection off", warning)
        self.assertIn("does not</strong> stop the FM broadcast", warning)
        self.assertNotIn("turns off the network connection", warning.lower())
        help_html = html.split('id="view-help"', 1)[1]
        self.assertIn("simulate default does not turn the network off", help_html)

        config_doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
        self.assertIn("rf_quiet_mode", config_doc)
        self.assertIn("rf_quiet_seconds", config_doc)
        self.assertIn(
            "The shipped default `simulate` does not take the network interface down.",
            config_doc,
        )
        self.assertIn("`timed` is an explicit opt-in", config_doc)
        manual = (ROOT / "docs" / "USER-MANUAL.md").read_text(encoding="utf-8")
        self.assertIn("does not** turn the network off", manual)

    def test_version_stays_at_public_release(self):
        self.assertEqual(__version__, "0.6.2")
        self.assertEqual(DEFAULTS["software_version"], "0.6.2")
        for rel in ("config/default.json", "examples/config.example.json"):
            data = json.loads((ROOT / rel).read_text(encoding="utf-8"))
            self.assertEqual(data["software_version"], "0.6.2")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("\n## Unreleased\n", changelog)
        self.assertNotIn("## 0.6.3", changelog)


class OperatorCopyTests(unittest.TestCase):
    def test_doubled_hardware_heading_and_track_plural_cannot_appear(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
        copy = COPY_JS.read_text(encoding="utf-8")
        for source in (html, js, copy):
            self.assertNotIn("Hardware not detected detected", source)
        self.assertNotIn('hardwareLabel(s) + " detected"', js)
        self.assertIn("window.PifmCopy.hardwareSetupHeading", js)
        self.assertIn("window.PifmCopy.trackCountLabel", js)
        self.assertIn("window.PifmCopy.quietNetworkConfirmMessage", js)
        self.assertNotIn('+ " tracks)"', js)
        self.assertNotIn("This turns OFF the network connection.", js)
        self.assertLess(html.index("/js/copy.js"), html.index("/js/app.js"))

        result = _run_node(
            textwrap.dedent(
                """
                const assert = require("assert");
                const copy = require("./appliance/web/static/js/copy.js");
                const labels = [
                  "",
                  "Hardware not detected",
                  "Radio not detected",
                  "Raspberry Pi Model A+",
                  "Raspberry Pi Model A+ detected",
                ];
                assert.strictEqual(
                  copy.hardwareSetupHeading("Hardware not detected"),
                  "Hardware not detected"
                );
                assert.strictEqual(
                  copy.hardwareSetupHeading(""),
                  "Hardware not detected"
                );
                assert.strictEqual(
                  copy.hardwareSetupHeading("Radio not detected"),
                  "Radio not detected"
                );
                assert.strictEqual(
                  copy.hardwareSetupHeading("Raspberry Pi Model A+"),
                  "Raspberry Pi Model A+ detected"
                );
                assert.strictEqual(
                  copy.hardwareSetupHeading("Raspberry Pi Model A+ detected"),
                  "Raspberry Pi Model A+ detected"
                );
                labels.forEach(function (label) {
                  assert.ok(
                    !copy.hardwareSetupHeading(label).includes("detected detected"),
                    label
                  );
                });
                assert.strictEqual(copy.trackCountLabel(0), "0 tracks");
                assert.strictEqual(copy.trackCountLabel(1), "1 track");
                assert.strictEqual(copy.trackCountLabel(2), "2 tracks");
                const simulate = copy.quietNetworkConfirmMessage("simulate", 60);
                assert.ok(!/turns OFF/i.test(simulate), simulate);
                assert.ok(simulate.includes("does NOT turn the network"));
                assert.ok(simulate.includes("Mode: simulate"));
                const timed = copy.quietNetworkConfirmMessage("timed", 45);
                assert.ok(timed.includes("turns OFF the network connection"));
                assert.ok(timed.includes("Mode: timed"));
                const fallback = copy.quietNetworkConfirmMessage(undefined, 60);
                assert.ok(!/turns OFF/i.test(fallback), fallback);
                """
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class PublicationWordingTests(unittest.TestCase):
    def test_public_repo_wording(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertNotIn("When published", readme)
        self.assertNotIn("When published", contributing)
        self.assertNotIn("when that public repo exists", security)
        self.assertIn("https://github.com/pumpkinpi85/pifm", readme)
        self.assertIn("GitHub Security Advisories", security)
        self.assertIn("canonical public repository", contributing.lower())


class StarterPlaylistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def library(self) -> Library:
        return _library(self.root)

    def test_first_run_seeds_starters_once(self):
        library = self.library()
        first = library.ensure_default_playlists()
        self.assertEqual(first["created"], list(STARTER_PLAYLIST_NAMES))
        self.assertEqual(
            [item["id"] for item in library.list_playlists()],
            sorted(STARTER_PLAYLIST_NAMES),
        )
        self.assertTrue(
            all(not item["id"].startswith(".") for item in library.list_playlists())
        )
        library.delete_playlist("jazz")
        second = library.ensure_default_playlists()
        self.assertTrue(second["already_seeded"])
        ids = {item["id"] for item in library.list_playlists()}
        self.assertNotIn("jazz", ids)
        with self.assertRaises(FileNotFoundError):
            library.load_playlist("jazz")

    def test_existing_library_does_not_restore_a_missing_starter(self):
        library = self.library()
        for name in ("default", "favorites", "classical", "rock", "custom"):
            library.save_playlist(name, {"name": name, "tracks": []})
        result = library.ensure_default_playlists()
        self.assertTrue(result["skipped_existing_library"])
        self.assertNotIn("jazz", {item["id"] for item in library.list_playlists()})
        library.ensure_default_playlists()
        self.assertNotIn("jazz", {item["id"] for item in library.list_playlists()})

    def test_demo_default_playlist_is_kept_and_other_starters_are_added(self):
        library = self.library()
        library.save_playlist("default", {"name": "default", "tracks": ["track-1"]})
        result = library.ensure_default_playlists()
        self.assertNotIn("default", result["created"])
        self.assertIn("jazz", result["created"])
        self.assertEqual(library.load_playlist("default")["tracks"], ["track-1"])

    def test_deleted_starter_can_be_created_again_by_the_operator(self):
        library = self.library()
        library.ensure_default_playlists()
        library.delete_playlist("jazz")
        created = library.create_playlist("jazz")
        self.assertEqual(created["id"], "jazz")
        reopened = Library(
            library.library_dir, library.playlists_dir, library.db_path
        )
        reopened.ensure_default_playlists()
        self.assertEqual(reopened.load_playlist("jazz")["name"], "jazz")

    def test_in_progress_seed_finishes_without_clobbering_default(self):
        library = self.library()
        library.save_playlist("default", {"name": "default", "tracks": ["keep"]})
        marker = library.playlists_dir / ".starter-playlists.json"
        marker.write_text(
            json.dumps({"state": "in_progress", "names": list(STARTER_PLAYLIST_NAMES)})
            + "\n"
        )
        result = library.ensure_default_playlists()
        self.assertIn("jazz", result["created"])
        self.assertEqual(library.load_playlist("default")["tracks"], ["keep"])
        meta = json.loads(marker.read_text())
        self.assertEqual(meta["state"], "complete")

    def test_starter_playlists_are_documented(self):
        music = (ROOT / "docs" / "music.md").read_text(encoding="utf-8")
        for name in STARTER_PLAYLIST_NAMES:
            self.assertIn(name, music)
        self.assertIn("do not recreate it", music)
        install = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
        self.assertIn("starters are not restored later", install)


class HttpAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write_config(self.root)
        self.ctrl = _boot(self.root)
        self.httpd = serve(
            "127.0.0.1", 0, self.ctrl, self.ctrl.library, self.ctrl.events
        )
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        finally:
            self.tmp.cleanup()

    def request(self, method, path, body=None, parse_json=True):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        status = response.status
        content_type = response.getheader("Content-Type") or ""
        conn.close()
        if not parse_json:
            return status, raw, content_type
        parsed = json.loads(raw.decode("utf-8")) if raw else None
        return status, parsed, content_type

    def playlist_ids(self):
        status, body, _content_type = self.request("GET", "/api/playlists")
        self.assertEqual(status, 200)
        return {item["id"] for item in body["playlists"]}

    def restart_process(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.ctrl = _boot(self.root)
        self.httpd = serve(
            "127.0.0.1", 0, self.ctrl, self.ctrl.library, self.ctrl.events
        )
        self.port = self.httpd.server_address[1]

    def test_favicon_is_served(self):
        ico = STATIC / "favicon.ico"
        self.assertTrue(ico.is_file())
        data = ico.read_bytes()
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")
        self.assertLess(len(data), 64_000)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon"', html)
        self.assertIn('href="/favicon.ico"', html)
        status, raw, content_type = self.request(
            "GET", "/favicon.ico", parse_json=False
        )
        self.assertEqual(status, 200)
        self.assertIn("image/x-icon", content_type)
        self.assertEqual(raw[:4], b"\x00\x00\x01\x00")

    def test_create_rename_delete_and_restart(self):
        self.assertEqual(self.playlist_ids(), set(STARTER_PLAYLIST_NAMES))
        status, created, _content_type = self.request(
            "POST", "/api/playlists", {"name": "Evening"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(created["id"], "evening")
        status, renamed, _content_type = self.request(
            "PUT",
            "/api/playlists/evening",
            {"name": "Late Show", "tracks": []},
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed["name"], "Late Show")
        status, deleted, _content_type = self.request("DELETE", "/api/playlists/jazz")
        self.assertEqual(status, 200)
        self.assertTrue(deleted["ok"])
        self.assertNotIn("jazz", self.playlist_ids())
        self.assertIn("evening", self.playlist_ids())

        self.restart_process()

        ids = self.playlist_ids()
        self.assertNotIn("jazz", ids)
        self.assertIn("evening", ids)
        for name in STARTER_PLAYLIST_NAMES:
            if name != "jazz":
                self.assertIn(name, ids)
        status, detail, _content_type = self.request("GET", "/api/playlists/evening")
        self.assertEqual(status, 200)
        self.assertEqual(detail["name"], "Late Show")
        missing, _body, _content_type = self.request("GET", "/api/playlists/jazz")
        self.assertEqual(missing, 404)
        self.assertFalse(
            (self.root / "data" / "playlists" / "jazz.json").exists()
        )
        self.assertTrue(
            (self.root / "data" / "playlists" / "evening.json").is_file()
        )

    def test_rfquiet_simulate_does_not_touch_the_interface(self):
        with patch.object(NetworkManager, "_down_iface") as down, patch.object(
            NetworkManager, "_up_iface"
        ) as up, patch.object(
            NetworkManager, "_schedule_os_recovery"
        ) as recover:
            status, body, _content_type = self.request("POST", "/api/rfquiet", {})
            self.assertEqual(status, 400)
            self.assertIn("confirmed", body["error"])
            status, body, _content_type = self.request(
                "POST", "/api/rfquiet", {"confirmed": True}
            )
            self.assertEqual(status, 200, body)
            self.assertEqual(body["network"]["mode"], "simulate")
            self.assertEqual(body["network"]["network_state"], "SIMULATED_RF_QUIET")
            self.assertTrue(body["network"]["rf_quiet_active"])
            self.assertFalse(body["tx_running"])
            self.assertNotEqual(body["state"], "ON_AIR")
            down.assert_not_called()
            up.assert_not_called()
            recover.assert_not_called()
            restored, after, _content_type = self.request(
                "POST", "/api/rfquiet/restore", {}
            )
            self.assertEqual(restored, 200)
            self.assertEqual(after["network"]["network_state"], "NETWORK_CONNECTED")
            self.assertFalse(after["network"]["rf_quiet_active"])
            down.assert_not_called()
            up.assert_not_called()
            recover.assert_not_called()


class ServiceUserTests(unittest.TestCase):
    def run_resolver(self, env):
        return subprocess.run(
            ["bash", str(ROOT / "scripts" / "service-user.sh")],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            env=env,
            check=False,
        )

    def test_installer_defaults_to_the_installing_user_not_pi(self):
        user = subprocess.check_output(["id", "-un"], text=True).strip()
        env = os.environ.copy()
        env.pop("PIFM_USER", None)
        env["USER"] = "root"
        env["SUDO_USER"] = user
        result = self.run_resolver(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), user)

        missing = os.environ.copy()
        missing["PIFM_USER"] = "pifm-missing-user-zz"
        missing.pop("SUDO_USER", None)
        failed = self.run_resolver(missing)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("does not exist", failed.stderr)
        self.assertIn("not required", failed.stderr)

        root_env = os.environ.copy()
        root_env.pop("PIFM_USER", None)
        root_env.pop("SUDO_USER", None)
        root_env["USER"] = "root"
        unresolved = self.run_resolver(root_env)
        self.assertNotEqual(unresolved.returncode, 0)
        self.assertIn("Could not determine the installing user", unresolved.stderr)

        for rel in (
            "scripts/install.sh",
            "scripts/configure-hardware.sh",
            "scripts/deploy-sha.sh",
            "scripts/service-user.sh",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("${PIFM_USER:-pi}", text, rel)
            self.assertNotIn("PIFM_USER:-pi", text, rel)
            self.assertNotIn("chown -R pi:pi", text, rel)
        install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
        hardware = (ROOT / "scripts" / "configure-hardware.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("pifm_resolve_service_user", install)
        self.assertIn("pifm_resolve_service_user", hardware)
        self.assertIn('s|User=pi|User=$SERVICE_USER|g', install)
        install_doc = (ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
        self.assertIn("A user named `pi` is", install_doc)
        self.assertIn("not required", install_doc)


class ValidationWorkflowTests(unittest.TestCase):
    def test_github_actions_runs_validation_on_prs_and_default_branch(self):
        workflow = (ROOT / ".github" / "workflows" / "validation.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request:", workflow)
        self.assertIn("main", workflow)
        self.assertIn("scripts/run-validation.sh", workflow)


if __name__ == "__main__":
    unittest.main()
