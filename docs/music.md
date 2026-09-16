# Music workspace

The web interface is the normal piFM music workflow. Uploading and organizing
music never starts an FM broadcast.

## Add Music

Open **Music → Library**, then drag files into **Add Music** or choose multiple
files. Files are streamed to disk in 64 KiB chunks so imports do not need to fit
in the Raspberry Pi A+'s memory.

piFM accepts files up to 128 MiB and asks the installed FFmpeg/FFprobe tools to
verify that each file contains decodable audio. Target formats are MP3, WAV,
FLAC, unprotected M4A/AAC, and OGG. Damaged, encrypted, DRM-protected, empty,
unsupported, or incomplete files fail without entering the library.

Filenames are reduced to a safe basename. An identical same-name file is
reported as already present; different content receives a numbered filename.
Imports are atomic, and temporary files are removed after failure.

When an active playlist exists, browser uploads are also added to it. This makes
first run useful without hiding a second queue or filesystem workflow.

## Library, Playlists, Queue

- **Library:** search, add a track to the active playlist, or delete media with
  confirmation. Deletion removes playlist references and is blocked while the
  transmitter is running.
- **Playlists:** create, rename, duplicate, delete, add/remove tracks, and
  reorder with buttons or drag-and-drop.
- **Queue:** the active playlist's current order. Reordering while idle persists
  that order to the playlist. Read/status requests never shuffle or mutate it.

Shuffle is applied only at an explicit queue rebuild boundary. Repeat controls
what happens at the end of the program. Pause/reset affect music; only
**Lower the Black Flag / Stop Broadcast** is the absolute RF stop.
