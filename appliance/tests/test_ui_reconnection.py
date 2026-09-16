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
function snapshot(track, revision) {
  return {
    snapshot_revision: revision,
    state: "ON_AIR", broadcast_ui: "ON AIR",
    broadcast_state: "on_air", broadcast_recovery: {armed: true, valid: true},
    tx: {running: true}, tx_backend: "mock", tx_running: true,
    fault_reason: null, program_state: "playing", program_ui: "PLAYING",
    program_pending: null,
    current_track: {id: track}, next_track: {id: track + "-next"},
    now_playing: {id: track}, up_next: {id: track + "-next"},
    first_up: null, active_playlist: "demo",
    selected_playlist_name: "Demo", queue_index: 0, queue_length: 1,
    queue: [{id: track, queue_pos: 0, is_current: true}],
    shuffle: false, repeat: true,
    broadcast: {ready: true, blockers: [], items: []},
    frequency_mhz: 100.1, rds_ps: "PIFM", rds_rt: "Test", rds_pi: "1234",
    network: {ip: "127.0.0.1"}, health: {}, hardware_status: "SUPPORTED",
    hardware_environment: {checks: []}, hardware_profile_doc: {},
    software_version: "test", git_sha: "abc", build_label: "test"
  };
}
const snapshots = [snapshot("track-2", 1), snapshot("track-3", 2)];
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
    snapshot_revision: 1,
    state: "SAFE_OFF", broadcast_ui: "OFF",
    broadcast_state: "off", broadcast_recovery: {armed: false, valid: true},
    tx: {running: false}, tx_backend: "mock", tx_running: false,
    fault_reason: null, program_state: "stopped", program_ui: "READY",
    program_pending: null,
    current_track: null, next_track: null, active_playlist: "demo",
    now_playing: null, up_next: null, first_up: null,
    selected_playlist_name: "Demo", queue_index: -1, queue_length: 0,
    queue: [], shuffle: false, repeat: true,
    broadcast: {ready: true, blockers: [], items: []},
    frequency_mhz: 100.1, rds_ps: "PIFM", rds_rt: "Test", rds_pi: "1234",
    network: {}, health: {}, hardware_status: "SUPPORTED",
    hardware_environment: {checks: []}, hardware_profile_doc: {},
    software_version: "test", git_sha: "abc", build_label: "test"
  });
});
(async () => {
  assert.strictEqual(await pending, false);
  assert.strictEqual(snapshotsApplied, 0);
  assert.strictEqual(coordinator.isSynchronized(), false);
})().catch((error) => { console.error(error); process.exit(1); });
"""
        )

    def test_live_snapshot_supersedes_older_inflight_http_snapshot(self):
        self.run_node(
            """
const assert = require("assert");
const api = require("./appliance/web/static/js/connection.js");
function snapshot(track, revision) {
  return {
    snapshot_revision: revision,
    state: "ON_AIR", broadcast_ui: "ON AIR",
    broadcast_state: "on_air", broadcast_recovery: {armed: true, valid: true},
    tx: {running: true}, tx_backend: "mock", tx_running: true,
    fault_reason: null, program_state: "playing", program_ui: "PLAYING",
    program_pending: null, current_track: {id: track}, next_track: null,
    now_playing: {id: track}, up_next: null, first_up: null,
    active_playlist: "demo", selected_playlist_name: "Demo",
    queue_index: 0, queue_length: 1,
    queue: [{id: track, queue_pos: 0, is_current: true}],
    shuffle: false, repeat: true,
    broadcast: {ready: true, blockers: [], items: []},
    frequency_mhz: 100.1, rds_ps: "PIFM", rds_rt: "Test", rds_pi: "1234",
    network: {}, health: {}, hardware_status: "SUPPORTED",
    hardware_environment: {checks: []}, hardware_profile_doc: {},
    software_version: "test", git_sha: "abc", build_label: "test"
  };
}
let resolveFetch;
let browserState = null;
let firstFetch = true;
const coordinator = api.createCoordinator({
  fetchSnapshot: () => {
    if (firstFetch) {
      firstFetch = false;
      return Promise.resolve(snapshot("baseline", 1));
    }
    return new Promise((resolve) => { resolveFetch = resolve; });
  },
  onUnavailable: () => { browserState = null; },
  onSnapshot: (state) => { browserState = state; },
  onSynchronized: () => {}
});
(async () => {
  assert.strictEqual(await coordinator.reconcile(), true);
  const pendingHttp = coordinator.reconcile();
  await new Promise((resolve) => setImmediate(resolve));
  assert.strictEqual(coordinator.acceptLiveSnapshot(snapshot("newer-sse", 3)), true);
  resolveFetch(snapshot("older-http", 2));
  assert.strictEqual(await pendingHttp, false);
  assert.strictEqual(browserState.current_track.id, "newer-sse");
  assert.strictEqual(coordinator.isSynchronized(), true);
})().catch((error) => { console.error(error); process.exit(1); });
"""
        )

    def test_delayed_sse_cannot_regress_newer_http_snapshot(self):
        self.run_node(
            """
