# Security Policy

## Reporting

Report security issues privately to the maintainers of the canonical repository
**github.com/pumpkinpi85/pifm** (GitHub Security Advisories when that public
repo exists, or the contact listed on the pumpkinpi85 project page). Please do
**not** open public issues that include exploit details for remote compromise
of appliances.

## RF and safety

piFM can drive real RF via `pi_fm_rds`. Misconfiguration or misuse can violate
local law. That is an operator responsibility, not a “vulnerability” in the
usual software sense — but we do treat failures of **absolute STOP**, unintended
auto-transmit, or privilege-escalation bugs as security-relevant.

## Scope we care about

- Unintended transmission / failure to stop TX
- Path traversal in uploads
- Auth bypass if authentication is added later
- Dependency or install scripts that phone home unexpectedly

## Out of scope

- Requests to help evade radio regulation
- Harmonic/spectral performance of unfiltered GPIO as a “bug”
