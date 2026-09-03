# Third-party notices

ASH Captions itself is proprietary (see `LICENSE`). The built application
redistributes the following third-party components, each under its own
licence. The licence texts ship with the application at the paths given.

## ffmpeg / ffprobe -- GPL v2 or later (built with `--enable-gpl --enable-version3`)

`bin/ffmpeg.exe` and `bin/ffprobe.exe` are BtbN's static Windows build of
FFmpeg (https://github.com/BtbN/FFmpeg-Builds), the `win64-gpl` variant,
chosen for libx264. The exact build is recorded in `bin/ffmpeg-build-info.txt`
and the licence text that ships in that archive is at `bin/LICENSE.txt`.

ASH Captions runs ffmpeg as a separate process and is not linked against it.
The corresponding source code for the shipped binaries is available from the
FFmpeg project (https://ffmpeg.org, https://git.ffmpeg.org/ffmpeg.git) and,
for the exact build configuration, from the BtbN repository above, which also
publishes the sources of every bundled library.

## faster-whisper -- MIT

https://github.com/SYSTRAN/faster-whisper. Copyright (c) 2023 SYSTRAN.

## CTranslate2 -- MIT

https://github.com/OpenNMT/CTranslate2. Copyright (c) 2018 SYSTRAN.
(Its Windows wheel bundles Intel OpenMP, `libiomp5md.dll`, under Intel's
simplified software licence, and NVIDIA's `cudnn64_9.dll` loader shim under
the NVIDIA cuDNN SLA.)

## onnxruntime -- MIT

https://github.com/microsoft/onnxruntime. Copyright (c) Microsoft
Corporation. Used by faster-whisper for voice-activity detection (Silero
VAD, also MIT: https://github.com/snakers4/silero-vad).

## Whisper model weights -- MIT

The bundled `models/` directory holds OpenAI Whisper weights
(https://github.com/openai/whisper, MIT, Copyright (c) 2022 OpenAI) converted
to CTranslate2 format and published by SYSTRAN at
https://huggingface.co/Systran (MIT). The exact size and source repository are
recorded in `models/model-info-<size>.txt`.

## Fonts -- SIL Open Font License 1.1 / Apache 2.0 / Ubuntu Font Licence

Every family in `assets/fonts/manifest.json` is redistributed under the
licence named in its `license` field; the full text is the file named in its
`license_file` field, under `assets/fonts/licenses/`. All families currently
bundled are under the SIL Open Font License 1.1. Font names are trademarks or
reserved font names of their respective authors, as those licences describe.

## JASSUB 1.8.8 (browser caption renderer) -- MIT, with bundled components

`src/ash_captions/web/static/vendor/jassub/` vendors JASSUB, libass compiled to
WebAssembly, so the Studio page can draw the same captions in the browser that
ffmpeg burns. JASSUB itself is MIT. Its wasm links libass (ISC), FreeType (FTL
or GPL-2.0-or-later), FriBidi (LGPL-2.1-or-later), HarfBuzz
(MIT-Modern-Variant), expat and brotli (MIT), and ships Liberation Sans (OFL
1.1) as its fallback face. The package's own licence string is compound, not
plain MIT; the full notices are in that directory's `COPYRIGHT` and
`LICENSE`, and `README.md` there records the exact version and files.

## Other Python dependencies

The remaining Python packages in the bundle (FastAPI, Starlette, uvicorn,
pydantic, watchdog, pystray, Pillow, huggingface_hub, tokenizers, numpy,
PyAV and their dependencies) are used under MIT, BSD, Apache 2.0 or
HPND/PIL licences; their licence texts are included in the bundle's
package metadata directories as PyInstaller collects them.
