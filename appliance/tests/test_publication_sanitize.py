"""Fail if private/local artifacts leak into the public candidate tree."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Negative-test file may contain forbidden tokens as scan needles only.
ALLOWLIST_REL = {
    "appliance/tests/test_publication_sanitize.py",
    "appliance/tests/test_pifmrds_pin.py",
}

FORBIDDEN = [
    "192.168.1.190",
    "/home/pi/piFM",
    "/home/pi/pifm",
    "/home/pi/cabinet",
    "/Users/nathankoops",
    "commissioning-",
    "nathankoops",
    "Nathan Koops",
    "Nathan's",
    "ssh pifm",
    "--remote pifm",
    "imager.service",
    "48aeca95",
    "github.com/pumpkinpi85/PiFmRds.git",
]

FORBIDDEN_CONTENT = [
    "Star Wars",
    "starwars",
]


def _iter_text_files():
    skip_dirs = {".git", "__pycache__", "data"}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".wav", ".mp3", ".pyc", ".svg"}:
            continue
        if path.name == "LICENSE":
            continue
        yield path


class PublicationSanitizeTests(unittest.TestCase):
    def test_no_private_host_or_path_leaks(self):
        offenders = []
        for path in _iter_text_files():
            rel = str(path.relative_to(ROOT))
            if rel in ALLOWLIST_REL:
                continue
            text = path.read_text(errors="replace")
            for token in FORBIDDEN:
                if token in text:
                    offenders.append("{}: {}".format(rel, token))
        self.assertEqual(offenders, [], "private artifacts:\n" + "\n".join(offenders))

    def test_no_star_wars_demo_content(self):
        offenders = []
        for path in _iter_text_files():
            rel = str(path.relative_to(ROOT))
            if rel in ALLOWLIST_REL:
                continue
            text = path.read_text(errors="replace")
            for token in FORBIDDEN_CONTENT:
                if token in text:
                    offenders.append("{}: {}".format(rel, token))
        self.assertEqual(offenders, [], "demo content leaks:\n" + "\n".join(offenders))

    def test_no_tracked_operator_media_or_live_config(self):
        tracked = {
            line.strip()
            for line in __import__("subprocess")
            .check_output(["git", "ls-files"], cwd=str(ROOT), text=True)
            .splitlines()
        }
        allowed_demo_prefix = "examples/demo/"
        banned_suffixes = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".sqlite3")
        offenders = [
            path
            for path in tracked
            if (
                path.endswith(banned_suffixes)
                and not path.startswith(allowed_demo_prefix)
            )
            or path == "config/appliance.json"
        ]
        self.assertEqual(offenders, [], "operator data tracked:\n" + "\n".join(offenders))

    def test_bundled_demo_track_is_present(self):
        demo = ROOT / "examples" / "demo" / "Brynja Vinter - The Sky Belongs to No King.wav"
        self.assertTrue(demo.is_file(), "missing bundled demo track")
        self.assertGreater(demo.stat().st_size, 1_000_000)
        readme = ROOT / "examples" / "demo" / "README.md"
        self.assertTrue(readme.is_file())
        text = readme.read_text()
        self.assertIn("The Sky Belongs to No King", text)
        self.assertIn("Brynja Vinter", text)


if __name__ == "__main__":
    unittest.main()
