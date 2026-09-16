# Troubleshooting

| Symptom | Checks |
|---------|--------|
| Deck unreachable | Service active? `web_host`/`web_port`? firewall? |
| Can’t go on air | Playlist empty? frequency invalid? FAULT state? |
| STARTING stuck | Absolute STOP; check `pi_fm_rds` path; disk space for WAV |
| FAULT / POSSIBLE TRANSMISSION | STOP BROADCAST; System diagnostics; ensure one TX worker |
| No audio on air | Seekable WAV prepare failed? ffmpeg installed? |
| Unexpected recovery after reboot | Lower the Black Flag to persist OFF intent, then inspect Broadcast Recovery under System |

Mock/fake backends will not produce RF — expected in tests.
