# piFM

**Lightweight Raspberry Pi FM broadcast appliance**

piFM turns a Raspberry Pi into a browser-operated FM station using GPIO-based
FM generation (`pi_fm_rds`) and a simple Broadcast Deck console.

## Product principles

- **Real transmitter, explicit operator intent.** Production targets
  `pi_fm_rds`. Fresh installs boot **OFF AIR**. Raising the Black Flag arms
  safe power-restoration recovery; absolute STOP disarms it.
- **The Pi runs the station.** The browser is a remote control. Broadcasting
  does not require LAN, Internet, DNS, NTP, or an open browser.
- **Reference hardware:** Raspberry Pi Model A+ Rev 1.1 (minimum / performance floor).
- **Stay light:** Python + stdlib HTTP server + vanilla HTML/CSS/JS. No Docker,
  no Node production runtime, no React, no cloud requirement.

## Quick start (experienced Pi users)

1. Flash Raspberry Pi OS Lite and boot with network/SSH.
2. Install packages, build [PiFmRds](https://github.com/ChristopheJacquet/PiFmRds),
   then install this repository (see [docs/installation.md](docs/installation.md)).
3. Open the Broadcast Deck in a browser (`http://<pi-hostname>:8080/`).
4. Add music, set frequency/RDS, then **Raise the Black Flag** only when you
   intend to transmit — and only where you are legally allowed to do so.

Installation and configuration **do not** start broadcasting.

## Documentation

- [PROJECT_MASTER.md](PROJECT_MASTER.md) — durable project authority
- [docs/](docs/) — installation, hardware, operation, RF/law
- [Hardware and first run](docs/hardware-and-first-run.md) — detection,
  readiness, setup, original wiring/pipeline diagrams
- [Music workspace](docs/music.md) — browser imports, playlists, and queue
- [Standalone appliance](docs/standalone-appliance.md) — browser/network
  independence, persisted broadcast intent, and power recovery
- [CONTRIBUTING.md](CONTRIBUTING.md) — including the A+ minimum-hardware rule
- [SECURITY.md](SECURITY.md)

## License

GPL-3.0. See [LICENSE](LICENSE) and [third_party/NOTICE.md](third_party/NOTICE.md).

## Maintainer / canonical home

Canonical public repository (when published): **https://github.com/pumpkinpi85/pifm**

Maintainer account: **[pumpkinpi85](https://github.com/pumpkinpi85)** — not any other
organization. Clone/fork rights under GPL-3.0 are separate from write access to
that repository.

## Status

P1G product candidate **0.6.0** adds atomic operator broadcast intent,
standalone playlist progression, and safety-gated service/power recovery.
Physical power-restoration RF validation remains founder-gated.
The reference station runs the canonical product from `/opt/pifm`.
Legacy personal trees remain read-only pending final retirement.
GitHub publication is not performed until separately authorized.
