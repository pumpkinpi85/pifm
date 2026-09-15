# Library and music

- Store files under `data/library/` (or upload via the Deck).
- Playlists live in `data/playlists/` as JSON.
- On prepare/play, FFmpeg converts to a seekable WAV; durable cache avoids
  re-encoding unchanged sources.
- Do not ship copyrighted demo albums in the repository.

Large libraries on A+-class SD cards: prefer reasonable library sizes and rely
on the cache; avoid forcing reconvert loops.
