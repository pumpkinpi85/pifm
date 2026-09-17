#!/usr/bin/env python3
"""Run the real dashboard against a temporary, RF-incapable mock appliance."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import tempfile
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from appliance.config import Config
from appliance.controller import Controller
from appliance.events import EventLog
from appliance.library import Library
from appliance.webapp import serve


def write_silence(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(44100)
        output.writeframes(b"\x00\x00" * 44100)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument(
        "--initial-fault",
        action="store_true",
        help="start in a synthetic FAULT state for browser rendering tests",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="pifm-ui-harness-") as tmp:
        root = Path(tmp)
        music = root / "data/library/Flagpole Test.wav"
        write_silence(music)
        config_path = root / "config/appliance.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "frequency_mhz": 99.9,
                    "rds_ps": "piFM",
                    "rds_rt": "Non-RF browser validation",
                    "rds_pi": "FFFF",
                    "active_playlist": "default",
                    "shuffle": False,
                    "repeat": True,
                    "library_dir": "data/library",
                    "playlists_dir": "data/playlists",
                    "led_pin": 18,
                    "switch_pin": 5,
                    "tx_pin": 4,
                    "web_host": args.host,
                    "web_port": args.port,
                    "tx_backend": "mock",
                    "pi_fm_rds_path": "/usr/local/bin/pi_fm_rds",
                    "pi_fm_rds_ppm": 0.0,
                    "network_iface": "eth0",
                    "hardware_profile": "raspberry-pi-a-plus",
                    "hardware_profile_mode": "manual",
                    "setup_completed": True,
                    "gpio_enabled": False,
                    "local_monitor": False,
                    "rf_quiet_mode": "simulate",
                    "rf_quiet_seconds": 60,
                    "product_name": "piFM Pirate Radio",
                    "software_version": "browser-harness",
                }
            ),
            encoding="utf-8",
        )
        library = Library(
            library_dir=root / "data/library",
            playlists_dir=root / "data/playlists",
            db_path=root / "data/library.sqlite3",
        )
        library.ensure_default_playlists(["default"])
        tracks = library.reindex()
        indexed = library.search("")
        if tracks != 1 or not indexed:
            raise RuntimeError("failed to index non-RF harness track")
        playlist = library.load_playlist("default")
        playlist["tracks"] = [indexed[0]["id"]]
        library.save_playlist("default", playlist)

        config = Config(config_path, root)
        events = EventLog(maxlen=200)
        controller = Controller(config, library, events)
        if args.initial_fault:
            controller.sm.enter_fault(
                "Synthetic non-RF browser validation fault"
            )
        server = serve(
            host=args.host,
            port=args.port,
            controller=controller,
            library=library,
            events=events,
            gpio=None,
        )
        stopping = {"value": False}

        def stop(signum, frame) -> None:
            stopping["value"] = True

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        print(
            "Non-RF piFM UI harness listening on http://{}:{}".format(
                args.host, args.port
            ),
            flush=True,
        )
        try:
            while not stopping["value"]:
                controller.watchdog()
                time.sleep(0.1)
        finally:
            controller.service_shutdown()
            server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
