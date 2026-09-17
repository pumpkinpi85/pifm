# Third-party notices

## PiFmRds

piFM’s production transmitter backend is designed to supervise the external
binary **`pi_fm_rds`** from the PiFmRds project:

- Upstream: https://github.com/ChristopheJacquet/PiFmRds
- Validated / pinned revision: `777f8e52648b88156483d067e24e0b8682abe8c1`
  ([commit](https://github.com/ChristopheJacquet/PiFmRds/commit/777f8e52648b88156483d067e24e0b8682abe8c1))
- Machine-readable pin: [`third_party/pifmrds.pin`](pifmrds.pin)
- License: GNU General Public License v3.0 (GPL-3.0)
- RF output (upstream documentation): GPIO 4 / header pin 7 (GPCLK-based FM + RDS)
- Canonical installed binary path: `/usr/local/bin/pi_fm_rds`

piFM does **not** vendor the PiFmRds sources in this repository. Public install
procedures clone the upstream tree at the pinned SHA, build on-device, and
point `pi_fm_rds_path` at `/usr/local/bin/pi_fm_rds`. Do not depend on
`pumpkinpi85/PiFmRds` for product installation; that fork is not part of the
piFM dependency graph unless it later carries documented product-specific
deltas (it does not today).

When distributing a combined system image that includes modified PiFmRds
sources, comply with GPL-3.0 obligations for those components.

## Bundled demo audio

`examples/demo/Brynja Vinter - The Sky Belongs to No King.wav` is shipped as
piFM’s intentional product demo / validation reference audio.

**Artist credit:** Brynja Vinter — *The Sky Belongs to No King*.

Fresh installs may copy it into the operator library without overwriting
existing files. Redistribution with this repository is founder-authorized for
piFM; do not substitute unrelated copyrighted catalog.

## Other runtime dependencies (typical)

- Python 3 (system)
- FFmpeg (system package)
- libsndfile (PiFmRds build dependency)

Exact package names vary by Raspberry Pi OS release; see `docs/INSTALL.md`.
