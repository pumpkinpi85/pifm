"""Browser authority/reconnection state-machine tests (no RF)."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "appliance/web/static/js/app.js"


@unittest.skipUnless(shutil.which("node"), "node is unavailable")
class BrowserReconnectionTests(unittest.TestCase):
    def run_node(self, source: str) -> None:
        result = subprocess.run(
            [str(shutil.which("node")), "-e", source],
            cwd=str(ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reconnect_discards_stale_state_before_new_authoritative_snapshot(self):
        self.run_node(
            """
const assert = require("assert");
const api = require("./appliance/web/static/js/connection.js");
function snapshot(track) {
  return {
    broadcast_state: "on_air", broadcast_recovery: {armed: true},
    tx_running: true, fault_reason: null, program_state: "playing",
    current_track: {id: track}, next_track: {id: track + "-next"},
    active_playlist: "demo", queue: [{id: track}], shuffle: false,
    repeat: true, broadcast: {ready: true}, hardware_status: "SUPPORTED"
  };
}
const snapshots = [snapshot("track-2"), snapshot("track-3")];
const events = [];
let browserState = null;
const coordinator = api.createCoordinator({
  fetchSnapshot: () => {
    events.push("fetch");
    return Promise.resolve(snapshots.shift());
  },
  onUnavailable: () => {
    browserState = null;
    events.push("unavailable");
  },
  onSnapshot: (state) => {
    browserState = state;
    events.push("snapshot:" + state.current_track.id);
  },
  onSynchronized: () => events.push("synchronized")
});
(async () => {
  assert.strictEqual(await coordinator.reconcile(), true);
  assert.strictEqual(browserState.current_track.id, "track-2");
  coordinator.markUnavailable("Connection lost");
  assert.strictEqual(browserState, null);
  assert.strictEqual(coordinator.isSynchronized(), false);
  assert.strictEqual(await coordinator.reconcile(), true);
  assert.strictEqual(browserState.current_track.id, "track-3");
  assert.deepStrictEqual(events, [
    "fetch", "snapshot:track-2", "synchronized",
    "unavailable", "fetch", "snapshot:track-3", "synchronized"
  ]);
})().catch((error) => { console.error(error); process.exit(1); });
"""
        )

    def test_stale_inflight_snapshot_cannot_reconfirm_connection(self):
        self.run_node(
            """
const assert = require("assert");
const api = require("./appliance/web/static/js/connection.js");
let resolveFetch;
let snapshotsApplied = 0;
const coordinator = api.createCoordinator({
  fetchSnapshot: () => new Promise((resolve) => { resolveFetch = resolve; }),
  onUnavailable: () => {},
  onSnapshot: () => { snapshotsApplied += 1; },
  onSynchronized: () => {}
});
const pending = coordinator.reconcile();
setImmediate(() => {
  coordinator.markUnavailable("Connection lost");
  resolveFetch({
    broadcast_state: "off", broadcast_recovery: {armed: false},
    tx_running: false, fault_reason: null, program_state: "stopped",
    current_track: null, next_track: null, active_playlist: "demo",
    queue: [], shuffle: false, repeat: true, broadcast: {ready: true},
    hardware_status: "SUPPORTED"
  });
});
(async () => {
  assert.strictEqual(await pending, false);
  assert.strictEqual(snapshotsApplied, 0);
  assert.strictEqual(coordinator.isSynchronized(), false);
})().catch((error) => { console.error(error); process.exit(1); });
"""
        )

    def test_mutations_are_guarded_until_synchronized(self):
        source = APP_JS.read_text()
        self.assertIn('method !== "GET" && !uiSynchronized', source)
        self.assertIn("connectionCoordinator.reconcile()", source)
        self.assertIn("connectionCoordinator.acceptLiveSnapshot", source)
        self.assertNotIn('api("/api/queue").then', source)


if __name__ == "__main__":
    unittest.main()
