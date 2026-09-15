"""Filesystem library + SQLite index + JSON playlists."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

AUDIO_EXT = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}


def _parse_name(stem: str) -> Tuple[str, str]:
    if " - " in stem:
        a, t = stem.split(" - ", 1)
        return a.strip() or "Unknown", t.strip() or stem
    return "Unknown", stem


class Library:
    def __init__(self, library_dir: Path, playlists_dir: Path, db_path: Path) -> None:
        self.library_dir = library_dir
        self.playlists_dir = playlists_dir
        self.db_path = db_path
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.playlists_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    id TEXT PRIMARY KEY,
                    path TEXT UNIQUE NOT NULL,
                    filename TEXT NOT NULL,
                    artist TEXT,
                    title TEXT,
                    format TEXT,
                    size INTEGER,
                    mtime REAL,
                    duration REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)"
            )

    def reindex(self) -> int:
        count = 0
        with self._conn() as conn:
            conn.execute("DELETE FROM tracks")
            for path in sorted(self.library_dir.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in AUDIO_EXT:
                    continue
                rel = str(path.relative_to(self.library_dir))
                artist, title = _parse_name(path.stem)
                tid = uuid.uuid5(uuid.NAMESPACE_URL, rel).hex
                st = path.stat()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO tracks
                    (id, path, filename, artist, title, format, size, mtime, duration)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tid,
                        rel,
                        path.name,
                        artist,
                        title,
                        path.suffix.lower().lstrip("."),
                        st.st_size,
                        st.st_mtime,
                        None,
                    ),
                )
                count += 1
        return count

    def search(self, q: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        q = (q or "").strip()
        with self._conn() as conn:
            if q:
                like = "%{}%".format(q.replace("%", ""))
                rows = conn.execute(
                    """
                    SELECT * FROM tracks
                    WHERE title LIKE ? OR artist LIKE ? OR filename LIKE ?
                    ORDER BY artist, title LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tracks ORDER BY artist, title LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_track(self, track_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
        return dict(row) if row else None

    def absolute_path(self, rel: str) -> Path:
        path = (self.library_dir / rel).resolve()
        if not str(path).startswith(str(self.library_dir.resolve())):
            raise ValueError("path escapes library")
        return path

    # --- playlists (JSON files) ---

    def list_playlists(self) -> List[Dict[str, Any]]:
        out = []
        for path in sorted(self.playlists_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                tracks = data.get("tracks") or []
                out.append(
                    {
                        "id": path.stem,
                        "name": data.get("name") or path.stem,
                        "track_count": len(tracks),
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def _playlist_path(self, playlist_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", playlist_id).strip("_") or "playlist"
        return self.playlists_dir / "{}.json".format(safe)

    def load_playlist(self, playlist_id: str) -> Dict[str, Any]:
        path = self._playlist_path(playlist_id)
        if not path.exists():
            raise FileNotFoundError("playlist not found: {}".format(playlist_id))
        data = json.loads(path.read_text())
        data["id"] = path.stem
        data.setdefault("name", path.stem)
        data.setdefault("tracks", [])
        return data

    def save_playlist(self, playlist_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        path = self._playlist_path(playlist_id)
        payload = {
            "name": data.get("name") or path.stem,
            "tracks": list(data.get("tracks") or []),
            "updated": time.time(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        os.replace(str(tmp), str(path))
        return self.load_playlist(path.stem)

    def create_playlist(self, name: str) -> Dict[str, Any]:
        pid = re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_").lower() or "playlist"
        path = self._playlist_path(pid)
        if path.exists():
            pid = "{}_{}".format(pid, uuid.uuid4().hex[:6])
        return self.save_playlist(pid, {"name": name, "tracks": []})

    def delete_playlist(self, playlist_id: str) -> None:
        path = self._playlist_path(playlist_id)
        if path.exists():
            path.unlink()

    def ensure_default_playlists(self, names: List[str]) -> None:
        for name in names:
            path = self._playlist_path(name)
            if not path.exists():
                self.save_playlist(name, {"name": name, "tracks": []})
