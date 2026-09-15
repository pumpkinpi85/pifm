# Optional controls

When `gpio_enabled` is true (default **false**):

- LED on `led_pin` (default BCM 18) reflects READY / ON AIR / FAULT patterns
- Falling edge on `switch_pin` (default BCM 5) requests shutdown (stops TX, then
  `shutdown -h now`)

These pins are conveniences, not required for core Broadcast Deck operation.
