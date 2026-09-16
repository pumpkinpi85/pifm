# Installation

Assumes you can flash Raspberry Pi OS, boot, and use a terminal/SSH.

## 1. Prerequisites

On Raspberry Pi OS Lite (recommended):

```bash
sudo apt update
sudo apt install -y git python3 ffmpeg libsndfile1-dev build-essential
```

## 2. Build PiFmRds

```bash
git clone https://github.com/ChristopheJacquet/PiFmRds.git
cd PiFmRds/src
make clean && make
sudo install -m 755 pi_fm_rds /usr/local/bin/pi_fm_rds
```

Always rebuild when changing Pi model/architecture.

Or use the helper (still builds on-device for A+/ARMv6):

```bash
./scripts/install-pi-fm-rds.sh --src /path/to/PiFmRds
```

Canonical runtime path: `/usr/local/bin/pi_fm_rds` (see `examples/config.example.json`).
Do not hard-code personal home-directory transmitter trees in product config.

## 3. Obtain piFM

Clone this repository to a working path, then install:

```bash
cd /path/to/pifm-open-source
sudo ./scripts/install.sh
```

Default install prefix: `/opt/pifm` (override with `PIFM_PREFIX`).

Optional: `PI_FM_RDS_SRC=/path/to/PiFmRds` builds/installs the binary during install.

The installer reports hardware readiness without changing boot settings. On a
detected Model A+, explicitly opt into the backed-up headless/onboard-audio
configuration with:

```bash
sudo PIFM_CONFIGURE_HARDWARE=1 ./scripts/install.sh
```

Review [hardware and first run](hardware-and-first-run.md) before applying it.

Clean-room / lifecycle-only (no RF intent):

```bash
sudo PIFM_TX_BACKEND=mock ./scripts/install.sh
```

Host non-RF gate before claiming a candidate ready:

```bash
./scripts/run-validation.sh
```

## 4. Configure

Fresh installations open the short browser setup for detected hardware,
frequency/RDS identity, and music. Advanced configuration remains available in
`/opt/pifm/config/appliance.json`.

## 5. Open the Deck

Browse to `http://<pi-address>:8080/`.

**Installation does not transmit.** The station should report OFF AIR until you
explicitly move the brass Broadcast handle into the FM tuner.
