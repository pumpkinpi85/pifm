# Operation

## Broadcast Deck

- **Music transport** (Play / Pause / Prev / Next) changes the program.
  Pause while on air holds silence on the carrier; it does not end RF.
- The Black Flag starts at the bottom when **OFF AIR**. Dragging through the
  lower half of the pole never transmits.
- Release at or above the midpoint to preview/confirm **ON AIR**. The upper
  half tunes the complete `87.1–108.2 MHz` piFM range in `0.1 MHz` steps.
- Release below the midpoint to **Lower the Black Flag** and end transmission.
- The masthead **STOP BROADCAST** remains available during STARTING, ON AIR,
  FAULT, or unknown state.
- Station remains the precision frequency editor. Its configured frequency is
  shown by the passive `SET` marker while the OFF AIR flag stays at the bottom.
- Pointer/touch dragging previews locally and sends no command until release.
  Keyboard focus supports arrows, Home (OFF), End (maximum), Enter/Space
  (commit), and Escape (cancel).

## Daily flow

1. Choose playlist / upload music (Music)
2. Set frequency and RDS (Station)
3. Confirm OFF AIR readiness (Broadcast)
4. Drag the Black Flag into the tuning half and release only when intentional
   and lawful
5. Drag below the midpoint or use STOP BROADCAST when finished

Ship’s Log shows operator-meaningful events. System → Diagnostics holds
engineering detail.

## Recovery and reconnection

Raising the flag persists ON intent before transmitter startup. A service or
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
