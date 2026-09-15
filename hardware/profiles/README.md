# Hardware profiles

Profiles describe board-specific defaults and compatibility evidence.

| Status | Meaning |
|--------|---------|
| SUPPORTED | Physically validated with piFM + compatible `pi_fm_rds` build |
| EXPERIMENTAL | Plausible from upstream/docs; needs piFM validation |
| UNSUPPORTED | Known incompatible (or no viable backend) |
| UNKNOWN | Insufficient evidence |

Only `raspberry-pi-a-plus` is SUPPORTED in the public candidate.

Runtime resolution: `appliance/hardware_profile.py` loads
`hardware/profiles/<id>.json`, exposes the document via `/api/status`, and may
include read-only `/proc` board hints. Config key `hardware_profile` selects the
profile. Detection never invents SUPPORTED status for unvalidated boards.
