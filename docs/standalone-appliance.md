# Standalone appliance and power recovery

The Raspberry Pi runs the station. The web interface is a remote control, not
part of the broadcast path.

After setup, normal playback and FM transmission do not require an open
browser, another computer, LAN access, DNS, NTP, Internet access, or cloud
services. Removing the network does not issue a playback or transmitter
command. Reconnecting a browser reads the authoritative state from the Pi.

## Browser reconnection

The Raspberry Pi is always authoritative. If the browser loses its live
connection, it discards its local station snapshot and shows **Live station
state unavailable**. Broadcast, playback, playlist, queue, recovery, fault,
and hardware information are no longer presented as confirmed, and mutation
requests are blocked.

After connectivity returns, the browser performs one read-only
`GET /api/status` containing the complete station and queue snapshot. It
replaces local state with that response, marks itself synchronized, and only
then opens a new SSE stream. Every snapshot carries an appliance-generated
monotonic revision, so delayed HTTP or SSE responses cannot replace newer
state. Pending reads from before a disconnect are rejected. Reconciliation
does not send playback or transmitter commands and therefore cannot restart or
otherwise disturb RF.

## Broadcast recovery intent

piFM persists the operator's command, not the incidental presence of a
transmitter process:

- **Raise the Black Flag** atomically records desired broadcast intent **ON**
  before transmitter startup.
- **Lower the Black Flag / Stop Broadcast** removes that ON marker before
  stopping and verifying the transmitter.
- A fresh installation has no ON marker and therefore starts **OFF AIR**.

The small marker is stored under `data/recovery/` and is preserved during
deploy and rollback. A corrupt or unsupported marker fails closed: piFM remains
OFF AIR and records why recovery was refused.

## Startup behavior

Every service start first clears and verifies orphan transmitter processes.
When valid ON intent exists, piFM then repeats the canonical checks for:

- setup and station configuration;
- hardware profile and board-specific environment requirements;
- transmitter backend readiness;
- an available active playlist and media file;
- exactly one transmitter worker after startup.

If a check or audio preparation fails, piFM remains OFF AIR. A failed recovery
is latched for that machine boot so systemd restart loops cannot repeatedly
attempt transmission. A later machine boot may retry because the operator's ON
intent still exists; use **Lower the Black Flag** to deliberately disarm it.

Each ON command has a unique durable intent revision. The transmitter lifecycle
barrier rechecks that revision immediately before spawn, so a stale startup or
prepared-audio task cannot override a newer STOP. STOP waits for every admitted
spawn before verifying zero transmitters and returning.

Only one appliance controller process may hold the local controller lease.
Rapid service failures are rate-limited, and restoration is not marked
successful until the transmitter remains stable through an initial observation
window.

Service shutdown stops the process-owned transmitter without changing operator
intent. This distinction allows a service or machine restart to recover an
intentional broadcast. Absolute STOP always changes intent to OFF first.

## Program recovery

The recovery marker records the active playlist, queue order, current track,
shuffle/repeat-derived order, and whether the program was playing, paused, or
stopped. An interrupted playing track restarts from its beginning.

When repeat is disabled and the playlist ends, Broadcast remains ON with the
program stopped and a silence hold. With repeat enabled, progression wraps to
the beginning; shuffle may produce a new order at that boundary.

Missing files are skipped before startup when another queued file exists.
Failed audio preparation while already broadcasting changes to a safe silence
hold and records `MEDIA_TRACK_FAILED`, avoiding an unbounded retry loop.

## Storage failures

Intent and configuration replacement use a temporary file, file `fsync`,
atomic rename, and directory `fsync`. OFF intent is represented by removing the
ON marker, which normally remains possible when storage has no free data
blocks.

Uploads and WAV preparation use temporary files. A full filesystem leaves
existing media untouched and reports a useful error. If the Ship's Log is
unwritable, operation continues with an in-memory event buffer.

If the filesystem is read-only and the ON marker cannot be removed, piFM still
stops the transmitter but reports a fault: future OFF persistence cannot be
guaranteed until storage is repaired.

## Current validation boundary

The recovery state machine, storage failures, service restart, progression,
network independence, and single-worker behavior are covered by non-RF tests.
Physical A+ power-loss restoration and network-disconnect behavior require the
founder-operated RF tests before they may be described as physically proven.
The network-disconnect test must also confirm that the browser visibly loses
authority and later displays the Pi's current track/state after read-only
reconciliation, without any transmitter restart.
