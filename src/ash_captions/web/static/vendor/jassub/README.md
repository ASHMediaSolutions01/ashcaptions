# JASSUB (vendored)

libass compiled to WebAssembly, wrapped for the browser. The Studio page
(`/studio/{job_id}`, `static/studio_player.js`) uses it to draw a job's
`.ass` captions live over the `<video>` so an editor can click through
looks before burning one in. Loaded from `/static/vendor/jassub/...` only:
no CDN, no network -- editors may be offline.

* Package: `jassub` **1.8.8** (https://github.com/ThaUnknown/jassub),
  fetched with `npm pack jassub@1` and copied verbatim from its `dist/`.
  The 2.x line is an ES-module rewrite whose `dist/` imports bare
  specifiers (`abslink`, `rvfc-polyfill`) that need a bundler or import
  map to resolve -- and module workers honour neither -- so the
  self-contained 1.x UMD build is the one that runs here with no build step.
* Files (all from `dist/` unless noted):
  * `jassub.umd.js` (15 KB) -- the main-thread script; defines `window.JASSUB`.
  * `jassub-worker.js` (31 KB) -- the worker: libass glue, font loading, rendering.
  * `jassub-worker.wasm` (1.9 MB) -- libass + FreeType + HarfBuzz + FriBidi + expat + brotli, baseline WebAssembly.
  * `jassub-worker-modern.wasm` (2.0 MB) -- the same with SIMD; picked automatically when the browser supports it.
  * `default.woff2` (146 KB) -- Liberation Sans, the renderer's fallback face.
  * `COPYRIGHT` -- upstream's per-project copyright/licence listing for everything compiled into the wasm.
  * `LICENSE` -- JASSUB's own MIT licence (from the package root).
  * Not shipped: `jassub-worker.wasm.js` (3.7 MB asm.js fallback for browsers without WebAssembly -- none we support), `jassub.es.js`, source maps.

## Licence

JASSUB's own code is MIT (see `LICENSE`). The wasm binaries link a stack of
libraries under their own terms; `package.json` declares the combination as
`LGPL-2.1-or-later AND (FTL OR GPL-2.0-or-later) AND MIT AND
MIT-Modern-Variant AND ISC AND NTP AND Zlib AND BSL-1.0`, and `COPYRIGHT`
carries every notice. The notable ones: libass (ISC), FreeType (FTL or
GPL-2.0-or-later), FriBidi (LGPL-2.1-or-later), HarfBuzz (MIT-Modern-Variant),
expat (MIT), brotli (MIT), Liberation Sans in `default.woff2` (SIL OFL 1.1).
ASH Captions ships these files unmodified and loads them at runtime in the
editor's browser; it is not linked against them.
