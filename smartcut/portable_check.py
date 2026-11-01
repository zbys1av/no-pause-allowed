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
            from .adaptive import AdaptiveAnalysis
            from .motion import MotionTracker
            import numpy as np
            adaptive=AdaptiveAnalysis(MotionTracker(95),settings,30)
            fixed=MotionTracker(95)
            for i in range(160):
                frame=np.full((100,160,3),200,np.uint8)
                if i==82:frame[40:44,50:54]=20
                if i>=110:frame[70,90]=20
                adaptive.add(i/8,frame);fixed.add(i/8,frame)
            key=lambda s:(s.time,s.score,s.active,s.transient,s.persistent,s.bounds)
            if [key(s) for s in adaptive.tracker.samples]!=[key(s) for s in fixed.samples] or adaptive.stats['certified_quiet']<40:
                raise RuntimeError('Packaged adaptive analysis differs from fixed decisions or fails to accelerate idle frames')
            # Exercise the two-pixel/2-second tail regression in frozen NumPy.
            import numpy as np
            from .motion import MotionTracker
            from .model import analyze_plan
            tracker=MotionTracker(95)
            for i in range(144):
                frame=np.full((100,160,3),200,np.uint8)
                if (i//16)%2:frame[30,40:42]=30
                tracker.add(i/8,frame)
            if not any(s.evidence=='repeated_local_cycle' for s in tracker.samples):
                raise RuntimeError('Packaged local-cycle detector failed')
            if any(s.action=='keep' for s in analyze_plan(tracker.samples,18,settings).plan):
                raise RuntimeError('Packaged detector kept the idle blinking tail')
            samples = analyze(video, settings, Job())
            from dataclasses import replace
            parallel_job=Job()
            parallel_samples=analyze(video,replace(settings,workers=2),parallel_job)
            if [key(s) for s in samples]!=[key(s) for s in parallel_samples]:
                raise RuntimeError('Packaged parallel analysis differs from serial results')
            if parallel_job.debug_analysis_performance['effective_workers']!=2:
                raise RuntimeError('Packaged parallel workers did not run')
            plan = make_plan(samples, video.duration, settings)
            target = directory / "edited output.mp4"
            export(video, plan, target, Job())
            output = probe(target)
            if not output.audio or abs(output.duration-statistics(plan)["output"]) > .15:
                raise RuntimeError("Packaged export has incorrect duration or missing audio")
            app = QApplication.instance() or QApplication([])
            app.setStyleSheet(STYLE)
            window = Window(language='uk', remember=False)
            from .icons import icon
            from . import __version__
            if icon('folder-open').isNull():
                raise RuntimeError('The portable bundle has no SVG icons')
            if window.advanced.toggle.isChecked() or window.manual.toggle.isChecked():
                raise RuntimeError('Advanced/manual controls should start collapsed')
            if window.analyze_button.text() != 'Аналізувати відео':
                raise RuntimeError('Default Ukrainian UI is unavailable')
            window.set_language('en')
            if window.analyze_button.text() != 'Analyze Video':
                raise RuntimeError('English UI is unavailable')
            window.set_language('uk')
            # A CI machine has no physical speakers. Video decoding is checked
            # here; encoded audio is checked above and by the tone-marker suite.
            window.player.setAudioOutput(None)
            window.show()
            valid_frames = []
            window.view.item.videoSink().videoFrameChanged.connect(lambda f: valid_frames.append(f.isValid()))
            window.loaded(output)
            window.analyzed(samples)
            from .debug import snapshot, write_report
            import zipfile
            debug_path=directory/'diagnostic.zip'
            write_report(snapshot(window),debug_path,Job())
            with zipfile.ZipFile(debug_path) as archive:
                if json.loads(archive.read('report.json'))['schema']!='smartcut-debug':
                    raise RuntimeError('Invalid packaged diagnostic report')
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
                          audio=True, decoded_video_frame=True, version=__version__,
                          localized_ui=True, svg_icons=True, simple_mode=True, debug_report=True, local_cycles=True,
                          adaptive_analysis=True,adaptive_certified_quiet=adaptive.stats['certified_quiet'],parallel_analysis=True)
    except Exception:
        report["error"] = traceback.format_exc()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
