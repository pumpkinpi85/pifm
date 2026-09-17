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

  // Visual / mapping: lowest FM sits at 18% travel so OFF owns a clear bottom
  // pocket; OFF snaps to position 0.
  var OFF_DETENT_TRIGGER = 0.18;
  var TUNER_MIN_POSITION = 0.18;
  // Pointer hit thresholds. OFF_HIT_ENTER must not sit above TUNER_MIN_POSITION
  // or the lowest FM band would latch OFF on every grab/move. The raised floor
  // gives operators room to park on 87.1 without brushing the OFF edge.
  var OFF_HIT_ENTER = 0.18;
  var OFF_HIT_LEAVE = 0.18;

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
    if (normalized < OFF_DETENT_TRIGGER) return null;
    if (normalized <= TUNER_MIN_POSITION) return validBand.min_units;
    var span = validBand.max_units - validBand.min_units;
    var tunerPosition = (
      normalized - TUNER_MIN_POSITION
    ) / (1 - TUNER_MIN_POSITION);
    var index = Math.round(tunerPosition * span);
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
    return TUNER_MIN_POSITION + (1 - TUNER_MIN_POSITION) * (
      (units - validBand.min_units) /
      (validBand.max_units - validBand.min_units)
    );
  }

  function offTarget() {
    return {
      position: 0,
      desired_broadcast: "off",
      frequency_mhz: null
    };
  }

  function targetForPosition(position, band) {
    var normalized = clampPosition(position);
    var frequency = positionToFrequency(normalized, band);
    return {
      position: frequency === null
        ? 0
        : Math.max(TUNER_MIN_POSITION, normalized),
      desired_broadcast: frequency === null ? "off" : "on",
      frequency_mhz: frequency
    };
  }

  function targetForPointerPosition(position, band, latchedOff) {
    var normalized = clampPosition(position);
    var nextLatched = !!latchedOff;
    if (nextLatched) {
      if (normalized >= OFF_HIT_LEAVE) nextLatched = false;
    } else if (normalized < OFF_HIT_ENTER) {
      nextLatched = true;
    }
    var target = nextLatched
      ? offTarget()
      : targetForPosition(normalized, band);
    return { target: target, latchedOff: nextLatched };
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
    // Allow pointer travel below the visible track to keep mapping into OFF.
    // Values past the bottom clamp to 0; values above the top clamp to 1.
    return clampPosition(
      (trackBottom - (handle / 2) - Number(clientY)) /
      (height - handle)
    );
  }

  function createGesture(options) {
    var active = false;
    var committed = false;
    var preview = null;
    var latchedOff = false;

    function resolve(position, band) {
      var resolved = targetForPointerPosition(position, band, latchedOff);
      latchedOff = resolved.latchedOff;
      return resolved.target;
    }

    function begin(position, band) {
      active = true;
      committed = false;
      var initial = targetForPosition(position, band);
      latchedOff = initial.desired_broadcast === "off" ||
        clampPosition(position) < OFF_HIT_ENTER;
      preview = resolve(position, band);
      options.onPreview(preview);
      return preview;
    }

    function move(position, band) {
      if (!active) return null;
      preview = resolve(position, band);
      options.onPreview(preview);
      return preview;
    }

    function cancel(reason) {
      var wasActive = active;
      active = false;
      committed = false;
      preview = null;
      latchedOff = false;
      if (wasActive) options.onCancel(reason || "cancelled");
    }

    function release(position, band) {
      if (!active || committed) return null;
      preview = resolve(position, band);
      active = false;
      committed = true;
      latchedOff = false;
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
    OFF_DETENT_TRIGGER: OFF_DETENT_TRIGGER,
    TUNER_MIN_POSITION: TUNER_MIN_POSITION,
    OFF_HIT_ENTER: OFF_HIT_ENTER,
    OFF_HIT_LEAVE: OFF_HIT_LEAVE,
    clampPosition: clampPosition,
    createGesture: createGesture,
    frequencyToPosition: frequencyToPosition,
    isCommitBlocked: isCommitBlocked,
    shouldResolveTxPending: shouldResolveTxPending,
    pointerPosition: pointerPosition,
    positionToFrequency: positionToFrequency,
    positionToUnits: positionToUnits,
    targetForPosition: targetForPosition,
    targetForPointerPosition: targetForPointerPosition,
    validateBand: validateBand
  };
}));
