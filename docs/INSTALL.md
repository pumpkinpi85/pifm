# Installation

Complete blank-card journey for a new Raspberry Pi. This guide never starts
FM transmission. After install the service should report **OFF AIR**.

## 1. Flash Raspberry Pi OS

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose **Raspberry Pi OS Lite** (32-bit) for Model A+ / ARMv6-class boards.
3. In Imager advanced options, set hostname, user, password, locale, and
   enable SSH if you will administer the Pi remotely.
4. Write the image, insert the card, and boot.

Card capacity is an operator choice: Raspberry Pi OS needs some space, and the
rest is mainly your music library plus disposable WAV-cache growth. A larger
card simply holds more media; piFM does not require one fixed large size.

For the physically validated Model A+ path, plan on a **headless**
(`multi-user.target`) boot with **onboard audio disabled**. See
[HARDWARE.md](HARDWARE.md).

## 2. Network and first login

1. Connect Ethernet (A+ reference) or configure Wi-Fi as appropriate for your
   board.
2. Log in locally or via SSH:
   ```bash
   ssh <user>@<pi-hostname-or-ip>
   ```
3. Update packages:
   ```bash
   sudo apt update
   sudo apt full-upgrade -y
   ```

## 3. Install build dependencies

```bash
sudo apt install -y git python3 ffmpeg libsndfile1-dev build-essential rsync
```

`ffmpeg` is required for media validation and, when needed, conversion to a
seekable WAV that `pi_fm_rds` can play. Suitable seekable PCM WAVs may skip
conversion. `libsndfile1-dev` and a C toolchain are required to build PiFmRds.

## 4. Build and install `pi_fm_rds`

