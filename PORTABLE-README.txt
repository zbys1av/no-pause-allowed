Smart Video Cutter 1.9 — Windows 10/11 x64 Portable

This is the only application edition. All code and packaging come from main.
Copy the entire SmartVideoCutter folder and run SmartVideoCutter.exe.
The application never downloads or installs dependencies when running.

1. Extract the entire ZIP archive to a normal folder.
2. Open the extracted folder.
3. Double-click SmartVideoCutter.exe.

Python, FFmpeg, Qt, codecs and runtime DLLs are already included.
No installer, administrator account, registration or Internet connection is needed.
Keep _internal and tools next to the EXE. Do not launch from inside the ZIP.
Do not move only the EXE out of this folder.

Ukrainian is the default language. Settings switches between Ukrainian/English.
Open Video -> choose a profile -> Analyze Video -> review -> Export Video.
Select or drag one or more videos; Analyze All processes the entire project.
Export can produce one combined MP4 or separate files in list order.
Analysis shows an approximate remaining time based on its measured speed.
Three separate statistics show raw still time, qualifying continuous pauses
and actual removal after padding/manual edits. Analysis Details explains cuts
and unchanged plans. Advanced Settings and Manual Editing start collapsed.
Defaults preserve fine drawing: 95% sensitivity, 960 px detail, 8 samples/s,
minimum idle 3 seconds, 0.6 seconds padding and 0.3 seconds motion debouncing.
Brief confirmed cursor/noise bursts can be joined; lasting brush marks remain.
If a source codec cannot play, the player offers compatible playback.
The original video is never overwritten.

To remove the program, close it and delete its folder.
Preview files use the system temporary directory and are removed on normal exit.
Exported videos stay in the folder you chose.
Only the language preference is stored next to the EXE, when writable.

Source and documentation: https://github.com/zbys1av/no-pause-allowed
Third-party notices: THIRD_PARTY.md, licenses/ and tools/licenses/.

Save debug report / Зберегти звіт: save the ZIP after analysis or an error,
then send that file for diagnosis. Contains decisions, scores and errors,
not the video or audio. Technical errors and the filename may identify files.

Small regularly repeating interface blinks are recognized from matching pixel
states. Growing strokes remain protected. Set Motion Debouncing to 0 to
retain such animations. Reanalyze old videos to collect the new evidence.

Adaptive analysis is enabled by default. Every base sample is still checked;
full audits become less frequent after confirmed stillness. Any possible change
restores base precision. Advanced Settings can disable it for comparison.
Analysis Details and diagnostic reports show full-check / fast-guard counts.

Advanced > Performance > Parallel Analysis: Auto / 1 / 2 / 4 / 8.
One decoder supplies bounded pair workers; temporal decisions stay ordered.
Auto leaves short/trivial work serial and enables threads for costly checks.
Recorded scene changes no longer revoke older confirmed blinks unnecessarily.

Purple hatching marks ambiguous visual changes, while the action color remains
visible. Hover or select a segment for reasons; choose Keep/Remove/Speed manually.
Purple is a review flag, not a probability or a new export action.

Fine drawing, normal cursor movement and gradual strokes do not become purple
just because their changes are small. Review flags require an explicit conflict
or a brief local event surrounded by long stillness on both sides.
Tooltips show exactly how much of a segment needs review.

Кілька відео: виберіть файли разом або додайте через «Додати відео».
Задайте порядок кнопками ↑/↓, натисніть «Аналізувати всі».
В експорті виберіть один спільний MP4 або окремі файли.
Єдина актуальна гілка — main. Програма завжди portable.

Builds and tests run locally from main using scripts/build_windows.ps1.
Verified archives are uploaded manually to GitHub Releases; GitHub Actions is not used.
