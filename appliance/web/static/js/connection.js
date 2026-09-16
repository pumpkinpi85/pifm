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
    "broadcast_state",
    "broadcast_recovery",
    "tx_running",
    "fault_reason",
    "program_state",
    "current_track",
    "next_track",
    "active_playlist",
    "queue",
    "shuffle",
    "repeat",
    "broadcast",
    "hardware_status"
  ];

  function validateSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object") {
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
    if (!snapshot.broadcast_recovery ||
        typeof snapshot.broadcast_recovery !== "object") {
      throw new Error("authoritative recovery state is invalid");
    }
    return snapshot;
  }

  function createCoordinator(options) {
    var synchronized = false;
    var epoch = 0;
    var requestSequence = 0;

    function markUnavailable(reason) {
      epoch += 1;
      synchronized = false;
      options.onUnavailable(reason || "Connection lost");
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
          options.onSnapshot(snapshot);
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
      options.onSnapshot(snapshot);
      return true;
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