const assert = require("assert");
const api = require("./appliance/web/static/js/connection.js");
function snapshot(track, revision) {
  return {
    snapshot_revision: revision, state: "ON_AIR", broadcast_ui: "ON AIR",
    broadcast_state: "on_air", broadcast_recovery: {armed: true, valid: true},
    tx: {running: true}, tx_backend: "mock", tx_running: true,
    fault_reason: null, program_state: "playing", program_ui: "PLAYING",
    program_pending: null, current_track: {id: track}, next_track: null,
    now_playing: {id: track}, up_next: null, first_up: null,
    active_playlist: "demo", selected_playlist_name: "Demo",
    queue_index: 0, queue_length: 1,
    queue: [{id: track, queue_pos: 0, is_current: true}],
    shuffle: false, repeat: true,
    broadcast: {ready: true, blockers: [], items: []},
    frequency_mhz: 100.1, rds_ps: "PIFM", rds_rt: "Test", rds_pi: "1234",
    network: {}, health: {}, hardware_status: "SUPPORTED",
    hardware_environment: {checks: []}, hardware_profile_doc: {},
    software_version: "test", git_sha: "abc", build_label: "test"
  };
}
const snapshots = [snapshot("baseline", 1), snapshot("newer-http", 3)];
let browserState = null;
const coordinator = api.createCoordinator({
  fetchSnapshot: () => Promise.resolve(snapshots.shift()),
  onUnavailable: () => { browserState = null; },
  onSnapshot: (state) => { browserState = state; },
  onSynchronized: () => {}
});
(async () => {
  assert.strictEqual(await coordinator.reconcile(), true);
  assert.strictEqual(await coordinator.reconcile(), true);
  assert.strictEqual(
    coordinator.acceptLiveSnapshot(snapshot("delayed-sse", 2)), false
  );
  assert.strictEqual(browserState.current_track.id, "newer-http");
  const malformedNested = snapshot("bad-network", 4);
  malformedNested.network = [];
  assert.throws(() => api.validateSnapshot(malformedNested));
  const contradictoryQueue = snapshot("bad-queue", 4);
  contradictoryQueue.queue[0].is_current = false;
  assert.throws(() => api.validateSnapshot(contradictoryQueue));
})().catch((error) => { console.error(error); process.exit(1); });
"""
        )

    def test_incomplete_snapshot_cannot_mark_browser_synchronized(self):
        self.run_node(
            """
const assert = require("assert");
const api = require("./appliance/web/static/js/connection.js");
let synchronized = false;
const coordinator = api.createCoordinator({
  fetchSnapshot: () => Promise.resolve({
    snapshot_revision: 1, state: "SAFE_OFF", broadcast_ui: "OFF"
  }),
  onUnavailable: () => {},
  onSnapshot: () => {},
  onSynchronized: () => { synchronized = true; }
});
(async () => {
  assert.strictEqual(await coordinator.reconcile(), false);
  assert.strictEqual(synchronized, false);
  assert.strictEqual(coordinator.isSynchronized(), false);
})().catch((error) => { console.error(error); process.exit(1); });
"""
        )

    def test_mutations_are_guarded_until_synchronized(self):
        source = APP_JS.read_text()
        self.assertIn('method !== "GET" && !uiSynchronized', source)
        self.assertIn("Remaining uploads were not sent.", source)
        self.assertIn("batchAuthorityEpoch !== uiAuthorityEpoch", source)
        self.assertIn("batchPlaylistId = state && state.active_playlist", source)
        self.assertIn('"X-Playlist-ID", batchPlaylistId', source)
        self.assertIn("requestAuthorityEpoch !== uiAuthorityEpoch", source)
        self.assertIn("connectionCoordinator.reconcile()", source)
        self.assertIn("connectionCoordinator.acceptLiveSnapshot", source)
        self.assertNotIn('api("/api/queue").then', source)


if __name__ == "__main__":
    unittest.main()
