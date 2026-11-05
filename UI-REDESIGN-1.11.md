# Workspace redesign 1.11

The previous UI exposed three project/header rows, persistent statistics above
the timeline, quick segment buttons plus a second action editor, three large
confirmation buttons, and two equally prominent primary buttons. Shortcuts
Ctrl+Enter/Escape were unique; no pre-existing conflict was found.

## Primary command locations

| Command | Location |
| --- | --- |
| Add clips | Project toolbar, Ctrl+O (appends to an open project) |
| Replace project, reorder/remove, analyze all | Project menu beside file selector |
| Analyze current clip | One accented toolbar button |
| Export project/current clip | Toolbar, Ctrl+E |
| Language, file information, diagnostic ZIP | Settings menu |
| Profiles, ROI, adaptive/performance settings | Analysis context page |
| Segment boundaries and action | Segment context page, opened on selection |
| Detailed plan table | Optional fold in Segment page |
| Statistics and evidence | Results context page |
| Manual source-time range and confirmations | Range context page, opened by timeline selection |
| Preview result | Below timeline |
| Color layers, restore hidden | Timeline filter row and existing context menu |
| Zoom/pan | Slider, reset indicator, native horizontal scrollbar, Ctrl+wheel |
| Expanded video | Player button, double-click, Ctrl+Enter; Escape restores |

Segment quick buttons were removed; the action combo uses the existing
set_action command. Confirmation choices use one combo/apply command. The
existing controller and per-file states are retained. Analysis/export logic is
unchanged. A collapsed context panel gives its width back to the splitter.
Zoom changes canvas width only; original-time coordinates and plans are intact.

Shortcuts: Ctrl+O add, Ctrl+E export, Ctrl+Shift+A analyze all, Ctrl+L select
range, Ctrl+= / Ctrl+- zoom, Ctrl+0 reset zoom, Ctrl+Enter expand, Escape restore.
Collapsible details use a 120 ms nonblocking height transition; no opacity,
shadow or per-segment widget effects are used.

Validation covers 1280x720, 1366x768 and 1920x1080, coordinate invariance under
zoom, context switching, retained commands, bilingual relabeling, ROI gestures,
fullscreen and existing media tests. Offscreen Qt screenshots use a registered
system Segoe UI font because this sandbox's font database is empty. They are
layout QA, not proof of native GPU/fullscreen behavior on every monitor.
