# Operation

## Broadcast Deck

- **Music transport** (Play / Pause / Prev / Next) changes the program.
  Pause while on air holds silence on the carrier; it does not end RF.
- The pirate flag remains raised as identity artwork. The brass handle sits at
  the bottom **OFF AIR** detent when transmission is stopped.
- A small upward hop enters the tuner at `87.1 MHz`; the remaining handle
  travel covers the complete range through `108.2 MHz` in `0.1 MHz` steps.
- Release below 3% travel selects OFF AIR. Release from 3% through 6% snaps to
  the 6% lowest-frequency position, creating the mechanical detent gap.
- Return the handle to the bottom detent to end transmission.
- The masthead **STOP BROADCAST** remains available during STARTING, ON AIR,
  FAULT, or unknown state.
- Station remains the precision frequency editor. Its configured frequency is
  shown by a selectable frequency marker while the OFF AIR handle stays at the
  bottom. Selecting that marker uses the same confirmed start flow as the handle.
- Pointer/touch dragging previews locally and sends no command until release.
  Keyboard focus supports arrows, Home (OFF), End (maximum), Enter/Space
  (commit), and Escape (cancel).

## Daily flow

1. Choose playlist / upload music (Music)
2. Set frequency and RDS (Station)
3. Confirm OFF AIR readiness (Broadcast)
4. Drag the brass handle out of the OFF AIR detent and release only when
   intentional and lawful
5. Return the handle to the detent or use STOP BROADCAST when finished

Ship’s Log shows operator-meaningful events. System → Diagnostics holds
engineering detail.

## Recovery and reconnection

Starting from the brass handle persists ON intent before transmitter startup. A service or
power restoration may restore that intent only after the normal hardware,
media, readiness, and single-transmitter checks. To prevent automatic
restoration, lower the flag or use STOP BROADCAST before shutdown; OFF intent
survives the next boot.

The browser is only a remote control. Network or browser loss does not change
RF or local playlist progression. While disconnected, flag position is
unknown and controls are locked. Reconnection first replaces the page with one
complete authoritative appliance snapshot; it never starts, stops, or retunes
the transmitter.

FAULT or possible-transmission state takes precedence over flag position. Use
the independent STOP BROADCAST control, then inspect System → Diagnostics.
