"""Safe, streaming media imports for the browser and other operator surfaces."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import subprocess
import unicodedata
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional

from .library import AUDIO_EXT, Library


DEFAULT_MAX_UPLOAD_BYTES = 128 * 1024 * 1024


class MediaImportError(ValueError):
    """An upload error that is safe to present to an operator."""


def safe_media_filename(filename: str) -> str:
    """Return a filesystem-safe basename while retaining a useful title."""
    name = unicodedata.normalize("NFKC", str(filename or ""))
    name = name.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f").strip()
    name = re.sub(r"\s+", " ", name)
    name = name.lstrip(".")
    if not name or name in (".", ".."):
        raise MediaImportError("Choose a music file with a valid filename.")
    if len(name) > 180:
        stem = Path(name).stem[:150].rstrip()
        name = stem + Path(name).suffix[:16]
    suffix = Path(name).suffix.lower()
    if suffix not in AUDIO_EXT:
        raise MediaImportError(
            "This file type is not supported. Try MP3, WAV, FLAC, M4A/AAC, or OGG."
        )
    return name


def media_capabilities() -> Dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    return {
        "ffmpeg_available": bool(ffmpeg),
        "ffprobe_available": bool(ffprobe),
        "import_ready": bool(ffmpeg and ffprobe),
        "formats": ["mp3", "wav", "flac", "m4a", "aac", "ogg"],
        "max_upload_bytes": DEFAULT_MAX_UPLOAD_BYTES,
    }


def _probe_media(path: Path) -> Dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise MediaImportError(
            "Music import is not ready because FFmpeg is not installed."
        )
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=codec_type,codec_name:format=duration,format_name",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise MediaImportError(
            "This file could not be inspected. Try another unprotected audio file."
        )
    if result.returncode != 0:
        raise MediaImportError(
            "This file could not be decoded. It may be damaged, encrypted, or DRM-protected."
        )
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MediaImportError("This file returned invalid audio information.")
    streams = payload.get("streams") or []
    audio = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not audio:
        raise MediaImportError("No playable audio track was found in this file.")
    fmt = payload.get("format") or {}
    try:
        duration = float(fmt.get("duration")) if fmt.get("duration") else None
    except (TypeError, ValueError):
        duration = None
    return {
        "codec": audio[0].get("codec_name"),
        "container": fmt.get("format_name"),
        "duration": duration,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _available_destination(directory: Path, filename: str) -> Path:
    candidate = directory / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for number in range(2, 10000):
        numbered = directory / "{} ({}){}".format(stem, number, suffix)
        if not numbered.exists():
            return numbered
    raise MediaImportError("Too many files already use this name.")


def _find_duplicate(library_dir: Path, uploaded: Path) -> Optional[Path]:
    uploaded_size = uploaded.stat().st_size
    uploaded_hash = None  # type: Optional[str]
    for candidate in sorted(library_dir.rglob("*")):
        if (
            not candidate.is_file()
            or candidate.suffix.lower() not in AUDIO_EXT
            or candidate.stat().st_size != uploaded_size
        ):
            continue
        if uploaded_hash is None:
            uploaded_hash = _sha256(uploaded)
        if _sha256(candidate) == uploaded_hash:
            return candidate
    return None


def import_media_stream(
    library: Library,
    stream: BinaryIO,
    filename: str,
    content_length: int,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> Dict[str, Any]:
    """Stream, validate, and atomically add one operator-owned media file."""
    safe_name = safe_media_filename(filename)
    if content_length <= 0:
        raise MediaImportError("The selected file is empty.")
    if content_length > max_bytes:
        raise MediaImportError(
            "This file is too large. The upload limit is {} MB.".format(
                max_bytes // (1024 * 1024)
            )
        )
    partial = library.library_dir / ".upload-{}.partial".format(uuid.uuid4().hex)
    remaining = content_length
    try:
        with partial.open("wb") as handle:
            while remaining:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    raise MediaImportError("The upload ended before the file was complete.")
                handle.write(chunk)
                remaining -= len(chunk)
        probe = _probe_media(partial)
        duplicate = _find_duplicate(library.library_dir, partial)
        if duplicate is not None:
            partial.unlink()
            indexed = library.reindex()
            relative_path = str(duplicate.relative_to(library.library_dir))
            track = library.get_track_by_path(relative_path)
            return {
                "ok": True,
                "filename": duplicate.name,
                "indexed": indexed,
                "duplicate": True,
                "renamed": False,
                "media": probe,
                "track": track,
            }
        destination = _available_destination(library.library_dir, safe_name)
        os.replace(str(partial), str(destination))
        indexed = library.reindex()
        track = library.get_track_by_path(destination.name)
        return {
            "ok": True,
            "filename": destination.name,
            "indexed": indexed,
            "duplicate": False,
            "renamed": destination.name != safe_name,
            "media": probe,
            "track": track,
        }
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, errno.EDQUOT):
            raise MediaImportError(
                "Storage is full. Existing music was not changed."
            )
        raise MediaImportError(
            "This music file could not be saved. Existing music was not changed."
        )
    finally:
        if partial.exists():
            try:
                partial.unlink()
            except OSError:
                pass
