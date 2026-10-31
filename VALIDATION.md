# Validation — Windows portable, 2026-10-31

Source environment: Debian 13, Python 3.12.14, Qt/PySide6 6.8.3 LTS,
NumPy 2.5.4, external FFmpeg 7.1.5. Locked dependency installation passed.

Full source suite: **24 passed**, 39.94 seconds, no skips or expected failures.
Coverage includes motion/static/noise/small changes, sensitivity, ROI, padding,
manual boundaries, VFR, rotation/SAR, original-file protection, mute/proxy,
cancellation, export at every idle speed, frame ordering and black-frame checks,
audio tone markers and delayed audio, GUI workers and mouse selection.
Portable tests reject checksum mismatches and preserve upstream notices.
Compilation, Git whitespace and workflow YAML checks passed.

## Actual Windows executable

Built using real Windows x64 Python 3.12.14, PyInstaller 6.22.3 and locked
Windows wheels under Wine 10 on Linux. The distribution includes Qt, Python
runtime DLLs and official Gyan FFmpeg/FFprobe 8.0.1 Essentials executables.
The FFmpeg archive SHA-256 was verified against Microsoft WinGet's manifest.
No dependencies are downloaded at application startup.

The relocated EXE's `--portable-self-test` returned:

```json
{
  "ok": true,
  "frozen": true,
  "ffmpeg": "ffmpeg.exe",
  "ffprobe": "ffprobe.exe",
  "analysis_samples": 18,
  "output_duration": 2.999,
  "audio": true,
  "decoded_video_frame": true
}
```

The EXE ran outside the checkout with only bundled DLL/tool directories and
Windows System32 on PATH. It generated real H.264/AAC media, probed it, streamed
motion analysis, exported MP4 with audio and decoded a frame in the packaged
Qt player. The headless test disconnects physical audio output; the encoded
audio track remains present and validated. A virtual display/audio sink was
used only in Wine, never added to the application or required on the user's PC.

Packaging work corrected frozen-process DLL inheritance, progress-pipe handling
and encoder lookahead. CPU H.264 uses zerolatency tuning to bound buffering;
CRF quality settings remain 18/23/28.

## Distribution and limits

The `portable-windows` branch contains the ready-to-run folder. GitHub generates
its ZIP directly, without Actions or a local build by the user. Source code
and build recipes remain on `main`. The Windows workflow also rebuilds,
extracts, tests and publishes a ZIP when Actions is available.

GitHub Actions could not run here: the existing run reports "The job was not
started because your account is locked due to a billing issue." The portable
was built locally instead; that restriction does not prevent branch downloads.

Not certified here: native Windows 10/11 hardware, physical speaker playback,
hardware encoders, long real recordings, HDR and code signing. Wine/media tests
do not replace those checks. The EXE is unsigned.
