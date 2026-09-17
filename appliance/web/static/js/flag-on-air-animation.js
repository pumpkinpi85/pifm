(function (root, factory) {
  "use strict";
  var api = factory();
  if (typeof module === "object" && module.exports) {
    module.exports = api;
  } else {
    root.PifmOnAirFlagAnimation = api;
  }
}(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Experimental ON AIR fabric animation. Static broadcast-flag-on.png remains
  // the rollback / reduced-motion / failure fallback and must not be deleted.
  var FRAME_COUNT = 100;
  var FRAME_MS = 100;
  var LOOP_MS = FRAME_COUNT * FRAME_MS;
  var BASE_PATH = "/images/broadcast-control/flag-on-air-seamless-100/";
  // Decode to ~2× dashboard flag box (162.7×108.4) so A+ memory stays viable
  // while full-resolution PNGs remain on disk for founder evaluation.
  var PLAYBACK_WIDTH = 326;
  var PLAYBACK_HEIGHT = 217;
  var PRELOAD_CONCURRENCY = 3;

  var STATIC_ON_SRC = "/images/broadcast-control/broadcast-flag-on.png?v=5";

  var frameObjectUrls = null;
  var preloadPromise = null;
  var preloadFailed = false;
  var playing = false;
  var targetImg = null;
  var rafId = 0;
  var epochStart = 0;
  var lastFrameIndex = -1;
  var staticFallbackSrc = STATIC_ON_SRC;

  function padFrame(index) {
    var n = index + 1;
    var s = String(n);
    while (s.length < 3) s = "0" + s;
    return "flag_on_" + s + ".png";
  }

  function frameUrl(index) {
    return BASE_PATH + padFrame(index);
  }

  function prefersReducedMotion() {
    try {
      return !!(window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (error) {
      return false;
    }
  }

  function revokeObjectUrls(urls) {
    if (!urls) return;
    for (var i = 0; i < urls.length; i += 1) {
      try { URL.revokeObjectURL(urls[i]); } catch (error) {}
    }
  }

  function canvasToObjectUrl(canvas) {
    return new Promise(function (resolve, reject) {
      if (canvas.toBlob) {
        canvas.toBlob(function (blob) {
          if (!blob) {
            reject(new Error("flag frame encode failed"));
            return;
          }
          resolve(URL.createObjectURL(blob));
        }, "image/png");
        return;
      }
      try {
        resolve(canvas.toDataURL("image/png"));
      } catch (error) {
        reject(error);
      }
    });
  }

  function decodeFrameToObjectUrl(blob) {
    return new Promise(function (resolve, reject) {
      var url = URL.createObjectURL(blob);
      var img = new Image();
      img.onload = function () {
        try {
          var canvas = document.createElement("canvas");
          canvas.width = PLAYBACK_WIDTH;
          canvas.height = PLAYBACK_HEIGHT;
          var ctx = canvas.getContext("2d");
          if (!ctx) {
            URL.revokeObjectURL(url);
            reject(new Error("canvas unavailable"));
            return;
          }
          ctx.drawImage(img, 0, 0, PLAYBACK_WIDTH, PLAYBACK_HEIGHT);
          URL.revokeObjectURL(url);
          canvasToObjectUrl(canvas).then(resolve, reject);
        } catch (error) {
          URL.revokeObjectURL(url);
          reject(error);
        }
      };
      img.onerror = function () {
        URL.revokeObjectURL(url);
        reject(new Error("flag frame decode failed"));
      };
      img.src = url;
    });
  }

  function fetchFrame(index) {
    return fetch(frameUrl(index), { credentials: "same-origin", cache: "force-cache" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("flag frame HTTP " + response.status);
        }
        return response.blob();
      })
      .then(decodeFrameToObjectUrl);
  }

  function preloadAll() {
    if (frameObjectUrls && frameObjectUrls.length === FRAME_COUNT) {
      return Promise.resolve(frameObjectUrls);
    }
    if (preloadFailed) {
      return Promise.reject(new Error("flag animation preload previously failed"));
    }
    if (preloadPromise) return preloadPromise;

    preloadPromise = new Promise(function (resolve, reject) {
      var urls = new Array(FRAME_COUNT);
      var next = 0;
      var active = 0;
      var failed = false;

      function pump() {
        if (failed) return;
        while (active < PRELOAD_CONCURRENCY && next < FRAME_COUNT) {
          (function (index) {
            active += 1;
            next += 1;
            fetchFrame(index).then(function (objectUrl) {
              urls[index] = objectUrl;
              active -= 1;
              if (next >= FRAME_COUNT && active === 0) {
                frameObjectUrls = urls;
                resolve(urls);
                return;
              }
              pump();
            }).catch(function (error) {
              failed = true;
              preloadFailed = true;
              preloadPromise = null;
              revokeObjectUrls(urls);
              reject(error);
            });
          }(next));
        }
      }

      pump();
    });

    return preloadPromise;
  }

  function showStatic(img) {
    if (!img) return;
    img.src = staticFallbackSrc || STATIC_ON_SRC;
  }

  function applyFrame(img, index) {
    if (!img || !frameObjectUrls || !frameObjectUrls[index]) return;
    if (img.src === frameObjectUrls[index]) return;
    img.src = frameObjectUrls[index];
  }

  function stopRaf() {
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
  }

  function tick(now) {
    if (!playing || !targetImg || !frameObjectUrls) {
      rafId = 0;
      return;
    }
    var elapsed = now - epochStart;
    if (elapsed < 0) elapsed = 0;
    var index = Math.floor(elapsed / FRAME_MS) % FRAME_COUNT;
    if (index !== lastFrameIndex) {
      applyFrame(targetImg, index);
      lastFrameIndex = index;
    }
    rafId = requestAnimationFrame(tick);
  }

  function startPlayback(img) {
    targetImg = img;
    playing = true;
    epochStart = performance.now();
    lastFrameIndex = -1;
    stopRaf();
    applyFrame(img, 0);
    rafId = requestAnimationFrame(tick);
  }

  function stop() {
    playing = false;
    stopRaf();
    lastFrameIndex = -1;
    if (targetImg) showStatic(targetImg);
    targetImg = null;
  }

  function start(img) {
    if (!img) return;
    if (!staticFallbackSrc && img.getAttribute("src")) {
      staticFallbackSrc = img.getAttribute("src");
    }
    if (prefersReducedMotion()) {
      stop();
      showStatic(img);
      return;
    }
    if (preloadFailed) {
      stop();
      showStatic(img);
      return;
    }
    targetImg = img;
    showStatic(img);
    preloadAll().then(function () {
      if (targetImg !== img) return;
      if (prefersReducedMotion()) {
        showStatic(img);
        return;
      }
      startPlayback(img);
    }).catch(function () {
      preloadFailed = true;
      if (targetImg === img) showStatic(img);
    });
  }

  return {
    FRAME_COUNT: FRAME_COUNT,
    FRAME_MS: FRAME_MS,
    LOOP_MS: LOOP_MS,
    BASE_PATH: BASE_PATH,
    STATIC_ON_SRC: STATIC_ON_SRC,
    frameUrl: frameUrl,
    prefersReducedMotion: prefersReducedMotion,
    preload: preloadAll,
    start: start,
    stop: stop,
    isPlaying: function () { return playing; },
    preloadFailed: function () { return preloadFailed; },
    // Test / measurement hooks — decorative only.
    _forceStartForMeasurement: start,
    _getLoadedFrameCount: function () {
      return frameObjectUrls ? frameObjectUrls.length : 0;
    }
  };
}));
