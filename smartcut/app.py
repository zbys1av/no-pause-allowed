"""Qt desktop interface. Media work runs off the event thread."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFileDialog, QSlider, QDoubleSpinBox, QComboBox, QFormLayout,
    QGroupBox, QProgressBar, QMessageBox, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QHeaderView, QSplitter, QDialog, QDialogButtonBox, QScrollArea,
)
from .model import Settings, make_plan, move_boundary, statistics
from .motion import analyze
from .video import probe
from .export import export, proxy
from .process import Job, Cancelled


def clock(value):
    value = max(0, value)
    return f"{int(value)//3600:02}:{int(value)//60%60:02}:{value%60:05.2f}"


class Worker(QThread):
    result = Signal(object)
    failure = Signal(str)
    progress = Signal(float, str)
    cancelled = Signal()

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.job = Job()
        self.operation = operation

    def run(self):
        try:
            result = self.operation(self.job, self.progress.emit)
            self.result.emit(result)
        except Cancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failure.emit(str(exc))


class VideoView(QGraphicsView):
    area_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsVideoItem()
        self.scene().addItem(self.item)
        self.outline = QGraphicsRectItem()
        self.outline.setPen(QPen(QColor("#65dcb6"), 2))
        self.outline.setBrush(QBrush(QColor(101, 220, 182, 28)))
        self.outline.setZValue(2)
        self.scene().addItem(self.outline)
        self.outline.hide()
        self.custom = False
        self.origin = None
        self.setMinimumSize(320, 200)
        self.setBackgroundBrush(QColor("#101216"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def dimensions(self, width, height):
        from PySide6.QtCore import QSizeF
        self.item.setSize(QSizeF(width, height))
        self.setSceneRect(0, 0, width, height)
        self.outline.hide()
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def point(self, event):
        from PySide6.QtCore import QPointF
        p = self.mapToScene(event.position().toPoint())
        rect = self.sceneRect()
        return QPointF(min(rect.right(), max(0, p.x())), min(rect.bottom(), max(0, p.y())))

    def mousePressEvent(self, event):
        if self.custom and event.button() == Qt.LeftButton:
            self.origin = self.point(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.outline.setRect(QRectF(self.origin, self.point(event)).normalized())
            self.outline.show()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.origin is not None:
            rect = QRectF(self.origin, self.point(event)).normalized()
            self.origin = None
            bounds = self.sceneRect()
            if rect.width()/bounds.width() >= .02 and rect.height()/bounds.height() >= .02:
                self.outline.setRect(rect)
                self.area_changed.emit((rect.x()/bounds.width(), rect.y()/bounds.height(),
                                        rect.width()/bounds.width(), rect.height()/bounds.height()))
            else:
                self.outline.hide()
        else:
            super().mouseReleaseEvent(event)


class Timeline(QWidget):
    selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.plan = []
        self.index = -1
        self.position = 0.
        self.setMinimumHeight(48)
        self.setToolTip("Click a section to seek and edit it. Green = keep, red = remove, amber = speed up.")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#252a33"))
        if not self.plan:
            painter.setPen(QColor("#929ba9"))
            painter.drawText(self.rect(), Qt.AlignCenter, "Analyze a video to see its editing plan")
            return
        duration = self.plan[-1].end
        colors = {"keep": "#56c8a2", "remove": "#df697c", "speed": "#dca95b"}
        for i, s in enumerate(self.plan):
            rect = QRectF(s.start/duration*self.width(), 5,
                          (s.end-s.start)/duration*self.width(), self.height()-10)
            painter.fillRect(rect, QColor(colors[s.action]))
            painter.setPen(QColor("#ffffff" if i == self.index else "#171a20"))
            painter.drawRect(rect)
        painter.setPen(QPen(QColor("#fff"), 2))
        x = int(self.position/duration*self.width())
        painter.drawLine(x, 0, x, self.height())

    def mousePressEvent(self, event):
        if not self.plan:
            return
        time = event.position().x()/max(1, self.width())*self.plan[-1].end
        for i, s in enumerate(self.plan):
            if s.start <= time <= s.end:
                self.selected.emit(i)
                break


class ExportDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Export MP4 · H.264")
        layout = QFormLayout(self)
        self.quality = QComboBox()
        self.quality.addItems(["High Quality · 18", "Balanced · 23", "Small File · 28"])
        self.quality.setCurrentIndex(1)
        self.audio = QComboBox()
        self.audio.addItems(["Preserve Audio", "Mute Video"])
        self.encoder = QComboBox()
        self.encoder.addItems(["CPU · libx264", "NVIDIA · NVENC", "Intel · QSV", "AMD · AMF"])
        layout.addRow("Quality", self.quality)
        layout.addRow("Audio", self.audio)
        layout.addRow("Encoder", self.encoder)
        note = QLabel("Hardware options require a supported GPU and FFmpeg build.\nQuality uses the encoder's own CQ/QP scale; results vary.\nThe source resolution is retained (odd sizes pad one pixel).\nSpeed-up audio uses chained atempo filters.")
        note.setWordWrap(True)
        layout.addRow(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Smart Video Cutter")
        self.resize(1260, 900)
        self.setAcceptDrops(True)
        self.video = None
        self.samples = []
        self.plan = []
        self.automatic = []
        self.area = (0, 0, 1, 1)
        self.worker = None
        self.closing = False
        self.dirty = False
        self.preview_path = None
        self.proxy_path = None
        self.cache = Path(tempfile.mkdtemp(prefix="smartcut-preview-"))
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.view = VideoView()
        self.player.setVideoOutput(self.view.item)
        self.player.errorOccurred.connect(self.media_error)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda _: self.position_changed(self.player.position()))
        self.player.playbackStateChanged.connect(lambda state: self.play.setText("Pause" if state == QMediaPlayer.PlayingState else "Play"))
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 18, 22, 18)
        header = QHBoxLayout()
        brand = QLabel("◈  Smart Video Cutter")
        brand.setObjectName("brand")
        header.addWidget(brand)
        header.addStretch()
        self.open_button = QPushButton("Open Video")
        self.open_button.clicked.connect(self.open_dialog)
        header.addWidget(self.open_button)
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(lambda: self.sidebar.setVisible(not self.sidebar.isVisible()))
        header.addWidget(self.settings_button)
        layout.addLayout(header)
        self.metadata = QLabel("Drop a video here · MP4, MOV, MKV, WebM, AVI · Local processing")
        self.metadata.setWordWrap(True)
        layout.addWidget(self.metadata)
        splitter = QSplitter()
        layout.addWidget(splitter, 1)
        editor = QWidget()
        center = QVBoxLayout(editor)
        center.setContentsMargins(0, 0, 12, 0)
        center.addWidget(self.view, 1)
        controls = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.clicked.connect(self.toggle_play)
        controls.addWidget(self.play)
        self.time = QLabel("00:00:00.00 / 00:00:00.00")
        controls.addWidget(self.time)
        controls.addStretch()
        self.preview_mode = QComboBox()
        self.preview_mode.addItems(["Original", "Edited"])
        self.preview_mode.currentIndexChanged.connect(self.switch_preview)
        controls.addWidget(self.preview_mode)
        self.proxy_button = QPushButton("Compatible preview")
        self.proxy_button.setToolTip("Create an H.264/AAC proxy when the original codec cannot play")
        self.proxy_button.clicked.connect(self.create_proxy)
        controls.addWidget(self.proxy_button)
        center.addLayout(controls)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 10000)
        self.seek.sliderMoved.connect(lambda v: self.player.setPosition(round(v/10000*self.player.duration())))
        center.addWidget(self.seek)
        center.addWidget(QLabel("EDITING PLAN     green · keep     red · remove     amber · speed up"))
        self.timeline = Timeline()
        self.timeline.selected.connect(self.select_segment)
        center.addWidget(self.timeline)
        self.stats = QLabel("Analyze a video to calculate real durations and reduction.")
        self.stats.setWordWrap(True)
        center.addWidget(self.stats)
        self.ratio = QProgressBar()
        self.ratio.setFormat("Active / idle")
        self.ratio.setRange(0, 1000)
        center.addWidget(self.ratio)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Start", "End", "Action"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setMaximumHeight(170)
        self.table.cellClicked.connect(lambda row, _: self.select_segment(row))
        center.addWidget(self.table)
        edit = QHBoxLayout()
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for spin in (self.start, self.end):
            spin.setDecimals(3)
            spin.setRange(0, 1e9)
            spin.setSuffix(" s")
        self.action = QComboBox()
        self.action.addItems(["Keep", "Remove", "Speed Up"])
        edit.addWidget(QLabel("Selected:"))
        edit.addWidget(self.start)
        edit.addWidget(self.end)
        edit.addWidget(self.action)
        self.apply = QPushButton("Apply")
        self.apply.clicked.connect(self.apply_edit)
        edit.addWidget(self.apply)
        self.reset = QPushButton("Reset automatic")
        self.reset.clicked.connect(self.reset_plan)
        edit.addWidget(self.reset)
        center.addLayout(edit)
        splitter.addWidget(editor)
        self.sidebar = QScrollArea()
        self.sidebar.setWidgetResizable(True)
        self.sidebar.setMinimumWidth(270)
        side = QWidget()
        settings_layout = QVBoxLayout(side)
        title = QLabel("Motion detection")
        title.setObjectName("subtitle")
        settings_layout.addWidget(title)
        self.sensitivity = QSlider(Qt.Horizontal)
        self.sensitivity.setRange(0, 100)
        self.sensitivity.setValue(80)
        self.sensitivity_label = QLabel("Motion Sensitivity · 80%")
        settings_layout.addWidget(self.sensitivity_label)
        settings_layout.addWidget(self.sensitivity)
        self.sensitivity.valueChanged.connect(self.analysis_changed)
        form = QFormLayout()
        self.minimum = self.spin(.5, 10, 1.5, .1)
        self.padding = self.spin(0, 2, .4, .1)
        self.frequency = self.spin(1, 12, 6, 1)
        form.addRow("Minimum Idle Duration", self.minimum)
        form.addRow("Motion Padding", self.padding)
        form.addRow("Analysis frames / s", self.frequency)
        self.minimum.valueChanged.connect(self.replan)
        self.padding.valueChanged.connect(self.replan)
        self.frequency.valueChanged.connect(self.analysis_changed)
        self.detection = QComboBox()
        self.detection.addItems(["Full Frame", "Custom Area"])
        self.detection.currentIndexChanged.connect(self.area_mode)
        form.addRow("Detection Area", self.detection)
        settings_layout.addLayout(form)
        self.area_label = QLabel("Select Custom Area, then drag on the original video.\nOnly detection is cropped; export stays full frame.")
        self.area_label.setWordWrap(True)
        settings_layout.addWidget(self.area_label)
        self.view.area_changed.connect(self.set_area)
        settings_layout.addSpacing(20)
        settings_layout.addWidget(QLabel("Processing"))
        self.mode = QComboBox()
        self.mode.addItems(["Remove Idle Sections", "Speed Up Idle Sections"])
        self.mode.currentIndexChanged.connect(self.replan)
        settings_layout.addWidget(self.mode)
        self.speed = QComboBox()
        self.speed.addItems(["2×", "5×", "10×", "20×"])
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self.speed_changed)
        settings_layout.addWidget(QLabel("Idle Playback Speed (also used for manual Speed Up)"))
        settings_layout.addWidget(self.speed)
        self.analyze_button = QPushButton("Analyze Video")
        self.analyze_button.setObjectName("primary")
        self.analyze_button.clicked.connect(self.start_analysis)
        settings_layout.addWidget(self.analyze_button)
        self.export_button = QPushButton("Export Video")
        self.export_button.clicked.connect(self.start_export)
        settings_layout.addWidget(self.export_button)
        self.status = QLabel("Ready to open a video")
        self.status.setWordWrap(True)
        settings_layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        settings_layout.addWidget(self.progress)
        self.cancel = QPushButton("Cancel")
        self.cancel.clicked.connect(self.cancel_job)
        settings_layout.addWidget(self.cancel)
        settings_layout.addStretch()
        note = QLabel("All processing happens on your computer.\nOriginal files are never changed.\nDetection is heuristic: inspect the timeline before exporting.")
        note.setWordWrap(True)
        settings_layout.addWidget(note)
        self.sidebar.setWidget(side)
        splitter.addWidget(self.sidebar)
        splitter.setSizes([900, 310])
        self.refresh_controls()

    @staticmethod
    def spin(low, high, value, step):
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setValue(value)
        spin.setSingleStep(step)
        spin.setDecimals(1)
        return spin

    def settings(self):
        return Settings(self.sensitivity.value(), self.minimum.value(), self.padding.value(), self.frequency.value(), self.area)

    def speed_value(self):
        return (2, 5, 10, 20)[self.speed.currentIndex()]

    def refresh_controls(self):
        busy = self.worker is not None
        loaded = self.video is not None
        for widget in (self.open_button,):
            widget.setEnabled(not busy)
        for widget in (self.sensitivity, self.minimum, self.padding, self.frequency, self.detection, self.mode, self.speed):
            widget.setEnabled(not busy)
        self.view.custom = loaded and not busy and self.detection.currentIndex() == 1 and self.preview_mode.currentIndex() == 0
        self.analyze_button.setEnabled(loaded and not busy)
        self.export_button.setEnabled(bool(self.plan) and not busy and not self.dirty)
        self.proxy_button.setEnabled(loaded and not busy and self.preview_mode.currentIndex() == 0)
        self.preview_mode.setEnabled(bool(self.plan) and not busy and not self.dirty)
        self.apply.setEnabled(bool(self.plan) and not busy and not self.dirty)
        self.reset.setEnabled(bool(self.automatic) and not busy and not self.dirty)
        self.cancel.setEnabled(busy)
        self.play.setEnabled(loaded)
        self.table.setEnabled(not busy)
        self.timeline.setEnabled(not busy)

    def work(self, operation, done, stage):
        if self.worker:
            return
        self.player.pause()
        worker = Worker(operation, self)
        self.worker = worker
        worker.result.connect(done)
        worker.failure.connect(self.show_error)
        worker.cancelled.connect(lambda: self.status.setText("Cancelled · temporary output removed"))
        worker.progress.connect(self.on_progress)
        worker.finished.connect(self.job_finished)
        self.status.setText(stage)
        self.progress.setRange(0, 0)
        self.refresh_controls()
        worker.start()

    def on_progress(self, fraction, stage):
        self.progress.setRange(0, 1000)
        self.progress.setValue(round(fraction*1000))
        self.status.setText(stage)

    def job_finished(self):
        old = self.worker
        self.worker = None
        old.deleteLater()
        self.progress.setRange(0, 1000)
        if self.preview_mode.currentIndex() == 1 and self.preview_path is None:
            self.preview_mode.setCurrentIndex(0)
        self.refresh_controls()
        if self.closing:
            self.close()

    def cancel_job(self):
        if self.worker:
            self.worker.job.cancel()
            self.status.setText("Cancelling…")

    def show_error(self, message):
        self.status.setText(message[-1500:])
        if not self.closing:
            QMessageBox.warning(self, "Smart Video Cutter", message[-3500:])

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Video", "", "Video (*.mp4 *.mov *.mkv *.webm *.avi)")
        if path:
            self.open_video(path)

    def open_video(self, path):
        if self.plan and QMessageBox.question(self, "Open another video?", "Discard the current editing plan?") != QMessageBox.Yes:
            return
        self.work(lambda job, progress: probe(path, job), self.loaded, "Reading video metadata")

    def loaded(self, video):
        self.player.stop()
        self.player.setSource(QUrl())
        # Release multimedia files before deleting cached previews (important on Windows).
        self.video = video
        self.samples, self.plan, self.automatic = [], [], []
        self.preview_path = self.proxy_path = None
        self.area = (0, 0, 1, 1)
        self.detection.setCurrentIndex(0)
        self.dirty = False
        self.timeline.index = -1
        self.view.dimensions(video.display_width, video.height)
        self.cleanup_cache()
        self.preview_mode.blockSignals(True)
        self.preview_mode.setCurrentIndex(0)
        self.preview_mode.blockSignals(False)
        self.player.setSource(QUrl.fromLocalFile(str(video.path)))
        self.metadata.setText(f"{video.path.name}  ·  {clock(video.duration)}  ·  {video.width} × {video.height}  ·  {video.fps:.2f} avg FPS\n{video.size/1024**2:.1f} MB  ·  {video.format}  ·  {'Audio' if video.audio else 'No audio'}")
        self.status.setText("Video loaded · configure detection and analyze")
        self.render_plan()

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def position_changed(self, position):
        duration = self.player.duration()
        if not self.seek.isSliderDown():
            self.seek.setValue(round(position/max(1, duration)*10000))
        self.time.setText(f"{clock(position/1000)} / {clock(duration/1000)}")
        # The plan timeline always represents source time.
        source_time = position/1000
        if self.preview_mode.currentIndex() == 1:
            cursor = 0.
            for s in self.plan:
                if s.output_duration and source_time <= cursor+s.output_duration:
                    source_time = s.start+(source_time-cursor)*(s.speed if s.action == "speed" else 1)
                    break
                cursor += s.output_duration
        self.timeline.position = source_time
        self.timeline.update()

    def media_error(self, error, message):
        if error != QMediaPlayer.NoError and self.video and not self.closing:
            self.status.setText("Preview cannot play this codec. Use Compatible preview. " + message)

    def analysis_changed(self, *_):
        self.sensitivity_label.setText(f"Motion Sensitivity · {self.sensitivity.value()}%")
        if self.samples:
            self.dirty = True
            self.invalidate_preview()
            self.status.setText("Detection settings changed · Analyze Video again; displayed plan is from the previous analysis")
        self.refresh_controls()

    def area_mode(self, index):
        if index == 0:
            self.area = (0, 0, 1, 1)
            self.view.outline.hide()
        else:
            self.preview_mode.setCurrentIndex(0)
            self.player.pause()
        self.analysis_changed()

    def set_area(self, area):
        self.area = area
        self.area_label.setText(f"Detection area: x {area[0]:.1%}, y {area[1]:.1%}, width {area[2]:.1%}, height {area[3]:.1%}\nExport remains full frame.")
        self.analysis_changed()

    def speed_changed(self, *_):
        if self.plan and not self.dirty:
            self.plan = [replace(s, speed=self.speed_value()) for s in self.plan]
            self.automatic = [replace(s, speed=self.speed_value()) for s in self.automatic]
            self.invalidate_preview()
            self.render_plan()

    def replan(self, *_):
        if self.samples and not self.dirty:
            self.automatic = make_plan(self.samples, self.video.duration, self.settings(),
                                       "remove" if self.mode.currentIndex() == 0 else "speed", self.speed_value())
            self.plan = list(self.automatic)
            self.invalidate_preview()
            self.render_plan()
            self.status.setText("Plan recalculated · manual edits reset to automatic")

    def start_analysis(self):
        settings = self.settings()
        if self.detection.currentIndex() == 1 and settings.area == (0, 0, 1, 1):
            self.show_error("Draw a detection rectangle on the original video first")
            return
        self.work(lambda job, progress: analyze(self.video, settings, job, progress), self.analyzed, "Starting motion analysis")

    def analyzed(self, samples):
        self.samples = samples
        self.dirty = False
        self.replan()
        if not any(s.action == "keep" for s in self.plan):
            self.status.setText("No motion detected. Increase sensitivity or manually Keep a section. Empty exports are blocked.")
        else:
            self.status.setText("Analysis complete · inspect the timeline before exporting")

    def render_plan(self):
        self.timeline.plan = self.plan
        self.timeline.update()
        self.table.setRowCount(len(self.plan))
        for i, s in enumerate(self.plan):
            for j, value in enumerate((clock(s.start), clock(s.end), {"keep": "Keep", "remove": "Remove", "speed": f"Speed Up {s.speed}×"}[s.action])):
                self.table.setItem(i, j, QTableWidgetItem(value))
        st = statistics(self.plan)
        self.stats.setText(f"Original {clock(st['original'])}   ·   Active {clock(st['active'])}   ·   Idle {clock(st['idle'])}\nEstimated output {clock(st['output'])}   ·   Reduction {st['reduction']:.1f}%   ·   Idle sections {st['sections']}")
        self.ratio.setValue(round(st['active']/max(.001, st['original'])*1000))
        if self.plan:
            index = min(max(0, self.timeline.index), len(self.plan)-1)
            self.populate_segment(index)
        self.refresh_controls()

    def populate_segment(self, index):
        self.timeline.index = index
        self.timeline.update()
        self.table.selectRow(index)
        s = self.plan[index]
        self.start.setValue(s.start)
        self.end.setValue(s.end)
        self.start.setEnabled(index > 0)
        self.end.setEnabled(index < len(self.plan)-1)
        self.action.setCurrentIndex({"keep": 0, "remove": 1, "speed": 2}[s.action])

    def select_segment(self, index):
        if not self.plan:
            return
        self.preview_mode.setCurrentIndex(0)
        self.populate_segment(index)
        self.player.setPosition(round(self.plan[index].start*1000))

    def apply_edit(self):
        index = self.timeline.index
        try:
            plan = move_boundary(self.plan, index, self.start.value(), self.end.value(), self.video.duration)
            plan[index] = replace(plan[index], action=("keep", "remove", "speed")[self.action.currentIndex()], speed=self.speed_value())
            self.plan = plan
            self.invalidate_preview()
            self.render_plan()
            self.status.setText("Manual edit applied · shared boundaries and statistics updated")
        except ValueError as exc:
            self.show_error(str(exc))

    def reset_plan(self):
        self.plan = list(self.automatic)
        self.invalidate_preview()
        self.render_plan()

    def invalidate_preview(self):
        self.preview_mode.setCurrentIndex(0)
        self.preview_path = None
        self.cleanup_cache()

    def cleanup_cache(self):
        for path in self.cache.glob("*.mp4"):
            if path not in (self.proxy_path, self.preview_path):
                try:
                    path.unlink()
                except OSError:
                    pass  # Qt may still be releasing a source on Windows; retry later.

    def switch_preview(self, index):
        if not self.video:
            return
        self.player.pause()
        if index == 0:
            self.view.dimensions(self.video.display_width, self.video.height)
            self.player.setSource(QUrl.fromLocalFile(str(self.proxy_path or self.video.path)))
            # Keep a selected detection rectangle when switching sources.
            if self.detection.currentIndex() == 1:
                x, y, w, h = self.area
                self.view.outline.setRect(x*self.video.display_width, y*self.video.height, w*self.video.display_width, h*self.video.height)
                self.view.outline.show()
            self.refresh_controls()
        elif self.preview_path:
            self.player.setSource(QUrl.fromLocalFile(str(self.preview_path)))
            self.view.outline.hide()
            self.refresh_controls()
        else:
            destination = self.cache / f"edited-{uuid.uuid4().hex}.mp4"
            self.work(lambda job, progress: export(self.video, list(self.plan), destination, job, progress, preview=True),
                      self.preview_ready, "Creating edited preview from the current plan")

    def preview_ready(self, path):
        self.preview_path = path
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.view.outline.hide()
        self.status.setText("Edited preview ready · same plan as export, H.264/AAC")

    def create_proxy(self):
        destination = self.cache / f"original-{uuid.uuid4().hex}.mp4"
        self.work(lambda job, progress: proxy(self.video, destination, job, progress), self.proxy_ready, "Creating compatible original preview")

    def proxy_ready(self, path):
        self.proxy_path = path
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.status.setText("Compatible preview ready · analysis and export still use the original")

    def start_export(self):
        dialog = ExportDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        suggested = str(self.video.path.with_name(self.video.path.stem+"_smartcut.mp4"))
        destination, _ = QFileDialog.getSaveFileName(self, "Export Video · choose a new file", suggested,
                          "MP4 (*.mp4)", options=QFileDialog.DontConfirmOverwrite)
        if not destination:
            return
        crf = (18, 23, 28)[dialog.quality.currentIndex()]
        mute = dialog.audio.currentIndex() == 1
        encoder = ("libx264", "h264_nvenc", "h264_qsv", "h264_amf")[dialog.encoder.currentIndex()]
        self.work(lambda job, progress: export(self.video, list(self.plan), destination, job, progress,
                                              crf=crf, mute=mute, encoder=encoder),
                  lambda path: self.status.setText(f"Export complete: {path}"), "Starting export")

    def dragEnterEvent(self, event):
        if not self.worker and event.mimeData().hasUrls() and len(event.mimeData().urls()) == 1 and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.worker:
            self.open_video(event.mimeData().urls()[0].toLocalFile())

    def closeEvent(self, event):
        if self.worker:
            self.closing = True
            self.cancel_job()
            event.ignore()
            return
        self.player.stop()
        self.player.setSource(QUrl())
        shutil.rmtree(self.cache, ignore_errors=True)
        event.accept()


STYLE = """
QWidget { background: #191c23; color: #dce2eb; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
QMainWindow { background: #191c23; }
QLabel { background: transparent; }
QLabel#brand { font-size: 22px; font-weight: 650; color: #eef4fc; padding: 6px 0; }
QLabel#subtitle { font-size: 17px; font-weight: 600; }
QPushButton, QComboBox, QDoubleSpinBox { background: #2a303b; border: 1px solid #3b4351; border-radius: 6px; padding: 8px; }
QPushButton:hover { background: #394352; }
QPushButton:disabled { color: #657080; background: #222730; }
QPushButton#primary { background: #63d7b0; color: #10271f; font-weight: 650; }
QSlider::groove:horizontal { height: 5px; background: #343c49; border-radius: 2px; }
QSlider::handle:horizontal { background: #69d9b5; width: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::sub-page:horizontal { background: #58b999; }
QProgressBar { border: 0; background: #a55163; border-radius: 3px; height: 10px; text-align: center; }
QProgressBar::chunk { background: #58b999; border-radius: 3px; }
QTableWidget { background: #20252d; gridline-color: #333c49; border: 0; selection-background-color: #34554d; }
QHeaderView::section { background: #2a303b; padding: 6px; border: 0; }
QScrollArea { border: 0; }
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Smart Video Cutter")
    app.setStyleSheet(STYLE)
    window = Window()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