Always build on the target architecture (especially A+ / ARMv6). piFM pins a
specific upstream [ChristopheJacquet/PiFmRds](https://github.com/ChristopheJacquet/PiFmRds)
commit (see `third_party/pifmrds.pin`); do **not** float on `master`, do **not**
use `pumpkinpi85/PiFmRds`, and do **not** expect PiFmRds sources inside this
repository.

Recommended (clones/checks out the pin, builds, installs):

```bash
# from a piFM checkout; requires sudo for /usr/local
sudo ./scripts/install-pi-fm-rds.sh
```

Manual equivalent:

```bash
PIN=777f8e52648b88156483d067e24e0b8682abe8c1
git clone https://github.com/ChristopheJacquet/PiFmRds.git
cd PiFmRds
git checkout "$PIN"
cd src
make clean && make
sudo install -m 755 pi_fm_rds /usr/local/bin/pi_fm_rds
pi_fm_rds 2>&1 | head -n 5 || true
```

If you already have a checkout, pass it only when it is at the pinned SHA:

```bash
sudo ./scripts/install-pi-fm-rds.sh --src /path/to/PiFmRds
```

Canonical binary path: `/usr/local/bin/pi_fm_rds`.
Validated upstream pin: `777f8e52648b88156483d067e24e0b8682abe8c1`
([commit](https://github.com/ChristopheJacquet/PiFmRds/commit/777f8e52648b88156483d067e24e0b8682abe8c1)).

## 5. Obtain piFM

```bash
git clone https://github.com/pumpkinpi85/pifm.git
cd pifm
```

You only need the product tree — not any private developer machine, personal
music library, or reference-station backup.

## 6. Install the appliance

Default install root: `/opt/pifm`.

```bash
sudo ./scripts/install.sh
```

Useful variants:

| Goal | Command |
|------|---------|
| Also build pinned PiFmRds during install | `sudo ./scripts/install.sh` (clones upstream pin when `/usr/local/bin/pi_fm_rds` is missing) |
| Use an existing pinned checkout | `sudo PI_FM_RDS_SRC=/path/to/PiFmRds ./scripts/install.sh` |
| Skip TX build (mock / pre-provisioned host) | `sudo PIFM_SKIP_PIFMRDS_BUILD=1 ./scripts/install.sh` |
| Apply A+ headless/onboard-audio prerequisites | `sudo PIFM_CONFIGURE_HARDWARE=1 ./scripts/install.sh` |
| Lifecycle-only (no live TX backend) | `sudo PIFM_TX_BACKEND=mock ./scripts/install.sh` |
| Non-root service user | `sudo PIFM_USER=myuser ./scripts/install.sh` |
| Alternate prefix | `sudo PIFM_PREFIX=/srv/pifm ./scripts/install.sh` |

When `tx_backend` is `pi_fm_rds` and `/usr/local/bin/pi_fm_rds` is missing,
`install.sh` runs `scripts/install-pi-fm-rds.sh`, which checks out
`ChristopheJacquet/PiFmRds` at the SHA in `third_party/pifmrds.pin`, builds
on-device, and installs the binary to `/usr/local/bin/pi_fm_rds`.

The installer:

- copies application files to the prefix
- creates operator-data directories
- writes `config/appliance.json` from the example **only if missing**
- builds/installs pinned upstream `pi_fm_rds` when needed (see above)
- installs and enables `pifm-appliance.service`
- does **not** start broadcasting

## Demo media

Fresh installs seed **The Sky Belongs to No King** by **Brynja Vinter** from
`examples/demo/` into `data/library/demo/` when that file is not already
present, and create a `default` playlist for it when no playlist exists yet.
Existing operator media and playlists are never overwritten by this seed.

## First-run setup

1. Open `http://<pi-address>:8080/`
2. Complete the five-step setup (hardware → station → music → deck)
3. Confirm Broadcast state shows **OFF AIR** / **READY**

Service checks:

```bash
systemctl status pifm-appliance.service
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool | head
```

Look for `broadcast_state: "off"`, `software_version`, and `git_sha` /
`build_label` when present.

## Expected filesystem layout

```
/opt/pifm/                 # application + operator data root (PIFM_ROOT)
  appliance/               # application (replaced on update)
  config/
    default.json           # application template
    appliance.json         # LIVE operator config (preserved)
  data/
    library/               # uploaded music (preserved); demo/ seeded when missing
    playlists/             # playlists (preserved)
    library.sqlite3        # library index (preserved)
    recovery/              # broadcast intent markers (preserved)
    logs/                  # logs + WAV cache (disposable)
  examples/
    demo/                  # bundled demo source (application)
  hardware/
  scripts/
  systemd/
  build_meta.json          # stamped on exact-SHA deploy (optional)
```

## Update procedure (preserve operator data)

1. Backup first (below).
2. Fetch the desired release / SHA into a checkout.
3. Re-run `sudo ./scripts/install.sh` **or** use exact-SHA deploy:

```bash
./scripts/deploy-sha.sh --sha <commit> --target /opt/pifm --dry-run
# when ready:
sudo ./scripts/deploy-sha.sh --sha <commit> --target /opt/pifm --restart
```

Preserved by default: `config/appliance.json`, `data/library/`,
`data/playlists/`, `data/library.sqlite3`, `data/recovery/`.

Never overwrite a live station’s music or config unless you intentionally
restore from backup.

## Backup and restore

```bash
./scripts/station-backup.sh --root /opt/pifm --dest ./backups
```

Remote example (SSH destination is whatever host you administer):

```bash
./scripts/station-backup.sh --remote pi@station.local --root /opt/pifm --dest /home/pi/backups
```

Restore by stopping the service, restoring the backup archive into the install
root, fixing ownership, and starting the service again. Prefer restoring onto
the same software major line you backed up.

## Rollback

```bash
./scripts/rollback.sh --target /opt/pifm
```

Rollback restores the previous **application** snapshot. It does not casually
overwrite newer operator music/playlists/config.

## Uninstall

```bash
sudo systemctl disable --now pifm-appliance.service
sudo rm -f /etc/systemd/system/pifm-appliance.service
sudo systemctl daemon-reload
# Optional: remove application only
sudo rm -rf /opt/pifm/appliance /opt/pifm/scripts /opt/pifm/systemd
# Optional destructive: also remove operator data (music, config, DB)
# sudo rm -rf /opt/pifm
```

## Build / version identification

- Package version: `appliance/__init__.py` → `software_version`
- Deploy stamp (when used): `/opt/pifm/build_meta.json` with `git_sha`
- Status API: `software_version`, `git_sha`, `build_label`, `hardware_profile`

Host non-RF validation before claiming a candidate ready:

```bash
./scripts/run-validation.sh
```

## Non-Pi / mock validation hosts

`PIFM_TX_BACKEND=mock` is for lifecycle testing only and produces no RF. On a
real Raspberry Pi, `hardware_profile_mode=auto` reads board identity from the
host. **Only a detected Model A+** currently maps to the SUPPORTED profile;
other boards are not auto-promoted to SUPPORTED and do not receive extra
runtime features from recognition alone. On a laptop/VM used only to exercise
documentation, set in `config/appliance.json`:

```json
"tx_backend": "mock",
"hardware_profile_mode": "manual",
"hardware_profile": "raspberry-pi-a-plus"
```

Then complete Music + Station setup until Broadcast reports READY / OFF AIR.
Do not treat mock-host readiness as physical hardware validation.

## Next steps

- Wire RF carefully: [HARDWARE.md](HARDWARE.md)
- Learn the Deck: [USER-MANUAL.md](USER-MANUAL.md)
- If something fails: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
