# RF output

Minimum conceptual hookup:

1. BCM GPIO 4 (P1-07) — RF-bearing clock output when transmitting
2. GND — return
3. User RF path — series filter / matching / shielded load / antenna **as you design**

piFM does not ship a certified filter network. Raw GPIO output should be treated
as experimental and spectrally unclean until measured and filtered appropriately.
