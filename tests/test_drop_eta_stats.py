from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData, QUrl, Qt, QPoint, QPointF
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from smartcut.app import Window, video_drop_path
from smartcut.model import Segment
from smartcut.video import Video
from smartcut.progress import AnalysisEstimate


def test_drop_on_zone_and_player_and_reject_non_video():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.show()
    received = []
    window.open_video = received.append
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(Path('/tmp/drawing.MP4')))])
    assert video_drop_path(mime)
    window.table.show()
    for target in (window.drop_zone, window.view.viewport(), window.table.viewport()):
        enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(target, enter)
        assert enter.isAccepted()
        assert window.drop_zone.property('dragActive')
        drop = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(target, drop)
        app.processEvents()
        assert drop.isAccepted()
        assert not window.drop_zone.property('dragActive')
    assert len(received) == 3
    window.worker = object()
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(window.drop_zone, enter)
    assert not enter.isAccepted() and len(received) == 3
    window.worker = None
    mime.setUrls([QUrl('https://example.com/video.mp4')])
    assert video_drop_path(mime) is None
    mime.setUrls([QUrl.fromLocalFile('/tmp/readme.txt')])
    assert video_drop_path(mime) is None
    mime.setUrls([QUrl.fromLocalFile('/tmp/a.mp4'), QUrl.fromLocalFile('/tmp/b.mp4')])
    assert video_drop_path(mime) is None
    window.close()
    app.processEvents()


def test_raw_stats_stay_stable_when_manual_edit_keeps_idle():
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.video = Video(Path('/tmp/input.mp4'), 10, 320, 180, 24, 1, 'mp4', False)
    window.samples = [(0, 0, False), (1, 1, True), (2, 0, False), (10, 0, False)]
    window.plan = [Segment(0, 1), Segment(1, 10, 'remove')]
    window.render_plan()
    assert '10.0%' in window.motion_value.text()
    assert '90.0%' in window.still_value.text()
    detected = window.motion_value.text(), window.still_value.text()
    window.plan = [Segment(0, 10)]
    window.render_plan()
    assert detected == (window.motion_value.text(), window.still_value.text())
    assert '00:00:10.00' in window.stats.text()
    window.analysis_estimate = AnalysisEstimate(started=0)
    window.analysis_state = 'running'
    for value in (.1, .2, .3): window.analysis_estimate.update(value)
    window.update_analysis_eta()
    assert 'Приблизно залишилось' in window.eta_label.text()
    window.operation_cancelled()
    assert 'скасовано' in window.eta_label.text()
    window.close()
    app.processEvents()
