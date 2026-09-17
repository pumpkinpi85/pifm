# RF and law

## Evidence labels

| Label | Meaning |
|-------|---------|
| SOFTWARE-PROVEN | Observed in piFM / upstream source behaviour |
| HARDWARE-PROVEN | Physically demonstrated on a named board |
| ASSUMED | Reasonable but unmeasured |
| REQUIRES MEASUREMENT | Must not be claimed as fact |

## Facts

- Upstream PiFmRds emits FM/RDS using Pi clock/PWM-class mechanisms on **GPIO 4**
  (header pin 7) — SOFTWARE-PROVEN (upstream docs/source).
- A+ class hardware operated successfully with this software line —
  HARDWARE-PROVEN for the validated reference profile; public clean-room RF
  remains a separate publication concern.
- GPIO carriers are widely described as harmonic-rich — ASSUMED /
  REQUIRES MEASUREMENT for any specific spectral claim.
- Filtering, matching, and antenna fitness — REQUIRES MEASUREMENT; not provided
  as a certified RF chain by this repository.

## Operator duty

Transmitting without appropriate authorization may be illegal. Prefer shielded
loads into a receiver for experimentation. piFM does not grant a license to
radiate.
