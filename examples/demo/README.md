# Demo media

Bundled product demo track (not live operator data):

| Field | Value |
|-------|--------|
| File | `Brynja Vinter - The Sky Belongs to No King.wav` |
| Artist | **Brynja Vinter** |
| Title | The Sky Belongs to No King |
| Format | PCM16 LE WAV, 44.1 kHz, mono |
| Duration | 276.28 s |
| Role | Fresh-install seed + non-RF / RF validation reference audio |

On install, piFM copies this file into `data/library/demo/` **only when that
destination does not already exist**, and creates a `default` playlist that
references it when no playlist file exists yet. Operator uploads remain
separate and are never committed to Git.

**Credit:** Recording by Brynja Vinter. Redistribution of this recording with
the public piFM product is intentional and founder-authorized. Do not replace
it with unrelated copyrighted catalog without a clear redistribution right.
