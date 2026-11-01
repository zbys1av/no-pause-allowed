# Repository instructions

- `main` must contain a complete runnable portable application at its root:
  SmartVideoCutter.exe, _internal/, tools/, licenses/ and the matching source.
  Never remove the bundled application or replace it with a source-only checkout.
- Version 1.10 adds expanded video and a compact, responsive UI on top of
  version 1.9. Version 1.9 extends the restored 1.8 baseline with multiple-file projects,
  per-file editing, batch analysis and merged/separate export, explicitly
  requested by the owner on 2026-11-01. RESTORATION.json is historical.
- Do not create, restore, run, or depend on GitHub Actions workflows. Do not
  maintain a separate portable branch or installer.
- End users must be able to extract main.zip and run SmartVideoCutter.exe without
  installing Python, FFmpeg, Qt, codecs or other dependencies, or downloading
  anything at runtime. Keep developer setup instructions in DEVELOPMENT.md.
- Source changes that affect the app must be accompanied by a verified rebuilt
  portable bundle before calling the downloadable app updated. Use the local
  Windows x64 build script and its tests and extracted-EXE self-test.
- Run source tests locally. Do not claim native Windows execution based on Linux
  tests; distinguish new validation from the preserved historical build reports.
