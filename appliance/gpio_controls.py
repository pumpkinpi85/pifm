"""Optional GPIO LED/switch. Disabled by default; never starts TX."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .controller import Controller
    from .events import EventLog


class GpioControls:
    def __init__(
        self,
        led_pin: int,
        switch_pin: int,
        enabled: bool,
        on_shutdown: Callable[[], None],
        events: "EventLog",
    ) -> None:
        self.led_pin = led_pin
        self.switch_pin = switch_pin
        self.enabled = enabled
        self.on_shutdown = on_shutdown
        self.events = events
        self._gpio = None
        self._thread = None  # type: Optional[threading.Thread]
        self._stop = threading.Event()
        self._pattern = "off"  # off|ready|on_air|fault

    def start(self) -> None:
        if not self.enabled:
            self.events.emit("gpio", "GPIO disabled in config")
            return
        try:
            import RPi.GPIO as GPIO  # type: ignore

            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.led_pin, GPIO.OUT)
            GPIO.output(self.led_pin, GPIO.LOW)
            GPIO.setup(self.switch_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self.switch_pin,
                GPIO.FALLING,
                callback=self._switch_cb,
                bouncetime=300,
            )
            self._thread = threading.Thread(target=self._led_loop, daemon=True)
            self._thread.start()
            self.events.emit(
                "gpio",
                "GPIO started led={} switch={}".format(self.led_pin, self.switch_pin),
            )
        except Exception as exc:  # noqa: BLE001
            self.enabled = False
            self.events.emit("gpio", "GPIO unavailable: {}".format(exc))

    def _switch_cb(self, channel: int) -> None:
        self.events.emit("physical_switch", "falling edge on {}".format(channel))
        # Conservative: shutdown semantics only — never TX ON
        try:
            self.on_shutdown()
        except Exception as exc:  # noqa: BLE001
            self.events.emit("gpio", "shutdown handler error: {}".format(exc))
        try:
            import subprocess

            subprocess.Popen(["sudo", "shutdown", "-h", "now"])
        except Exception as exc:  # noqa: BLE001
            self.events.emit("gpio", "shutdown command failed: {}".format(exc))

    def set_pattern(self, pattern: str) -> None:
        self._pattern = pattern

    def _led_loop(self) -> None:
        GPIO = self._gpio
        if GPIO is None:
            return
        while not self._stop.is_set():
            p = self._pattern
            try:
                if p == "off":
                    GPIO.output(self.led_pin, GPIO.LOW)
                    time.sleep(0.2)
                elif p == "ready":
                    GPIO.output(self.led_pin, GPIO.HIGH)
                    time.sleep(0.2)
                elif p == "on_air":
                    GPIO.output(self.led_pin, GPIO.HIGH)
                    time.sleep(0.15)
                    GPIO.output(self.led_pin, GPIO.LOW)
                    time.sleep(0.15)
                elif p == "fault":
                    GPIO.output(self.led_pin, GPIO.HIGH)
                    time.sleep(0.05)
                    GPIO.output(self.led_pin, GPIO.LOW)
                    time.sleep(0.05)
                else:
                    GPIO.output(self.led_pin, GPIO.LOW)
                    time.sleep(0.2)
            except Exception:
                time.sleep(0.5)

    def stop(self) -> None:
        self._stop.set()
        if self._gpio is not None:
            try:
                self._gpio.output(self.led_pin, self._gpio.LOW)
                self._gpio.cleanup()
            except Exception:
                pass
