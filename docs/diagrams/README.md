# Original piFM diagram specifications

Do **not** copy MagPi or third-party GPIO artwork. Generate original diagrams later.

## 1. Header / GPIO4
- **Purpose:** Identify P1-07 / BCM4 on a 40-pin header
- **Must show:** pin numbers, BCM4 highlight, GND references
- **Verify:** header orientation, pin 1 location
- **Style:** high-contrast line diagram, printable B&W friendly

## 2. GPIO RF + ground
- **Purpose:** Minimum electrical connection for experimentation
- **Must show:** BCM4, GND, warning that filtering is operator-supplied
- **Verify:** no implied “legal antenna” claim
- **Style:** schematic-ish, sparse labels

## 3. Minimum hardware wiring
- **Purpose:** Pi + power + network + GPIO RF tap
- **Must show:** no enclosure-specific parts
- **Verify:** matches installation docs
- **Style:** block diagram

## 4. Recommended RF path
- **Purpose:** GPIO → filter/match (placeholder) → load/antenna
- **Must show:** stages marked ASSUMED / REQUIRES MEASUREMENT where unproven
- **Verify:** does not invent component values without measurement
- **Style:** annotated flow

## 5. Optional LED / switch
- **Purpose:** BCM18 LED, BCM5 switch to GND
- **Must show:** series resistor note for LED
- **Verify:** matches optional-controls.md
- **Style:** simple wiring

## 6. Software architecture
- **Purpose:** Deck ↔ API/SSE ↔ controller ↔ backends
- **Must show:** mock/fake as test-only
- **Verify:** matches architecture.md
- **Style:** layered boxes

## 7. Audio pipeline
- **Purpose:** MP3 → FFmpeg → WAV cache → pi_fm_rds → GPIO
- **Must show:** cache hit path
- **Verify:** seekable WAV requirement
- **Style:** left-to-right pipeline

## 8. Broadcast lifecycle / absolute STOP
- **Purpose:** OFF → STARTING → ON AIR → STOPPING → OFF; STOP from any state
- **Must show:** Pause ≠ Lower Flag
- **Verify:** matches operation.md / PROJECT_MASTER
- **Style:** state diagram
