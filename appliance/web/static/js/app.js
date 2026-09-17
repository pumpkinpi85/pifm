(function () {
  "use strict";

  var state = null;
  var selectedPl = null;
  var toastTimer = null;
  var statusLoaded = false;
  var playlistsCache = null;
  var libraryLoadedOnce = false;
  var commandPending = null; // play|pause|next|prev|txon|txoff
  var txCommandPendingRevision = null;
  var sseConnected = false;
  var uiSynchronized = false;
  var uiAuthorityEpoch = 0;
  var activeAuthorityId = null;
  var activeEventSource = null;
  var reconnectTimer = null;
  var lastSseMessageAt = 0;
  var lastLogFingerprint = "";
  var mediaCapabilitiesLoaded = false;
  var setupStep = 0;
  var libraryTrackCount = 0;
  var draggedTrackId = null;
  var flagpoleGesture = null;
  var flagpolePointerId = null;
  var flagpoleGestureEpoch = null;
  var flagpoleBandKey = "";
  var flagpoleGrabOffsetY = 0;
  var hardwareFormDirty = false;

  var ENGINEERING_KINDS = {
    TX_PID: 1,
    STATE_RECONCILE: 1,
    TX_TERM: 1,
    TX_EXIT: 1,
    TX_KILL: 1,
    TX_DUPLICATE_DETECTED: 1,
    TX_START_IDEMPOTENT: 1,
    TX_START_REQUEST: 1,
    TX_STOP_REQUEST: 1,
    program_pending: 1,
    tx_audio_prefetch: 1,
    tx_audio: 1,
    boot: 1,
    controller_startup: 1,
    gpio: 1,
    TX_START_REJECTED: 1
  };

  function $(id) { return document.getElementById(id); }

  function toast(msg) {
    var el = $("toast");
    el.hidden = false;
    el.textContent = msg;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.hidden = true; }, 4500);
  }

  function api(path, opts) {
    opts = opts || {};
    var method = String(opts.method || "GET").toUpperCase();
    var requestAuthorityEpoch = uiAuthorityEpoch;
    if (method !== "GET" && !uiSynchronized) {
      return Promise.reject(new Error(
        "Live station state is unavailable. Wait for reconnection."
      ));
    }
    if (method !== "GET") {
      if (!activeAuthorityId) {
        return Promise.reject(new Error(
          "Authoritative controller identity is unavailable."
        ));
      }
      opts.headers = opts.headers || {};
      opts.headers["X-PiFM-Authority-ID"] = activeAuthorityId;
    }
    if (method === "GET" && !uiSynchronized && path !== "/api/status") {
      return new Promise(function () {});
    }
    return fetch(path, opts).then(function (r) {
      return r.text().then(function (text) {
        var j = {};
        try { j = text ? JSON.parse(text) : {}; } catch (e) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          throw new Error("The radio console returned an unexpected response.");
        }
        if (!r.ok) throw new Error((j && j.error) || ("Request failed (" + r.status + ")"));
        if (method === "GET" && requestAuthorityEpoch !== uiAuthorityEpoch) {
          return new Promise(function () {});
        }
        return j;
      });
    }).catch(function (error) {
      if (method === "GET" && path !== "/api/status" &&
          (requestAuthorityEpoch !== uiAuthorityEpoch || !uiSynchronized)) {
        return new Promise(function () {});
      }
      throw error;
    });
  }

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  function formatMediaList(labels) {
    if (labels.length < 2) return labels[0] || "";
    if (labels.length === 2) return labels[0] + " or " + labels[1];
    return labels.slice(0, -1).join(", ") + ", or " + labels[labels.length - 1];
  }

  function loadMediaCapabilities() {
    if (mediaCapabilitiesLoaded) return;
    api("/api/media/capabilities").then(function (capabilities) {
      var extensions = Array.isArray(capabilities.accepted_extensions)
        ? capabilities.accepted_extensions.filter(function (extension) {
          return /^\.[a-z0-9]+$/.test(String(extension));
        })
        : [];
      if (!extensions.length) return;
      mediaCapabilitiesLoaded = true;
      document.querySelectorAll("[data-media-picker]").forEach(function (picker) {
        picker.setAttribute("accept", extensions.join(","));
      });
      var display = formatMediaList(extensions.map(function (extension) {
        return extension.slice(1).toUpperCase();
      }));
      document.querySelectorAll("[data-media-formats]").forEach(function (node) {
        node.textContent = display;
      });
    }).catch(function () {
      // The truthful static fallback remains in place if capabilities are unavailable.
    });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderStateUnavailable(reason) {
    uiAuthorityEpoch += 1;
    uiSynchronized = false;
    statusLoaded = false;
    sseConnected = false;
    state = null;
    activeAuthorityId = null;
    commandPending = null;
    txCommandPendingRevision = null;
    window._queueTracks = [];
    document.body.classList.add("state-unavailable");
    var banner = $("connectionBanner");
    if (banner) {
      banner.classList.remove("synchronized");
      banner.textContent = reason === "Connecting"
        ? "CONNECTING — LIVE STATION STATE UNAVAILABLE"
        : "CONNECTION LOST — LIVE STATION STATE UNAVAILABLE";
    }
    $("broadcastState").textContent = "LIVE STATE UNAVAILABLE";
    $("broadcastState").className = "broadcast-state unknown";
    $("statusBadge").hidden = false;
    $("statusBadge").className = "status-badge attention";
    $("statusBadge").textContent = "CONNECTION LOST";
    $("nowLabel").textContent = "STATE UNAVAILABLE";
    $("nowLabel").className = "player-mode mode-busy";
    $("nowTrack").textContent = "Waiting for the Raspberry Pi";
    $("nowPlaylist").textContent = "Previous station state discarded";
    $("upNextLabel").textContent = "UP NEXT";
    $("nowNext").textContent = "—";
    $("netLamp").className = "lamp warn";
    $("netLamp").textContent = "LOST";
    $("txLamp").className = "lamp warn";
    $("txLamp").textContent = "?";
    $("activePlSummary").textContent = "Live state unavailable";
    $("queueBox").innerHTML =
      '<div class="empty">Live queue unavailable — reconnecting…</div>';
    $("recoverySummary").textContent = "Unavailable";
    $("recoveryDetail").textContent =
      "Reconnect to read persisted broadcast intent from the appliance.";
    $("faultRecoveryText").textContent =
      "Live fault and transmitter state unavailable.";
    $("hardwareSummary").textContent = "Live state unavailable";
    $("hardwareOutput").textContent = "";
    if ($("hardwareProfileSummary")) $("hardwareProfileSummary").textContent = "";
    $("hardwareChecks").innerHTML = "";
    hardwareFormDirty = false;
    $("devHarnessBanner").hidden = true;
    document.body.classList.remove("has-dev-harness");
    $("btnStopBroadcastHeader").hidden = true;
    $("blockerBox").hidden = true;
    $("blockerBox").innerHTML = "";
    $("faultLine").hidden = true;
    document.body.classList.remove("has-fault");
    $("freqBig").textContent = "—";
    $("netSummary").textContent = "Live state unavailable";
    $("netDetail").textContent = "Reconnect to read network state.";
    $("healthBox").innerHTML =
      '<div class="empty">Live health unavailable.</div>';
    $("applianceBox").textContent = "Live appliance identity unavailable.";
    $("operatorLog").innerHTML =
      '<div class="empty">Live Ship’s Log unavailable.</div>';
    $("operatorLog").dataset.hydrated = "";
    lastLogFingerprint = "";
    $("eventsBox").textContent = "Live diagnostics unavailable.";
    $("libList").innerHTML =
      '<div class="empty">Live music library unavailable.</div>';
    $("plList").innerHTML =
      '<div class="empty">Live playlists unavailable.</div>';
    $("plDetail").innerHTML = "";
    $("libraryPlaylistTargets").innerHTML =
      '<span class="meta">Live playlists unavailable.</span>';
    $("uploadProgress").innerHTML = "";
    $("setupUploadProgress").innerHTML = "";
    ["cfgFreq", "cfgPs", "cfgRt", "cfgPi", "setupFreq", "setupPs",
      "setupRt"].forEach(function (id) {
      if ($(id)) $(id).value = "";
    });
    ["cfgShuffle", "cfgRepeat"].forEach(function (id) {
      if ($(id)) $(id).checked = false;
    });
    ["hardwareMode", "hardwareProfile", "setupHardwareProfile"].forEach(
      function (id) {
        if ($(id)) $(id).selectedIndex = -1;
      }
    );
    $("setupHardwareName").textContent = "Live state unavailable";
    $("setupHardwareStatus").textContent = "Unavailable";
    $("setupHardwareOutput").textContent = "";
    $("setupHardwareChecks").innerHTML = "";
    $("setupWizard").hidden = true;
    renderFlagpoleUnavailable();
    selectedPl = null;
    playlistsCache = null;
    libraryLoadedOnce = false;
    mediaCapabilitiesLoaded = false;
    libraryTrackCount = 0;
    window._plTracks = [];
  }

  function markStateSynchronized() {
    var wasSynchronized = uiSynchronized;
    uiSynchronized = true;
    document.body.classList.remove("state-unavailable");
    var banner = $("connectionBanner");
    if (banner && (
      !wasSynchronized ||
      !banner.classList.contains("synchronized") ||
      banner.textContent !== "CONNECTED — AUTHORITATIVE STATE SYNCHRONIZED"
    )) {
      banner.classList.add("synchronized");
      banner.textContent = "CONNECTED — AUTHORITATIVE STATE SYNCHRONIZED";
    }
    if (!wasSynchronized &&
        $("view-music").classList.contains("active")) {
      loadPlaylists();
      loadLibrary();
    }
    if (!wasSynchronized) loadMediaCapabilities();
    if (!wasSynchronized && state) renderFlagpoleStatus(state);
  }

  function flagpoleHandleHeight() {
    return $("flagpoleHandle").getBoundingClientRect().height || 43;
  }

  function flagpolePositionStyle(element, position) {
    var p = window.PifmFlagpole.clampPosition(position);
    var handleHeight = flagpoleHandleHeight();
    element.style.bottom = "calc(" + (p * 100) + "% - " +
      (p * handleHeight) + "px)";
  }

  function flagpoleScalePositionStyle(element, position) {
    var p = window.PifmFlagpole.clampPosition(position);
    var handleHeight = flagpoleHandleHeight();
    element.style.bottom = "calc(" + (p * 100) + "% - " +
      (p * handleHeight) + "px + " + (handleHeight / 2) + "px)";
  }

  function flagpoleActiveRailStyle(position, visible) {
    var rail = $("flagpoleActiveRail");
    rail.hidden = !visible;
    if (visible) flagpoleScalePositionStyle(rail, position);
  }

  function setRaisedFlagVisible(visible) {
    $("raisedFlag").hidden = !visible;
  }

  function ensureFlagpoleTicks(band) {
    var key = [
      band.min_units, band.max_units, band.scale
    ].join(":");
    if (key === flagpoleBandKey) return;
    flagpoleBandKey = key;
    var box = $("flagpoleTicks");
    box.innerHTML = "";
    var labelClearance = Math.max(1, Math.ceil(band.scale * 0.4));
    for (var units = band.min_units; units <= band.max_units; units += 1) {
      var endpoint = units === band.min_units || units === band.max_units;
      var labeledMajor = units % 20 === 0 &&
        units - band.min_units >= labelClearance &&
        band.max_units - units >= labelClearance;
      if (!endpoint && units % 10 !== 0) continue;
      var tick = document.createElement("div");
      var position = window.PifmFlagpole.frequencyToPosition(
        units / band.scale, band
      );
      tick.className = "flagpole-tick" + (
        endpoint || labeledMajor ? " major" : ""
      );
      flagpoleScalePositionStyle(tick, position);
      if (endpoint || labeledMajor) {
        var label = document.createElement("span");
        label.textContent = (units / band.scale).toFixed(1);
        tick.appendChild(label);
      }
      box.appendChild(tick);
    }
  }

  function renderFlagpolePreview(target) {
    var handle = $("flagpoleHandle");
    handle.hidden = false;
    handle.disabled = false;
    handle.className = "flagpole-handle preview";
    handle._previewPosition = target.position;
    flagpolePositionStyle(handle, target.position);
    if (target.desired_broadcast === "off") {
      flagpoleActiveRailStyle(0, false);
      $("flagpoleReadout").textContent = "OFF AIR";
      $("flagpoleFeedback").textContent =
        "OFF AIR DETENT · RELEASE TO STOP BROADCAST";
      handle.setAttribute("aria-valuenow", "0");
      handle.setAttribute("aria-valuetext", "Preview OFF AIR");
      return;
    }
    var frequency = Number(target.frequency_mhz).toFixed(1);
    flagpoleActiveRailStyle(target.position, true);
    $("freqBig").textContent = frequency;
    $("flagpoleReadout").textContent = "PREVIEW " + frequency + " FM";
    $("flagpoleFeedback").textContent =
      target.position === window.PifmFlagpole.TUNER_MIN_POSITION
        ? "LOWEST FM · RELEASE TO BROADCAST"
        : "TUNING · RELEASE TO COMMIT";
    var band = state.frequency_band;
    var units = Math.round(Number(target.frequency_mhz) * band.scale);
    handle.setAttribute(
      "aria-valuenow", String(units - band.min_units + 1)
    );
    handle.setAttribute(
      "aria-valuetext", "Preview " + frequency + " FM"
    );
  }

  function renderFlagpoleUnavailable() {
    if (flagpoleGesture && flagpoleGesture.isActive()) {
      flagpoleGesture.cancel("authority unavailable");
    }
    var handle = $("flagpoleHandle");
    if (!handle) return;
    handle.hidden = true;
    handle.disabled = true;
    handle._previewPosition = null;
    $("flagpolePreset").hidden = true;
    $("flagpolePreset").disabled = true;
    flagpoleActiveRailStyle(0, false);
    setRaisedFlagVisible(false);
    $("flagpoleUnknown").hidden = false;
    $("flagpoleUnknown").textContent = "LIVE POSITION UNAVAILABLE";
    $("flagpoleReadout").textContent = "SET —";
    $("flagpoleFeedback").textContent = "RECONNECTING · CONTROL LOCKED";
  }

  function flagpoleSnapshotBlocksCommit(snapshot) {
    return !window.PifmFlagpole ||
      window.PifmFlagpole.isCommitBlocked(snapshot);
  }

  function flagpoleTxCommandPending() {
    return commandPending === "txon" || commandPending === "txoff";
  }

  function renderFlagpoleStatus(snapshot) {
    if (!window.PifmFlagpole || !snapshot || !snapshot.frequency_band) return;
    var blocked = flagpoleSnapshotBlocksCommit(snapshot);
    if (flagpoleGesture && flagpoleGesture.isActive()) {
      if (blocked) {
        flagpoleGesture.cancel("authoritative state changed");
      } else {
        renderFlagpolePreview(window.PifmFlagpole.targetForPosition(
          $("flagpoleHandle")._previewPosition,
          snapshot.frequency_band
        ));
        return;
      }
    }
    var band = snapshot.frequency_band;
    ensureFlagpoleTicks(band);
    var frequency = Number(snapshot.frequency_mhz);
    var presetPosition;
    try {
      presetPosition = window.PifmFlagpole.frequencyToPosition(
        frequency, band
      );
    } catch (error) {
      $("flagpoleHandle").hidden = true;
      $("flagpoleHandle").disabled = true;
      $("flagpolePreset").hidden = true;
      $("flagpolePreset").disabled = true;
      flagpoleActiveRailStyle(0, false);
      setRaisedFlagVisible(false);
      $("flagpoleUnknown").hidden = false;
      $("flagpoleUnknown").textContent = "FREQUENCY NEEDS CORRECTION";
      $("flagpoleReadout").textContent = "SET INVALID";
      $("flagpoleFeedback").textContent = "OPEN STATION · CHOOSE 0.1 MHz STEP";
      return;
    }
    var preset = $("flagpolePreset");
    $("freqBig").textContent = frequency.toFixed(1);
    preset.hidden = false;
    flagpoleScalePositionStyle(preset, presetPosition);
    preset.querySelector("span").textContent = frequency.toFixed(1);
    $("flagpoleReadout").textContent =
      "SET " + frequency.toFixed(1) + " FM";

    var handle = $("flagpoleHandle");
    var unknownBox = $("flagpoleUnknown");
    var broadcastUi = snapshot.broadcast_ui || "OFF";
    var unknown = snapshot.state === "FAULT" ||
      broadcastUi.indexOf("STATE UNKNOWN") === 0 ||
      broadcastUi.indexOf("POSSIBLE TRANSMISSION") >= 0;
    handle._previewPosition = null;
    if (unknown) {
      handle.hidden = true;
      handle.disabled = true;
      preset.hidden = true;
      preset.disabled = true;
      flagpoleActiveRailStyle(0, false);
      setRaisedFlagVisible(false);
      unknownBox.hidden = false;
      unknownBox.textContent = snapshot.state === "FAULT"
        ? "FAULT · POSITION UNKNOWN"
        : "POSITION UNKNOWN";
      $("flagpoleFeedback").textContent =
        "USE STOP BROADCAST · CHECK DIAGNOSTICS";
      return;
    }

    unknownBox.hidden = true;
    handle.hidden = false;
    handle.disabled = !uiSynchronized || blocked ||
      flagpoleTxCommandPending();
    handle.setAttribute(
      "aria-valuemax",
      String(band.max_units - band.min_units + 1)
    );
    var onAir = broadcastUi === "ON AIR";
    var starting = broadcastUi === "STARTING BROADCAST…" ||
      broadcastUi === "STARTING";
    var stopping = broadcastUi === "STOPPING BROADCAST…";
    setRaisedFlagVisible(onAir || starting || stopping);
    var canStartFromPreset = !onAir && !starting && !stopping &&
      uiSynchronized && !blocked && !flagpoleTxCommandPending() &&
      (!snapshot.broadcast || snapshot.broadcast.ready !== false);
    preset.disabled = !canStartFromPreset;
    preset.setAttribute(
      "aria-label",
      canStartFromPreset
        ? "Start broadcasting at " + frequency.toFixed(1) + " FM"
        : "Selected frequency " + frequency.toFixed(1) + " FM"
    );
    var position = onAir || starting ? presetPosition : 0;
    flagpolePositionStyle(handle, position);
    flagpoleActiveRailStyle(position, onAir || starting);
    if (onAir) {
      handle.className = "flagpole-handle on-air";
      $("flagpoleFeedback").textContent =
        frequency.toFixed(1) + " FM · ON AIR";
    } else if (starting) {
      handle.className = "flagpole-handle pending";
      $("flagpoleFeedback").textContent =
        frequency.toFixed(1) + " FM · STARTING";
    } else if (stopping) {
      handle.className = "flagpole-handle pending lowering";
      $("flagpoleFeedback").textContent = "LOWERING · STOPPING BROADCAST";
    } else {
      handle.className = "flagpole-handle off-air";
      $("flagpoleFeedback").textContent =
        "OFF AIR · SET " + frequency.toFixed(1) + " FM";
    }
    var valueNow = onAir || starting
      ? Math.round(frequency * band.scale) - band.min_units + 1
      : 0;
    handle.setAttribute("aria-valuenow", String(valueNow));
    handle.setAttribute(
      "aria-valuetext",
      onAir
        ? frequency.toFixed(1) + " FM, ON AIR"
        : (starting
          ? frequency.toFixed(1) + " FM, starting"
          : "OFF AIR, set frequency " + frequency.toFixed(1) + " FM")
    );
  }

  function humanPlaylistName(name, id) {
    var raw = (name || id || "").trim();
    if (!raw) return "Untitled playlist";
    if (/^[a-z0-9_-]+$/i.test(raw) && raw === (id || raw)) {
      return raw.replace(/[_-]+/g, " ").replace(/\b\w/g, function (c) {
        return c.toUpperCase();
      });
    }
    return raw;
  }

  function trackTitle(t) {
    if (!t) return null;
    if (typeof t === "string") return t;
    return t.title || t.name || t.filename || t.path || "Unknown track";
  }

  function trackArtist(t) {
    if (!t || typeof t === "string") return "";
    var art = t.artist || "";
    return art && art !== "Unknown" ? art : "";
  }

  function trackLabel(t) {
    var title = trackTitle(t);
    if (!title) return "—";
    var art = trackArtist(t);
    return art ? (art + " — " + title) : title;
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-tab") === name);
    });
    document.querySelectorAll(".view").forEach(function (v) {
      v.classList.toggle("active", v.id === "view-" + name);
    });
    if (name === "music") {
      loadPlaylists();
      loadLibrary();
    }
    if (name === "system") loadSystem();
    if (name === "broadcast") loadOperatorLog();
  }

  function showMusicPane(name) {
    document.querySelectorAll(".music-tab").forEach(function (button) {
      button.classList.toggle(
        "active", button.getAttribute("data-music-tab") === name
      );
    });
    document.querySelectorAll(".music-pane").forEach(function (pane) {
      pane.classList.toggle(
        "active", pane.getAttribute("data-music-pane") === name
      );
    });
    if (name === "library") {
      loadLibraryPlaylistTargets();
      loadLibrary();
    } else if (name === "playlists") {
      loadPlaylists();
    } else if (name === "queue" && state) {
      renderQueue(state);
    }
  }

  document.querySelectorAll(".music-tab").forEach(function (button) {
    button.addEventListener("click", function () {
      showMusicPane(button.getAttribute("data-music-tab"));
    });
  });

  document.querySelectorAll(".tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      showTab(btn.getAttribute("data-tab"));
    });
  });

  function normalizeBlockers(bc) {
    var raw = (bc && bc.blockers) || [];
    return raw.map(function (b) {
      if (typeof b === "string") {
        return { id: "", message: b, cta: "music", cta_label: "Choose tracks" };
      }
      return {
        id: b.id || "",
        message: b.message || b.detail || "Something needs attention.",
        cta: b.cta || "",
        cta_label: b.cta_label || "Fix this"
      };
    });
  }

  function renderBlocker(bc, onAir, unknown) {
    var box = $("blockerBox");
    if (onAir || unknown || !bc || bc.ready || !(bc.blockers && bc.blockers.length)) {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    var blockers = normalizeBlockers(bc);
    var first = blockers[0];
    if (!first) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    var cta = first.cta
      ? '<button type="button" class="cta" data-goto="' + esc(first.cta) + '">' +
        esc(first.cta_label || "Fix this") + "</button>"
      : "";
    box.innerHTML =
      '<div class="blocker-title">CAN\'T START BROADCASTING</div>' +
      "<p>" + esc(first.message) + "</p>" + cta;
  }

  function hardwareLabel(s) {
    var detected = (s && s.detected_hardware) || {};
    if (detected.display_name) return detected.display_name;
    var hints = (s && s.board_hints) || {};
    return hints.model || "Hardware not detected";
  }

  function hardwareSupportStatus(s) {
    var detected = (s && s.detected_hardware) || {};
    if (detected.status) return String(detected.status).toUpperCase();
    return "UNKNOWN";
  }

  function hardwareOutput(s) {
    var detected = (s && s.detected_hardware) || {};
    if (detected.rf_gpio_bcm != null && detected.rf_header_pin != null) {
      return "FM output: GPIO " + detected.rf_gpio_bcm +
        " / physical pin " + detected.rf_header_pin;
    }
    return "";
  }

  function profileDisplayName(s) {
    var doc = (s && s.hardware_profile_doc) || {};
    if (doc.display_name) return doc.display_name;
    return (s && s.hardware_profile) || "—";
  }

  function syncHardwareProfileOptions(s) {
    var select = $("hardwareProfile");
    if (!select) return;
    var profiles = s.available_hardware_profiles || [];
    if (!profiles.length) return;
    var current = select.value;
    var html = profiles.map(function (profile) {
      return '<option value="' + esc(profile.id) + '">' +
        esc(profile.display_name || profile.id) + "</option>";
    }).join("");
    if (select.innerHTML !== html) select.innerHTML = html;
    if (current) select.value = current;
  }

  function applyHardwareFormMode(mode) {
    var profile = $("hardwareProfile");
    var hint = $("hardwareProfileHint");
    var isManual = mode === "manual";
    if (profile) profile.disabled = !isManual;
    if (hint) {
      hint.textContent = isManual
        ? "Manual profile is an operating choice. It does not change detected hardware identity."
        : "In automatic mode, piFM selects the profile that matches the detected board.";
    }
  }

  function renderHardware(s) {
    var detected = s.detected_hardware || {};
    var status = hardwareSupportStatus(s);
    var environment = s.hardware_environment || {};
    var checks = environment.checks || [];
    var mode = String(s.hardware_profile_mode || (
      s.hardware_profile_source === "manual" ? "manual" : "auto"
    )).toLowerCase();
    if (mode !== "manual") mode = "auto";
    if ($("hardwareSummary")) {
      $("hardwareSummary").textContent = hardwareLabel(s) + " · " + status;
    }
    if ($("hardwareOutput")) $("hardwareOutput").textContent = hardwareOutput(s);
    if ($("hardwareProfileSummary")) {
      $("hardwareProfileSummary").textContent =
        "Hardware profile: " + profileDisplayName(s) +
        (mode === "manual" ? " (Manual)" : " (Automatic)");
    }
    if ($("hardwareChecks")) {
      $("hardwareChecks").innerHTML = checks.length ? checks.map(function (check) {
        return '<div class="check-row"><span class="' +
          (check.ok ? "ok" : "needs-action") + '">' +
          (check.ok ? "✓ " : "Needs attention · ") + esc(check.label) +
          "</span>" + (check.ok ? "" : "<div class=\"meta\">" +
          esc(check.action || "") + "</div>") + "</div>";
      }).join("") : '<div class="meta">No profile-specific checks available.</div>';
    }
    syncHardwareProfileOptions(s);
    if (!hardwareFormDirty) {
      if ($("hardwareMode")) $("hardwareMode").value = mode;
      if ($("hardwareProfile") && s.hardware_profile) {
        $("hardwareProfile").value = s.hardware_profile;
      } else if ($("hardwareProfile") && s.suggested_hardware_profile) {
        $("hardwareProfile").value = s.suggested_hardware_profile;
      }
    }
    applyHardwareFormMode($("hardwareMode") ? $("hardwareMode").value : mode);
  }

  function renderRecovery(s) {
    var summary = $("recoverySummary");
    var detail = $("recoveryDetail");
    if (!summary || !detail) return;
    var recovery = s.broadcast_recovery || {};
    if (!recovery.valid) {
      summary.textContent = "Blocked";
      detail.textContent = recovery.invalid_reason ||
        "Saved recovery state is invalid. Broadcast will remain off.";
    } else if (recovery.armed) {
      summary.textContent = "Armed";
      detail.textContent = recovery.last_restore_reason
        ? "Last recovery: " + recovery.last_restore_reason
        : "After power returns, piFM will validate safety and resume this station.";
    } else {
      summary.textContent = "Off";
      detail.textContent =
        "After power returns, piFM will remain OFF AIR until you move the brass handle out of its OFF AIR detent.";
    }
  }

  function showSetupStep(nextStep) {
    setupStep = Math.max(0, Math.min(4, nextStep));
    var names = ["WELCOME", "HARDWARE", "STATION", "MUSIC", "BROADCAST"];
    document.querySelectorAll(".setup-step").forEach(function (step) {
      step.classList.toggle(
        "active", Number(step.getAttribute("data-setup-step")) === setupStep
      );
    });
    if ($("setupProgress")) {
      $("setupProgress").textContent =
        names[setupStep] + " · " + (setupStep + 1) + " OF 5";
    }
  }

  function renderSetup(s) {
    var wizard = $("setupWizard");
    if (!wizard) return;
    wizard.hidden = !s.setup_required;
    if (!s.setup_required) return;
    $("setupHardwareName").textContent = hardwareLabel(s) + " detected";
    var hwStatus = hardwareSupportStatus(s);
    $("setupHardwareStatus").textContent =
      hwStatus === "SUPPORTED" ? "Supported for piFM" : hwStatus;
    $("setupHardwareStatus").className =
      "hardware-status " + hwStatus.toLowerCase();
    $("setupHardwareOutput").textContent = hardwareOutput(s);
    var environment = s.hardware_environment || {};
    $("setupHardwareChecks").innerHTML = (environment.checks || []).map(
      function (check) {
        return '<div class="check-row"><span class="' +
          (check.ok ? "ok" : "needs-action") + '">' +
          (check.ok ? "✓ " : "Needs attention · ") + esc(check.label) +
          "</span>" + (check.ok ? "" : "<div class=\"meta\">" +
          esc(check.action || "") + "</div>") + "</div>";
      }
    ).join("");
    if (document.activeElement !== $("setupFreq")) {
      $("setupFreq").value = s.frequency_mhz != null ? s.frequency_mhz : "";
    }
    if (document.activeElement !== $("setupPs")) $("setupPs").value = s.rds_ps || "";
    if (document.activeElement !== $("setupRt")) $("setupRt").value = s.rds_rt || "";
    showSetupStep(setupStep);
  }

  function renderStatus(s) {
    state = s;
    statusLoaded = true;
    var st = s.state || "SAFE_OFF";
    var broadcastUi = s.broadcast_ui || "OFF";
    var onAir = broadcastUi === "ON AIR";
    var starting = broadcastUi === "STARTING BROADCAST…" || broadcastUi === "STARTING";
    var stopping = broadcastUi === "STOPPING BROADCAST…";
    var faulted = st === "FAULT";
    var possible = broadcastUi.indexOf("POSSIBLE TRANSMISSION") >= 0;
    var unknown = broadcastUi.indexOf("STATE UNKNOWN") === 0 || faulted || possible;
    var bc = s.broadcast || {};
    var freq = s.frequency_mhz;
    var program = s.program_state || "stopped";
    var programUi = s.program_ui || "";
    var harness = !!s.dev_harness || (s.tx_backend || "") === "mock";

    // Clear local commandPending once backend confirms the matching state.
    if (window.PifmFlagpole.shouldResolveTxPending(
      commandPending, txCommandPendingRevision, s
    )) {
      commandPending = null;
      txCommandPendingRevision = null;
    }
    if (commandPending === "play" && (program === "playing" || programUi === "PLAYING")) commandPending = null;
    if (commandPending === "pause" && (program === "paused" || programUi === "PAUSED")) commandPending = null;
    if ((commandPending === "next" || commandPending === "prev") && (programUi === "PLAYING" || programUi === "READY" || programUi === "PAUSED")) {
      if (programUi !== "CHANGING TRACK…" && programUi !== "STARTING MUSIC…" && programUi !== "RESUMING…") {
        commandPending = null;
      }
    }
    if (s.program_pending == null && (commandPending === "play" || commandPending === "pause" || commandPending === "next" || commandPending === "prev")) {
      if (programUi === "PLAYING" || programUi === "PAUSED" || programUi === "READY") commandPending = null;
    }

    var banner = $("devHarnessBanner");
    if (banner) {
      banner.hidden = !harness;
      document.body.classList.toggle("has-dev-harness", harness);
    }

    var stateEl = $("broadcastState");
    var statusBadge = $("statusBadge");
    if (unknown) {
      stateEl.textContent = possible
        ? "STATE UNKNOWN / POSSIBLE TRANSMISSION"
        : "STATE UNKNOWN / NEEDS ATTENTION";
      stateEl.className = "broadcast-state unknown";
      if (statusBadge) {
        statusBadge.hidden = false;
        statusBadge.className = "status-badge attention";
        statusBadge.textContent = "NEEDS ATTENTION";
      }
    } else if (onAir) {
      stateEl.textContent = "ON AIR";
      stateEl.className = "broadcast-state on";
      if (statusBadge) {
        statusBadge.hidden = false;
        statusBadge.className = "status-badge onair";
        statusBadge.textContent = program === "paused" ? "ON AIR · PAUSED" : "ON AIR";
      }
    } else if (stopping) {
      stateEl.textContent = "STOPPING BROADCAST…";
      stateEl.className = "broadcast-state starting";
      if (statusBadge) {
        statusBadge.hidden = false;
        statusBadge.className = "status-badge starting";
        statusBadge.textContent = "STOPPING";
      }
    } else if (starting) {
      stateEl.textContent = "STARTING BROADCAST…";
      stateEl.className = "broadcast-state starting";
      if (statusBadge) {
        statusBadge.hidden = false;
        statusBadge.className = "status-badge starting";
        statusBadge.textContent = "STARTING";
      }
    } else {
      stateEl.textContent = "OFF AIR";
      stateEl.className = "broadcast-state off";
      if (statusBadge) {
        statusBadge.hidden = !bc.ready;
        statusBadge.className = "status-badge ready";
        statusBadge.textContent = "READY";
      }
    }

    $("freqBig").textContent = freq != null ? Number(freq).toFixed(1) : "—";

    var readyHint = $("readyHint");
    if (readyHint) readyHint.hidden = true;

    renderBlocker(bc, onAir, unknown);

    var transmitting = onAir || starting || stopping || unknown;
    var stopHead = $("btnStopBroadcastHeader");
    if (stopHead) {
      stopHead.disabled = false;
      stopHead.hidden = !(onAir || unknown || faulted || starting || stopping);
      stopHead.textContent = stopping ? "STOPPING…" : "STOP BROADCAST";
      stopHead.className = "mast-stop" + (onAir || unknown || faulted || starting || stopping ? " urgent" : "");
    }

    var plName = humanPlaylistName(
      s.selected_playlist_name || bc.playlist_name || s.active_playlist,
      s.active_playlist
    );
    var nowLabel = $("nowLabel");
    var upLabel = $("upNextLabel");
    var player = document.querySelector(".player");
    if (player) player.classList.toggle("is-paused", program === "paused");
    var playPause = $("btnPlayPause");
    function setPlayPause(mode) {
      if (!playPause) return;
      playPause.classList.remove("is-busy");
      if (mode === "pause") {
        playPause.className = "transport-play is-pause";
        playPause.innerHTML = "<span class=\"glyph play-glyph\" aria-hidden=\"true\">❚❚</span><span class=\"play-label\">PAUSE</span>";
        playPause.setAttribute("aria-label", "Pause music program");
        playPause.title = "Pause music — FM stays on air";
        playPause.disabled = false;
      } else if (mode === "busy") {
        var busyLabel = arguments[1] || "STARTING…";
        playPause.className = "transport-play is-busy";
        playPause.innerHTML = "<span class=\"glyph play-glyph spin\" aria-hidden=\"true\">⟳</span><span class=\"play-label\">" + busyLabel + "</span>";
        playPause.setAttribute("aria-label", busyLabel);
        playPause.title = "Working…";
        playPause.disabled = true;
      } else {
        playPause.className = "transport-play";
        var label = mode === "resume" ? "RESUME" : "PLAY";
        playPause.innerHTML = "<span class=\"glyph play-glyph\" aria-hidden=\"true\">▶</span><span class=\"play-label\">" + label + "</span>";
        playPause.setAttribute("aria-label", mode === "resume" ? "Resume music program" : "Play music program");
        playPause.title = mode === "resume"
          ? "Resume music — still on air if broadcasting"
          : "Play music for preview — does not start FM by itself";
        playPause.disabled = false;
      }
    }
    function followTrack(selected) {
      var q = window._queueTracks || [];
      if (selected && q.length > 1) {
        var sid = selected.id || selected;
        for (var i = 0; i < q.length; i++) {
          var tid = typeof q[i] === "string" ? q[i] : (q[i] && q[i].id);
          if (tid === sid && i + 1 < q.length) return q[i + 1];
        }
        if (s.repeat && q.length) return q[0];
      }
      if (s.next_track && selected && (s.next_track.id !== (selected.id || selected))) {
        return s.next_track;
      }
      return null;
    }
    var busyProgram = programUi === "STARTING MUSIC…" || programUi === "RESUMING…" ||
      programUi === "PAUSING…" || programUi === "CHANGING TRACK…" ||
      commandPending === "play" || commandPending === "pause" ||
      commandPending === "next" || commandPending === "prev";
    if (busyProgram) {
      var busyText = programUi || (
        commandPending === "pause" ? "PAUSING…" :
        commandPending === "play" ? (program === "paused" ? "RESUMING…" : "STARTING MUSIC…") :
        "CHANGING TRACK…"
      );
      nowLabel.textContent = busyText.replace(/…$/, "");
      nowLabel.className = "player-mode mode-busy";
      var curBusy = s.now_playing || s.current_track || s.first_up;
      $("nowTrack").textContent = curBusy ? trackTitle(curBusy) : "Working…";
      $("nowPlaylist").textContent = plName;
      upLabel.textContent = "UP NEXT";
      $("nowNext").textContent = (s.up_next || s.next_track) ? trackLabel(s.up_next || s.next_track) : "—";
      setPlayPause("busy", busyText.indexOf("PAUS") === 0 ? "PAUSING…" : (busyText.indexOf("RESUM") === 0 ? "RESUMING…" : "STARTING…"));
    } else if (program === "playing" && s.now_playing) {
      nowLabel.textContent = "NOW PLAYING";
      nowLabel.className = "player-mode mode-playing";
      $("nowTrack").textContent = trackTitle(s.now_playing);
      $("nowPlaylist").textContent = plName + (trackArtist(s.now_playing) ? (" · " + trackArtist(s.now_playing)) : "");
      upLabel.textContent = "UP NEXT";
      $("nowNext").textContent = s.up_next ? trackLabel(s.up_next) : "—";
      setPlayPause("pause");
    } else if (program === "paused" && (s.now_playing || s.current_track)) {
      nowLabel.textContent = "PAUSED";
      nowLabel.className = "player-mode mode-paused";
      var pausedTrack = s.now_playing || s.current_track;
      $("nowTrack").textContent = trackTitle(pausedTrack);
      $("nowPlaylist").textContent = plName;
      upLabel.textContent = "UP NEXT";
      $("nowNext").textContent = (s.up_next || s.next_track) ? trackLabel(s.up_next || s.next_track) : "—";
      setPlayPause("resume");
    } else {
      nowLabel.textContent = "READY";
      nowLabel.className = "player-mode mode-ready";
      var selected = s.first_up || s.current_track;
      $("nowTrack").textContent = selected ? trackTitle(selected) : "Ready to broadcast";
      if (s.active_playlist) {
        $("nowPlaylist").textContent = "Playlist · " + plName;
      } else {
        $("nowPlaylist").textContent = "Choose a playlist to get started.";
      }
      upLabel.textContent = "THEN";
      var follow = followTrack(selected);
      if (follow) {
        $("nowNext").textContent = trackLabel(follow);
      } else if (selected && (s.queue_length || 0) <= 1) {
        $("nowNext").textContent = "End of playlist";
      } else {
        $("nowNext").textContent = "—";
      }
      setPlayPause("play");
      if (playPause) playPause.disabled = !(s.active_playlist && (s.queue_length || 0) > 0);
    }
    // Side transport: usable when a playlist exists
    var sides = document.querySelectorAll(".player-transport .transport-side");
    for (var si = 0; si < sides.length; si++) {
      sides[si].disabled = !(s.active_playlist && (s.queue_length || 0) > 0);
    }

    if ($("activePlSummary")) {
      if (s.active_playlist) {
        $("activePlSummary").textContent =
          plName +
          (bc.track_count != null ? (" · " + bc.track_count + " track" + (bc.track_count === 1 ? "" : "s")) : "");
      } else {
        $("activePlSummary").textContent = "No playlist selected";
      }
    }

    var net = s.network || {};
    var quiet = !!net.rf_quiet_active;
    var netLabel = quiet
      ? (net.network_state === "SIMULATED_RF_QUIET" ? "Network quiet (test)" : "Network quiet")
      : (net.ip ? "Connected" : "Disconnected");
    if ($("netSummary")) $("netSummary").textContent = netLabel;
    if ($("netDetail")) {
      $("netDetail").textContent =
        (net.ip ? ("IP " + net.ip) : "No IP address") +
        (net.quiet_remaining_s != null ? " · restores in ~" + net.quiet_remaining_s + "s" : "");
    }

    $("netLamp").className = "lamp" + (quiet ? " warn" : (net.ip ? " on" : ""));
    $("netLamp").textContent = quiet ? "QUIET" : "NET";
    $("txLamp").className = "lamp" + (onAir ? " hot" : (faulted || unknown ? " warn" : ""));
    $("txLamp").textContent = onAir ? "AIR" : (faulted || unknown ? "!!!" : "OFF");

    renderHealth(s);
    renderAppliance(s);
    renderFaultRecovery(s);
    renderHardware(s);
    renderRecovery(s);
    renderSetup(s);

    if (!(document.activeElement && document.activeElement.id && document.activeElement.id.indexOf("cfg") === 0)) {
      if ($("cfgFreq")) $("cfgFreq").value = freq != null ? freq : "";
      if ($("cfgPs")) $("cfgPs").value = s.rds_ps || "";
      if ($("cfgRt")) $("cfgRt").value = s.rds_rt || "";
      if ($("cfgPi")) $("cfgPi").value = s.rds_pi || "";
      if ($("cfgShuffle")) $("cfgShuffle").checked = !!s.shuffle;
      if ($("cfgRepeat")) $("cfgRepeat").checked = !!s.repeat;
    }

    var fault = $("faultLine");
    if (fault) {
      if (s.fault_reason || unknown) {
        fault.hidden = false;
        document.body.classList.add("has-fault");
        fault.textContent = s.fault_reason
          ? ("Needs attention: " + s.fault_reason + " — use STOP BROADCAST.")
          : "State unknown — use STOP BROADCAST, then check System.";
      } else {
        fault.hidden = true;
        document.body.classList.remove("has-fault");
      }
    }

    renderQueue(s);
    renderFlagpoleStatus(s);
  }

  function renderHealth(s) {
    var box = $("healthBox");
    if (!box) return;
    var h = s.health || {};
    var mem = h.mem || {};
    var avail = mem.available_kb != null ? Math.round(mem.available_kb / 1024) : null;
    var temp = h.temp_c;
    var tempAlert = temp != null && Number(temp) >= 75;
    var load = h.loadavg;
    var loadStr = Array.isArray(load) ? load.map(function (n) { return Number(n).toFixed(2); }).join(" / ") : "—";
    var up = s.uptime_s != null ? Math.floor(s.uptime_s) + "s" : "—";
    box.innerHTML =
      '<div><div class="hk">Temperature</div><div class="hv' + (tempAlert ? " alert" : "") + '">' +
      (temp != null ? temp + " °C" : "—") + (tempAlert ? " — warm" : "") + "</div></div>" +
      '<div><div class="hk">CPU load</div><div class="hv">' + esc(loadStr) + "</div></div>" +
      '<div><div class="hk">RAM free</div><div class="hv">' + (avail != null ? avail + " MiB" : "—") + "</div></div>" +
      '<div><div class="hk">Uptime</div><div class="hv">' + esc(up) + "</div></div>";
  }

  function renderAppliance(s) {
    var box = $("applianceBox");
    if (!box) return;
    box.textContent = [
      "Version: " + (s.software_version || "—"),
      "Build: " + (s.build_label || s.git_sha || "—"),
      "Detected: " + ((s.detected_hardware && s.detected_hardware.display_name) || "—"),
      "Hardware profile: " + (s.hardware_profile || "—") + ((s.hardware_profile_mode === "manual") ? " (Manual)" : " (Automatic)"),
      "PiFmRds timing: " + (s.pi_fm_rds_ppm != null ? s.pi_fm_rds_ppm + " ppm" : "—"),
      "Transmitter: " + (s.dev_harness ? "test harness (no FM)" : "FM transmitter"),
      "GPIO: " + (s.gpio_enabled ? "enabled" : "disabled"),
      "Product: " + (s.product_name || "piFM Pirate Radio")
    ].join("\n");
  }

  function renderFaultRecovery(s) {
    var text = $("faultRecoveryText");
    var btn = $("btnClearFault");
    if (!text || !btn) return;
    if (s.state === "FAULT" || s.fault_reason) {
      text.textContent = "Fault: " + (s.fault_reason || "needs attention") + ". Stop Broadcast first if needed, then clear.";
      btn.disabled = false;
    } else {
      text.textContent = "No fault — station is ready.";
      btn.disabled = true;
    }
  }

  function renderQueue(s) {
    var box = $("queueBox");
    if (!box) return;
    var q = s.queue || [];
    window._queueTracks = q;
    if (!statusLoaded) {
      box.innerHTML = '<div class="empty loading">Loading…</div>';
      return;
    }
    if (!q.length) {
      box.innerHTML = '<div class="empty">No tracks queued. Select a playlist with music.</div>';
      return;
    }
    box.innerHTML = q.slice(0, 24).map(function (t, i) {
      var mark = t.is_current ? " ▶ " : (" " + (i + 1) + ". ");
      return '<div class="item" draggable="true" data-queue-index="' + i +
        '"><span class="title">' + mark + esc(trackLabel(t)) + "</span></div>";
    }).join("");
  }

  function operatorMessage(e, s) {
    var kind = e.kind || "";
    var msg = e.message || "";
    var freq = (s && s.frequency_mhz != null) ? Number(s.frequency_mhz).toFixed(1) : "?";
    if (kind === "TX_START" || (kind === "tx_start" && /ON_AIR/i.test(msg))) {
      if (e.source === "power_or_service_restore") {
        return "Broadcast restored automatically on " + freq + " FM";
      }
      return "Black flag raised — broadcasting on " + freq + " FM";
    }
    if (kind === "TX_TERM" || kind === "TX_EXIT" || (kind === "TX_STOP_REQUEST" && /STOP/i.test(msg))) {
      if (kind === "TX_STOP_REQUEST") return "Broadcast stop requested";
      if (kind === "TX_EXIT") return "Broadcast stopped";
      return null;
    }
    if (kind === "track_changed") {
      var t = e.track;
      var title = trackTitle(t) || msg;
      if (msg === "play" || msg === "next" || msg === "previous") {
        return "Now playing: " + title;
      }
      return "Now playing: " + title;
    }
    if (/paused/i.test(msg) || kind === "program_paused" || kind === "program_pause") return "Music paused";
    if (/resumed/i.test(msg) || kind === "program_resumed" || kind === "program_resume") return "Music resumed";
    if (kind === "rf_quiet" || kind === "network_disabled") return "Network quieted";
    if (kind === "network_restored") return "Network restored";
    if (kind === "library_import") return "Music library updated";
    if (kind === "FAULT") return "Needs attention: " + msg;
    if (kind === "TX_START" ) return "Black flag raised — broadcasting on " + freq + " FM";
    if (msg === "ON_AIR") return "Black flag raised — broadcasting on " + freq + " FM";
    if (/Practice|practice mode|test harness/i.test(msg)) return null;
    if (ENGINEERING_KINDS[kind]) return null;
    if (/STOP BROADCAST/i.test(msg)) return "Broadcast stopped";
    if (/ON_AIR/i.test(msg) && kind.indexOf("TX_") === 0) {
      return "Black flag raised — broadcasting on " + freq + " FM";
    }
    return null;
  }

  function loadOperatorLog() {
    var box = $("operatorLog");
    if (!box) return;
    if (!box.dataset.hydrated) {
      box.innerHTML = '<div class="empty loading">Loading…</div>';
    }
    api("/api/events").then(function (data) {
      var ev = data.events || [];
      var lines = [];
      var lastHuman = null;
      for (var i = 0; i < ev.length && lines.length < 40; i++) {
        var e = ev[i];
        var human = operatorMessage(e, state);
        if (!human) continue;
        // Collapse consecutive identical operator lines (rapid clicks / poll artifacts).
        if (human === lastHuman) continue;
        lastHuman = human;
        var when = e.ts ? new Date(e.ts * 1000).toLocaleString() : "";
        lines.push(
          '<div class="log-item"><span class="when">' + esc(when) + "</span>" + esc(human) + "</div>"
        );
      }
      var fp = lines.join("|");
      if (box.dataset.hydrated === "1" && fp === lastLogFingerprint) return;
      lastLogFingerprint = fp;
      box.dataset.hydrated = "1";
      box.innerHTML = lines.length
        ? lines.join("")
        : '<div class="empty">No operator events yet.</div>';
    }).catch(function () {
      if (!box.dataset.hydrated) {
        box.innerHTML = '<div class="empty">Could not load Ship\'s Log.</div>';
      }
    });
  }

  function applyAuthoritativeSnapshot(snapshot) {
    if (activeAuthorityId !== null &&
        snapshot.authority_id !== activeAuthorityId) {
      uiAuthorityEpoch += 1;
      uiSynchronized = false;
      if (flagpoleGesture && flagpoleGesture.isActive()) {
        flagpoleGesture.cancel("controller authority changed");
      }
    }
    activeAuthorityId = snapshot.authority_id;
    window._queueTracks = snapshot.queue || [];
    renderStatus(snapshot);
    if ($("view-broadcast").classList.contains("active")) loadOperatorLog();
  }

  var connectionCoordinator = window.PifmConnection.createCoordinator({
    fetchSnapshot: function () { return api("/api/status"); },
    onUnavailable: renderStateUnavailable,
    onSnapshot: applyAuthoritativeSnapshot,
    onSynchronized: markStateSynchronized
  });

  function refresh() {
    return connectionCoordinator.reconcile();
  }

  function loadLibrary() {
    var box = $("libList");
    if (box && !libraryLoadedOnce) {
      box.innerHTML = '<div class="empty loading">Loading…</div>';
    }
    var q = ($("libSearch") && $("libSearch").value.trim()) || "";
    var url = "/api/library" + (q ? ("?q=" + encodeURIComponent(q)) : "");
    loadLibraryPlaylistTargets();
    api(url).then(function (data) {
      libraryLoadedOnce = true;
      var tracks = data.tracks || [];
      libraryTrackCount = tracks.length;
      if (!tracks.length) {
        box.innerHTML = '<div class="empty">No tracks yet. Drop files into Add Music above.</div>';
        return;
      }
      box.innerHTML = tracks.slice(0, 100).map(function (t) {
        return '<div class="item" draggable="true" data-track-id="' + esc(t.id) +
          '"><span class="title">' + esc(trackLabel(t)) +
          '</span><span class="meta">' + esc(t.format || "") +
          '</span><span><button type="button" data-add="' + esc(t.id) +
          '">Add to ' + esc(humanPlaylistName(
            null, state && state.active_playlist
          )) + '</button> <button type="button" data-delete-track="' +
          esc(t.id) + '" data-track-label="' + esc(trackLabel(t)) +
          '">Delete</button></span></div>';
      }).join("");
    }).catch(function (e) {
      if (!libraryLoadedOnce) box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
      toast(e.message);
    });
  }

  function renderLibraryPlaylistTargets(playlists) {
    var box = $("libraryPlaylistTargets");
    if (!box) return;
    if (!playlists.length) {
      box.innerHTML = '<span class="meta">Create a playlist first.</span>';
      return;
    }
    box.innerHTML = playlists.map(function (playlist) {
      var id = playlist.id || playlist.name;
      var active = state && state.active_playlist === id;
      return '<button type="button" class="playlist-target' +
        (active ? " active-row" : "") + '" data-playlist-target="' + esc(id) +
        '">' + esc(humanPlaylistName(playlist.name, id)) +
        (active ? " · active" : "") + "</button>";
    }).join("");
  }

  function loadLibraryPlaylistTargets() {
    if (playlistsCache) {
      renderLibraryPlaylistTargets(playlistsCache);
      return;
    }
    api("/api/playlists").then(function (data) {
      playlistsCache = data.playlists || [];
      renderLibraryPlaylistTargets(playlistsCache);
    }).catch(function () {
      renderLibraryPlaylistTargets([]);
    });
  }

  $("libList").addEventListener("click", function (ev) {
    var deleteButton = ev.target.closest("[data-delete-track]");
    if (deleteButton) {
      var trackId = deleteButton.getAttribute("data-delete-track");
      var label = deleteButton.getAttribute("data-track-label") || "this track";
      if (!confirm("Delete \"" + label + "\"?\n\nIt will also be removed from playlists. This cannot be undone.")) return;
      api("/api/library/" + encodeURIComponent(trackId), { method: "DELETE" })
        .then(function (result) {
          toast("Track deleted" + ((result.removed_from_playlists || []).length
            ? " and removed from playlists." : "."));
          libraryLoadedOnce = false;
          playlistsCache = null;
          loadLibrary();
          loadPlaylists();
          return refresh();
        }).catch(function (e) { toast(e.message); });
      return;
    }
    var btn = ev.target.closest("[data-add]");
    if (!btn || !state) return;
    var pid = state.active_playlist;
    if (!pid) return toast("Select a playlist first.");
    post("/api/playlists/" + encodeURIComponent(pid) + "/tracks", { track_id: btn.getAttribute("data-add") })
      .then(function () {
        toast("Added to " + humanPlaylistName(null, pid) + ".");
        refresh();
        if (selectedPl === pid) openPlaylist(pid);
      })
      .catch(function (e) { toast(e.message); });
  });

  $("libList").addEventListener("dragstart", function (ev) {
    var row = ev.target.closest("[data-track-id]");
    if (!row) return;
    draggedTrackId = row.getAttribute("data-track-id");
    if (ev.dataTransfer) ev.dataTransfer.setData("text/plain", draggedTrackId);
  });

  $("libraryPlaylistTargets").addEventListener("dragover", function (ev) {
    var target = ev.target.closest("[data-playlist-target]");
    if (!target) return;
    ev.preventDefault();
    target.classList.add("dragging");
  });
  $("libraryPlaylistTargets").addEventListener("dragleave", function (ev) {
    var target = ev.target.closest("[data-playlist-target]");
    if (target) target.classList.remove("dragging");
  });
  $("libraryPlaylistTargets").addEventListener("drop", function (ev) {
    var target = ev.target.closest("[data-playlist-target]");
    var trackId = draggedTrackId ||
      (ev.dataTransfer && ev.dataTransfer.getData("text/plain"));
    if (!target || !trackId) return;
    ev.preventDefault();
    target.classList.remove("dragging");
    var playlistId = target.getAttribute("data-playlist-target");
    post("/api/playlists/" + encodeURIComponent(playlistId) + "/tracks", {
      track_id: trackId
    }).then(function () {
      toast("Added to " + humanPlaylistName(null, playlistId) + ".");
      playlistsCache = null;
      loadLibraryPlaylistTargets();
      loadPlaylists();
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  function loadPlaylists() {
    var box = $("plList");
    if (box && !playlistsCache) {
      box.innerHTML = '<div class="empty loading">Loading…</div>';
    }
    api("/api/playlists").then(function (data) {
      playlistsCache = data.playlists || [];
      renderLibraryPlaylistTargets(playlistsCache);
      var showEmpty = $("showEmptyPl") && $("showEmptyPl").checked;
      var playlists = playlistsCache.filter(function (p) {
        var n = p.track_count != null ? p.track_count : 0;
        var id = p.id || p.name;
        var active = state && state.active_playlist === id;
        return showEmpty || n > 0 || active;
      });
      if (!playlists.length) {
        box.innerHTML = '<div class="empty">No playlists with tracks yet. Create one or show empty playlists.</div>';
        return;
      }
      box.innerHTML = playlists.map(function (p) {
        var id = p.id || p.name;
        var active = state && state.active_playlist === id;
        var n = p.track_count != null ? p.track_count : 0;
        var label = humanPlaylistName(p.name, id);
        return '<div class="item' + (active ? " active-row" : "") +
          '" data-playlist-drop="' + esc(id) + '">' +
          '<span class="title">' + esc(label) +
          (active ? " · ACTIVE" : "") +
          ' <span class="meta">(' + n + " tracks)</span></span>" +
          '<span><button type="button" data-use="' + esc(id) + '">Select</button> ' +
          '<button type="button" data-edit="' + esc(id) + '">View / Edit</button> ' +
          '<button type="button" data-dup="' + esc(id) + '">Duplicate</button> ' +
          '<button type="button" data-del="' + esc(id) + '">Delete</button></span></div>';
      }).join("");
    }).catch(function (e) {
      if (!playlistsCache) box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
      toast(e.message);
    });
  }

  function openPlaylist(id) {
    selectedPl = id;
    var box = $("plDetail");
    box.innerHTML = '<div class="empty loading">Loading…</div>';
    api("/api/playlists/" + encodeURIComponent(id)).then(function (p) {
      var tracks = p.tracks || [];
      var detailById = {};
      (p.track_details || []).forEach(function (track) {
        detailById[track.id] = track;
      });
      window._plTracks = tracks.slice();
      var rows = tracks.map(function (tid, i) {
        var detail = typeof tid === "string" ? detailById[tid] : tid;
        var label = detail ? trackLabel(detail) : "Missing music";
        var tidStr = typeof tid === "string" ? tid : (tid.id || String(i));
        return '<div class="item" draggable="true" data-playlist-index="' + i +
          '"><span class="title">' + (i + 1) + ". " + esc(label) + "</span>" +
          '<span><button type="button" data-up="' + i + '">↑</button> ' +
          '<button type="button" data-down="' + i + '">↓</button> ' +
          '<button type="button" data-rm="' + esc(tidStr) + '">Remove</button></span></div>';
      }).join("");
      box.innerHTML = '<div class="row"><input id="renamePlName" value="' +
        esc(humanPlaylistName(p.name, id)) +
        '" aria-label="Playlist name"><button type="button" data-rename-playlist>Rename</button></div>' +
        (rows || '<div class="empty">Playlist empty. Drag library tracks onto this playlist.</div>');
    }).catch(function (e) {
      box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
      toast(e.message);
    });
  }

  function loadSystem() {
    refresh();
    var box = $("eventsBox");
    if (box && !box.dataset.hydrated) {
      box.textContent = "Loading…";
    }
    api("/api/events").then(function (data) {
      var ev = data.events || [];
      box.dataset.hydrated = "1";
      box.textContent = ev.slice(0, 120).map(function (e) {
        var when = e.ts ? new Date(e.ts * 1000).toLocaleString() : "";
        return when + "  " + (e.kind || "") + "  " + (e.message || "");
      }).join("\n") || "No events yet.";
    }).catch(function (e) {
      if (!box.dataset.hydrated) box.textContent = e.message;
    });
  }

  function bindProgramTransport(root) {
    if (!root) return;
    root.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-act]");
      if (!btn) return;
      if (btn.disabled) return;
      var act = btn.getAttribute("data-act");
      if (act === "playpause") {
        if (commandPending === "play" || commandPending === "pause") return;
        act = (state && (state.program_state === "playing" || state.program_ui === "PLAYING")) ? "pause" : "play";
      }
      if ((act === "play" || act === "pause" || act === "next" || act === "prev") && commandPending) {
        return;
      }
      var path = { play: "/api/play", pause: "/api/pause", stop: "/api/stop", next: "/api/next", prev: "/api/prev" }[act];
      if (!path) return;
      commandPending = act === "stop" ? null : act;
      // Immediate acknowledgement — never leave the deck looking unchanged.
      if (state) {
        var optimistic = Object.assign({}, state);
        if (act === "play") {
          optimistic.program_ui = state.program_state === "paused" ? "RESUMING…" : "STARTING MUSIC…";
          optimistic.program_pending = state.program_state === "paused" ? "resuming" : "starting";
        } else if (act === "pause") {
          optimistic.program_ui = "PAUSING…";
          optimistic.program_pending = "pausing";
        } else if (act === "next" || act === "prev") {
          optimistic.program_ui = "CHANGING TRACK…";
          optimistic.program_pending = "changing";
        }
        renderStatus(optimistic);
      }
      post(path, {}).then(function () {
        return refresh();
      }).catch(function (e) {
        commandPending = null;
        toast(e.message);
        return refresh();
      });
    });
  }
  bindProgramTransport(document.querySelector(".player-transport"));
  bindProgramTransport(document.querySelector(".music-reset"));

  $("blockerBox").addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-goto]");
    if (!btn) return;
    showTab(btn.getAttribute("data-goto"));
  });

  function commitFlagpoleTarget(target) {
    if (!uiSynchronized || !state ||
        flagpoleGestureEpoch !== uiAuthorityEpoch) {
      toast("Live station state changed. Try the brass handle again.");
      if (state) renderFlagpoleStatus(state);
      return;
    }
    if (flagpoleSnapshotBlocksCommit(state)) {
      toast("Broadcast state changed. Use Stop Broadcast or wait for READY.");
      renderFlagpoleStatus(state);
      return;
    }
    var broadcastUi = state.broadcast_ui || "OFF";
    var onAir = broadcastUi === "ON AIR";
    if (target.desired_broadcast === "off") {
      if (!onAir && broadcastUi.indexOf("STARTING") !== 0 &&
          state.state !== "FAULT") {
        renderFlagpoleStatus(state);
        return;
      }
      if (commandPending === "txoff") return;
      commandPending = "txoff";
      txCommandPendingRevision = Number(state.snapshot_revision);
      $("flagpoleHandle").disabled = true;
      $("flagpoleFeedback").textContent = "LOWERING · STOPPING BROADCAST";
      post("/api/tx/off", {}).then(function () {
        commandPending = null;
        txCommandPendingRevision = null;
        toast("Pirate flag lowered. Broadcast is OFF AIR.");
        return refresh();
      }).catch(function (error) {
        commandPending = null;
        txCommandPendingRevision = null;
        toast(error.message);
        return refresh();
      });
      return;
    }

    var bc = (state && state.broadcast) || {};
    if (!onAir && bc.ready === false) {
      toast("Not ready yet — see the message above.");
      renderBlocker(bc, false, false);
      renderFlagpoleStatus(state);
      return;
    }
    if (commandPending === "txon") return;
    var frequency = Number(target.frequency_mhz).toFixed(1);
    if (onAir && Number(state.frequency_mhz).toFixed(1) === frequency) {
      renderFlagpoleStatus(state);
      return;
    }
    var harness = !!(state && state.dev_harness);
    var pl = humanPlaylistName(bc.playlist_name, state && state.active_playlist);
    var msg;
    if (onAir) {
      msg =
        "Retune the live station to " + frequency + " FM?\n\n" +
        "This safely stops and restarts the transmitter. The current track " +
        "will restart from the beginning.";
    } else {
      msg =
        "Start broadcasting at " + frequency + " FM?\n\n" +
        "Playlist: " + pl + " (" + (bc.track_count || 0) + " tracks)\n" +
        "Station: " + (bc.rds_ps || "?") + "\n\n" +
        "This starts the selected program AND the FM transmitter.\n" +
        (harness
          ? "\nNOTE: This console is running the internal test harness (no FM)."
          : "\nThis will transmit on FM.");
    }
    if (!confirm(msg)) {
      renderFlagpoleStatus(state);
      return;
    }
    setRaisedFlagVisible(true);
    commandPending = "txon";
    txCommandPendingRevision = Number(state.snapshot_revision);
    $("flagpoleHandle").disabled = true;
    var stateEl = $("broadcastState");
    if (stateEl) {
      stateEl.textContent = "STARTING BROADCAST…";
      stateEl.className = "broadcast-state starting";
    }
    $("flagpoleHandle").className = "flagpole-handle pending";
    $("flagpoleFeedback").textContent = frequency + " FM · STARTING";
    toast(harness ? "Starting test harness…" : "Starting broadcast…");
    post("/api/tx/on", {
      frequency_mhz: Number(target.frequency_mhz)
    }).then(function () {
      toast(harness
        ? "Starting (test harness) — no FM signal."
        : "Starting — preparing audio…");
      return refresh();
    }).catch(function (e) {
      commandPending = null;
      txCommandPendingRevision = null;
      toast(e.message);
      return refresh();
    });
  }

  function flagpolePositionFromPointer(event, applyGrabOffset) {
    var rect = $("flagpoleTrack").getBoundingClientRect();
    var handleHeight = $("flagpoleHandle").getBoundingClientRect().height || 43;
    return window.PifmFlagpole.pointerPosition(
      event.clientY - (applyGrabOffset ? flagpoleGrabOffsetY : 0),
      rect.top,
      rect.height,
      handleHeight
    );
  }

  function initializeFlagpole() {
    var handle = $("flagpoleHandle");
    flagpoleGesture = window.PifmFlagpole.createGesture({
      onPreview: renderFlagpolePreview,
      onCancel: function () {
        handle._previewPosition = null;
        if (state) renderFlagpoleStatus(state);
      },
      onCommit: commitFlagpoleTarget
    });

    handle.addEventListener("pointerdown", function (event) {
      if (!uiSynchronized || !state || handle.disabled) return;
      event.preventDefault();
      flagpolePointerId = event.pointerId;
      flagpoleGestureEpoch = uiAuthorityEpoch;
      var handleRect = handle.getBoundingClientRect();
      flagpoleGrabOffsetY = event.clientY -
        (handleRect.top + (handleRect.height / 2));
      handle.setPointerCapture(event.pointerId);
      flagpoleGesture.begin(
        flagpolePositionFromPointer(event, true), state.frequency_band
      );
    });
    handle.addEventListener("pointermove", function (event) {
      if (!flagpoleGesture.isActive() ||
          event.pointerId !== flagpolePointerId) return;
      event.preventDefault();
      flagpoleGesture.move(
        flagpolePositionFromPointer(event, true), state.frequency_band
      );
    });
    handle.addEventListener("pointerup", function (event) {
      if (!flagpoleGesture.isActive() ||
          event.pointerId !== flagpolePointerId) return;
      event.preventDefault();
      var position = flagpolePositionFromPointer(event, true);
      flagpoleGesture.release(position, state.frequency_band);
      try { handle.releasePointerCapture(event.pointerId); } catch (error) {}
      flagpolePointerId = null;
      flagpoleGrabOffsetY = 0;
    });
    handle.addEventListener("pointercancel", function () {
      flagpolePointerId = null;
      flagpoleGrabOffsetY = 0;
      flagpoleGesture.cancel("pointer cancelled");
    });
    handle.addEventListener("lostpointercapture", function () {
      if (flagpoleGesture.isActive()) {
        flagpolePointerId = null;
        flagpoleGrabOffsetY = 0;
        flagpoleGesture.cancel("pointer capture lost");
      }
    });
    handle.addEventListener("blur", function () {
      if (flagpoleGesture.isActive() && flagpolePointerId === null) {
        flagpoleGesture.cancel("keyboard focus left control");
      }
    });
    handle.addEventListener("keydown", function (event) {
      if (!uiSynchronized || !state || handle.disabled) return;
      var band = state.frequency_band;
      var position = handle._previewPosition;
      if (event.key === "Escape") {
        event.preventDefault();
        flagpoleGesture.cancel("keyboard cancelled");
        return;
      }
      if (event.key === "Enter" || event.key === " ") {
        if (flagpoleGesture.isActive()) {
          event.preventDefault();
          flagpoleGesture.release(position, band);
        }
        return;
      }
      if (position == null) {
        position = state.broadcast_ui === "ON AIR" ||
          state.broadcast_ui.indexOf("STARTING") === 0
          ? window.PifmFlagpole.frequencyToPosition(
            state.frequency_mhz, band
          )
          : 0;
      }
      var nextPosition = null;
      if (event.key === "Home") {
        nextPosition = 0;
      } else if (event.key === "End") {
        nextPosition = 1;
      } else if (event.key === "ArrowUp" || event.key === "ArrowRight") {
        if (position == null ||
            position < window.PifmFlagpole.TUNER_MIN_POSITION) {
          nextPosition = window.PifmFlagpole.TUNER_MIN_POSITION;
        } else {
          var upUnits = window.PifmFlagpole.positionToUnits(position, band);
          upUnits = Math.min(band.max_units, upUnits + 1);
          nextPosition = window.PifmFlagpole.frequencyToPosition(
            upUnits / band.scale, band
          );
        }
      } else if (event.key === "ArrowDown" || event.key === "ArrowLeft") {
        if (position <= window.PifmFlagpole.TUNER_MIN_POSITION) {
          nextPosition = 0;
        } else {
          var downUnits = window.PifmFlagpole.positionToUnits(position, band);
          downUnits = Math.max(band.min_units, downUnits - 1);
          nextPosition = window.PifmFlagpole.frequencyToPosition(
            downUnits / band.scale, band
          );
        }
      }
      if (nextPosition == null) return;
      event.preventDefault();
      flagpoleGestureEpoch = uiAuthorityEpoch;
      if (flagpoleGesture.isActive()) {
        flagpoleGesture.move(nextPosition, band);
      } else {
        flagpoleGesture.begin(nextPosition, band);
      }
    });

    $("flagpolePreset").addEventListener("click", function () {
      var preset = $("flagpolePreset");
      if (!uiSynchronized || !state || preset.disabled) return;
      var position = window.PifmFlagpole.frequencyToPosition(
        state.frequency_mhz, state.frequency_band
      );
      flagpoleGestureEpoch = uiAuthorityEpoch;
      commitFlagpoleTarget(window.PifmFlagpole.targetForPosition(
        position, state.frequency_band
      ));
    });
  }

  function bindStop(el) {
    if (!el) return;
    el.addEventListener("click", function () {
      if (!confirm("STOP BROADCAST?\n\nThis immediately ends FM transmission.\nUse this any time — including after a fault.")) return;
      commandPending = "txoff";
      txCommandPendingRevision = Number(
        state && state.snapshot_revision
      );
      if (flagpoleGesture && flagpoleGesture.isActive()) {
        flagpoleGesture.cancel("STOP BROADCAST requested");
      }
      if ($("flagpoleHandle")) {
        $("flagpoleHandle").disabled = true;
      }
      var stateEl = $("broadcastState");
      if (stateEl) {
        stateEl.textContent = "STOPPING BROADCAST…";
        stateEl.className = "broadcast-state starting";
      }
      post("/api/tx/off", {}).then(function () {
        commandPending = null;
        txCommandPendingRevision = null;
        toast("Broadcast stopped. Transmitter is OFF.");
        return refresh();
      }).catch(function (e) {
        commandPending = null;
        txCommandPendingRevision = null;
        toast(e.message);
        return refresh();
      });
    });
  }
  bindStop($("btnStopBroadcastHeader"));

  $("btnRfQuiet").addEventListener("click", function () {
    var net = (state && state.network) || {};
    var mode = net.mode || "simulate";
    var secs = net.recovery_seconds || 60;
    if (!confirm(
      "Quiet network for interference test?\n\n" +
      "This turns OFF the network connection.\n" +
      "It does NOT stop the FM broadcast.\n" +
      "You may lose this web page until the network returns (~" + secs + "s).\n\n" +
      "Mode: " + mode
    )) return;
    var n = 3;
    function tick() {
      if (n > 0) {
        toast("Network quiet in " + n + "…");
        n -= 1;
        setTimeout(tick, 1000);
        return;
      }
      post("/api/rfquiet", { confirmed: true }).then(function () {
        toast(mode === "simulate" ? "Network quiet simulation on." : "Network quiet — link may drop.");
        return refresh();
      }).catch(function (e) { toast(e.message); });
    }
    tick();
  });

  $("btnRfRestore").addEventListener("click", function () {
    post("/api/rfquiet/restore", {}).then(function () {
      toast("Network restore requested.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  function saveStation(includeAdvanced) {
    var body = {
      frequency_mhz: parseFloat($("cfgFreq").value),
      rds_ps: $("cfgPs").value,
      rds_rt: $("cfgRt").value
    };
    if (includeAdvanced) body.rds_pi = $("cfgPi").value;
    post("/api/config", body).then(function (result) {
      if (result && result.status) {
        connectionCoordinator.acceptLiveSnapshot(result.status);
      }
      toast("Station saved. Broadcast was not started.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  }

  $("btnSaveCfg").addEventListener("click", function () { saveStation(false); });
  $("btnSaveAdvanced").addEventListener("click", function () { saveStation(true); });

  document.querySelectorAll("[data-setup-back]").forEach(function (button) {
    button.addEventListener("click", function () {
      showSetupStep(setupStep - 1);
    });
  });
  document.querySelectorAll("[data-setup-next]").forEach(function (button) {
    button.addEventListener("click", function () {
      if (setupStep === 1) {
        var hardwareStatus = String(
          (state && state.hardware_status) ||
          ((state && state.hardware_profile_doc) || {}).status || "UNKNOWN"
        ).toUpperCase();
        if (hardwareStatus !== "SUPPORTED" && hardwareStatus !== "EXPERIMENTAL") {
          toast("This hardware is not recognized. Choose a profile under Advanced.");
          return;
        }
      }
      if (setupStep === 3) {
        api("/api/library").then(function (data) {
          if (!(data.tracks || []).length) {
            toast("Add at least one track before continuing.");
            return;
          }
          showSetupStep(4);
        }).catch(function (e) { toast(e.message); });
        return;
      }
      showSetupStep(setupStep + 1);
    });
  });
  $("setupSaveStation").addEventListener("click", function () {
    post("/api/setup", {
      frequency_mhz: parseFloat($("setupFreq").value),
      rds_ps: $("setupPs").value,
      rds_rt: $("setupRt").value
    }).then(function () {
      showSetupStep(3);
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });
  $("setupSaveHardware").addEventListener("click", function () {
    post("/api/setup", {
      hardware_profile_mode: "manual",
      hardware_profile: $("setupHardwareProfile").value
    }).then(function () {
      toast("Manual hardware profile selected as experimental.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });
  $("setupFinish").addEventListener("click", function () {
    post("/api/setup", { setup_completed: true }).then(function () {
      $("setupWizard").hidden = true;
      showTab("broadcast");
      toast("Setup complete. You are OFF AIR.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });
  $("btnSaveHardware").addEventListener("click", function () {
    var mode = $("hardwareMode").value;
    post("/api/config", {
      hardware_profile_mode: mode,
      hardware_profile: $("hardwareProfile").value
    }).then(function () {
      hardwareFormDirty = false;
      toast(mode === "auto"
        ? "Automatic hardware detection enabled."
        : "Hardware profile saved.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  ["hardwareMode", "hardwareProfile"].forEach(function (id) {
    if (!$(id)) return;
    $(id).addEventListener("change", function () {
      hardwareFormDirty = true;
      if (id === "hardwareMode") applyHardwareFormMode($("hardwareMode").value);
    });
  });

  $("btnSaveMusicFlags").addEventListener("click", function () {
    post("/api/config", {
      shuffle: $("cfgShuffle").checked,
      repeat: $("cfgRepeat").checked
    }).then(function () {
      toast("Playback options saved. Broadcast was not started.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  $("btnReindex").addEventListener("click", function () {
    post("/api/library/reindex", {}).then(function (r) {
      toast("Music refreshed (" + (r.indexed != null ? r.indexed : "") + " tracks).");
      libraryLoadedOnce = false;
      loadLibrary();
      refresh();
    }).catch(function (e) { toast(e.message); });
  });

  $("libSearch").addEventListener("input", function () {
    clearTimeout($("libSearch")._t);
    $("libSearch")._t = setTimeout(loadLibrary, 280);
  });

  function uploadOne(
    file, progressBox, index, batchAuthorityEpoch, batchPlaylistId
  ) {
    if (!uiSynchronized || batchAuthorityEpoch !== uiAuthorityEpoch) {
      return Promise.reject(new Error(
        "Connection lost. Remaining uploads were not sent."
      ));
    }
    return new Promise(function (resolve, reject) {
      var rowId = "upload-" + Date.now() + "-" + index;
      progressBox.insertAdjacentHTML(
        "beforeend",
        '<div class="upload-row" id="' + rowId + '"><div>' +
        esc(file.name) + '</div><progress max="100" value="0"></progress>' +
        '<div class="upload-result">Starting…</div></div>'
      );
      var row = $(rowId);
      var progress = row.querySelector("progress");
      var result = row.querySelector(".upload-result");
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload");
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.setRequestHeader("X-PiFM-Authority-ID", activeAuthorityId);
      xhr.setRequestHeader("X-Filename-Encoded", encodeURIComponent(file.name));
      if (batchPlaylistId) {
        xhr.setRequestHeader("X-Playlist-ID", batchPlaylistId);
      }
      xhr.upload.onprogress = function (event) {
        if (batchAuthorityEpoch !== uiAuthorityEpoch) return;
        if (event.lengthComputable) {
          progress.value = Math.round((event.loaded / event.total) * 100);
          result.textContent = progress.value + "%";
        }
      };
      xhr.onload = function () {
        if (batchAuthorityEpoch !== uiAuthorityEpoch) {
          reject(new Error("Connection changed during upload."));
          return;
        }
        var payload = {};
        try { payload = JSON.parse(xhr.responseText || "{}"); } catch (e) {}
        if (xhr.status < 200 || xhr.status >= 300) {
          result.textContent = payload.error || "Upload failed.";
          result.classList.add("error");
          reject(new Error(result.textContent));
          return;
        }
        progress.value = 100;
        result.textContent = payload.duplicate
          ? "Already in your library."
          : (payload.renamed
            ? "Added as " + payload.filename
            : "Added to your library.");
        resolve(payload);
      };
      xhr.onerror = function () {
        if (batchAuthorityEpoch !== uiAuthorityEpoch) {
          reject(new Error("Connection changed during upload."));
          return;
        }
        result.textContent = "Connection lost during upload.";
        result.classList.add("error");
        reject(new Error(result.textContent));
      };
      xhr.send(file);
    });
  }

  function uploadFiles(files, progressId) {
    if (!uiSynchronized) {
      return Promise.reject(new Error(
        "Live station state is unavailable. Wait for reconnection."
      ));
    }
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return Promise.resolve([]);
    var batchAuthorityEpoch = uiAuthorityEpoch;
    var batchPlaylistId = state && state.active_playlist;
    var progressBox = $(progressId);
    progressBox.innerHTML = "";
    var results = [];
    var failures = [];
    var chain = Promise.resolve();
    list.forEach(function (file, index) {
      chain = chain.then(function () {
        return uploadOne(
          file, progressBox, index, batchAuthorityEpoch, batchPlaylistId
        ).then(function (result) {
          results.push(result);
        }).catch(function (error) {
          failures.push(error);
        });
      });
    });
    return chain.then(function () {
      if (batchAuthorityEpoch !== uiAuthorityEpoch) return results;
      libraryLoadedOnce = false;
      playlistsCache = null;
      loadLibrary();
      loadPlaylists();
      return refresh();
    }).then(function () {
      toast(
        results.length + (results.length === 1 ? " file added" : " files added") +
        (failures.length ? "; " + failures.length + " could not be added." : ".")
      );
      return results;
    });
  }

  $("fileUpload").addEventListener("change", function () {
    uploadFiles($("fileUpload").files, "uploadProgress").catch(function () {});
    $("fileUpload").value = "";
  });
  $("setupFileUpload").addEventListener("change", function () {
    uploadFiles(
      $("setupFileUpload").files, "setupUploadProgress"
    ).catch(function () {});
    $("setupFileUpload").value = "";
  });

  document.querySelectorAll("[data-upload-zone]").forEach(function (zone) {
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.add("dragging");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (event) {
        event.preventDefault();
        zone.classList.remove("dragging");
      });
    });
    zone.addEventListener("drop", function (event) {
      var progressId = zone.classList.contains("setup-upload-zone")
        ? "setupUploadProgress" : "uploadProgress";
      uploadFiles(event.dataTransfer.files, progressId).catch(function () {});
    });
  });

  $("btnNewPl").addEventListener("click", function () {
    var name = $("newPlName").value.trim();
    if (!name) return toast("Enter a playlist name.");
    post("/api/playlists", { name: name }).then(function () {
      $("newPlName").value = "";
      playlistsCache = null;
      loadPlaylists();
    }).catch(function (e) { toast(e.message); });
  });

  if ($("showEmptyPl")) {
    $("showEmptyPl").addEventListener("change", loadPlaylists);
  }

  $("plList").addEventListener("click", function (ev) {
    var t = ev.target;
    if (t.getAttribute("data-use")) {
      post("/api/config", { active_playlist: t.getAttribute("data-use") }).then(function () {
        toast("Playlist selected. Choose Start Broadcasting when ready.");
        refresh(); loadPlaylists();
      }).catch(function (e) { toast(e.message); });
    } else if (t.getAttribute("data-edit")) {
      openPlaylist(t.getAttribute("data-edit"));
    } else if (t.getAttribute("data-dup")) {
      var src = t.getAttribute("data-dup");
      api("/api/playlists/" + encodeURIComponent(src)).then(function (p) {
        var copyName = humanPlaylistName(p.name, src) + " copy";
        return post("/api/playlists", { name: copyName }).then(function (np) {
          var nid = np.id || np.name;
          return api("/api/playlists/" + encodeURIComponent(nid), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: copyName, tracks: p.tracks || [] })
          });
        });
      }).then(function () {
        playlistsCache = null;
        loadPlaylists();
      }).catch(function (e) { toast(e.message); });
    } else if (t.getAttribute("data-del")) {
      var id = t.getAttribute("data-del");
      var label = humanPlaylistName(null, id);
      if (!confirm("Delete playlist \"" + label + "\"?\nMusic files on disk are kept.")) return;
      api("/api/playlists/" + encodeURIComponent(id), { method: "DELETE" })
        .then(function () {
          playlistsCache = null;
          loadPlaylists();
          $("plDetail").innerHTML = "";
        }).catch(function (e) { toast(e.message); });
    }
  });

  $("plList").addEventListener("dragover", function (ev) {
    if (ev.target.closest("[data-playlist-drop]")) ev.preventDefault();
  });
  $("plList").addEventListener("drop", function (ev) {
    var row = ev.target.closest("[data-playlist-drop]");
    var trackId = draggedTrackId ||
      (ev.dataTransfer && ev.dataTransfer.getData("text/plain"));
    if (!row || !trackId) return;
    ev.preventDefault();
    var playlistId = row.getAttribute("data-playlist-drop");
    post("/api/playlists/" + encodeURIComponent(playlistId) + "/tracks", {
      track_id: trackId
    }).then(function () {
      toast("Added to " + humanPlaylistName(null, playlistId) + ".");
      playlistsCache = null;
      loadPlaylists();
      if (selectedPl === playlistId) openPlaylist(playlistId);
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  $("plDetail").addEventListener("click", function (ev) {
    if (!selectedPl) return;
    var t = ev.target;
    var tracks = (window._plTracks || []).slice();
    if (t.hasAttribute("data-rename-playlist")) {
      var name = $("renamePlName").value.trim();
      if (!name) return toast("Enter a playlist name.");
      api("/api/playlists/" + encodeURIComponent(selectedPl), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name, tracks: tracks })
      }).then(function () {
        playlistsCache = null;
        loadPlaylists();
        openPlaylist(selectedPl);
        toast("Playlist renamed.");
      }).catch(function (e) { toast(e.message); });
    } else if (t.getAttribute("data-rm")) {
      api("/api/playlists/" + encodeURIComponent(selectedPl) + "/tracks/" + encodeURIComponent(t.getAttribute("data-rm")), { method: "DELETE" })
        .then(function () { openPlaylist(selectedPl); loadPlaylists(); })
        .catch(function (e) { toast(e.message); });
    } else if (t.getAttribute("data-up") != null || t.getAttribute("data-down") != null) {
      var i = parseInt(t.getAttribute("data-up") != null ? t.getAttribute("data-up") : t.getAttribute("data-down"), 10);
      var j = t.getAttribute("data-up") != null ? i - 1 : i + 1;
      if (j < 0 || j >= tracks.length) return;
      var tmp = tracks[i];
      tracks[i] = tracks[j];
      tracks[j] = tmp;
      var order = tracks.map(function (x) { return typeof x === "string" ? x : x.id; });
      post("/api/playlists/" + encodeURIComponent(selectedPl) + "/reorder", { tracks: order })
        .then(function () { openPlaylist(selectedPl); })
        .catch(function (e) { toast(e.message); });
    }
  });

  var playlistDragIndex = null;
  $("plDetail").addEventListener("dragstart", function (ev) {
    var row = ev.target.closest("[data-playlist-index]");
    if (row) playlistDragIndex = Number(row.getAttribute("data-playlist-index"));
  });
  $("plDetail").addEventListener("dragover", function (ev) {
    if (ev.target.closest("[data-playlist-index]")) ev.preventDefault();
  });
  $("plDetail").addEventListener("drop", function (ev) {
    var row = ev.target.closest("[data-playlist-index]");
    if (!row || playlistDragIndex == null) return;
    ev.preventDefault();
    var target = Number(row.getAttribute("data-playlist-index"));
    var tracks = (window._plTracks || []).slice();
    var moved = tracks.splice(playlistDragIndex, 1)[0];
    tracks.splice(target, 0, moved);
    playlistDragIndex = null;
    post("/api/playlists/" + encodeURIComponent(selectedPl) + "/reorder", {
      tracks: tracks
    }).then(function () {
      openPlaylist(selectedPl);
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  var queueDragIndex = null;
  $("queueBox").addEventListener("dragstart", function (ev) {
    var row = ev.target.closest("[data-queue-index]");
    if (row) queueDragIndex = Number(row.getAttribute("data-queue-index"));
  });
  $("queueBox").addEventListener("dragover", function (ev) {
    if (ev.target.closest("[data-queue-index]")) ev.preventDefault();
  });
  $("queueBox").addEventListener("drop", function (ev) {
    var row = ev.target.closest("[data-queue-index]");
    if (!row || queueDragIndex == null) return;
    ev.preventDefault();
    var target = Number(row.getAttribute("data-queue-index"));
    var queue = (window._queueTracks || []).slice();
    var moved = queue.splice(queueDragIndex, 1)[0];
    queue.splice(target, 0, moved);
    queueDragIndex = null;
    post("/api/queue/reorder", {
      tracks: queue.map(function (track) { return track.id; })
    }).then(function () {
      toast("Queue order saved to the active playlist.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  $("btnClearFault").addEventListener("click", function () {
    post("/api/fault/clear", {}).then(function () {
      toast("Error cleared. Station is idle — not on air.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  function scheduleReconnect() {
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(function () {
      connectionCoordinator.reconcile().then(function (ok) {
        if (ok) connectSSE();
        else scheduleReconnect();
      });
    }, 2500);
  }

  function connectSSE() {
    if (typeof EventSource === "undefined" ||
        !connectionCoordinator.isSynchronized()) return;
    if (activeEventSource) {
      try { activeEventSource.close(); } catch (closeError) {}
    }
    var es;
    try {
      es = new EventSource("/api/events/stream");
    } catch (error) {
      connectionCoordinator.markUnavailable(error.message);
      scheduleReconnect();
      return;
    }
    activeEventSource = es;
    es.onopen = function () {
      if (activeEventSource === es) {
        sseConnected = true;
        lastSseMessageAt = Date.now();
      }
    };
    es.onerror = function () {
      if (activeEventSource !== es) return;
      sseConnected = false;
      activeEventSource = null;
      try { es.close(); } catch (closeError) {}
      lastSseMessageAt = 0;
      connectionCoordinator.markUnavailable("Connection lost");
      scheduleReconnect();
    };
    es.onmessage = function (ev) {
      if (activeEventSource !== es) return;
      var msg;
      try { msg = JSON.parse(ev.data); } catch (error) { return; }
      lastSseMessageAt = Date.now();
      if (!msg || msg.type === "ping") return;
      if (msg.type === "status" && msg.status) {
        connectionCoordinator.acceptLiveSnapshot(msg.status);
      }
    };
  }

  initializeFlagpole();
  renderStateUnavailable("Connecting");
  connectionCoordinator.reconcile().then(function (ok) {
    if (ok) {
      loadOperatorLog();
      connectSSE();
    } else {
      scheduleReconnect();
    }
  });

  // Reconciliation is always a complete read-only appliance snapshot.
  setInterval(function () {
    if (!connectionCoordinator.isSynchronized()) {
      scheduleReconnect();
    } else if (sseConnected && lastSseMessageAt &&
        Date.now() - lastSseMessageAt > 22000) {
      if (activeEventSource) {
        try { activeEventSource.close(); } catch (closeError) {}
        activeEventSource = null;
      }
      sseConnected = false;
      lastSseMessageAt = 0;
      connectionCoordinator.markUnavailable("Live updates timed out");
      scheduleReconnect();
    } else {
      connectionCoordinator.reconcile().then(function (ok) {
        if (ok && !sseConnected) connectSSE();
      });
    }
  }, 8000);

  window.addEventListener("offline", function () {
    if (activeEventSource) {
      try { activeEventSource.close(); } catch (closeError) {}
      activeEventSource = null;
    }
    lastSseMessageAt = 0;
    connectionCoordinator.markUnavailable("Connection lost");
  });
  window.addEventListener("online", function () {
    connectionCoordinator.reconcile().then(function (ok) {
      if (ok) connectSSE();
      else scheduleReconnect();
    });
  });
})();
