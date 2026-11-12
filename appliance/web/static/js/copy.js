(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.PifmCopy = api;
  }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function trackCountLabel(count) {
    var n = Number(count);
    if (!Number.isFinite(n) || n < 0) n = 0;
    n = Math.floor(n);
    return n === 1 ? "1 track" : n + " tracks";
  }

  function hardwareSetupHeading(label) {
    var text = String(label == null ? "" : label).trim();
    if (!text || /not detected/i.test(text)) {
      return text || "Hardware not detected";
    }
    if (/detected$/i.test(text)) return text;
    return text + " detected";
  }

  function quietNetworkConfirmMessage(mode, seconds) {
    var secs = Number(seconds);
    if (!Number.isFinite(secs) || secs < 0) secs = 60;
    secs = Math.round(secs);
    if (mode === "timed") {
      return (
        "Quiet network for interference test?\n\n" +
        "This turns OFF the network connection for about " + secs +
        " seconds.\n" +
        "It does NOT stop the FM broadcast.\n" +
        "You may lose this web page until the network returns.\n\n" +
        "Mode: timed"
      );
    }
    return (
      "Quiet network for interference test?\n\n" +
      "This simulates a quiet network. It does NOT turn the network " +
      "connection off.\n" +
      "It does NOT stop the FM broadcast.\n\n" +
      "Mode: simulate"
    );
  }

  return {
    trackCountLabel: trackCountLabel,
    hardwareSetupHeading: hardwareSetupHeading,
    quietNetworkConfirmMessage: quietNetworkConfirmMessage
  };
}));
