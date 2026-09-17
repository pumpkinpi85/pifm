"""Incremental library indexing regressions for normal operator imports."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from appliance.library import Library
from appliance.media_import import import_media_stream


class IncrementalIndexingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.library = Library(
            self.root / "library",
            self.root / "playlists",
            self.root / "library.sqlite3",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, name: str, data: bytes):
        with patch(
            "appliance.media_import._probe_media",
            return_value={"codec": "mp3", "duration": 12.5},
        ), patch.object(
            self.library,
            "reindex",
            side_effect=AssertionError("normal upload must not full-reindex"),
        ):
            return import_media_stream(
                self.library,
                io.BytesIO(data),
                name,
                len(data),
            )

    def test_one_duplicate_and_sequential_uploads_are_incremental(self):
        first = self.upload("Artist - First.mp3", b"first")
        duplicate = self.upload("Duplicate name.mp3", b"first")
        second = self.upload("Artist - Second.flac", b"second")

        self.assertFalse(first["duplicate"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["track"]["id"], first["track"]["id"])
        self.assertFalse(second["duplicate"])
        self.assertEqual(self.library.track_count(), 2)
        self.assertEqual(
            {track["filename"] for track in self.library.search()},
            {"Artist - First.mp3", "Artist - Second.flac"},
        )

    def test_delete_updates_one_row_and_playlist_references(self):
        uploaded = self.upload("Delete Me.ogg", b"delete-me")
        track_id = uploaded["track"]["id"]
        self.library.save_playlist(
            "show",
            {"name": "Show", "tracks": [track_id]},
        )
        with patch.object(
            self.library,
            "reindex",
            side_effect=AssertionError("normal delete must not full-reindex"),
        ):
            result = self.library.delete_track(track_id)

        self.assertEqual(result["removed_from_playlists"], ["show"])
        self.assertEqual(self.library.track_count(), 0)
        self.assertEqual(self.library.load_playlist("show")["tracks"], [])
        self.assertFalse((self.library.library_dir / "Delete Me.ogg").exists())

    def test_restart_and_deliberate_reindex_preserve_equivalent_rows(self):
        first = self.upload("Artist - First.m4a", b"one")
        second = self.upload("Artist - Second.aac", b"two")
        before = self.library.search()

        reopened = Library(
            self.library.library_dir,
            self.library.playlists_dir,
            self.library.db_path,
        )
        self.assertEqual(reopened.search(), before)
        self.assertEqual(reopened.reindex(), 2)
        self.assertEqual(reopened.search(), before)
        with closing(reopened._conn()) as conn:
            self.assertEqual(
                conn.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )
        self.assertEqual(
            {first["track"]["id"], second["track"]["id"]},
            {track["id"] for track in reopened.search()},
        )


if __name__ == "__main__":
    unittest.main()
