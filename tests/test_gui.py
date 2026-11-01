from PySide6.QtWidgets import QApplication
from smartcut.app import Window, STYLE
from smartcut.model import Segment
from smartcut.video import Video
from pathlib import Path
import subprocess
import time
from smartcut.process import tool


def test_ui_plan_editing_and_invalidation():
    app = QApplication.instance() or QApplication([])
    window = Window(language='uk', remember=False)
    window.video = Video(Path("/tmp/example.mp4"), 10, 320, 180, 24, 100, "mp4", False)
    window.plan = [Segment(0, 3), Segment(3, 7, "remove"), Segment(7, 10)]
    window.automatic = list(window.plan)
    window.render_plan()
    window.populate_segment(1)
    window.action.setCurrentIndex(2)
    window.apply_edit()
    assert window.plan[1].action == "speed"
    assert "00:00:06.80" in window.stats.text()
    window.reset_plan()
    assert window.plan[1].action == "remove"
    window.samples = [(0, 0, False)]
    window.sensitivity.setValue(90)
    assert window.dirty and not window.export_button.isEnabled()
    window.close()
    app.processEvents()


def test_open_analyze_preview_worker_pipeline(tmp_path):
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    source = tmp_path/"drawing.mp4"
    subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=s=160x90:r=24:d=3", "-c:v", "libx264", str(source)], check=True)
    window = Window(language='uk', remember=False)
    errors = []
    window.show_error = errors.append
    window.show()

    def wait_until(predicate):
        deadline = time.monotonic()+20
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert predicate(), "GUI operation timed out"
        assert not errors, errors

    window.open_video(source)
    wait_until(lambda: window.worker is None)
    assert window.video.duration == 3
    window.start_analysis()
    wait_until(lambda: window.worker is None)
    assert window.plan and window.export_button.isEnabled()
    assert window.last_analysis_debug['performance']['frames']>0
    assert window.settings().adaptive
    assert 'Аналіз завершено' in window.eta_label.text()
    assert not window.eta_timer.isActive()
    window.preview_mode.setCurrentIndex(1)
    wait_until(lambda: window.worker is None)
    assert window.preview_path.is_file()
    assert window.player.source().toLocalFile() == str(window.preview_path)
    frames = []
    window.view.item.videoSink().videoFrameChanged.connect(lambda frame: frames.append(frame.isValid()))
    window.player.play()
    wait_until(lambda: any(frames))
    window.player.pause()
    assert window.player.duration() > 2000
    assert "/ 00:00:03." in window.time.text()
    screenshot = tmp_path/"interface.png"
    assert window.grab().save(str(screenshot))
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt, QPointF
    window.preview_mode.setCurrentIndex(0)
    window.detection.setCurrentIndex(1)
    a = window.view.mapFromScene(QPointF(32, 18))
    b = window.view.mapFromScene(QPointF(128, 72))
    QTest.mousePress(window.view.viewport(), Qt.LeftButton, pos=a)
    QTest.mouseMove(window.view.viewport(), b)
    QTest.mouseRelease(window.view.viewport(), Qt.LeftButton, pos=b)
    assert abs(window.area[0]-.2) < .02
    assert abs(window.area[2]-.6) < .02
    assert window.dirty
    cache = window.cache
    window.close()
    app.processEvents()
    assert not cache.exists()
