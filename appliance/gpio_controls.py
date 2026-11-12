"""Optional GPIO LED and shutdown switch. Disabled by default; never starts TX.

The loader matches the reference Model A+ station: try the imported
``RPi.GPIO`` first, then the dist-packages copy when that import can only
open ``/dev/mem``. The lamp is steady only while a transmitter is running.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional, Tuple

if TYPE_CHECKING:
    from .events import EventLog

# OS package path used when a shadowed RPi.GPIO can only open /dev/mem.
SYSTEM_GPIO_DIST_PACKAGES = "/usr/lib/python3/dist-packages"
# Single documented FM pin for 40-pin boards. Panel code must not claim it.
DOCUMENTED_RF_BCM = 4


def broadcast_led_pattern(state: str, transmitting: bool) -> str:
    """Steady light only while a transmitter is actually on.

    OFF and READY stay dark. A fault blinks. ON_AIR without a running
    transmitter stays dark so the lamp cannot claim a broadcast that is not there.
    """
    if state == "FAULT":
        return "fault"
    if state == "ON_AIR" and transmitting:
        return "on_air"
    return "off"


def led_steps(pattern: str) -> Tuple[Tuple[bool, float], ...]:
    """One visual cycle: (level_high, seconds). On-air is steady, not a blink."""
    if pattern == "on_air":
        return ((True, 0.2),)
    if pattern == "fault":
        return ((True, 0.05), (False, 0.05))
    return ((False, 0.2),)


def _load_imported_gpio():
    # Optional dependency, imported here so non-Pi hosts and unit tests can
    # load this module. The system loader repeats the import after it puts
    # dist-packages first and drops a shadowed RPi entry.
    import RPi.GPIO as gpio  # type: ignore

    return gpio


def _load_system_gpio():
    """Prefer the OS RPi.GPIO when the shadowed copy can only open /dev/mem."""
    system_path = SYSTEM_GPIO_DIST_PACKAGES
    for name in list(sys.modules):
        if name == "RPi" or name.startswith("RPi."):
            del sys.modules[name]
    sys.path.insert(0, system_path)
    try:
        return importlib.import_module("RPi.GPIO")
    finally:
        try:
            sys.path.remove(system_path)
        except ValueError:
            pass


def _gpio_loaders():
    return (_load_imported_gpio, _load_system_gpio)


class GpioControls:
    def __init__(
        self,
        led_pin: int,
        switch_pin: int,
        enabled: bool,
        on_shutdown: Callable[[], None],
        events: "EventLog",
        led_enabled: Optional[bool] = None,
        switch_enabled: Optional[bool] = None,
    ) -> None:
        self.led_pin = int(led_pin)
        self.switch_pin = int(switch_pin)
        use_led = enabled if led_enabled is None else led_enabled
        use_switch = enabled if switch_enabled is None else switch_enabled
        self.led_enabled = bool(enabled) and bool(use_led)
        self.switch_enabled = bool(enabled) and bool(use_switch)
        self.enabled = self.led_enabled or self.switch_enabled
        self.on_shutdown = on_shutdown
        self.events = events
        self._gpio = None
        self._thread = None  # type: Optional[threading.Thread]
        self._stop = threading.Event()
        self._pattern = "off"  # off|on_air|fault

    def start(self) -> None:
        if not self.enabled:
            self.events.emit("gpio", "GPIO disabled in config")
            return
        problem = self._pin_problem()
        if problem:
            self.enabled = False
            self.led_enabled = False
            self.switch_enabled = False
            self.events.emit("gpio", "GPIO unavailable: {}".format(problem))
            return
        last_error = None  # type: Optional[BaseException]
        for load_gpio in _gpio_loaders():
            gpio = None
            try:
                gpio = load_gpio()
                self._gpio = gpio
                gpio.setmode(gpio.BCM)
                if self.led_enabled:
                    gpio.setup(self.led_pin, gpio.OUT)
                    gpio.output(self.led_pin, gpio.LOW)
                if self.switch_enabled:
                    gpio.setup(
                        self.switch_pin, gpio.IN, pull_up_down=gpio.PUD_UP
                    )
                    gpio.add_event_detect(
                        self.switch_pin,
                        gpio.FALLING,
                        callback=self._switch_cb,
                        bouncetime=300,
                    )
                if self.led_enabled:
                    self._thread = threading.Thread(
                        target=self._led_loop, daemon=True
                    )
                    self._thread.start()
                self.events.emit(
                    "gpio",
                    "GPIO started led={} switch={}".format(
                        self.led_pin if self.led_enabled else "off",
                        self.switch_pin if self.switch_enabled else "off",
                    ),
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                self._gpio = None
                self._stop.set()
                if self._thread is not None:
                    self._thread.join(timeout=1.0)
                    self._thread = None
                self._stop = threading.Event()
                if gpio is not None:
                    try:
                        gpio.cleanup()
                    except Exception:
                        pass
        self.enabled = False
        self.events.emit("gpio", "GPIO unavailable: {}".format(last_error))

    def _pin_problem(self) -> Optional[str]:
        if self.led_enabled and self.led_pin == DOCUMENTED_RF_BCM:
            return "led_pin must not use BCM {} (FM output)".format(DOCUMENTED_RF_BCM)
        if self.switch_enabled and self.switch_pin == DOCUMENTED_RF_BCM:
            return "switch_pin must not use BCM {} (FM output)".format(
                DOCUMENTED_RF_BCM
            )
        if (
            self.led_enabled
            and self.switch_enabled
            and self.led_pin == self.switch_pin
        ):
            return "led_pin and switch_pin must differ"
        return None

    def _switch_cb(self, channel: int) -> None:
        self.events.emit("physical_switch", "falling edge on {}".format(channel))
        # Shutdown after STOP only. This callback must never start TX.
        try:
            self.on_shutdown()
        except Exception as exc:  # noqa: BLE001
            self.events.emit("gpio", "shutdown handler error: {}".format(exc))
        try:
            subprocess.Popen(["sudo", "shutdown", "-h", "now"])
        except Exception as exc:  # noqa: BLE001
            self.events.emit("gpio", "shutdown command failed: {}".format(exc))

    def set_pattern(self, pattern: str) -> None:
        self._pattern = pattern

    def _led_loop(self) -> None:
        gpio = self._gpio
        if gpio is None or not self.led_enabled:
            return
        while not self._stop.is_set():
            try:
                for level_high, seconds in led_steps(self._pattern):
                    if self._stop.is_set():
                        break
                    gpio.output(
                        self.led_pin, gpio.HIGH if level_high else gpio.LOW
                    )
                    if self._stop.wait(seconds):
                        break
            except Exception:
                time.sleep(0.5)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._gpio is not None:
            try:
                if self.led_enabled:
                    self._gpio.output(self.led_pin, self._gpio.LOW)
                self._gpio.cleanup()
            except Exception:
                pass
