import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FLAGPOLE_JS = ROOT / "appliance/web/static/js/flagpole.js"


class FlagpoleMappingTests(unittest.TestCase):
    def run_node(self, body: str) -> None:
        script = "const flagpole = require({!r});\n{}".format(
            str(FLAGPOLE_JS),
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

    def test_full_tuning_grid_has_reversible_integer_mapping(self):
        self.run_node(
            """
            const assert = require("assert");
            const band = {min_units: 871, max_units: 1082, scale: 10};
            assert.strictEqual(flagpole.OFF_DETENT_TRIGGER, 0.10);
            assert.strictEqual(flagpole.TUNER_MIN_POSITION, 0.10);
            assert.strictEqual(flagpole.positionToFrequency(0.099999, band), null);
            assert.strictEqual(flagpole.positionToFrequency(0.10, band), 87.1);
            assert.strictEqual(flagpole.positionToFrequency(1, band), 108.2);
            for (let units = band.min_units; units <= band.max_units; units += 1) {
              const frequency = units / band.scale;
              const position = flagpole.frequencyToPosition(frequency, band);
              assert.strictEqual(flagpole.positionToUnits(position, band), units);
            }
            """
        )

    def test_preview_never_commits_and_release_commits_once(self):
        self.run_node(
            """
            const assert = require("assert");
            const band = {min_units: 871, max_units: 1082, scale: 10};
            const previews = [];
            const commits = [];
            const gesture = flagpole.createGesture({
              onPreview: value => previews.push(value),
              onCancel: () => {},
              onCommit: value => commits.push(value)
            });
            gesture.begin(0.01, band);
            gesture.move(0.10, band);
            gesture.move(0.75, band);
            assert.strictEqual(commits.length, 0);
            const target = gesture.release(0.75, band);
            assert.strictEqual(commits.length, 1);
            assert.strictEqual(target.desired_broadcast, "on");
            assert.strictEqual(gesture.release(1, band), null);
            assert.strictEqual(commits.length, 1);
            assert.ok(previews.length >= 4);
            """
        )

    def test_cancel_and_off_release_are_fail_safe(self):
        self.run_node(
            """
            const assert = require("assert");
            const band = {min_units: 871, max_units: 1082, scale: 10};
            let cancelled = 0;
            const commits = [];
            const gesture = flagpole.createGesture({
              onPreview: () => {},
              onCancel: () => { cancelled += 1; },
              onCommit: value => commits.push(value)
            });
            gesture.begin(0.9, band);
            gesture.cancel("disconnect");
            assert.strictEqual(cancelled, 1);
            assert.strictEqual(gesture.release(0.9, band), null);
            gesture.begin(0.09, band);
            gesture.release(0.09, band);
            assert.strictEqual(commits.length, 1);
            assert.strictEqual(commits[0].desired_broadcast, "off");
            assert.strictEqual(commits[0].frequency_mhz, null);
            """
        )

    def test_full_detent_snaps_to_off_or_lowest_frequency(self):
        self.run_node(
            """
            const assert = require("assert");
            const band = {min_units: 871, max_units: 1082, scale: 10};
            assert.deepStrictEqual(flagpole.targetForPosition(0.099999, band), {
              position: 0,
              desired_broadcast: "off",
              frequency_mhz: null
            });
            assert.deepStrictEqual(flagpole.targetForPosition(0.10, band), {
              position: 0.10,
              desired_broadcast: "on",
              frequency_mhz: 87.1
            });
            """
        )

    def test_pointer_centers_map_to_exact_travel_endpoints(self):
        self.run_node(
            """
            const assert = require("assert");
            const top = 100;
            const trackHeight = 448;
            const handleHeight = 44;
            assert.strictEqual(
              flagpole.pointerPosition(top + 22, top, trackHeight, handleHeight),
              1
            );
            assert.strictEqual(
              flagpole.pointerPosition(
                top + (trackHeight / 2), top, trackHeight, handleHeight
              ),
              0.5
            );
            assert.strictEqual(
              flagpole.pointerPosition(
                top + trackHeight - 22, top, trackHeight, handleHeight
              ),
              0
            );
            """
        )

    def test_indeterminate_and_transition_states_block_commits(self):
        self.run_node(
            """
            const assert = require("assert");
            const blocked = [
              {state: "FAULT", broadcast_ui: "STATE UNKNOWN"},
              {state: "READY", broadcast_ui: "STATE UNKNOWN"},
              {
                state: "READY",
                broadcast_ui: "STATE UNKNOWN / POSSIBLE TRANSMISSION"
              },
              {state: "READY", broadcast_ui: "STARTING BROADCAST…"},
              {state: "ON_AIR", broadcast_ui: "STOPPING BROADCAST…"}
            ];
            blocked.forEach(snapshot => {
              assert.strictEqual(flagpole.isCommitBlocked(snapshot), true);
            });
            assert.strictEqual(
              flagpole.isCommitBlocked({state: "READY", broadcast_ui: "OFF"}),
              false
            );
            assert.strictEqual(
              flagpole.isCommitBlocked({state: "ON_AIR", broadcast_ui: "ON AIR"}),
              false
            );
            """
        )

    def test_stale_snapshot_cannot_resolve_pending_tx_command(self):
        self.run_node(
            """
            const assert = require("assert");
            const staleOff = {
              state: "READY",
              broadcast_ui: "OFF",
              snapshot_revision: 12
            };
            const newerOff = Object.assign(
              {}, staleOff, {snapshot_revision: 14}
            );
            const newerOn = {
              state: "ON_AIR",
              broadcast_ui: "ON AIR",
              snapshot_revision: 14
            };
            assert.strictEqual(
              flagpole.shouldResolveTxPending("txon", 12, staleOff), false
            );
            assert.strictEqual(
              flagpole.shouldResolveTxPending("txon", 12, newerOff), false
            );
            assert.strictEqual(
              flagpole.shouldResolveTxPending("txon", 12, newerOn), true
            );
            assert.strictEqual(
              flagpole.shouldResolveTxPending("txoff", 12, staleOff), false
            );
            assert.strictEqual(
              flagpole.shouldResolveTxPending("txoff", 12, newerOff), true
            );
            """
        )


if __name__ == "__main__":
    unittest.main()
