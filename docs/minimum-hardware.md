# Minimum hardware

**Reference / floor:** Raspberry Pi Model A+ Rev 1.1 (~512 MB RAM, ARMv6-class).

piFM is intentionally useful on this class of hardware:

- stdlib Python web server
- vanilla Broadcast Deck (no SPA framework)
- SQLite + filesystem library
- FFmpeg convert with durable WAV cache
- SSE with light polling fallback

See CONTRIBUTING.md for the binding engineering rule.
