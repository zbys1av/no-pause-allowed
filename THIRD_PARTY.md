# Third-party components

No third-party executables are downloaded by this application.

* Python: PSF license; include its license in binary distributions.
* PySide6 / Shiboken / Qt: LGPLv3/GPLv3/commercial terms, with module-specific
  exceptions and third-party notices. This application uses dynamically loaded Qt
  libraries. Preserve license texts and notices, provide the applicable matching
  library source or required source information, and allow library replacement.
  See https://doc.qt.io/qtforpython-6/licenses.html and the exact installed wheel.
* NumPy: BSD-3-Clause and the bundled dependency notices in its wheel.
* PyInstaller: GPL with its bootloader exception; see its distribution notices.
* Lucide icons: ISC license. The official SVG icons and license are preserved
  under `smartcut/assets/` and bundled with the portable application.
* External FFmpeg/FFprobe: the exact build's license applies. A build containing
  libx264 is normally GPL-enabled. Keep the exact binary build information,
  license, and corresponding source/source offer required by that distribution.
  See https://ffmpeg.org/legal.html. Qt also bundles its own multimedia libraries;
  retain their notices in addition to those for the external FFmpeg tools.

The build script downloads Gyan's FFmpeg 8.0.1 Essentials x64 distribution from
its official GitHub release. `scripts/ffmpeg.lock.json` pins its SHA-256 from
Microsoft WinGet. The unmodified FFmpeg/FFprobe executables, upstream GPLv3
license, original README with build configuration/source link, and provenance
manifest are included under `tools/`. The matching FFmpeg source is linked in
that upstream README: https://github.com/FFmpeg/FFmpeg/commit/894da5ca7d.
The original distributor archive is linked in `tools/licenses/provenance.json`.
Installed Qt/Python/NumPy notices are collected in `licenses/` automatically.

The portable program never downloads dependencies at startup. The source branch
contains the build recipe; the `portable-windows` branch contains the prepared
Windows distribution. No administrator installation or system codec pack is used.
