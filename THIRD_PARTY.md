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
* External FFmpeg/FFprobe: the exact build's license applies. A build containing
  libx264 is normally GPL-enabled. Keep the exact binary build information,
  license, and corresponding source/source offer required by that distribution.
  See https://ffmpeg.org/legal.html. Qt also bundles its own multimedia libraries;
  retain their notices in addition to those for the external FFmpeg tools.

The Windows packaging script copies `tools/licenses` and installed dependency
license files. Before public distribution, supply the exact FFmpeg distributor's
required notices/source information there and verify the complete distribution's
license obligations. This repository does not ship FFmpeg binaries or claim that
an arbitrary system FFmpeg build is redistributable.
