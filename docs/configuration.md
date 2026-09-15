# Configuration

Primary file: `$PIFM_ROOT/config/appliance.json`

See `examples/config.example.json`.

| Key | Production intent |
|-----|-------------------|
| `tx_backend` | `pi_fm_rds` |
| `pi_fm_rds_path` | Absolute path to `pi_fm_rds` |
| `frequency_mhz` | Operator-chosen FM frequency |
| `rds_ps` / `rds_rt` / `rds_pi` | RDS identity |
| `hardware_profile` | e.g. `raspberry-pi-a-plus` |
| `network_iface` | e.g. `eth0` or `wlan0` |
| `gpio_enabled` | Optional panel LED/switch (default false) |
| `tx_pin` | Documented RF GPIO BCM (default 4) |

ON_AIR is **never** persisted. Service restart → OFF AIR.

`mock` / `fake` backends exist for automated tests and developer validation only.
