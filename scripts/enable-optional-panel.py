#!/usr/bin/env python3
"""Enable the optional status LED, shutdown switch, or both.

Writes the documented default pins (LED BCM 18 / header 12, shutdown switch
BCM 5 / header 29) and turns on only the part you name. Does not change the
FM pin, does not restart the appliance, and does not start a transmitter.

Stop the appliance service before an apply. A running Deck can rewrite
config from the settings it loaded at startup.
"""

from __future__ import annotations

import argparse
import fcntl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from appliance.config import Config, default_config_path, discover_root

LED_BCM = 18
SWITCH_BCM = 5


def _parse(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument(
        "--led",
        action="store_true",
        help="Enable only the status LED on BCM 18",
    )
    choice.add_argument(
        "--switch",
        action="store_true",
        help="Enable only the shutdown switch on BCM 5",
    )
    choice.add_argument(
        "--both",
        action="store_true",
        help="Enable the status LED and the shutdown switch",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the selection and do not write config",
    )
    parser.add_argument("--root", default=None, help="PIFM_ROOT override")
    parser.add_argument("--config", default=None, help="Appliance config path")
    return parser.parse_args(argv)


def _selection(args):
    if args.led:
        return True, False
    if args.switch:
        return False, True
    if args.both:
        return True, True
    raise SystemExit("Choose --led, --switch, or --both")


def _ensure_config_file(root, cfg_path):
    if cfg_path.exists():
        return
    default = root / "config" / "default.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if default.is_file():
        cfg_path.write_text(default.read_text(encoding="utf-8"), encoding="utf-8")


def _controller_lock(root):
    """Hold the controller lease so a live appliance cannot rewrite config."""
    path = root / "data" / "appliance-controller.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(str(path), "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _summary(candidate, tx_pin):
    led = "on" if candidate.get("led_enabled") else "off"
    switch = "on" if candidate.get("switch_enabled") else "off"
    lines = [
        "Optional panel selection",
        "  gpio_enabled: {}".format(bool(candidate.get("gpio_enabled"))),
        "  led_enabled: {} (BCM {})".format(led, candidate.get("led_pin")),
        "  switch_enabled: {} (BCM {})".format(switch, candidate.get("switch_pin")),
        "  tx_pin unchanged: {}".format(tx_pin),
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    args = _parse(argv)
    root = Path(args.root).resolve() if args.root else discover_root()
    cfg_path = (
        Path(args.config).resolve() if args.config else default_config_path(root)
    )
    _ensure_config_file(root, cfg_path)
    led_on, switch_on = _selection(args)
    patch = {
        "led_pin": LED_BCM,
        "switch_pin": SWITCH_BCM,
        "gpio_enabled": True,
        "led_enabled": led_on,
        "switch_enabled": switch_on,
    }
    config = Config(cfg_path, root)
    before_tx = config.get("tx_pin")
    before_backend = config.get("tx_backend")
    candidate = config.validate_update(patch)
    if candidate.get("tx_pin") != before_tx or candidate.get("tx_backend") != before_backend:
        print(
            "Refusing to change the FM pin or the transmitter backend.",
            file=sys.stderr,
        )
        return 1
    summary = _summary(candidate, before_tx)
    if args.dry_run:
        print(summary)
        print("Dry run: config was not written.")
        print(
            "This helper does not start the appliance and does not start a transmitter."
        )
        return 0
    lock = _controller_lock(root)
    if lock is None:
        print(
            "The appliance controller is running. Stop the service, rerun this helper, "
            "then start the service yourself only when the station should remain OFF AIR. "
            "This helper does not stop the service and does not start a transmitter.",
            file=sys.stderr,
        )
        return 1
    try:
        config.load()
        before_tx = config.get("tx_pin")
        before_backend = config.get("tx_backend")
        candidate = config.validate_update(patch)
        if (
            candidate.get("tx_pin") != before_tx
            or candidate.get("tx_backend") != before_backend
        ):
            print(
                "Refusing to change the FM pin or the transmitter backend.",
                file=sys.stderr,
            )
            return 1
        config.update(patch)
        summary = _summary(config.as_dict(), config.get("tx_pin"))
    finally:
        lock.close()
    print(summary)
    print("Wrote {}.".format(cfg_path))
    print(
        "Start the appliance service yourself only when the station should remain OFF AIR. "
        "This helper did not start it and did not start a transmitter."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
