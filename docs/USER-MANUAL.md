# User manual

How to operate the piFM Broadcast Deck. Vocabulary below matches the product UI.

Related detail: [music.md](music.md) (media contract), [configuration.md](configuration.md)
(advanced keys), [HARDWARE.md](HARDWARE.md) (wiring).

## Main navigation

| Tab | Purpose |
|-----|---------|
| **Broadcast** | On-air status, now playing, Ship’s Log, brass handle / PumpkinPi flag |
| **Music** | Library, playlists, queue, uploads |
| **Station** | Exact frequency and RDS identity |
| **System** | Hardware readiness, diagnostics, recovery, quiet-network tool |
| **Help** | In-product guidance |

## Canonical vocabulary

| Term | Meaning |
|------|---------|
| **OFF AIR** | No transmission intent; handle at bottom detent; black/white PumpkinPi flag |
| **ON AIR** | Authoritative transmitting state; orange PumpkinPi flag after confirmed start |
| **Stop Broadcast** | Absolute RF stop (also available from the masthead) |
| **Brass handle** | Separate movable control for broadcast + frequency on the flagpole |
| **PumpkinPi flag** | Permanently raised at the top; black/white = OFF AIR; orange = confirmed ON AIR |
| **Music program** | Play / Pause / Prev / Next — not RF start/stop |
| **Queue** | Current order of the active playlist |
| **Station frequency** | One canonical `frequency_mhz` shared by Station and Broadcast |

## Broadcast control (brass handle / PumpkinPi flag)

- The PumpkinPi flag stays **permanently raised** at the top of the pole.
- While **OFF AIR** (and during safe preview) the flag shows the black/white emblem.
- After release **and authoritative ON AIR confirmation**, the emblem lights orange
  in the same size and position (no layout shift).
- The brass handle is a **separate** interactive control; it is not part of the pole art.
- The handle rests in the bottom **OFF AIR** detent when stopped.
- A small upward hop enters the tuner at `87.1 MHz`; travel covers through
  `108.2 MHz` in `0.1 MHz` steps. There is no halfway threshold or dead travel zone.
- Release in the lower detent zone selects OFF AIR. The detent is sized so the
  visible stop zone is usable.
- Station remains the precision frequency editor. Its configured frequency is
  shown by a selectable marker while the handle is OFF AIR. Selecting that
  marker uses the same confirmed start flow.
- Pointer/touch dragging previews locally and sends **no** command until release.
- Keyboard: arrows, Home (OFF), End (maximum), Enter/Space (commit), Escape
  (cancel).

### Start broadcasting

1. Confirm music and Station frequency are ready.
2. Drag the brass handle out of OFF AIR (or select the Station frequency marker).
3. Confirm the start dialog.
4. Wait for authoritative **ON AIR**.

### Stop broadcasting

- Drag the handle into the **OFF AIR** detent and release, **or**
- Press masthead **Stop Broadcast** (available during STARTING, ON AIR, FAULT,
  or unknown state).

**Pause** holds silence on the carrier while staying on air. Pause is **not**
Stop Broadcast.

## Music / Library / Playlists / Queue

Uploading and organizing music **never** starts RF. How much you can store
depends on free disk on the Pi (typically the microSD card): your library plus
WAV-cache headroom share that space with the OS.

1. Open **Music**.
2. Drag files into **Add Music** or choose files.
3. Accepted filename families: MP3, WAV, FLAC, M4A, AAC, OGG (see [music.md](music.md)).
   When needed, the appliance uses FFmpeg to prepare seekable WAV for broadcast;
   suitable WAVs may skip conversion.
4. Create or select a playlist; set it active.
5. Use Play / Pause / Prev / Next to control the music program only.

While **ON AIR**, heavy library work (upload, delete, full reindex) is rejected
until the station is OFF AIR. Ordinary playback controls remain available.

## Station

- Set frequency in `0.1` MHz steps within `87.1–108.2` MHz.
- Set station name (`rds_ps`), radio text (`rds_rt`), and optional PI code.
- Saving Station settings does **not** start broadcasting.
- There is one authoritative station frequency — Broadcast and Station never
  keep separate conflicting values.

Choose only frequencies and conditions you are legally allowed to use.

## System / Diagnostics

- System shows hardware profile / readiness. Auto-detect can identify the board;
  only the Model A+ is a SUPPORTED profile today. See [HARDWARE.md](HARDWARE.md).
- Broadcast recovery intent (armed ON vs disarmed OFF)
- Fault reason and transmitter diagnostics
- Quiet network (interference troubleshooting) — **does not** stop FM

## Authoritative state and reconnection

The Raspberry Pi is always authoritative. If the browser disconnects:

1. Local UI state is discarded as unverified.
2. Mutation controls lock.
3. On reconnect, the browser fetches one complete status snapshot.
4. Only then does live SSE resume.

Reconnection never starts, stops, or retunes the transmitter by itself.

## Offline / network-disconnected operation

After setup, the appliance continues playback and RF without LAN, DNS, NTP,
Internet, or an open browser. Network loss does not issue TX commands.

## Power / recovery semantics

- Starting from the brass handle records durable **ON** intent before TX start.
- Stop Broadcast / OFF AIR records durable **OFF** intent before stopping RF.
- Fresh installs have no ON marker → boot **OFF AIR**.
- A service or power restoration may restore ON intent only after hardware,
  media, readiness, and single-transmitter checks succeed.
- To prevent automatic restoration, leave the station OFF AIR before shutdown.

## FAULT / unknown / absolute STOP

If the console shows Needs Attention, State Unknown, Possible Transmission, or
FAULT:

1. Press **Stop Broadcast**.
2. Confirm zero transmitter processes / OFF AIR in System diagnostics.
3. Fix the underlying problem before going on air again.

## Daily flow

1. Music — playlist with at least one track
2. Station — lawful frequency and identity
3. Broadcast — confirm READY / OFF AIR
4. Raise the handle only when intentional and lawful
5. Return to OFF AIR when finished

Ship’s Log shows operator-meaningful events. Engineering detail lives under
System → Diagnostics.
