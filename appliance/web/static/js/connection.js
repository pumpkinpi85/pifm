(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.PifmConnection = api;
  }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var REQUIRED_KEYS = [
    "snapshot_revision",
    "state",
    "broadcast_state",
    "broadcast_ui",
    "broadcast_recovery",
    "tx_running",
    "fault_reason",
    "program_state",
    "program_ui",
    "program_pending",
    "current_track",
    "next_track",
    "now_playing",
    "up_next",
    "first_up",
    "active_playlist",
    "selected_playlist_name",
    "queue",
    "queue_index",
    "queue_length",
    "shuffle",
    "repeat",
    "broadcast",
    "frequency_mhz",
    "rds_ps",
    "rds_rt",
    "rds_pi",
    "network",
    "health",
    "hardware_status",
    "hardware_environment",
    "hardware_profile_doc",
    "software_version",
    "git_sha",
    "build_label",
    "tx",
    "tx_backend"
  ];

  function validateSnapshot(snapshot) {
    function isPlainObject(value) {
      return !!value && typeof value === "object" && !Array.isArray(value);
    }

    if (!isPlainObject(snapshot)) {
      throw new Error("authoritative state snapshot is missing");
    }
    REQUIRED_KEYS.forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(snapshot, key)) {
        throw new Error("authoritative state is missing " + key);
      }
    });
    if (!Array.isArray(snapshot.queue)) {
      throw new Error("authoritative queue is invalid");
    }
    ["state", "broadcast_state", "broadcast_ui", "program_state",
      "program_ui", "tx_backend"].forEach(function (key) {
      if (typeof snapshot[key] !== "string") {
        throw new Error("authoritative " + key + " has an invalid type");
      }
    });
    ["tx_running", "shuffle", "repeat"].forEach(function (key) {
      if (typeof snapshot[key] !== "boolean") {
        throw new Error("authoritative " + key + " has an invalid type");
      }
    });
    ["queue_index", "queue_length", "frequency_mhz"].forEach(function (key) {
      if (typeof snapshot[key] !== "number" || !Number.isFinite(snapshot[key])) {
        throw new Error("authoritative " + key + " has an invalid type");
      }
    });
    ["current_track", "next_track", "now_playing", "up_next",
      "first_up"].forEach(function (key) {
      if (snapshot[key] !== null && !isPlainObject(snapshot[key])) {
        throw new Error("authoritative " + key + " has an invalid type");
      }
    });
    if (!Number.isInteger(snapshot.snapshot_revision) ||
        snapshot.snapshot_revision < 1) {
      throw new Error("authoritative snapshot revision is invalid");
    }
    var previousQueuePosition = -1;
    var currentRows = 0;
    snapshot.queue.forEach(function (track) {
      if (!isPlainObject(track) ||
          !Object.prototype.hasOwnProperty.call(track, "id") ||
          !Object.prototype.hasOwnProperty.call(track, "queue_pos") ||
          !Object.prototype.hasOwnProperty.call(track, "is_current") ||
          typeof track.id !== "string" || !track.id ||
          !Number.isInteger(track.queue_pos) ||
          track.queue_pos <= previousQueuePosition ||
          track.queue_pos < 0 ||
          track.queue_pos >= snapshot.queue_length ||
          typeof track.is_current !== "boolean" ||
          track.is_current !== (track.queue_pos === snapshot.queue_index)) {
        throw new Error("authoritative queue metadata is incomplete");
      }
      previousQueuePosition = track.queue_pos;
      if (track.is_current) currentRows += 1;
    });
    if (snapshot.queue_length < snapshot.queue.length ||
        !Number.isInteger(snapshot.queue_index) ||
        snapshot.queue_index < -1 ||
        snapshot.queue_index >= snapshot.queue_length ||
        currentRows > 1) {
      throw new Error("authoritative queue state is inconsistent");
    }
    if (!isPlainObject(snapshot.broadcast_recovery)) {
      throw new Error("authoritative recovery state is invalid");
    }
    if (typeof snapshot.broadcast_recovery.armed !== "boolean" ||
        typeof snapshot.broadcast_recovery.valid !== "boolean") {
      throw new Error("authoritative recovery flags are invalid");
    }
    ["broadcast", "network", "health", "hardware_environment",
      "hardware_profile_doc", "tx"].forEach(function (key) {
      if (!isPlainObject(snapshot[key])) {
        throw new Error("authoritative " + key + " is invalid");
      }
    });
    if (!Array.isArray(snapshot.broadcast.blockers) ||
        !Array.isArray(snapshot.broadcast.items) ||
        !Array.isArray(snapshot.hardware_environment.checks)) {
      throw new Error("authoritative readiness details are invalid");
    }
    return snapshot;
  }

  function createCoordinator(options) {
    var synchronized = false;
    var epoch = 0;
    var requestSequence = 0;
    var latestSnapshotRevision = null;

    function markUnavailable(reason) {
      epoch += 1;
      synchronized = false;
      latestSnapshotRevision = null;
      options.onUnavailable(reason || "Connection lost");
    }

    function applyIfNewer(snapshot) {
      if (latestSnapshotRevision !== null &&
          snapshot.snapshot_revision <= latestSnapshotRevision) {
        return false;
      }
      latestSnapshotRevision = snapshot.snapshot_revision;
      options.onSnapshot(snapshot);
      return true;
    }

    function reconcile() {
      var requestEpoch = epoch;
      var requestId = ++requestSequence;
      return Promise.resolve()
        .then(options.fetchSnapshot)
        .then(function (snapshot) {
          validateSnapshot(snapshot);
          if (requestEpoch !== epoch || requestId !== requestSequence) {
            return false;
          }
          if (!applyIfNewer(snapshot)) return false;
          synchronized = true;
          options.onSynchronized(snapshot);
          return true;
        })
        .catch(function (error) {
          if (requestEpoch === epoch && requestId === requestSequence) {
            markUnavailable(error && error.message);
          }
          return false;
        });
    }

    function acceptLiveSnapshot(snapshot) {
      if (!synchronized) return false;
      try {
        validateSnapshot(snapshot);
      } catch (error) {
        markUnavailable(error.message);
        return false;
      }
      if (latestSnapshotRevision !== null &&
          snapshot.snapshot_revision <= latestSnapshotRevision) {
        return false;
      }
      return applyIfNewer(snapshot);
    }

    return {
      acceptLiveSnapshot: acceptLiveSnapshot,
      isSynchronized: function () { return synchronized; },
      markUnavailable: markUnavailable,
      reconcile: reconcile,
      validateSnapshot: validateSnapshot
    };
  }

  return {
    createCoordinator: createCoordinator,
    validateSnapshot: validateSnapshot
  };
}));
