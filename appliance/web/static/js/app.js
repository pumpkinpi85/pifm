(function () {
  "use strict";

  var state = null;
  var selectedPl = null;
  var toastTimer = null;
  var statusLoaded = false;
  var playlistsCache = null;
  var libraryLoadedOnce = false;
  var commandPending = null; // play|pause|next|prev|txon|txoff
  var sseConnected = false;
  var lastLogFingerprint = "";

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
    return fetch(path, opts).then(function (r) {
      return r.text().then(function (text) {
        var j = {};
        try { j = text ? JSON.parse(text) : {}; } catch (e) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          throw new Error("The radio console returned an unexpected response.");
        }
        if (!r.ok) throw new Error((j && j.error) || ("Request failed (" + r.status + ")"));
        return j;
      });
    });
  }

  function post(path, body) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
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

  document.querySelectorAll(".tab").forEach(function (btn) {
    btn.addEventListener("click", function () {
      showTab(btn.getAttribute("data-tab"));
    });
  });

  function normalizeBlockers(bc) {
    var raw = (bc && bc.blockers) || [];
    return raw.map(function (b) {
      if (typeof b === "string") {
        return { id: "", message: b, cta: "music", cta_label: "Choose music" };
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
      '<div class="blocker-title">CAN\'T GO ON AIR</div>' +
      "<p>" + esc(first.message) + "</p>" + cta;
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
    if (commandPending === "txon" && (onAir || unknown || faulted || broadcastUi === "OFF")) {
      if (onAir || unknown || faulted || (!starting && broadcastUi === "OFF")) commandPending = null;
    }
    if (commandPending === "txoff" && !onAir && !starting && !stopping) commandPending = null;
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

    var goBtn = $("btnGoOnAir");
    var stopBtn = $("btnStopBroadcast");
    var transmitting = onAir || starting || stopping || unknown;
    if (goBtn) {
      goBtn.disabled = onAir || unknown || starting || stopping || bc.ready === false || commandPending === "txon";
      goBtn.hidden = !!transmitting;
      goBtn.className = "tx-btn raise-flag";
      if (starting || commandPending === "txon") {
        goBtn.innerHTML = "<span class=\"flag-skull\" aria-hidden=\"true\">☠</span>STARTING BROADCAST…<span class=\"tx-sub\">PLEASE WAIT</span>";
      } else {
        goBtn.innerHTML = "<span class=\"flag-skull\" aria-hidden=\"true\">☠</span>RAISE THE BLACK FLAG<span class=\"tx-sub\">GO ON AIR</span>";
      }
    }
    if (stopBtn) {
      // SAFETY: never hide/disable Stop — especially UNKNOWN/FAULT/STARTING.
      stopBtn.disabled = false;
      stopBtn.hidden = false;
      stopBtn.removeAttribute("aria-disabled");
      if (stopping || commandPending === "txoff") {
        stopBtn.innerHTML = "STOPPING BROADCAST…<span class=\"tx-sub\">ENDING TRANSMISSION</span>";
        stopBtn.className = "tx-btn stop-btn urgent primary-flag";
      } else if (transmitting) {
        stopBtn.innerHTML = "LOWER THE BLACK FLAG<span class=\"tx-sub\">STOP BROADCAST</span>";
        stopBtn.className = "tx-btn stop-btn urgent primary-flag";
      } else {
        stopBtn.innerHTML = "LOWER THE BLACK FLAG<span class=\"tx-sub\">STOP BROADCAST</span>";
        stopBtn.className = "tx-btn stop-btn stop-secondary";
      }
    }
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
      "Transmitter: " + (s.dev_harness ? "test harness (mock)" : "FM transmitter"),
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
      return '<div class="item"><span class="title">' + mark + esc(trackLabel(t)) + "</span></div>";
    }).join("");
  }

  function operatorMessage(e, s) {
    var kind = e.kind || "";
    var msg = e.message || "";
    var freq = (s && s.frequency_mhz != null) ? Number(s.frequency_mhz).toFixed(1) : "?";
    if (kind === "TX_START" || (kind === "tx_start" && /ON_AIR/i.test(msg))) {
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

  function refresh() {
    return api("/api/status").then(function (s) {
      return api("/api/queue").then(function (q) {
        s.queue = q.queue || [];
        window._queueTracks = s.queue;
        renderStatus(s);
        if ($("view-broadcast").classList.contains("active")) loadOperatorLog();
      }).catch(function () { renderStatus(s); });
    }).catch(function (e) {
      toast("Console offline: " + e.message);
    });
  }

  function loadLibrary() {
    var box = $("libList");
    if (box && !libraryLoadedOnce) {
      box.innerHTML = '<div class="empty loading">Loading…</div>';
    }
    var q = ($("libSearch") && $("libSearch").value.trim()) || "";
    var url = "/api/library" + (q ? ("?q=" + encodeURIComponent(q)) : "");
    api(url).then(function (data) {
      libraryLoadedOnce = true;
      var tracks = data.tracks || [];
      if (!tracks.length) {
        box.innerHTML = '<div class="empty">No tracks found. Upload audio or press Refresh music.</div>';
        return;
      }
      box.innerHTML = tracks.slice(0, 100).map(function (t) {
        return '<div class="item"><span class="title">' + esc(trackLabel(t)) +
          '</span><span class="meta">' + esc(t.format || "") +
          '</span><button type="button" data-add="' + esc(t.id) + '">Add to playlist</button></div>';
      }).join("");
    }).catch(function (e) {
      if (!libraryLoadedOnce) box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
      toast(e.message);
    });
  }

  $("libList").addEventListener("click", function (ev) {
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

  function loadPlaylists() {
    var box = $("plList");
    if (box && !playlistsCache) {
      box.innerHTML = '<div class="empty loading">Loading…</div>';
    }
    api("/api/playlists").then(function (data) {
      playlistsCache = data.playlists || [];
      var showEmpty = $("showEmptyPl") && $("showEmptyPl").checked;
      var playlists = playlistsCache.filter(function (p) {
        var n = p.track_count != null ? p.track_count : 0;
        var id = p.id || p.name;
        var active = state && state.active_playlist === id;
        return showEmpty || n > 0 || active;
      });
      if (!playlists.length) {
        box.innerHTML = '<div class="empty">No playlists with music yet. Create one or show empty playlists.</div>';
        return;
      }
      box.innerHTML = playlists.map(function (p) {
        var id = p.id || p.name;
        var active = state && state.active_playlist === id;
        var n = p.track_count != null ? p.track_count : 0;
        var label = humanPlaylistName(p.name, id);
        return '<div class="item' + (active ? " active-row" : "") + '">' +
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
      window._plTracks = tracks.slice();
      var rows = tracks.map(function (tid, i) {
        var label = typeof tid === "string" ? tid : trackLabel(tid);
        var tidStr = typeof tid === "string" ? tid : (tid.id || String(i));
        return '<div class="item"><span class="title">' + (i + 1) + ". " + esc(label) + "</span>" +
          '<span><button type="button" data-up="' + i + '">↑</button> ' +
          '<button type="button" data-down="' + i + '">↓</button> ' +
          '<button type="button" data-rm="' + esc(tidStr) + '">Remove</button></span></div>';
      }).join("");
      box.innerHTML = '<div class="meta" style="margin:0.5rem 0">Editing: ' +
        esc(humanPlaylistName(p.name, id)) +
        "</div>" + (rows || '<div class="empty">Playlist empty.</div>');
    }).catch(function (e) {
      box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
      toast(e.message);
    });
  }

  function loadSystem() {
    api("/api/status").then(function (s) {
      renderHealth(s);
      renderAppliance(s);
      renderFaultRecovery(s);
      var net = s.network || {};
      $("netSummary").textContent = net.rf_quiet_active
        ? "Network quiet"
        : (net.ip ? "Connected" : "Disconnected");
      $("netDetail").textContent = net.ip ? ("IP " + net.ip) : "No IP address";
    });
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
      post(path, {}).then(function (st) {
        if (st && st.program_state) {
          return api("/api/queue").then(function (q) {
            st.queue = q.queue || [];
            window._queueTracks = st.queue;
            renderStatus(st);
            if ($("view-broadcast").classList.contains("active")) loadOperatorLog();
          }).catch(function () { renderStatus(st); });
        }
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

  $("btnGoOnAir").addEventListener("click", function () {
    var bc = (state && state.broadcast) || {};
    if (bc.ready === false) {
      toast("Not ready yet — see the message above.");
      renderBlocker(bc, false, false);
      return;
    }
    if (commandPending === "txon") return;
    var harness = !!(state && state.dev_harness);
    var pl = humanPlaylistName(bc.playlist_name, state && state.active_playlist);
    var msg =
      "Go on air now?\n\n" +
      "Playlist: " + pl + " (" + (bc.track_count || 0) + " tracks)\n" +
      "Frequency: " + (bc.frequency_mhz || "?") + " MHz\n" +
      "Station: " + (bc.rds_ps || "?") + "\n\n" +
      "This starts the selected program AND the FM transmitter.\n" +
      (harness
        ? "\nNOTE: This console is running the internal test harness (no FM)."
        : "\nThis will transmit on FM.");
    if (!confirm(msg)) return;
    commandPending = "txon";
    var stateEl = $("broadcastState");
    if (stateEl) {
      stateEl.textContent = "STARTING BROADCAST…";
      stateEl.className = "broadcast-state starting";
    }
    var goBtn = $("btnGoOnAir");
    if (goBtn) {
      goBtn.disabled = true;
      goBtn.innerHTML = "<span class=\"flag-skull\" aria-hidden=\"true\">☠</span>STARTING BROADCAST…<span class=\"tx-sub\">PLEASE WAIT</span>";
    }
    toast(harness ? "Starting test harness…" : "Starting broadcast…");
    post("/api/tx/on", {}).then(function (st) {
      if (st) renderStatus(st);
      toast(harness
        ? "Starting (test harness) — no FM signal."
        : "Starting — preparing audio…");
      return refresh();
    }).catch(function (e) {
      commandPending = null;
      toast(e.message);
      return refresh();
    });
  });

  function bindStop(el) {
    if (!el) return;
    el.addEventListener("click", function () {
      if (!confirm("STOP BROADCAST?\n\nThis immediately ends FM transmission.\nUse this any time — including after a fault.")) return;
      commandPending = "txoff";
      var stateEl = $("broadcastState");
      if (stateEl) {
        stateEl.textContent = "STOPPING BROADCAST…";
        stateEl.className = "broadcast-state starting";
      }
      post("/api/tx/off", {}).then(function (st) {
        commandPending = null;
        toast("Broadcast stopped. Transmitter is OFF.");
        if (st) renderStatus(st);
        return refresh();
      }).catch(function (e) {
        commandPending = null;
        toast(e.message);
        return refresh();
      });
    });
  }
  bindStop($("btnStopBroadcast"));
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
    post("/api/config", body).then(function () {
      toast("Station saved. Broadcast was not started.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  }

  $("btnSaveCfg").addEventListener("click", function () { saveStation(false); });
  $("btnSaveAdvanced").addEventListener("click", function () { saveStation(true); });

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

  $("fileUpload").addEventListener("change", function () {
    var f = $("fileUpload").files[0];
    if (!f) return;
    fetch("/api/upload", {
      method: "POST",
      headers: { "X-Filename": f.name, "Content-Type": "application/octet-stream" },
      body: f
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || "Upload failed");
        return j;
      });
    }).then(function () {
      toast("Uploaded into the library.");
      libraryLoadedOnce = false;
      loadLibrary();
    }).catch(function (e) { toast(e.message); });
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
        toast("Playlist selected. Press Go On Air when you want to broadcast.");
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

  $("plDetail").addEventListener("click", function (ev) {
    if (!selectedPl) return;
    var t = ev.target;
    var tracks = (window._plTracks || []).slice();
    if (t.getAttribute("data-rm")) {
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

  $("btnClearFault").addEventListener("click", function () {
    post("/api/fault/clear", {}).then(function () {
      toast("Error cleared. Station is idle — not on air.");
      return refresh();
    }).catch(function (e) { toast(e.message); });
  });

  refresh();
  loadOperatorLog();
  // Lightweight polling remains as reconciliation fallback; SSE is primary.
  setInterval(function () {
    if (!sseConnected) refresh();
    else if (Math.random() < 0.25) refresh(); // occasional reconcile while SSE healthy
  }, 8000);

  function connectSSE() {
    if (typeof EventSource === "undefined") return;
    var es;
    try {
      es = new EventSource("/api/events/stream");
    } catch (e) {
      return;
    }
    es.onopen = function () { sseConnected = true; };
    es.onerror = function () {
      sseConnected = false;
      try { es.close(); } catch (e2) {}
      setTimeout(connectSSE, 2500);
    };
    es.onmessage = function (ev) {
      sseConnected = true;
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      if (!msg || msg.type === "ping") return;
      if (msg.type === "status" && msg.status) {
        var s = msg.status;
        api("/api/queue").then(function (q) {
          s.queue = q.queue || [];
          window._queueTracks = s.queue;
          renderStatus(s);
          if ($("view-broadcast").classList.contains("active")) loadOperatorLog();
        }).catch(function () { renderStatus(s); });
      }
    };
  }
  connectSSE();
})();
