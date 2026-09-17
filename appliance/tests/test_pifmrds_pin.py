"""Enforce pinned upstream PiFmRds dependency policy (not fork, not vendored)."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / "third_party" / "pifmrds.pin"

# Documents / scripts that must carry the pin SHA and upstream identity.
PIN_SURFACES = [
    ROOT / "README.md",
    ROOT / "docs" / "INSTALL.md",
    ROOT / "docs" / "HARDWARE.md",
    ROOT / "third_party" / "NOTICE.md",
    ROOT / "scripts" / "install-pi-fm-rds.sh",
    ROOT / "scripts" / "install.sh",
]

# Mentions of the org fork are allowed only when stating it is not a dependency.
FORK_MENTION_ALLOWLIST = {
    "third_party/NOTICE.md",
    "docs/INSTALL.md",
    "docs/HARDWARE.md",
    "PROJECT_MASTER.md",
    "appliance/tests/test_pifmrds_pin.py",
    "appliance/tests/test_publication_sanitize.py",
    "scripts/install-pi-fm-rds.sh",
}


def _load_pin() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in PIN_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class PiFmRdsPinPolicyTests(unittest.TestCase):
    def test_pin_file_defines_upstream_identity(self):
        pin = _load_pin()
        self.assertEqual(pin["PIFMRDS_UPSTREAM_OWNER"], "ChristopheJacquet")
        self.assertEqual(pin["PIFMRDS_UPSTREAM_REPO"], "PiFmRds")
        self.assertEqual(
            pin["PIFMRDS_UPSTREAM_URL"],
            "https://github.com/ChristopheJacquet/PiFmRds.git",
        )
        self.assertRegex(pin["PIFMRDS_UPSTREAM_SHA"], r"^[0-9a-f]{40}$")
        self.assertEqual(pin["PIFMRDS_BINARY_PATH"], "/usr/local/bin/pi_fm_rds")
        self.assertEqual(
            pin["PIFMRDS_UPSTREAM_SHA"],
            "777f8e52648b88156483d067e24e0b8682abe8c1",
        )

    def test_docs_and_installers_record_same_sha_and_upstream(self):
        pin = _load_pin()
        sha = pin["PIFMRDS_UPSTREAM_SHA"]
        for path in PIN_SURFACES:
            text = path.read_text()
            self.assertIn(
                "ChristopheJacquet",
                text,
                "{} must identify upstream ChristopheJacquet".format(path),
            )
            if path.name in ("install.sh", "install-pi-fm-rds.sh"):
                self.assertIn(
                    "pifmrds.pin",
                    text,
                    "{} must source third_party/pifmrds.pin".format(path),
                )
            else:
                self.assertIn(
                    sha,
                    text,
                    "{} must record pinned SHA {}".format(path, sha),
                )
            self.assertNotIn(
                "github.com/pumpkinpi85/PiFmRds.git",
                text,
                "{} must not clone the org fork".format(path),
            )

    def test_install_helper_defaults_to_pin_without_src(self):
        script = (ROOT / "scripts" / "install-pi-fm-rds.sh").read_text()
        self.assertIn('source "$PIN_FILE"', script)
        self.assertIn("git clone", script)
        self.assertIn("git -C \"$CLONE_DIR\" checkout", script)
        self.assertIn("pumpkinpi85/PiFmRds", script)  # explicit refuse
        self.assertIn('install -m 755 "$SRC/src/pi_fm_rds" "$DEST"', script)

    def test_no_vendored_pifmrds_sources(self):
        banned_names = {"fm_rds.c", "pi_fm_rds.c", "rds.c"}
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if path.name in banned_names:
                offenders.append(str(path.relative_to(ROOT)))
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("third_party/PiFmRds/") or rel == "PiFmRds":
                offenders.append(rel)
        self.assertEqual(offenders, [], "vendored PiFmRds sources:\n" + "\n".join(offenders))

    def test_tree_does_not_depend_on_org_fork_url(self):
        clone_fork = re.compile(
            r"github\.com/pumpkinpi85/PiFmRds(?:\.git)?",
            re.IGNORECASE,
        )
        offenders = []
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {".git", "__pycache__", "data"} for part in path.parts):
                continue
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".wav", ".mp3", ".pyc", ".svg"}:
                continue
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text(errors="replace")
            if not clone_fork.search(text):
                continue
            if rel in FORK_MENTION_ALLOWLIST:
                # Allowed only as "do not use / not a dependency" prose.
                lower = text.lower()
                self.assertTrue(
                    "not" in lower or "refus" in lower or "do not" in lower,
                    "{} mentions fork without negation".format(rel),
                )
                continue
            offenders.append(rel)
        self.assertEqual(
            offenders,
            [],
            "unexpected pumpkinpi85/PiFmRds dependency refs:\n" + "\n".join(offenders),
        )

    def test_install_helper_help_exposes_pin(self):
        pin = _load_pin()
        proc = subprocess.run(
            ["bash", str(ROOT / "scripts" / "install-pi-fm-rds.sh"), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn(pin["PIFMRDS_UPSTREAM_SHA"], proc.stdout)
        self.assertIn("ChristopheJacquet/PiFmRds", proc.stdout)
        self.assertIn(pin["PIFMRDS_BINARY_PATH"], proc.stdout)

    def test_install_scripts_are_valid_bash(self):
        for name in ("install-pi-fm-rds.sh", "install.sh"):
            subprocess.run(
                ["bash", "-n", str(ROOT / "scripts" / name)],
                check=True,
                capture_output=True,
                text=True,
            )


if __name__ == "__main__":
    unittest.main()
