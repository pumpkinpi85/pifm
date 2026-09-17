"""Unit coverage for experimental ON AIR flag animation helpers."""

from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANIM_JS = ROOT / "appliance/web/static/js/flag-on-air-animation.js"
FRAMES = ROOT / "appliance/web/static/images/broadcast-control/flag-on-air-extreme-80"
STATIC_ON = (
    ROOT / "appliance/web/static/images/broadcast-control/broadcast-flag-on.png"
)


class OnAirFlagAnimationTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        script = "const anim = require({!r});\n{}".format(
            str(ANIM_JS),
            textwrap.dedent(body),
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_static_orange_flag_preserved_for_rollback(self):
        self.assertTrue(STATIC_ON.is_file())

    def test_all_eighty_frames_and_manifest_present(self):
        manifest = json.loads((FRAMES / "manifest.json").read_text())
        self.assertEqual(manifest["fps"], 10)
        self.assertEqual(manifest["frame_duration_ms"], 100)
        self.assertEqual(manifest["frame_count"], 80)
        self.assertEqual(manifest["loop_duration_ms"], 8000)
        self.assertEqual(len(manifest["order"]), 80)
        for index, name in enumerate(manifest["order"], start=1):
            expected = "flag_on_{:03d}.png".format(index)
            self.assertEqual(name, expected)
            self.assertTrue((FRAMES / name).is_file(), name)

    def test_animation_module_timing_and_frame_urls(self):
        self.run_node(
            """
            const assert = require("assert");
            assert.strictEqual(anim.FRAME_COUNT, 80);
            assert.strictEqual(anim.FRAME_MS, 100);
            assert.strictEqual(anim.LOOP_MS, 8000);
            assert.strictEqual(anim.frameUrl(0), "/images/broadcast-control/flag-on-air-extreme-80/flag_on_001.png");
            assert.strictEqual(anim.frameUrl(79), "/images/broadcast-control/flag-on-air-extreme-80/flag_on_080.png");
            assert.ok(String(anim.STATIC_ON_SRC).indexOf("broadcast-flag-on.png") >= 0);
            // 080 -> 001 seam has no pause: next index after 79 wraps to 0 at exactly 8s.
            assert.strictEqual(Math.floor(7999 / anim.FRAME_MS) % anim.FRAME_COUNT, 79);
            assert.strictEqual(Math.floor(8000 / anim.FRAME_MS) % anim.FRAME_COUNT, 0);
            assert.strictEqual(Math.floor(8100 / anim.FRAME_MS) % anim.FRAME_COUNT, 1);
            """
        )


if __name__ == "__main__":
    unittest.main()
