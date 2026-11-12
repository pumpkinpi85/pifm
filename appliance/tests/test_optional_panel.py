"""Optional panel: split LED/switch, GPIO loader, helper, offline Help."""

from __future__ import annotations

import fcntl
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import appliance
from appliance.config import DEFAULTS, Config, ConfigError
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.gpio_controls import (
    DOCUMENTED_RF_BCM,
    GpioControls,
    broadcast_led_pattern,
    led_steps,
)
from appliance.gpio_controls import _gpio_loaders
from appliance import gpio_controls
from appliance.library import STARTER_PLAYLIST_NAMES, Library
from appliance.webapp import (
    Handler,
    is_public_install_url,
    lookup_public_install_file,
    serve,
)

HELPER = ROOT / "scripts" / "enable-optional-panel.py"
INDEX = ROOT / "appliance" / "web" / "static" / "index.html"
APP_JS = ROOT / "appliance" / "web" / "static" / "js" / "app.js"
MEM_ERROR = "No access to /dev/mem. Try running as root!"


class Events:
    def __init__(self):
        self.rows = []

    def emit(self, kind, message, **kwargs):
        self.rows.append((kind, str(message)))


class FakeGPIO:
    BCM = 11
    OUT = 0
    IN = 1
    LOW = 0
    HIGH = 1
    PUD_UP = 22
    FALLING = 31

    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []
        self.outputs = []
        self.cleaned = False
        self.callback = None

    def setmode(self, mode):
        self.calls.append(("setmode", mode))
        if self.fail:
            raise OSError(self.fail)

    def setup(self, pin, mode, pull_up_down=None):
        self.calls.append(("setup", pin, mode, pull_up_down))

    def output(self, pin, level):
        self.outputs.append((pin, level))

    def add_event_detect(self, pin, edge, callback=None, bouncetime=None):
        self.calls.append(("event", pin, edge, bouncetime))
        self.callback = callback

    def cleanup(self):
        self.cleaned = True


def _controls(events, **kwargs):
    params = dict(
        led_pin=18,
        switch_pin=5,
        enabled=True,
        on_shutdown=lambda: None,
        events=events,
    )
    params.update(kwargs)
    return GpioControls(**params)


