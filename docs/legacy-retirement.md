# Legacy install retirement gates

Before deleting any previous install root on a station you administer:

1. Canonical `/opt/pifm` install is healthy and OFF AIR
2. Operator backup exists and was restore-tested (or explicitly waived)
3. Music, playlists, database, and config match operator expectations
4. Exact product SHA / version identity is recorded
5. Absolute Stop Broadcast verified on the new install
6. Rollback path understood
7. Maintainer explicit deletion authorization — **required**

Do not name private developer filesystem paths in product docs. Speak only in
terms of “previous install root” versus `/opt/pifm`.
