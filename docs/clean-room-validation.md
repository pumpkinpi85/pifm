# Clean-room validation (contributor)

Validate that a **fresh** checkout behaves like a public user would experience —
without private developer trees, personal music libraries, or undocumented
host shortcuts.

**Do not use a personal reference station’s operator data for this procedure.**

## Host non-RF gate (required)

```bash
./scripts/run-validation.sh
```

## Isolated mock install (recommended)

On a spare Pi, VM, or throwaway directory:

```bash
sudo PIFM_TX_BACKEND=mock PIFM_PREFIX=/opt/pifm-cleanroom ./scripts/install.sh
```

Then follow README → INSTALL → first-run → Music → Station → READY using only
public documentation. Record every missing dependency as a documentation defect.

## Evidence template

Copy [evidence/clean-room-phase-a.template.md](evidence/clean-room-phase-a.template.md).
Do **not** commit filled private evidence into Git.
