# Music workspace

The web interface is the normal piFM music workflow. Uploading and organizing
music never starts an FM broadcast.

## Add Music

Open **Music → Library**, then drag files into **Add Music** or choose multiple
files. Files are streamed to disk in 64 KiB chunks so imports do not need to fit
in the Raspberry Pi A+'s memory.

piFM accepts six filename families up to 128 MiB: **MP3, WAV, FLAC, M4A, AAC,
and OGG**. The extension is only the first gate. The appliance's installed
FFprobe/FFmpeg decoder must also find decodable audio. An accepted container
extension does not imply support for every codec that container could hold.
Damaged, encrypted, DRM-protected, empty, unsupported, incomplete, or
undecodable files fail without entering the library.

Filenames are reduced to a safe basename. An identical same-name file is
reported as already present; different content receives a numbered filename.
Imports are atomic, temporary files are removed after failure, and normal
imports update only the new track's SQLite row. Full-library reindex remains a
deliberate repair operation.

## Playback preparation and storage bounds

The supported path is:

1. upload and stream to a temporary file
2. validate and probe with the appliance FFprobe
3. preserve the accepted source in the operator library
4. when required, use FFmpeg to create a seekable 44.1 kHz stereo PCM16 WAV
   cache entry
5. give that seekable WAV to `pi_fm_rds`

A suitable seekable PCM16 WAV at 44.1 or 48 kHz, mono or stereo, can bypass
conversion. Before any other source is converted, piFM reads decoded duration,
sample rate, and channel count, projects the PCM output size, checks free disk,
and enforces the configured WAV-cache budget. Old disposable cache entries are
evicted deterministically. Source media, playlists, configuration, the library
database, active conversion output, and the WAV owned by a transmitter are
never cache-eviction candidates.

FFmpeg is an explicitly owned child process. Stop Broadcast, startup
cancellation, and service shutdown terminate that exact child with bounded
TERM/wait/KILL handling, reap it, and remove partial output. piFM does not kill
unrelated FFmpeg processes by name.

The Raspberry Pi Model A+ is the support baseline. A family is officially
supported only while upload validation, the A+ decoder, bounded conversion,
reliable playback preparation, acceptable resource use, and automated coverage
all pass. This contract does not automatically expand when FFmpeg gains another
decoder.

## ON AIR resource policy

Transmission timing has priority. Searching, viewing, and ordinary playback
controls are safe while ON AIR. Required track preparation is limited to one
owned conversion. Speculative cache warming is disabled by default and never
runs ON AIR. Upload, probing, duplicate hashing, deletion, incremental indexing,
and full reindex are rejected until the station is OFF AIR, with no heavyweight
background queue.

When an active playlist exists, browser uploads are also added to it. This makes
first run useful without hiding a second queue or filesystem workflow.

## Library, Playlists, Queue

- **Library:** search, add a track to the active playlist, or delete media with
  confirmation. Deletion removes playlist references and is blocked unless the
  station is authoritatively OFF AIR.
- **Playlists:** create, rename, duplicate, delete, add/remove tracks, and
  reorder with buttons or drag-and-drop.
- **Queue:** the active playlist's current order. Reordering while idle persists
  that order to the playlist. Read/status requests never shuffle or mutate it.

Shuffle is applied only at an explicit queue rebuild boundary. Repeat controls
what happens at the end of the program. Pause/reset affect music; only the
**OFF AIR detent / Stop Broadcast** is the absolute RF stop.
