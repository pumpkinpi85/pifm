# Configuration

Primary file: `$PIFM_ROOT/config/appliance.json`

See `examples/config.example.json`.

| Key | Production intent |
|-----|-------------------|
| `tx_backend` | `pi_fm_rds` |
| `pi_fm_rds_path` | Absolute path to `pi_fm_rds` |
| `pi_fm_rds_ppm` | PiFmRds DMA/audio timing correction; default `0.0` |
| `frequency_mhz` | Operator-chosen FM frequency |
| `rds_ps` / `rds_rt` / `rds_pi` | RDS identity |
| `hardware_profile` | e.g. `raspberry-pi-a-plus` |
| `hardware_profile_mode` | `auto` (recommended) or explicit `manual` override |
| `setup_completed` | Fresh-install first-run state; pre-P1F configs default complete |
| `network_iface` | e.g. `eth0` or `wlan0` |
| `gpio_enabled` | Optional panel LED/switch (default false) |
| `tx_pin` | Documented RF GPIO BCM (default 4) |

Transient ON_AIR state and transmitter PIDs are **never** persisted here.
Operator ON intent is stored separately under `data/recovery/`; a service
restart may restore that intent only after safety validation. Absolute STOP
removes it first.

`mock` / `fake` backends exist for automated tests and developer validation only.

## PiFmRds timing calibration

`pi_fm_rds_ppm` is passed directly as `-ppm <value>`. It accepts finite numeric
values from `-999999` through `10000000`. Invalid values fail configuration
loading clearly.

The product default is always `0.0`. Do not guess a board correction or alter
source audio to compensate. A measured value belongs in the station's local
`appliance.json` and its validation evidence, not an A+-specific code fork.
