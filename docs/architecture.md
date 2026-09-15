# Architecture

```
Browser Broadcast Deck
    │  HTTP JSON + SSE
    ▼
appliance.webapp (ThreadingHTTPServer)
    ▼
Controller (state machine, queue, STOP)
    ├── Library (files + SQLite)
    ├── NetworkManager (RF Quiet; never starts TX)
    └── TxBackend
            ├── pi_fm_rds (production)
            ├── mock / fake (tests)
            └── OwnedTxProcess (PID ownership)
```

Audio path: source → FFmpeg → WAV cache → `pi_fm_rds -audio <wav>` → GPIO.
