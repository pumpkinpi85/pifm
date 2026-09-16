"""Filesystem library + SQLite index + JSON playlists."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
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
        with closing(self._conn()) as conn, conn:
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
        with closing(self._conn()) as conn, conn:
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
        with closing(self._conn()) as conn, conn:
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
        with closing(self._conn()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_tracks(self, track_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch an ordered queue's track metadata without one query per track."""
        unique_ids = list(dict.fromkeys(str(track_id) for track_id in track_ids))
        if not unique_ids:
            return {}
        found = {}  # type: Dict[str, Dict[str, Any]]
        with closing(self._conn()) as conn, conn:
            for start in range(0, len(unique_ids), 500):
                chunk = unique_ids[start : start + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    "SELECT * FROM tracks WHERE id IN ({})".format(
                        placeholders
                    ),
                    chunk,
                ).fetchall()
                for row in rows:
                    item = dict(row)
                    found[str(item["id"])] = item
        return found

    def get_track_by_path(self, rel_path: str) -> Optional[Dict[str, Any]]:
        with closing(self._conn()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE path = ?", (str(rel_path),)
            ).fetchone()
        return dict(row) if row else None

    def absolute_path(self, rel: str) -> Path:
        path = (self.library_dir / rel).resolve()
        if os.path.commonpath(
            [str(path), str(self.library_dir.resolve())]
        ) != str(self.library_dir.resolve()):
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
        playlist_id = str(playlist_id or "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", playlist_id):
            raise ValueError("invalid playlist id")
        return self.playlists_dir / "{}.json".format(playlist_id)

    def load_playlist(self, playlist_id: str) -> Dict[str, Any]:
        if not str(playlist_id or ""):
            raise FileNotFoundError("playlist not selected")
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
        tracks = [str(track_id) for track_id in list(data.get("tracks") or [])]
        payload = {
            "name": data.get("name") or path.stem,
            "tracks": tracks,
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

    def playlist_detail(self, playlist_id: str) -> Dict[str, Any]:
        """Return playlist IDs plus hydrated tracks for operator surfaces."""
        playlist = self.load_playlist(playlist_id)
        details = []
        missing = []
        for track_id in playlist.get("tracks") or []:
            track = self.get_track(str(track_id))
            if track:
                details.append(track)
            else:
                missing.append(str(track_id))
        playlist["track_details"] = details
        playlist["missing_track_ids"] = missing
        return playlist

    def rename_playlist(self, playlist_id: str, name: str) -> Dict[str, Any]:
        name = str(name or "").strip()
        if not name:
            raise ValueError("playlist name required")
        playlist = self.load_playlist(playlist_id)
        playlist["name"] = name[:80]
        return self.save_playlist(playlist_id, playlist)

    def delete_track(self, track_id: str) -> Dict[str, Any]:
        """Delete one media file and remove all playlist references."""
        track = self.get_track(str(track_id))
        if not track:
            raise FileNotFoundError("music file not found")
        path = self.absolute_path(str(track["path"]))
        if not path.is_file():
            raise FileNotFoundError("music file not found")
        referenced_by = []
        playlists = []
        for summary in self.list_playlists():
            playlist = self.load_playlist(str(summary["id"]))
            if track_id in (playlist.get("tracks") or []):
                referenced_by.append(str(summary["id"]))
                playlist["tracks"] = [
                    item for item in playlist.get("tracks") or [] if item != track_id
                ]
                playlists.append(playlist)
        staged = self.library_dir / ".deleted-{}".format(uuid.uuid4().hex)
        os.replace(str(path), str(staged))
        try:
            for playlist in playlists:
                self.save_playlist(str(playlist["id"]), playlist)
            indexed = self.reindex()
            staged.unlink()
        except Exception:
            if staged.exists():
                os.replace(str(staged), str(path))
            self.reindex()
            raise
        return {
            "ok": True,
            "track_id": track_id,
            "filename": track.get("filename"),
            "removed_from_playlists": referenced_by,
            "indexed": indexed,
        }

    def delete_playlist(self, playlist_id: str) -> None:
        path = self._playlist_path(playlist_id)
        if path.exists():
            path.unlink()

    def ensure_default_playlists(self, names: List[str]) -> None:
        for name in names:
            path = self._playlist_path(name)
            if not path.exists():
                self.save_playlist(name, {"name": name, "tracks": []})