class BroadcastLampTests(unittest.TestCase):
    def test_pattern_is_steady_only_while_transmitting(self):
        self.assertEqual(broadcast_led_pattern("SAFE_OFF", False), "off")
        self.assertEqual(broadcast_led_pattern("READY", False), "off")
        self.assertEqual(broadcast_led_pattern("READY", True), "off")
        self.assertEqual(broadcast_led_pattern("ON_AIR", False), "off")
        self.assertEqual(broadcast_led_pattern("ON_AIR", True), "on_air")
        self.assertEqual(broadcast_led_pattern("FAULT", False), "fault")
        self.assertEqual(broadcast_led_pattern("FAULT", True), "fault")

    def test_led_steps_stay_dark_except_on_air_and_fault(self):
        self.assertEqual(led_steps("off"), ((False, 0.2),))
        self.assertEqual(led_steps("ready"), ((False, 0.2),))
        self.assertEqual(led_steps("on_air"), ((True, 0.2),))
        self.assertEqual(led_steps("fault"), ((True, 0.05), (False, 0.05)))

    def test_entrypoint_keeps_playlist_seed_and_transmitter_lamp(self):
        text = (ROOT / "appliance" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn("library.ensure_default_playlists()", text)
        self.assertIn("broadcast_led_pattern(", text)
        self.assertIn("controller.tx.is_running()", text)
        self.assertNotIn('set_pattern("ready")', text)
        self.assertNotIn('set_pattern("on_air")', text)
        for name in ("default", "favorites", "classical", "rock", "jazz", "custom"):
            self.assertIn(name, STARTER_PLAYLIST_NAMES)

    def test_led_loop_follows_led_steps(self):
        events = Events()
        seen = []
        fake = FakeGPIO()
        ctl = _controls(events, led_enabled=True, switch_enabled=False)
        ctl._gpio = fake
        ctl._pattern = "on_air"

        def fast(pattern):
            seen.append(pattern)
            return ((True, 0.01), (False, 0.01))

        worker = threading.Thread(target=ctl._led_loop)
        with patch("appliance.gpio_controls.led_steps", side_effect=fast):
            worker.start()
            deadline = time.time() + 1.0
            while time.time() < deadline:
                levels = [level for _, level in list(fake.outputs)]
                if fake.HIGH in levels and fake.LOW in levels and seen:
                    break
                time.sleep(0.01)
            else:
                self.fail("lamp loop did not follow led_steps: {}".format(fake.outputs))
            ctl.stop()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertIn("on_air", seen)


class GpioLoaderTests(unittest.TestCase):
    def test_loader_order_is_import_then_dist_packages(self):
        self.assertEqual(
            _gpio_loaders(),
            (gpio_controls._load_imported_gpio, gpio_controls._load_system_gpio),
        )
        self.assertEqual(
            gpio_controls.SYSTEM_GPIO_DIST_PACKAGES,
            "/usr/lib/python3/dist-packages",
        )
        self.assertEqual(DOCUMENTED_RF_BCM, 4)

    def test_system_loader_replaces_shadowed_module(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        package = root / "RPi"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "GPIO.py").write_text("SOURCE = 'dist-packages'\n", encoding="utf-8")
        saved = {
            name: sys.modules[name]
            for name in list(sys.modules)
            if name == "RPi" or name.startswith("RPi.")
        }
        shadow = types.ModuleType("RPi.GPIO")
        shadow.SOURCE = "shadow-dev-mem"
        sys.modules["RPi"] = types.ModuleType("RPi")
        sys.modules["RPi.GPIO"] = shadow

        def restore():
            for name in list(sys.modules):
                if name == "RPi" or name.startswith("RPi."):
                    del sys.modules[name]
            sys.modules.update(saved)

        self.addCleanup(restore)
        with patch.object(gpio_controls, "SYSTEM_GPIO_DIST_PACKAGES", str(root)):
            loaded = gpio_controls._load_system_gpio()
        self.assertEqual(loaded.SOURCE, "dist-packages")
        self.assertTrue(str(loaded.__file__).startswith(str(root)))
        self.assertNotIn(str(root), sys.path)

    def test_dev_mem_failure_uses_the_second_loader(self):
        events = Events()
        first = FakeGPIO(fail=MEM_ERROR)
        second = FakeGPIO()

        def load_imported():
            return first

        def load_system():
            return second

        ctl = _controls(events)
        with patch.object(gpio_controls, "_load_imported_gpio", load_imported), patch.object(
            gpio_controls, "_load_system_gpio", load_system
        ):
            try:
                ctl.start()
                self.assertIs(ctl._gpio, second)
                self.assertTrue(ctl.enabled)
                self.assertTrue(ctl.led_enabled)
                self.assertTrue(ctl.switch_enabled)
                self.assertTrue(first.cleaned)
                self.assertFalse(second.cleaned)
                self.assertTrue(any(call[0] == "event" for call in second.calls))
                self.assertTrue(any(call[0] == "setup" and call[2] == FakeGPIO.OUT for call in second.calls))
                self.assertTrue(any("GPIO started led=18 switch=5" in row[1] for row in events.rows))
                self.assertTrue(any(MEM_ERROR not in row[1] for row in events.rows))
            finally:
                ctl.stop()

    def test_both_loaders_failing_on_dev_mem_stays_off(self):
        events = Events()

        def explode():
            raise OSError(MEM_ERROR)

        ctl = _controls(events)
        with patch.object(gpio_controls, "_load_imported_gpio", explode), patch.object(
            gpio_controls, "_load_system_gpio", explode
        ):
            ctl.start()
        self.assertFalse(ctl.enabled)
        self.assertIsNone(ctl._gpio)
        self.assertTrue(any(MEM_ERROR in row[1] for row in events.rows))
        self.assertFalse(any("GPIO started" in row[1] for row in events.rows))

    def test_working_import_does_not_call_the_system_loader(self):
        events = Events()
        first = FakeGPIO()

        def load_system():
            raise AssertionError("dist-packages loader should not run")

        ctl = _controls(events)
        with patch.object(gpio_controls, "_load_imported_gpio", lambda: first), patch.object(
            gpio_controls, "_load_system_gpio", load_system
        ):
            try:
                ctl.start()
                self.assertIs(ctl._gpio, first)
            finally:
                ctl.stop()

    def test_led_only_does_not_arm_the_switch(self):
        events = Events()
        fake = FakeGPIO()
        ctl = _controls(events, led_enabled=True, switch_enabled=False)

        def unused_loader():
            raise AssertionError("dist-packages loader should not run")

        with patch.object(gpio_controls, "_load_imported_gpio", lambda: fake), patch.object(
            gpio_controls, "_load_system_gpio", unused_loader
        ):
            try:
                ctl.start()
                self.assertTrue(ctl.led_enabled)
                self.assertFalse(ctl.switch_enabled)
                self.assertFalse(any(call[0] == "event" for call in fake.calls))
                self.assertTrue(any(call[2] == FakeGPIO.OUT for call in fake.calls if call[0] == "setup"))
                self.assertIn("switch=off", events.rows[-1][1])
            finally:
                ctl.stop()

    def test_switch_only_does_not_drive_the_led_pin(self):
        events = Events()
        fake = FakeGPIO()
        ctl = _controls(events, led_enabled=False, switch_enabled=True)

        def unused_loader():
            raise AssertionError("dist-packages loader should not run")

        with patch.object(gpio_controls, "_load_imported_gpio", lambda: fake), patch.object(
            gpio_controls, "_load_system_gpio", unused_loader
        ):
            try:
                ctl.start()
                self.assertIsNone(ctl._thread)
                self.assertFalse(any(call[2] == FakeGPIO.OUT for call in fake.calls if call[0] == "setup"))
                self.assertTrue(any(call[0] == "event" and call[1] == 5 for call in fake.calls))
                self.assertIn("led=off", events.rows[-1][1])
            finally:
                ctl.stop()

    def test_panel_refuses_the_fm_pin_and_a_shared_pin(self):
        events = Events()

        def unused():
            raise AssertionError("GPIO should not be opened")

        for kwargs in (
            {"led_pin": 4, "switch_pin": 5, "led_enabled": True, "switch_enabled": False},
            {"led_pin": 18, "switch_pin": 4, "led_enabled": False, "switch_enabled": True},
            {"led_pin": 18, "switch_pin": 18, "led_enabled": True, "switch_enabled": True},
        ):
            ctl = _controls(events, **kwargs)
            with patch.object(gpio_controls, "_load_imported_gpio", unused), patch.object(
                gpio_controls, "_load_system_gpio", unused
            ):
                ctl.start()
            self.assertFalse(ctl.enabled)
        self.assertTrue(any("BCM 4" in row[1] for row in events.rows))

    def test_switch_requests_shutdown_after_stop_and_never_starts_tx(self):
        events = Events()
        order = []

        def on_shutdown():
            order.append("stop")

        ctl = _controls(events, on_shutdown=on_shutdown, led_enabled=False, switch_enabled=True)
        source = inspect.getsource(GpioControls._switch_cb)
        shutdown_source = inspect.getsource(Controller.shutdown_request)
        self.assertNotIn("go_on_air", source)
        self.assertNotIn("pi_fm_rds", source)
        self.assertNotIn("go_on_air", shutdown_source)
        self.assertIn("tx_off", shutdown_source)
        with patch("appliance.gpio_controls.subprocess.Popen") as popen:
            self.assertIs(gpio_controls.subprocess.Popen, popen)
            ctl._switch_cb(5)
        self.assertEqual(order, ["stop"])
        popen.assert_called_once_with(["sudo", "shutdown", "-h", "now"])

    def test_disabled_config_does_not_open_gpio(self):
        events = Events()

        def unused():
            raise AssertionError("GPIO should not be opened")

        ctl = _controls(events, enabled=False)
        with patch.object(gpio_controls, "_load_imported_gpio", unused):
            ctl.start()
        self.assertFalse(ctl.enabled)
        self.assertEqual(events.rows[-1][1], "GPIO disabled in config")


class PanelConfigTests(unittest.TestCase):
    def test_defaults_stay_off_and_version_is_unchanged(self):
        self.assertEqual(appliance.__version__, "0.6.2")
        self.assertEqual(DEFAULTS["software_version"], "0.6.2")
        self.assertFalse(DEFAULTS["gpio_enabled"])
        self.assertFalse(DEFAULTS["led_enabled"])
        self.assertFalse(DEFAULTS["switch_enabled"])
        self.assertEqual(DEFAULTS["led_pin"], 18)
        self.assertEqual(DEFAULTS["switch_pin"], 5)
        self.assertEqual(DEFAULTS["tx_pin"], 4)
        for name in ("default.json",):
            payload = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
            self.assertFalse(payload["gpio_enabled"])
            self.assertFalse(payload["led_enabled"])
            self.assertFalse(payload["switch_enabled"])
            self.assertEqual(payload["tx_pin"], 4)
            self.assertEqual(payload["software_version"], "0.6.2")
        example = json.loads(
            (ROOT / "examples" / "config.example.json").read_text(encoding="utf-8")
        )
        self.assertFalse(example["led_enabled"])
        self.assertFalse(example["switch_enabled"])

    def _load(self, payload):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        path = root / "config" / "appliance.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return Config(path, root), root

    def test_legacy_gpio_enabled_turns_on_both_parts(self):
        cfg, _root = self._load({"gpio_enabled": True, "tx_backend": "mock", "tx_pin": 4})
        self.assertTrue(cfg.get("gpio_enabled"))
        self.assertTrue(cfg.get("led_enabled"))
        self.assertTrue(cfg.get("switch_enabled"))
        self.assertEqual(cfg.get("tx_pin"), 4)

    def test_explicit_flags_split_the_panel(self):
        cfg, _root = self._load(
            {
                "gpio_enabled": True,
                "led_enabled": True,
                "switch_enabled": False,
                "tx_backend": "mock",
            }
        )
        self.assertTrue(cfg.get("led_enabled"))
        self.assertFalse(cfg.get("switch_enabled"))
        ctl = _controls(
            Events(),
            enabled=bool(cfg.get("gpio_enabled")),
            led_enabled=bool(cfg.get("led_enabled")),
            switch_enabled=bool(cfg.get("switch_enabled")),
        )
        self.assertTrue(ctl.led_enabled)
        self.assertFalse(ctl.switch_enabled)

    def test_master_flag_off_keeps_parts_dark(self):
        cfg, root = self._load(
            {
                "gpio_enabled": False,
                "led_enabled": True,
                "switch_enabled": True,
                "tx_backend": "mock",
                "active_playlist": "default",
            }
        )
        ctl = _controls(
            Events(),
            enabled=bool(cfg.get("gpio_enabled")),
            led_enabled=bool(cfg.get("led_enabled")),
            switch_enabled=bool(cfg.get("switch_enabled")),
        )
        self.assertFalse(ctl.enabled)
        library = Library(
            root / "data" / "library",
            root / "data" / "playlists",
            root / "data" / "library.sqlite3",
        )
        controller = Controller(cfg, library, EventLog())
        status = controller.status()
        self.assertFalse(status["gpio_enabled"])
        self.assertFalse(status["led_enabled"])
        self.assertFalse(status["switch_enabled"])
        self.assertEqual(status["software_version"], "0.6.2")

    def test_part_flags_must_be_booleans(self):
        with self.assertRaises(ConfigError):
            self._load({"led_enabled": "yes", "tx_backend": "mock"})

    def test_unrelated_update_keeps_pins_and_flags(self):
        cfg, _root = self._load(
            {
                "gpio_enabled": True,
                "led_enabled": False,
                "switch_enabled": True,
                "tx_pin": 4,
                "tx_backend": "mock",
                "frequency_mhz": 90.0,
            }
        )
        cfg.update({"frequency_mhz": 100.1})
        self.assertEqual(cfg.get("frequency_mhz"), 100.1)
        self.assertEqual(cfg.get("tx_pin"), 4)
        self.assertFalse(cfg.get("led_enabled"))
        self.assertTrue(cfg.get("switch_enabled"))
        self.assertEqual(cfg.get("tx_backend"), "mock")


class OptionalPanelHelperTests(unittest.TestCase):
    def _root(self, **extra):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        payload = {
            "tx_backend": "mock",
            "tx_pin": 4,
            "frequency_mhz": 91.5,
            "gpio_enabled": False,
            "software_version": "0.6.2",
        }
        payload.update(extra)
        path = root / "config" / "appliance.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return root, path

    def _run(self, root, *args):
        return subprocess.run(
            [sys.executable, str(HELPER), "--root", str(root)] + list(args),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_each_selection_writes_defaults_without_touching_tx_pin(self):
        cases = (
            (["--led"], True, False),
            (["--switch"], False, True),
            (["--both"], True, True),
        )
        for args, led, switch in cases:
            root, path = self._root(tx_pin=17, rds_ps="KEEP")
            result = self._run(root, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(saved["gpio_enabled"])
            self.assertEqual(saved["led_enabled"], led)
            self.assertEqual(saved["switch_enabled"], switch)
            self.assertEqual(saved["led_pin"], 18)
            self.assertEqual(saved["switch_pin"], 5)
            self.assertEqual(saved["tx_pin"], 17)
            self.assertEqual(saved["tx_backend"], "mock")
            self.assertEqual(saved["frequency_mhz"], 91.5)
            self.assertEqual(saved["rds_ps"], "KEEP")
            self.assertEqual(saved["software_version"], "0.6.2")
            self.assertIn("tx_pin unchanged: 17", result.stdout)
            self.assertIn("did not start a transmitter", result.stdout)

    def test_switch_replaces_an_earlier_led_selection(self):
        root, path = self._root()
        self.assertEqual(self._run(root, "--led").returncode, 0)
        self.assertEqual(self._run(root, "--switch").returncode, 0)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertFalse(saved["led_enabled"])
        self.assertTrue(saved["switch_enabled"])
        again = self._run(root, "--both")
        self.assertEqual(again.returncode, 0, again.stderr)
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(saved["led_enabled"])
        self.assertTrue(saved["switch_enabled"])

    def test_dry_run_does_not_write(self):
        root, path = self._root()
        before = path.read_text(encoding="utf-8")
        result = self._run(root, "--dry-run", "--led")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertIn("Dry run", result.stdout)
        self.assertIn("led_enabled: on", result.stdout)

    def test_apply_refuses_while_the_controller_is_running(self):
        root, path = self._root()
        before = path.read_text(encoding="utf-8")
        lock_path = root / "data" / "appliance-controller.lock"
        lock_path.parent.mkdir(parents=True)
        handle = open(str(lock_path), "a+")
        self.addCleanup(handle.close)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            preview = self._run(root, "--dry-run", "--both")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            applied = self._run(root, "--both")
            self.assertEqual(applied.returncode, 1)
            self.assertIn("controller is running", applied.stderr)
            self.assertIn("does not start a transmitter", applied.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), before)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_choices_are_mutually_exclusive(self):
        root, _path = self._root()
        missing = self._run(root)
        self.assertNotEqual(missing.returncode, 0)
        both_flags = self._run(root, "--led", "--switch")
        self.assertNotEqual(both_flags.returncode, 0)


class InstallDocServingTests(unittest.TestCase):
    def test_symlink_and_parent_segments_stay_inside_the_install(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        secret = root / "secret.md"
        secret.write_text("SECRET-TOKEN", encoding="utf-8")
        docs = root / "docs"
        docs.mkdir()
        os.symlink(str(secret), str(docs / "alias.md"))
        code, found = lookup_public_install_file("/docs/alias.md", root=root)
        self.assertEqual(code, 403)
        self.assertIsNone(found)
        code, found = lookup_public_install_file("/docs/../secret.md", root=root)
        self.assertEqual(code, 403)
        self.assertIsNone(found)
        self.assertFalse(is_public_install_url("/favicon.ico"))

    def test_help_link_is_on_the_help_page_and_not_a_new_tab(self):
        html = INDEX.read_text(encoding="utf-8")
        js = APP_JS.read_text(encoding="utf-8")
        tabs = re.findall(r'data-tab="([^"]+)"', html)
        self.assertEqual(tabs, ["broadcast", "music", "station", "system", "help"])
        help_html = html.split('id="view-help"', 1)[1].split("</section>", 1)[0]
        self.assertIn('data-testid="optional-panel-help"', help_html)
        self.assertIn('href="/docs/optional-panel.html"', help_html)
        self.assertIn("Optional LED and shutdown switch", help_html)
        self.assertNotIn("I wired", html)
        self.assertNotIn("gpioEnabled", html)
        self.assertIn(
            '"GPIO: " + (s.gpio_enabled ? "enabled" : "disabled")',
            js,
        )

    def test_help_links_resolve_and_traversal_does_not(self):
        previous = (
            Handler.controller,
            Handler.library,
            Handler.events,
            Handler.gpio,
        )
        httpd = serve("127.0.0.1", 0, None, None, None)
        port = httpd.server_address[1]

        def restore_server():
            httpd.shutdown()
            httpd.server_close()
            (
                Handler.controller,
                Handler.library,
                Handler.events,
                Handler.gpio,
            ) = previous

        self.addCleanup(restore_server)

        def get(path):
            conn = HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            ctype = response.getheader("Content-Type") or ""
            status = response.status
            conn.close()
            return status, ctype, body

        status, _ctype, deck = get("/")
        self.assertEqual(status, 200)
        deck_html = deck.decode("utf-8")
        help_html = deck_html.split('id="view-help"', 1)[1].split("</section>", 1)[0]
        self.assertIn('href="/docs/optional-panel.html"', help_html)
        status, ctype, page = get("/docs/optional-panel.html")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/html"))
        html = page.decode("utf-8")
        self.assertIn("<h1>Optional LED and shutdown switch</h1>", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)
        self.assertIn('href="/"', html)
        self.assertIn("enable-optional-panel.py --both", html)
        self.assertIn("series resistor", html.lower())
        refs = []
        for kind, target in re.findall(r'(href|src)="([^"]+)"', html):
            absolute = urljoin("http://127.0.0.1/docs/optional-panel.html", target)
            refs.append((kind, urlparse(absolute).path))
        self.assertGreaterEqual(len(refs), 8)
        seen = set()
        for _kind, path in refs:
            if path in seen:
                continue
            seen.add(path)
            status, ctype, body = get(path)
            self.assertEqual(status, 200, path)
            self.assertGreater(len(body), 40, path)
            if path.endswith(".svg"):
                self.assertTrue(ctype.startswith("image/svg+xml"), path)
                text = body.decode("utf-8")
                self.assertIn("#0c0d10", text)
                self.assertIn("#e0b060", text)
                self.assertNotIn("power switch", text.lower())
                if "/optional-" in path:
                    self.assertIn("#5ecf8a", text)
                    self.assertIn("series resistor", text.lower())
                    self.assertIn("3.3 V", text)
                    self.assertIn("BCM 4", text)
            elif path.endswith(".py"):
                self.assertIn("text/plain", ctype)
                self.assertIn(b"--both", body)
                self.assertIn(b"tx_pin", body)
            elif path == "/":
                self.assertIn(b"optional-panel-help", body)
        self.assertIn("/docs/diagrams/optional-led.svg", seen)
        self.assertIn("/docs/diagrams/optional-shutdown-switch.svg", seen)
        self.assertIn("/docs/diagrams/optional-led-and-shutdown-switch.svg", seen)
        self.assertIn("/docs/HARDWARE.md", seen)
        self.assertIn("/scripts/enable-optional-panel.py", seen)
        led = get("/docs/diagrams/optional-led.svg")[2].decode("utf-8")
        switch = get("/docs/diagrams/optional-shutdown-switch.svg")[2].decode("utf-8")
        both = get("/docs/diagrams/optional-led-and-shutdown-switch.svg")[2].decode("utf-8")
        self.assertIn("BCM 18", led)
        self.assertIn("pin 12", led.lower())
        self.assertNotIn("BCM 5", led)
        self.assertIn("BCM 5", switch)
        self.assertIn("pin 29", switch.lower())
        self.assertIn("Shutdown switch", switch)
        self.assertIn("BCM 18", both)
        self.assertIn("BCM 5", both)
        hardware = get("/docs/HARDWARE.md")[2]
        self.assertIn(b"enable-optional-panel.py", hardware)
        self.assertIn(b"optional-led.svg", hardware)
        self.assertIn(b"optional-shutdown-switch.svg", hardware)
        for blocked in (
            "/docs/../config/default.json",
            "/docs/diagrams/../../config/default.json",
            "/docs/%2e%2e/%2e%2e/config/default.json",
            "/docs/diagrams%2f..%2f..%2fconfig/default.json",
            "/scripts/../config/default.json",
            "/scripts/install.sh",
            "/scripts/enable-optional-panel.py/../../config/default.json",
            "/docs/evidence/broadcast-control-APPROVED-BASELINE-metrics.json",
            "/docs/optional-panel.html%00.svg",
        ):
            status, _ctype, body = get(blocked)
            self.assertEqual(status, 403, blocked)
            self.assertNotIn(b"tx_backend", body)
            self.assertNotIn(b"software_version", body)
            self.assertNotIn(b"apt-get", body)
        missing, _ctype, _body = get("/docs/no-such-panel-note.md")
        self.assertEqual(missing, 404)
        icon, icon_type, _body = get("/favicon.ico")
        self.assertEqual(icon, 200)
        self.assertIn("image/x-icon", icon_type)


class DiagramOriginalityTests(unittest.TestCase):
    def test_new_drawings_match_the_existing_palette_and_are_not_copies(self):
        header = (ROOT / "docs" / "diagrams" / "raspberry-pi-header.svg").read_bytes()
        rf = (ROOT / "docs" / "diagrams" / "minimal-rf-path.svg").read_bytes()
        names = (
            "optional-led.svg",
            "optional-shutdown-switch.svg",
            "optional-led-and-shutdown-switch.svg",
        )
        for name in names:
            blob = (ROOT / "docs" / "diagrams" / name).read_bytes()
            self.assertNotEqual(blob, header)
            self.assertNotEqual(blob, rf)
            text = blob.decode("utf-8")
            self.assertNotIn("<image", text)
            self.assertNotIn("xlink:href", text)
            self.assertNotIn("https://", text)
            self.assertNotIn("power", text.lower())
