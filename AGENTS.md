# Repository instructions

- Do not create, restore, run, or depend on GitHub Actions workflows. The owner
  explicitly requires builds, tests, and releases without GitHub Actions.
- `main` is the single source for the portable application. Do not maintain a
  second application version or update the archived `portable-windows` branch.
- Run tests locally. Build the Windows portable package using
  `scripts/build_windows.ps1` on Windows x64; it runs the tests and validates the
  extracted EXE before reporting success.
- Publish verified build outputs manually through GitHub Releases. Never describe
  the archived portable ZIP as the current multi-video build or claim a Windows
  build passed based only on Linux tests.
