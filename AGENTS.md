# Repository instructions

- Do not create, restore, run, or depend on GitHub Actions workflows. The owner
  explicitly requires builds, tests, and releases without GitHub Actions.
- `main` is the single source for the application, which is always portable.
  Do not maintain a separate portable branch, installer, or application version.
  Shipped builds must include Python, Qt, NumPy, FFmpeg and FFprobe, run offline,
  and never install or download dependencies at launch. Keep developer setup
  instructions in DEVELOPMENT.md, separate from end-user launch instructions.
- Run tests locally. Build the Windows portable package using
  `scripts/build_windows.ps1` on Windows x64; it runs the tests and validates the
  extracted EXE before reporting success.
- Publish verified build outputs from `main` manually through GitHub Releases.
  Never claim a Windows build passed based only on Linux tests.
