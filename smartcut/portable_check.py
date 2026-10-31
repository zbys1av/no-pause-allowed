"""Exercise the frozen program without installed Python or a system FFmpeg."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback


def check(report_path):
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False))}
    report_path = Path(report_path).absolute()
    try:
        if not report["frozen"]:
            raise RuntimeError("This check must run inside the packaged executable")
        from .process import tool, Job
        base = Path(sys.executable).parent
        # Prove that neither the runner's Python nor FFmpeg can be used by accident.
        # Keep the application's private codec DLLs; remove only external tools.
        # Qt loads its FFmpeg plugin after startup, so its own DLL directory must
        # remain discoverable even though no installed Python/FFmpeg is allowed.
        internal = base / "_internal"
        os.environ["PATH"] = os.pathsep.join(str(p) for p in (
            base / "tools", internal, internal / "PySide6", internal / "shiboken6",
            Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"))
        for key in ("SMARTCUT_FFMPEG", "SMARTCUT_FFPROBE"):
            os.environ.pop(key, None)
        for name in ("ffmpeg", "ffprobe"):
            executable = Path(tool(name))
            if executable.parent != base / "tools":
                raise RuntimeError(f"{name} was not bundled")
            report[name] = executable.name
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QUrl, QCoreApplication, QEvent
        from .app import Window, STYLE
        from .video import probe
        from .motion import analyze
        from .model import Settings, make_plan, statistics
        from .export import export
        with tempfile.TemporaryDirectory(prefix="smartcut-portable-") as directory:
            directory = Path(directory)
            source = directory / "test input.mp4"
            result = subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin",
                "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=3", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(source)], capture_output=True, timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors="replace"))
            video = probe(source)
            settings = Settings()
            samples = analyze(video, settings, Job())
            plan = make_plan(samples, video.duration, settings)
            target = directory / "edited output.mp4"
            export(video, plan, target, Job())
            output = probe(target)
            if not output.audio or abs(output.duration-statistics(plan)["output"]) > .15:
                raise RuntimeError("Packaged export has incorrect duration or missing audio")
            app = QApplication.instance() or QApplication([])
            app.setStyleSheet(STYLE)
            window = Window()
            # A CI machine has no physical speakers. Video decoding is checked
            # here; encoded audio is checked above and by the tone-marker suite.
            window.player.setAudioOutput(None)
            window.show()
            valid_frames = []
            window.view.item.videoSink().videoFrameChanged.connect(lambda f: valid_frames.append(f.isValid()))
            window.loaded(output)
            window.player.setSource(QUrl.fromLocalFile(str(target)))
            window.player.play()
            deadline = time.monotonic()+25
            while not any(valid_frames) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.02)
            window.player.stop()
            player_error = window.player.errorString()
            window.player.setSource(QUrl())
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
            if not any(valid_frames):
                raise RuntimeError("The packaged Qt player did not decode a video frame: " + player_error)
            report.update(ok=True, analysis_samples=len(samples), output_duration=output.duration,
                          audio=True, decoded_video_frame=True)
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
