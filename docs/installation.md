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

## 3. Obtain piFM

Clone this repository to a working path, then install:

```bash
cd /path/to/pifm-open-source
sudo ./scripts/install.sh
```

Default install prefix: `/opt/pifm` (override with `PIFM_PREFIX`).

Optional: `PI_FM_RDS_SRC=/path/to/PiFmRds` builds/installs the binary during install.

## 4. Configure

Edit `/opt/pifm/config/appliance.json` (copied from `examples/config.example.json`
on first install). Confirm:

- `tx_backend`: `pi_fm_rds`
- `pi_fm_rds_path`: path to your binary
- frequency/RDS values lawful for your location

## 5. Open the Deck

Browse to `http://<pi-address>:8080/`.

**Installation does not transmit.** The station should report OFF AIR until you
explicitly Raise the Black Flag.
