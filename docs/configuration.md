# Configuration

Primary file: `$PIFM_ROOT/config/appliance.json`

See `examples/config.example.json`.

| Key | Production intent |
|-----|-------------------|
| `tx_backend` | `pi_fm_rds` |
| `pi_fm_rds_path` | Absolute path to `pi_fm_rds` |
| `pi_fm_rds_ppm` | PiFmRds DMA/audio timing correction; default `0.0` |
| `frequency_mhz` | Operator-chosen piFM tuning frequency, `87.1–108.2` MHz in `0.1` MHz increments |
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

## Frequency tuning contract

piFM intentionally preserves its legacy inclusive `87.1–108.2 MHz` tuning
range. New values must be aligned to integer tenths (`0.1 MHz`). The Broadcast
flagpole and Station input both write this one canonical `frequency_mhz`
setting; the flagpole stores no separate frequency.

This supported tuning range is not a claim that every value is a lawful
consumer broadcast channel in every region. The operator must choose a
frequency and operating conditions allowed by local law.

An existing in-range configuration with finer precision is preserved rather
than silently rounded. Broadcast readiness and automatic recovery remain
blocked until the operator explicitly corrects it on Station.

## PiFmRds timing calibration

`pi_fm_rds_ppm` is passed directly as `-ppm <value>`. It accepts finite numeric
values from `-999999` through `10000000`. Invalid values fail configuration
loading clearly.

The product default is always `0.0`. Do not guess a board correction or alter
source audio to compensate. A measured value belongs in the station's local
`appliance.json` and its validation evidence, not an A+-specific code fork.
