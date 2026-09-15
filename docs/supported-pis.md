# Supported Raspberry Pi models

**Evidence labels:** SUPPORTED · EXPERIMENTAL · UNSUPPORTED · UNKNOWN

A 40-pin header does **not** imply GPIO FM works. `pi_fm_rds` must be rebuilt
per model/architecture (upstream requirement).

| Board | Status | Notes |
|-------|--------|-------|
| Raspberry Pi Model A+ Rev 1.1 | **SUPPORTED** | Reference / minimum. Physically proven by the original project. |
| Raspberry Pi Zero | EXPERIMENTAL | Upstream PiFmRds lists Zero; piFM validation pending. |
| Raspberry Pi Zero W | EXPERIMENTAL | Same family as Zero; validate wireless + TX coexistence. |
| Raspberry Pi Zero 2 W | EXPERIMENTAL | Upstream lists Zero 2; rebuild required. |
| Raspberry Pi 2 | EXPERIMENTAL | Upstream lists Pi 2. |
| Raspberry Pi 3 | EXPERIMENTAL | Upstream lists Pi 3. |
| Raspberry Pi 4 | EXPERIMENTAL | Upstream lists Pi 4; validate carefully (clock/governor quirks reported for related tools). |
| Raspberry Pi 5 | **UNSUPPORTED** | RP1 GPIO/clock path; no proven `pi_fm_rds` port for piFM. |

## Promoting a board to SUPPORTED

Required evidence (documented in-repo or release notes):

1. Fresh install of this repository on that board
2. Compatible `pi_fm_rds` binary built on-device
3. Non-RF lifecycle: boot OFF AIR, Deck, library, Play/Pause, Raise/Lower on mock **or** `pi_fm_rds` with transmission inhibited/shielded as appropriate
4. Absolute STOP verified
5. Optional explicitly authorized RF check — never implied by software-only tests

Upstream PiFmRds compatibility claims: https://github.com/ChristopheJacquet/PiFmRds
