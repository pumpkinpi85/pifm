# piFM

**A lightweight Raspberry Pi FM station with a browser-based Broadcast Deck.**

piFM turns a Raspberry Pi into a small personal FM broadcast appliance. You
manage music, station identity, and on-air / off-air control from a simple web
console. Production RF uses [PiFmRds](https://github.com/ChristopheJacquet/PiFmRds)
(`pi_fm_rds`) on GPIO.

Installation and configuration **do not transmit**. The station boots
**OFF AIR** until you deliberately move the brass Broadcast handle out of its
**OFF AIR** detent — and only where that is lawful.

![Broadcast control metaphor](docs/diagrams/broadcast-lifecycle.svg)

## Design philosophy

piFM was engineered against the **Raspberry Pi Model A+ / ARMv6 / ~430–512 MiB
RAM** baseline. That is not a claim that every Raspberry Pi is automatically
supported. It is the **minimum physically validated architecture**, and it
forces the software to stay lightweight:

- Python stdlib HTTP server
- Vanilla HTML/CSS/JS Broadcast Deck (no React, no Node production runtime)
- Filesystem music library + SQLite index
- No Docker, no cloud dependency, no always-on Internet requirement

The Pi runs the station. The browser is a remote control.

## Features

- Browser **Broadcast Deck** with brass handle / pirate-flag tuner metaphor
- Explicit **OFF AIR** detent and masthead **Stop Broadcast**
- Music library, playlists, and queue (upload never starts RF)
- Station frequency (`87.1–108.2` MHz in `0.1` MHz steps) and RDS identity
- Authoritative appliance state with reconnect-safe browser synchronization
- Persisted operator broadcast intent with safe recovery gates
- Bounded FFmpeg preparation and WAV-cache limits sized for A+-class hardware
- When needed, FFmpeg prepares seekable WAV for `pi_fm_rds` (suitable WAVs may skip conversion)
- Evidence-based hardware profiles (only the A+ is physically validated / SUPPORTED)

## Minimum hardware

| Item | Notes |
|------|--------|
| Raspberry Pi Model A+ Rev 1.1 (or stronger experimental board) | A+ is the validated floor |
| microSD card | Size depends on OS plus how much music you keep and WAV-cache headroom; larger cards store more library media |
| Power supply | Adequate for your Pi model |
| Network (optional for RF) | Needed for install and browser control; not required to stay on air |
| RF path | GPIO 4 / header pin 7 → your filter/load/antenna (operator-supplied) |

## Supported hardware (summary)

| Board | Classification |
|-------|----------------|
| Raspberry Pi Model A+ Rev 1.1 | **SUPPORTED / PHYSICALLY VALIDATED** |
| Raspberry Pi Zero / Zero W / Zero 2 W | **EXPERIMENTAL / NOT PHYSICALLY VALIDATED** |
| Raspberry Pi 2 / 3 / 4 | **EXPERIMENTAL / NOT PHYSICALLY VALIDATED** |
| Raspberry Pi 5 | **UNSUPPORTED** |

Auto-detection can identify board text from the Pi, but **only the Model A+**
maps to a SUPPORTED hardware profile today. A newer board is not automatically
granted extra features or a higher support tier just because it is recognized.
Full matrix and detection rules: [docs/HARDWARE.md](docs/HARDWARE.md)

## Quick start

1. Flash **Raspberry Pi OS Lite**, enable SSH if desired, boot, and connect
   networking. Details: [docs/INSTALL.md](docs/INSTALL.md)
2. Install packages, build `pi_fm_rds`, and install piFM to `/opt/pifm`.
3. Open the Broadcast Deck: `http://<pi-address>:8080/`
4. Complete first-run setup (hardware, station name/frequency, music).
5. On **Music**, upload tracks you are allowed to use.
6. On **Station**, set the exact frequency you intend to use.
7. On **Broadcast**, when lawful and intentional, drag the brass handle out of
   **OFF AIR**, release, and confirm.
8. Return the handle to **OFF AIR** or press **Stop Broadcast** to end RF.

GPIO / RF connection details: [docs/HARDWARE.md](docs/HARDWARE.md)

## First-run workflow

Fresh installs open a short browser setup:

1. Welcome
2. Detected hardware and readiness
3. Frequency and station identity (RDS)
4. Music import
5. Handoff to the Broadcast Deck — still **OFF AIR**

## Adding music

Accepted filename families: **MP3, WAV, FLAC, M4A, AAC, OGG** (up to 128 MiB).
FFprobe/FFmpeg decide actual decodability. Fresh installs also seed one bundled
demo track — **The Sky Belongs to No King** by **Brynja Vinter** — into
`data/library/demo/` (see [examples/demo/README.md](examples/demo/README.md)).
You can keep it, replace it, or upload your own music. Details:
[docs/USER-MANUAL.md](docs/USER-MANUAL.md) and [docs/music.md](docs/music.md)

## Offline / disconnected behaviour

After setup, playback and FM transmission do not require LAN, DNS, NTP,
Internet, or an open browser. Losing the network does not stop a broadcast.
Reconnecting a browser rebuilds UI state from one authoritative appliance
snapshot — it never starts or stops RF by itself.

## Safety and regulatory notice

GPIO FM output can radiate. Filtering, antenna design, emissions, and legal
authorization are **your** responsibility. Prefer a shielded load into a
receiver for experimentation. See [docs/rf-and-law.md](docs/rf-and-law.md).

## Documentation

| Doc | Purpose |
|-----|---------|
| [docs/INSTALL.md](docs/INSTALL.md) | Blank-card install, update, backup, rollback |
| [docs/HARDWARE.md](docs/HARDWARE.md) | Board matrix, GPIO pin, prerequisites |
| [docs/USER-MANUAL.md](docs/USER-MANUAL.md) | Broadcast Deck daily use |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common failures |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Engineering overview |
| [docs/music.md](docs/music.md) | Media contract and resource rules |
| [SECURITY.md](SECURITY.md) | Security reporting |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution and A+ floor rule |
| [CHANGELOG.md](CHANGELOG.md) | Release history |

## Support / contributing

When published, the intended canonical home is
**https://github.com/pumpkinpi85/pifm** under the **pumpkinpi85** account.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

GPL-3.0 — see [LICENSE](LICENSE) and [third_party/NOTICE.md](third_party/NOTICE.md).

## Status

Product candidate **0.6.2** (P1H public-release documentation & sanitation) on
top of the A+-validated **0.6.1** appliance behaviour. GitHub publication and
any further RF work remain separately authorized founder gates.
