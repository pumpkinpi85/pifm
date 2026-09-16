(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.PifmFlagpole = api;
  }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var THRESHOLD = 0.5;

  function clampPosition(position) {
    var value = Number(position);
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(1, value));
  }

  function validateBand(band) {
    if (!band || !Number.isInteger(band.min_units) ||
        !Number.isInteger(band.max_units) ||
        !Number.isInteger(band.scale) ||
        band.min_units >= band.max_units || band.scale <= 0) {
      throw new Error("frequency band is invalid");
    }
    return band;
  }

  function positionToUnits(position, band) {
    var normalized = clampPosition(position);
    var validBand = validateBand(band);
    if (normalized < THRESHOLD) return null;
    var span = validBand.max_units - validBand.min_units;
    var index = Math.round(((normalized - THRESHOLD) / THRESHOLD) * span);
    return validBand.min_units + Math.max(0, Math.min(span, index));
  }

  function positionToFrequency(position, band) {
    var units = positionToUnits(position, band);
    return units === null ? null : units / validateBand(band).scale;
  }

  function frequencyToPosition(frequency, band) {
    var validBand = validateBand(band);
    var units = Math.round(Number(frequency) * validBand.scale);
    if (!Number.isFinite(Number(frequency)) ||
        units < validBand.min_units || units > validBand.max_units ||
        Math.abs(Number(frequency) * validBand.scale - units) > 1e-7) {
      throw new Error("frequency is outside the supported tuning grid");
    }
    return THRESHOLD + THRESHOLD * (
      (units - validBand.min_units) /
      (validBand.max_units - validBand.min_units)
    );
  }

  function targetForPosition(position, band) {
    var normalized = clampPosition(position);
    var frequency = positionToFrequency(normalized, band);
    return {
      position: normalized,
      desired_broadcast: frequency === null ? "off" : "on",
      frequency_mhz: frequency
    };
  }

  function isCommitBlocked(snapshot) {
    if (!snapshot) return true;
    var broadcastUi = String(snapshot.broadcast_ui || "");
    return snapshot.state === "FAULT" ||
      broadcastUi.indexOf("STATE UNKNOWN") === 0 ||
      broadcastUi.indexOf("POSSIBLE TRANSMISSION") >= 0 ||
      broadcastUi.indexOf("STARTING") === 0 ||
      broadcastUi.indexOf("STOPPING") === 0;
  }

  function shouldResolveTxPending(command, baselineRevision, snapshot) {
    if (command !== "txon" && command !== "txoff") return false;
    if (!snapshot || !Number.isInteger(snapshot.snapshot_revision) ||
        !Number.isInteger(baselineRevision) ||
        snapshot.snapshot_revision <= baselineRevision) {
      return false;
    }
    var broadcastUi = String(snapshot.broadcast_ui || "");
    var unknown = snapshot.state === "FAULT" ||
      broadcastUi.indexOf("STATE UNKNOWN") === 0 ||
      broadcastUi.indexOf("POSSIBLE TRANSMISSION") >= 0;
    if (command === "txon") {
      return broadcastUi === "ON AIR" || unknown;
    }
    return broadcastUi === "OFF" || unknown;
  }

  function pointerPosition(clientY, trackTop, trackHeight, handleHeight) {
    var height = Number(trackHeight);
    var handle = Number(handleHeight);
    if (!Number.isFinite(Number(clientY)) ||
        !Number.isFinite(Number(trackTop)) ||
        !Number.isFinite(height) ||
        !Number.isFinite(handle) ||
        height <= handle || handle < 0) {
      throw new Error("flagpole pointer geometry is invalid");
    }
    var trackBottom = Number(trackTop) + height;
    return clampPosition(
      (trackBottom - (handle / 2) - Number(clientY)) /
      (height - handle)
    );
  }

  function createGesture(options) {
    var active = false;
    var committed = false;
    var preview = null;

    function begin(position, band) {
      active = true;
      committed = false;
      preview = targetForPosition(position, band);
      options.onPreview(preview);
      return preview;
    }

    function move(position, band) {
      if (!active) return null;
      preview = targetForPosition(position, band);
      options.onPreview(preview);
      return preview;
    }

    function cancel(reason) {
      var wasActive = active;
      active = false;
      committed = false;
      preview = null;
      if (wasActive) options.onCancel(reason || "cancelled");
    }

    function release(position, band) {
      if (!active || committed) return null;
      preview = targetForPosition(position, band);
      active = false;
      committed = true;
      options.onPreview(preview);
      options.onCommit(preview);
      return preview;
    }

    return {
      begin: begin,
      cancel: cancel,
      isActive: function () { return active; },
      move: move,
      release: release
    };
  }

  return {
    THRESHOLD: THRESHOLD,
    clampPosition: clampPosition,
    createGesture: createGesture,
    frequencyToPosition: frequencyToPosition,
      isCommitBlocked: isCommitBlocked,
      shouldResolveTxPending: shouldResolveTxPending,
    pointerPosition: pointerPosition,
    positionToFrequency: positionToFrequency,
    positionToUnits: positionToUnits,
    targetForPosition: targetForPosition,
    validateBand: validateBand
  };
}));
