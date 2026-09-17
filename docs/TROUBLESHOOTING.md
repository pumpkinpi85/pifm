# Troubleshooting

Operator-oriented recovery. Prefer **Stop Broadcast** whenever transmission
state is unclear. Mock/fake backends produce no RF — expected in tests.

## Web interface unreachable

1. Confirm the Pi is powered and networked (for remote browsers).
2. Check the service:
   ```bash
   systemctl status pifm-appliance.service
   journalctl -u pifm-appliance.service -n 100 --no-pager
   ```
3. Confirm listen address/port in `config/appliance.json` (`web_host`, `web_port`,
   default `8080`).
4. From the Pi: `curl -sS http://127.0.0.1:8080/api/status | head`
5. Check firewall / router isolation if browsing from another device.

## Station not READY

Open **Broadcast** and **System**. Common blockers:

- First-run setup incomplete
- No active playlist / empty queue
- Invalid frequency (must be `87.1–108.2` in `0.1` MHz steps)
- Missing station name / RDS fields
- Hardware profile unknown or environment not ready
- Transmitter binary missing (`/usr/local/bin/pi_fm_rds`)

Fix the listed blocker, then re-check readiness. Do not force RF.

## Transmitter won’t start

1. Remain OFF AIR; read Ship’s Log and System → Diagnostics.
2. Verify `pi_fm_rds` exists and runs on this architecture.
3. Confirm disk space for WAV preparation (`df -h`).
4. Confirm hardware prerequisites (headless / onboard audio) on A+:
   `./scripts/hardware-readiness.py`
5. Try a known-good seekable WAV from your own library after OFF AIR upload.

## Wrong or slow audio

On the validated A+ path, desktop sessions or enabled onboard audio can steal
PWM/DMA clocks and produce incorrect speed/pitch. Apply the documented
headless + onboard-audio-disabled environment ([HARDWARE.md](HARDWARE.md)).
Do not invent PPM corrections casually; product default is `0.0`.

Also verify the source file is not corrupt and that FFmpeg conversion succeeded.

## Onboard-audio / PWM conflict

Symptoms: timing drift, half-speed audio, unstable TX.

```bash
./scripts/hardware-readiness.py
sudo ./scripts/configure-hardware.sh          # preview
sudo ./scripts/configure-hardware.sh --apply  # A+ only, reversible
```

## Unsupported, corrupt, or DRM audio

Extensions are only the first gate. FFprobe/FFmpeg must decode audio. Damaged,
encrypted, DRM-protected, empty, incomplete, or undecodable files are rejected
and never enter the library. Re-export from a tool that produces a normal
MP3/WAV/FLAC/M4A/AAC/OGG file you are allowed to use.

## Upload rejected while ON AIR

Expected. Transmission timing has priority. Return to **OFF AIR**, then upload,
delete, or reindex. Searching and ordinary playback controls remain available
while on air.

## Low storage / cache conditions

piFM projects PCM size before conversion and enforces `wav_cache_max_mb` and
`wav_cache_min_free_mb`. Free disk space, remove unused library tracks you no
longer need, or clear disposable cache under `data/logs/wav/cache/` while OFF
AIR. Source media and playlists are not cache-eviction candidates.

## Network disconnected while broadcasting

Expected to keep playing and transmitting. The browser shows live state as
unavailable until it reconnects and reads one authoritative snapshot. Quiet
network mode also does **not** stop FM.

## Reconnect / state reconstruction looks wrong

1. Do not mash Start/Stop while “Live station state unavailable”.
2. Wait for reconnection; the UI should replace itself from `/api/status`.
3. If FAULT/unknown appears, use **Stop Broadcast**, then inspect System.

## Power interruption / recovery

If you left ON intent armed, the appliance may attempt safe restoration after
boot only when readiness checks pass. To ensure OFF after power loss, stop
broadcast (OFF AIR) before shutdown. Inspect **Broadcast Recovery** under System.

## FAULT / UNKNOWN / Possible Transmission

1. **Stop Broadcast** immediately.
2. Confirm `broadcast_state` is `off` and no `pi_fm_rds` processes remain:
   ```bash
   pgrep -ax pi_fm_rds || echo "no tx"
   curl -s http://127.0.0.1:8080/api/status | python3 -c 'import sys,json;s=json.load(sys.stdin);print(s.get("broadcast_state"),s.get("system_tx_count"))'
   ```
3. Review diagnostics and logs before another start.

## Absolute Stop Broadcast does not clear RF

1. Use Stop Broadcast again from the masthead.
2. Check process list for `pi_fm_rds` / `fm_transmitter`.
3. As a last resort on a machine you administer, terminate leftover transmitter
   processes only after understanding you may interrupt audio uncleanly, then
   restart `pifm-appliance.service` and verify OFF AIR.
4. If durable OFF intent could not be written, repair storage before retrying.

## Service diagnostics / log collection

```bash
systemctl status pifm-appliance.service --no-pager
journalctl -u pifm-appliance.service -n 200 --no-pager
ls -la /opt/pifm/data/logs/
```

When asking for help, include: board model, `software_version` / `git_sha`,
`broadcast_state`, hardware readiness output, and redacted logs.
**Never** paste Wi-Fi passwords, API tokens, or private keys.
