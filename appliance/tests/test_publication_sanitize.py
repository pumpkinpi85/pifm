"""Fail if private/local artifacts leak into the public candidate tree."""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Files that may mention forbidden tokens only as scan instructions / negative tests.
ALLOWLIST_REL = {
    "PROJECT_MASTER.md",
    "CHANGELOG.md",
    "docs/clean-room-validation.md",
    "docs/canonical-convergence.md",
    "docs/cutover-reference-station.md",
    "docs/legacy-retirement.md",
    "docs/deployment.md",
    "docs/persistent-data.md",
    "docs/supported-pis.md",
    "docs/evidence/clean-room-phase-a.template.md",
    "appliance/tests/test_publication_sanitize.py",
    "appliance/tests/test_ux.py",
}

FORBIDDEN = [
    "192.168.1.190",
    "/home/pi/piFM",
    "/home/pi/pifm",
    "/home/pi/cabinet",
    "commissioning-",
    "nathankoops",
    "imager.service",
    "48aeca95",
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
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".wav", ".mp3", ".pyc"}:
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


if __name__ == "__main__":
    unittest.main()
