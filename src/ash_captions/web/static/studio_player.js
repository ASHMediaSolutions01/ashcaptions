/* Studio player: fits the <video> into the stage letterboxed, drives the
   transport controls, and draws the job's .ass captions over the picture
   with JASSUB (libass compiled to WebAssembly, vendored under
   /static/vendor/jassub -- see the README there). No framework. */
(function () {
  "use strict";

  const VENDOR = "/static/vendor/jassub/";
  const ICONS = {
    play: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z"/></svg>',
    pause: '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 2h4v12H3zM9 2h4v12H9z"/></svg>',
    sound:
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6h3l4-3v10l-4-3H2z"/>' +
      '<path d="M11 5.5a3.5 3.5 0 0 1 0 5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>',
    muted:
      '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2 6h3l4-3v10l-4-3H2z"/>' +
      '<path d="M10.5 6l4 4M14.5 6l-4 4" stroke="currentColor" stroke-width="1.5"/></svg>',
  };

  function formatTime(seconds) {
    const s = Math.max(0, Math.floor(seconds || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  // refs: { stage, frame, video, playBtn, muteBtn, seek, timeLabel }
  function createPlayer(refs) {
    const { stage, frame, video, playBtn, muteBtn, seek, timeLabel } = refs;
    let renderer = null;
    let scrubbing = false;
    let rafId = null;
    const timeListeners = [];

    // Letterbox: the frame takes the largest video-shaped box that fits
    // the stage, so the picture is never cropped and the caption canvas
    // (which JASSUB sizes from the video's box) lines up exactly.
    function fit() {
      const vw = video.videoWidth;
      const vh = video.videoHeight;
      if (!vw || !vh) return;
      const scale = Math.min(stage.clientWidth / vw, stage.clientHeight / vh);
      frame.style.width = `${Math.max(1, Math.floor(vw * scale))}px`;
      frame.style.height = `${Math.max(1, Math.floor(vh * scale))}px`;
    }
    window.addEventListener("resize", fit);
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(fit).observe(stage);

    function attachCaptions(assUrl, fonts) {
      if (typeof JASSUB === "undefined") {
        throw new Error("The caption renderer (JASSUB) didn't load. Reload the page.");
      }
      // Keys are lower-case face names -- the worker lower-cases each
      // style's Fontname before looking it up. Liberation Sans is the
      // renderer's own fallback for glyphs a face lacks.
      const availableFonts = { "liberation sans": VENDOR + "default.woff2" };
      for (const font of fonts || []) availableFonts[font.family.toLowerCase()] = font.url;
      renderer = new JASSUB({
        video,
        subUrl: assUrl,
        workerUrl: VENDOR + "jassub-worker.js",
        wasmUrl: VENDOR + "jassub-worker.wasm",
        modernWasmUrl: VENDOR + "jassub-worker-modern.wasm",
        availableFonts,
        fallbackFont: "liberation sans",
        useLocalFonts: false,
        prescaleFactor: 1,
      });
      renderer.addEventListener("error", (evt) => {
        console.error("JASSUB:", (evt && (evt.error || evt.message)) || evt);
      });
    }

    // Draw the current moment again on a paused video. The track arrives
    // asynchronously (JASSUB fetches and parses it in its worker), so ask
    // a few times over the next second rather than once, immediately.
    const REDRAW_DELAYS_MS = [60, 200, 500, 900];

    function redrawWhilePaused() {
      for (const delay of REDRAW_DELAYS_MS) {
        setTimeout(() => {
          if (!renderer || !video.paused) return;
          try {
            renderer.setCurrentTime(true, video.currentTime);
          } catch (err) {
            // An older renderer without that call: the captions still
            // appear as soon as the video plays. Never break the swap.
          }
        }, delay);
      }
    }

    // Resolves once the video's dimensions are known and (when asked)
    // the caption renderer is attached. `assUrl` null = no overlay (the
    // burned output already has its captions in the picture).
    function load(src, options) {
      const { assUrl, fonts } = options || {};
      return new Promise((resolve, reject) => {
        const onMeta = () => {
          fit();
          frame.hidden = false;
          syncTime();
          if (assUrl) {
            try {
              attachCaptions(assUrl, fonts);
            } catch (err) {
              reject(err);
              return;
            }
          }
          resolve();
        };
        video.addEventListener("loadedmetadata", onMeta, { once: true });
        video.addEventListener(
          "error",
          () => reject(new Error("The browser couldn't play this video file.")),
          { once: true }
        );
        video.src = src;
      });
    }

    // ---- transport ----

    function syncPlayIcon() {
      const playing = !video.paused && !video.ended;
      playBtn.innerHTML = playing ? ICONS.pause : ICONS.play;
      playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
    }
    function syncMuteIcon() {
      muteBtn.innerHTML = video.muted ? ICONS.muted : ICONS.sound;
      muteBtn.setAttribute("aria-label", video.muted ? "Unmute" : "Mute");
    }
    function syncTime() {
      const duration = video.duration || 0;
      timeLabel.textContent = `${formatTime(video.currentTime)} / ${formatTime(duration)}`;
      if (!scrubbing) seek.value = duration ? String(Math.round((video.currentTime / duration) * 1000)) : "0";
      for (const listener of timeListeners) listener(video.currentTime);
    }
    // Smooth seek-bar/transcript motion while playing; timeupdate alone
    // fires only ~4x a second.
    function tick() {
      syncTime();
      rafId = video.paused ? null : requestAnimationFrame(tick);
    }

    function toggle() {
      if (video.paused || video.ended) video.play().catch(() => {});
      else video.pause();
    }
    function seekTo(seconds) {
      video.currentTime = Math.max(0, Math.min(seconds, video.duration || seconds));
      syncTime();
    }

    playBtn.addEventListener("click", toggle);
    muteBtn.addEventListener("click", () => {
      video.muted = !video.muted;
      syncMuteIcon();
    });
    video.addEventListener("play", () => {
      syncPlayIcon();
      if (rafId === null) rafId = requestAnimationFrame(tick);
    });
    video.addEventListener("pause", syncPlayIcon);
    video.addEventListener("ended", syncPlayIcon);
    video.addEventListener("timeupdate", syncTime);
    video.addEventListener("volumechange", syncMuteIcon);
    seek.addEventListener("input", () => {
      scrubbing = true;
      const duration = video.duration || 0;
      video.currentTime = (Number(seek.value) / 1000) * duration;
      timeLabel.textContent = `${formatTime(video.currentTime)} / ${formatTime(duration)}`;
    });
    seek.addEventListener("change", () => {
      scrubbing = false;
    });
    syncPlayIcon();
    syncMuteIcon();

    return {
      load,
      toggle,
      seek: seekTo,
      onTime(listener) {
        timeListeners.push(listener);
      },
      // Swap the caption track without touching the video or playhead.
      // A paused video produces no frames, and the renderer only draws on
      // one, so the old captions would sit there until the editor pressed
      // play -- picking a look while paused looked like nothing happened.
      // Asking the renderer for this exact timestamp draws the new track
      // straight away.
      setTrack(assUrl) {
        if (!renderer) return;
        renderer.setTrackByUrl(assUrl);
        if (video.paused) redrawWhilePaused();
      },
      get currentTime() {
        return video.currentTime;
      },
      get paused() {
        return video.paused;
      },
    };
  }

  window.AshStudioPlayer = { createPlayer, formatTime };
})();
