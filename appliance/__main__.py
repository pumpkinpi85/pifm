"""piFM appliance entrypoint."""

from __future__ import annotations

import argparse
import atexit
import fcntl
import signal
import sys
import time
from pathlib import Path

from .config import Config, default_config_path, discover_root
from .controller import Controller
from .events import EventLog
from .gpio_controls import GpioControls
from .library import Library
from .tx import kill_all_transmitters
from .webapp import serve


def acquire_controller_lease(root: Path):
    """Prevent a second appliance controller from disturbing the active one."""
    path = root / "data" / "appliance-controller.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(str(path), "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("Another piFM appliance controller is already running")
    return handle


def ensure_startup_rf_off() -> None:
    """Clear orphan transmitter workers before the HTTP API is exposed."""
    sweep = kill_all_transmitters()
    if not sweep.get("clear", False):
        raise RuntimeError(
            "Startup refused: transmitter processes could not be cleared"
        )


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

    controller_lease = acquire_controller_lease(root)
    atexit.register(controller_lease.close)
    try:
        ensure_startup_rf_off()
    except Exception:
        controller_lease.close()
        raise
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
    # Construction is always OFF. Persisted operator ON intent is evaluated
    # only after the control server exists and through normal readiness gates.
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
        controller.begin_shutdown()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    controller.restore_persisted_broadcast_intent(wait=False)

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
        controller.service_shutdown()
        gpio.stop()
        httpd.shutdown()
        events.emit("controller_startup", "shutdown complete; TX=OFF")
        controller_lease.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
