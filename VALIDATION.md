# Validation — 2026-10-31

Validated in Debian 13, Python 3.12.14, external FFmpeg 7.1.5, Qt/PySide6
6.12.0, NumPy 2.5.4. Dependencies installed with `uv sync --locked --extra dev`.

* Full suite: **22 passed**, 18.72 seconds; no skipped or expected-failure tests.
* After the final duration-label/ROI GUI corrections: both GUI tests rerun,
  **2 passed**, 1.12 seconds.
* Python compilation and Git whitespace check completed successfully.
* Existing platform GitHub read access verified with `git ls-remote origin HEAD`;
  remote repository is empty. No push, reset or remote modification performed.

The GUI integration test opens generated media through the loader, executes the
background analyzer, creates an edited MP4, loads it into Qt, receives a valid
decoded video frame, checks duration display, simulates a mouse-drawn detection
rectangle and closes/cleans the preview cache. The other tests cover the six
requested motion/edit/export scenarios and additional failure/format cases.
Media fixtures and result files come from the current test run, not cached media.

Qt headless tests require local audio initialization access outside the restricted
command sandbox (`QT_QPA_PLATFORM=offscreen QT_AUDIO_BACKEND=pulseaudio`).
The application itself does not require Internet access.

Not validated here: Windows 10/11 GUI, Windows portable/installer generation,
hardware encoders, listening to physical audio, real long drawing recordings,
forced disk exhaustion or HDR. Windows packaging scripts and commands are
provided, but no Windows executable has been built or certified in Linux.
Quality and sensitivity should be checked against the user's own recordings.

Cloud configuration draft: `install_script` and `start_skill` were saved.
This confirms configuration persistence and current-instance validation only;
publication and restoration in a fresh task have not been performed.
