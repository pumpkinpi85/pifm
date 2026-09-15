# Third-party notices

## PiFmRds

piFM’s production transmitter backend is designed to supervise the external
binary **`pi_fm_rds`** from the PiFmRds project:

- Upstream: https://github.com/ChristopheJacquet/PiFmRds
- License: GNU General Public License v3.0 (GPL-3.0)
- RF output (upstream documentation): GPIO 4 / header pin 7 (GPCLK-based FM + RDS)

piFM does not currently vendor the PiFmRds sources in this repository. Operators
build/install `pi_fm_rds` separately and point `pi_fm_rds_path` at the binary.

When distributing a combined system image that includes modified PiFmRds
sources, comply with GPL-3.0 obligations for those components.

## Other runtime dependencies (typical)

- Python 3 (system)
- FFmpeg (system package)
- libsndfile (PiFmRds build dependency)

Exact package names vary by Raspberry Pi OS release; see `docs/installation.md`.
