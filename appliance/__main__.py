"""piFM appliance entrypoint."""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from .config import Config, default_config_path, discover_root
from .controller import Controller
from .events import EventLog
from .gpio_controls import GpioControls
from .library import Library
from .webapp import serve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="piFM appliance controller")
    parser.add_argument("--root", default=None, help="PIFM_ROOT override")
    parser.add_argument("--config", default=None, help="config json path")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else discover_root()
    cfg_path = Path(args.config).resolve() if args.config else default_config_path(root)
    if not cfg_path.exists():
        default = root / "config" / "default.json"
        if default.exists():
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(default.read_text())

    config = Config(cfg_path, root)
    events = EventLog(
        maxlen=500,
        persist_path=root / "data" / "logs" / "ships-log.jsonl",
    )
    library = Library(
        library_dir=config.resolve("library_dir"),
        playlists_dir=config.resolve("playlists_dir"),
        db_path=root / "data" / "library.sqlite3",
    )
    library.ensure_default_playlists(
        [
            "default",
            "favorites",
            "classical",
            "rock",
            "jazz",
            "custom",
        ]
    )
    library.reindex()

    controller = Controller(config, library, events)
    # Boot invariant: never ON_AIR; TX child not running
    assert controller.sm.state.value in ("SAFE_OFF", "READY")
    assert not controller.tx.is_running()
    events.emit(
        "controller_startup",
        "pifm-appliance online; state={} TX=OFF".format(controller.sm.state.value),
    )

    def on_shutdown() -> None:
        controller.shutdown_request()

    gpio = GpioControls(
        led_pin=int(config.get("led_pin")),
        switch_pin=int(config.get("switch_pin")),
        enabled=bool(config.get("gpio_enabled")),
        on_shutdown=on_shutdown,
        events=events,
    )
    gpio.start()

    httpd = serve(
        host=str(config.get("web_host")),
        port=int(config.get("web_port")),
        controller=controller,
        library=library,
        events=events,
        gpio=gpio,
    )
    events.emit(
        "controller_startup",
        "web listening on {}:{}".format(config.get("web_host"), config.get("web_port")),
    )

    stop = {"flag": False}

    def _stop(signum, frame) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while not stop["flag"]:
            controller.watchdog()
            st = controller.sm.state.value
            if st == "ON_AIR":
                gpio.set_pattern("on_air")
            elif st == "READY":
                gpio.set_pattern("ready")
            elif st == "FAULT":
                gpio.set_pattern("fault")
            else:
                gpio.set_pattern("off")
            time.sleep(0.5)
    finally:
        controller.tx_off()
        gpio.stop()
        httpd.shutdown()
        events.emit("controller_startup", "shutdown complete; TX=OFF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
