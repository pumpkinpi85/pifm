# Legacy retirement plan

## Mac private tree

Path: local private `pifm` project directory (historical / transitional, **read-only**).
Do not name personal filesystem paths in product docs beyond this generic reference.

**Do not delete** until all gates below PASS and Nathan explicitly authorizes
deletion.

### Retirement gates

1. Canonical cutover to `/opt/pifm` on the reference A+ — PASS  
2. A+ non-RF reference validation — PASS  
3. Authorized real-radio validation — PASS  
4. Rollback proof — PASS  
5. Feature/parity review vs private tree — PASS  
6. Operator data preservation confirmed — PASS  
7. Canonical repository / GitHub home established — PASS  
8. Nathan explicit deletion authorization — REQUIRED  

Until then: treat the private tree as an implementation/history reference only.
Do not keep two active codebases in sync.

## Pi-side legacy trees

Examples (station-local, not product):

- Historical home-directory appliance trees  
- Historical `PiFmRds` checkouts used only as build sources  
- Disabled legacy auto-start transmitter units  

Leave in place until cutover + validation + rollback gates pass. Prefer archive
over delete. Never commit their contents into the canonical repository.
