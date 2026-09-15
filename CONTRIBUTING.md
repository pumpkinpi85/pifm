# Contributing to piFM

## Minimum-hardware rule

piFM is deliberately designed to remain useful on low-resource Raspberry Pi
hardware. The **Raspberry Pi Model A+ Rev 1.1** is the reference minimum target.
Changes should preserve usable operation on that reference class unless a
documented architectural decision explicitly changes the minimum hardware
requirement.

Do not introduce without strong justification: Docker, Node production runtime,
React or large frontend frameworks, database servers, cloud services, heavy
daemon stacks, chatty polling, unbounded logging, or re-transcoding on every play.

## Development notes

- Prefer extending `appliance/` with stdlib Python and vanilla web assets.
- Use `tx_backend=mock` or `fake` for automated tests — never require RF in CI.
- Production configuration targets `pi_fm_rds` with **OFF AIR** default.
- Read [PROJECT_MASTER.md](PROJECT_MASTER.md) before large changes.

## Pull requests

When published, the canonical repository is **github.com/pumpkinpi85/pifm**.
Fork, branch, and open a PR against that repo. Maintainers under the
**pumpkinpi85** account merge to `main` and cut releases. Do not expect direct
push access. Do not open PRs against any other organization for this project.

## License

Contributions are under GPL-3.0 (see LICENSE).
