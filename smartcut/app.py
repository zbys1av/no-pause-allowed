"""Four-step desktop controller. Detection, planning and encoding stay independent."""
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QComboBox,QSlider,QDoubleSpinBox,QProgressBar,QScrollArea,QSplitter,
    QTableWidget,QTableWidgetItem,QAbstractItemView,QHeaderView,QFileDialog,QMessageBox,
    QDialog,QFormLayout,QFrame)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from . import __version__
from .model import Settings,analyze_plan,move_boundary,statistics,detection_statistics
from .motion import analyze
from .video import probe
from .export import export,proxy
from .progress import AnalysisEstimate
from .profiles import PROFILES
from .state import State,Workflow
from .i18n import tr,Text,install_qt_language
from .errors import AppError,user_error
from .preferences import read_language,save_language
from .icons import icon
from .worker import Worker
from .widgets import VideoView,Timeline,VideoDropFilter,video_drop_path,Fold,SettingRow,StatCard,MotionGraph,clock


class ExportDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);t=parent.t
        self.setWindowTitle(t('export.title'));layout=QFormLayout(self)
        self.quality=QComboBox();self.quality.addItems([t('quality.high'),t('quality.balanced'),t('quality.small')]);self.quality.setCurrentIndex(1)
        self.audio=QComboBox();self.audio.addItems([t('audio.keep'),t('audio.mute')])
        self.encoder=QComboBox();self.encoder.addItems([t('encoder.cpu'),'NVIDIA · NVENC','Intel · QSV','AMD · AMF'])
        layout.addRow(t('quality'),self.quality);layout.addRow(t('audio'),self.audio);layout.addRow(t('encoder'),self.encoder)
        note=QLabel(t('encoder.note'));note.setWordWrap(True);layout.addRow(note)
        buttons=QHBoxLayout();cancel=QPushButton(t('cancel'));save=QPushButton(t('save'));save.setObjectName('primary')
        cancel.clicked.connect(self.reject);save.clicked.connect(self.accept);buttons.addWidget(cancel);buttons.addWidget(save);layout.addRow(buttons)


class Window(QMainWindow):
    def __init__(self,language=None,remember=True):
        super().__init__();self.language=language or read_language();self.remember=remember
        self.setWindowTitle(f'Smart Video Cutter · {__version__}');self.resize(1280,920)
        self.video=None;self.samples=[];self.plan=[];self.automatic=[];self.report=None
        self.area=(0,0,1,1);self.worker=None;self.closing=False;self.dirty=False;self.manual_changed=False;self.plan_reset_notice=False
        self.capture_settings=None;self.capture_area_mode=0;self.pending_settings=None;self.pending_area_mode=0
        self.preview_path=self.proxy_path=None;self.last_export_path='';self.current_stage=None
        self.workflow=Workflow();self.analysis_estimate=None;self.last_analysis_elapsed=None;self.cancelling=False
        self._updating=False;self.bindings=[];self.rows={}
        self.cache=Path(tempfile.mkdtemp(prefix='smartcut-preview-'))
        self.player=QMediaPlayer(self);self.audio_output=QAudioOutput(self);self.player.setAudioOutput(self.audio_output)
        self.view=VideoView();self.player.setVideoOutput(self.view.item)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda _:self.position_changed(self.player.position()))
        self.player.playbackStateChanged.connect(lambda _:self.render_play())
        self.player.errorOccurred.connect(self.media_error)
        self.build_ui()
        self.eta_timer=QTimer(self);self.eta_timer.setInterval(500);self.eta_timer.timeout.connect(self.render_eta)
        self.drop_filter=VideoDropFilter(self)
        for widget in [self,*self.findChildren(QWidget)]:
            widget.setAcceptDrops(True);widget.installEventFilter(self.drop_filter)
        self.apply_profile(0);self.retranslate();self.render()

    def t(self,key,**params):return tr(self.language,key,**params)

    def text(self,key,object_name=None):
        label=QLabel();label.setWordWrap(True)
        if object_name:label.setObjectName(object_name)
        self.bindings.append((label,'setText',key));return label

    def button(self,key,slot,glyph=None,primary=False):
        button=QPushButton();self.bindings.append((button,'setText',key));button.clicked.connect(slot)
        if glyph:button.setIcon(icon(glyph,'#123025' if primary else '#c7d2e3'))
        if primary:button.setObjectName('primary')
        return button

    @staticmethod
    def spin(low,high,value,step=.1):
        box=QDoubleSpinBox();box.setRange(low,high);box.setValue(value);box.setSingleStep(step);box.setDecimals(1);return box

    def build_ui(self):
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root);layout.setContentsMargins(22,16,22,16);layout.setSpacing(12)
        header=QHBoxLayout();brand=QLabel('Smart Video Cutter');brand.setObjectName('brand');header.addWidget(brand);header.addStretch()
        self.open_button=self.button('open',self.open_dialog,'folder-open');header.addWidget(self.open_button)
        self.settings_button=self.button('settings',self.settings_dialog,'settings');header.addWidget(self.settings_button);layout.addLayout(header)
        steps=QHBoxLayout();self.steps=[]
        for key in ('step.video','step.settings','step.analyze','step.review'):
            label=self.text(key,'step');label.setAlignment(Qt.AlignCenter);steps.addWidget(label,1);self.steps.append(label)
        layout.addLayout(steps)
        meta=QHBoxLayout();self.metadata=QLabel();self.metadata.setWordWrap(True);meta.addWidget(self.metadata,1)
        self.file_details_button=self.button('file.details',self.file_details,'film');meta.addWidget(self.file_details_button);layout.addLayout(meta)
        self.splitter=QSplitter();layout.addWidget(self.splitter,1)
        center=QWidget();editor=QVBoxLayout(center);editor.setContentsMargins(0,0,14,0);editor.setSpacing(9)
        self.drop_zone=self.button('drop',self.open_dialog,'upload');self.drop_zone.setObjectName('dropZone');self.drop_zone.setMinimumHeight(110);editor.addWidget(self.drop_zone)
        editor.addWidget(self.view,3)
        self.player_controls=QWidget();controls=QHBoxLayout(self.player_controls);controls.setContentsMargins(0,0,0,0)
        self.play=self.button('play',self.toggle_play,'play');controls.addWidget(self.play)
        self.time=QLabel('00:00:00.00 / 00:00:00.00');controls.addWidget(self.time);controls.addStretch()
        self.preview_mode=QComboBox();self.preview_mode.currentIndexChanged.connect(self.switch_preview);controls.addWidget(self.preview_mode);editor.addWidget(self.player_controls)
        self.seek=QSlider(Qt.Horizontal);self.seek.setRange(0,10000);self.seek.sliderMoved.connect(lambda v:self.player.setPosition(round(v/10000*self.player.duration())));editor.addWidget(self.seek)
        self.codec_notice=QWidget();notice=QVBoxLayout(self.codec_notice);notice.setContentsMargins(0,0,0,0)
        notice.addWidget(self.text('codec.offer','muted'));self.proxy_button=self.button('codec.prepare',self.create_proxy,'film');notice.addWidget(self.proxy_button);self.codec_notice.hide();editor.addWidget(self.codec_notice)
        self.results_scroll=QScrollArea();self.results_scroll.setWidgetResizable(True);self.results_scroll.setFrameShape(QFrame.NoFrame);self.results_scroll.setMaximumHeight(370)
        self.results=QWidget();result=QVBoxLayout(self.results);result.setContentsMargins(0,4,4,4);result.setSpacing(10)
        result.addWidget(self.text('results','sectionTitle'));self.stats=QLabel();self.stats.setWordWrap(True);result.addWidget(self.stats)
        cards=QHBoxLayout();self.raw_card=StatCard('metric.raw','metric.raw_help');self.pause_card=StatCard('metric.pauses','metric.pause_help');self.removed_card=StatCard('metric.removed','metric.removed_help')
        for card in (self.raw_card,self.pause_card,self.removed_card):cards.addWidget(card,1)
        self.still_value=self.raw_card.value;result.addLayout(cards)
        self.result_message=QLabel();self.result_message.setWordWrap(True);self.result_message.setObjectName('resultMessage');result.addWidget(self.result_message)
        self.suggestions=QWidget();suggest=QHBoxLayout(self.suggestions);suggest.setContentsMargins(0,0,0,0)
        self.suggest_minimum=self.button('suggest.minimum',lambda:self.minimum.setValue(max(.5,self.minimum.value()-.5)))
        self.suggest_sensitivity=self.button('suggest.sensitivity',lambda:self.sensitivity.setValue(max(0,self.sensitivity.value()-10)))
        self.suggest_debounce=self.button('suggest.debounce',lambda:self.debounce.setValue(min(2,self.debounce.value()+.2)))
        for b in (self.suggest_minimum,self.suggest_sensitivity,self.suggest_debounce):suggest.addWidget(b)
        result.addWidget(self.suggestions)
        self.timeline_legend=self.text('legend','muted');self.timeline=Timeline();self.timeline.selected.connect(self.select_segment)
        self.selected_box=QWidget();selected=QHBoxLayout(self.selected_box);selected.setContentsMargins(0,0,0,0)
        self.selected_label=QLabel();selected.addWidget(self.selected_label,1)
        for key,action in (('keep','keep'),('remove','remove'),('speed_up','speed')):
            selected.addWidget(self.button(key,lambda checked=False,a=action:self.set_action(a)))
        self.selected_box.hide()
        self.manual=Fold('manual');self.table=QTableWidget(0,3);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setMaximumHeight(150)
        self.table.cellClicked.connect(lambda row,_:self.select_segment(row));self.manual.content.addWidget(self.table)
        edit=QHBoxLayout();self.start=QDoubleSpinBox();self.end=QDoubleSpinBox()
        for spin in (self.start,self.end):spin.setRange(0,1e9);spin.setDecimals(3);edit.addWidget(spin)
        self.action=QComboBox();edit.addWidget(self.action);self.apply=self.button('apply',self.apply_edit);edit.addWidget(self.apply);self.manual.content.addLayout(edit)
        self.reset=self.button('reset.plan',self.reset_plan,'rotate-ccw');self.manual.content.addWidget(self.reset);result.addWidget(self.manual)
        self.analysis_details=Fold('details');self.motion_value=QLabel();self.motion_value.setWordWrap(True);self.analysis_details.content.addWidget(self.motion_value)
        self.details_text=QLabel();self.details_text.setWordWrap(True);self.analysis_details.content.addWidget(self.details_text)
        self.top_pauses=QLabel();self.top_pauses.setWordWrap(True);self.analysis_details.content.addWidget(self.top_pauses)
        self.analysis_details.content.addWidget(self.text('details.graph','muted'));self.graph=MotionGraph();self.analysis_details.content.addWidget(self.graph);result.addWidget(self.analysis_details)
        self.results_scroll.setWidget(self.results);editor.addWidget(self.results_scroll,3)
        editor.addWidget(self.timeline_legend);editor.addWidget(self.timeline);editor.addWidget(self.selected_box)
        self.review_actions=QWidget();review=QHBoxLayout(self.review_actions);review.setContentsMargins(0,0,0,0)
        self.preview_button=self.button('preview',self.preview_result,'play');self.export_button=self.button('export',self.start_export,'download',True)
        review.addStretch();review.addWidget(self.preview_button);review.addWidget(self.export_button);editor.addWidget(self.review_actions)
        self.splitter.addWidget(center)
        self.sidebar=QWidget();self.sidebar.setMinimumWidth(320);side=QVBoxLayout(self.sidebar);side.setContentsMargins(10,0,0,0)
        self.settings_scroll=QScrollArea();self.settings_scroll.setWidgetResizable(True);self.settings_scroll.setFrameShape(QFrame.NoFrame);self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        settings=QWidget();settings_layout=QVBoxLayout(settings);settings_layout.setContentsMargins(0,0,7,0)
        settings_layout.addWidget(self.text('profile','sectionTitle'));self.profile=QComboBox();settings_layout.addWidget(self.profile)
        self.profile_note=self.text('profile.note','muted');settings_layout.addWidget(self.profile_note)
        self.sensitivity=QSlider(Qt.Horizontal);self.sensitivity.setRange(0,100)
        sensitivity_widget=QWidget();sl=QVBoxLayout(sensitivity_widget);sl.setContentsMargins(0,0,0,0);sl.addWidget(self.sensitivity);sl.addWidget(self.text('sensitivity.scale','muted'))
        self.minimum=self.spin(.5,10,3);self.mode=QComboBox()
        self.add_setting(settings_layout,'sensitivity',sensitivity_widget,lambda:self.sensitivity.setValue(int(self.defaults().sensitivity)))
        self.add_setting(settings_layout,'minimum',self.minimum,lambda:self.minimum.setValue(self.defaults().min_idle))
        self.add_setting(settings_layout,'mode',self.mode,lambda:self.mode.setCurrentIndex(0))
        self.advanced=Fold('advanced');settings_layout.addWidget(self.advanced)
        self.padding=self.spin(0,2,.6);self.debounce=self.spin(0,2,.3);self.frequency=self.spin(1,12,8,1)
        self.detail=QComboBox();self.detail.addItems(['320 px','640 px','960 px','1280 px']);self.detection=QComboBox();self.speed=QComboBox();self.speed.addItems(['2×','5×','10×','20×']);self.speed.setCurrentIndex(1)
        for key,widget,reset in (
            ('padding',self.padding,lambda:self.padding.setValue(self.defaults().padding)),
            ('debounce',self.debounce,lambda:self.debounce.setValue(self.defaults().debounce)),
            ('fps',self.frequency,lambda:self.frequency.setValue(self.defaults().sample_fps)),
            ('detail',self.detail,lambda:self.detail.setCurrentIndex((320,640,960,1280).index(self.defaults().analysis_width))),
            ('area',self.detection,lambda:self.reset_area()),('speed',self.speed,lambda:self.speed.setCurrentIndex(1))):self.add_setting(self.advanced.content,key,widget,reset)
        self.area_label=QLabel();self.area_label.setWordWrap(True);self.advanced.content.addWidget(self.area_label)
        settings_layout.addStretch();self.settings_scroll.setWidget(settings);side.addWidget(self.settings_scroll,1)
        self.analyze_button=self.button('analyze',self.start_analysis,'activity',True);side.addWidget(self.analyze_button);side.addWidget(self.text('privacy','muted'))
        self.splitter.addWidget(self.sidebar);self.splitter.setSizes([900,340])
        footer=QHBoxLayout();labels=QVBoxLayout();self.phase_label=QLabel();self.phase_label.setWordWrap(True);self.status=self.phase_label;self.eta_label=QLabel();self.eta_label.setObjectName('muted');labels.addWidget(self.phase_label);labels.addWidget(self.eta_label)
        footer.addLayout(labels,3);self.progress=QProgressBar();self.progress.setRange(0,1000);footer.addWidget(self.progress,2)
        self.cancel=self.button('cancel',self.cancel_job,'x');footer.addWidget(self.cancel);layout.addLayout(footer)
        self.sensitivity_label=self.rows['sensitivity'].name
        self.profile.currentIndexChanged.connect(self.apply_profile)
        for widget in (self.sensitivity,self.frequency,self.detail):
            (widget.valueChanged if hasattr(widget,'valueChanged') else widget.currentIndexChanged).connect(self.analysis_changed)
        for widget in (self.minimum,self.padding,self.debounce):widget.valueChanged.connect(self.temporal_changed)
        self.mode.currentIndexChanged.connect(self.temporal_changed);self.speed.currentIndexChanged.connect(self.speed_changed)
        self.detection.currentIndexChanged.connect(self.area_mode);self.view.area_changed.connect(self.set_area)

    def add_setting(self,layout,key,widget,reset):
        row=SettingRow(key,widget,'',reset,self.language);self.rows[key]=row;layout.addWidget(row)

    def defaults(self):return PROFILES[('drawing','screen','general')[max(0,self.profile.currentIndex())]]

    def settings(self):
        return Settings(self.sensitivity.value(),self.minimum.value(),self.padding.value(),self.frequency.value(),self.area,
                        (320,640,960,1280)[self.detail.currentIndex()],self.debounce.value())

    def speed_value(self):return (2,5,10,20)[self.speed.currentIndex()]

    @staticmethod
    def signature(settings,area_mode):return (settings.sensitivity,settings.sample_fps,settings.analysis_width,settings.area,area_mode)

    def apply_profile(self,index):
        if self.worker:return
        self._updating=True;settings=PROFILES[('drawing','screen','general')[max(0,index)]]
        self.sensitivity.setValue(int(settings.sensitivity));self.minimum.setValue(settings.min_idle);self.padding.setValue(settings.padding)
        self.debounce.setValue(settings.debounce);self.frequency.setValue(settings.sample_fps);self.detail.setCurrentIndex((320,640,960,1280).index(settings.analysis_width))
        self.mode.setCurrentIndex(0);self.speed.setCurrentIndex(1)
        self.area=(0,0,1,1);self.detection.setCurrentIndex(0);self.view.outline.hide();self._updating=False
        self.update_defaults();self.analysis_changed()

    def update_defaults(self):
        d=self.defaults()
        for key,value in {'sensitivity':f'{d.sensitivity:g}%', 'minimum':f'{d.min_idle:g} '+self.t('unit.seconds'), 'mode':Text('mode.remove'),
            'padding':f'{d.padding:g} '+self.t('unit.seconds'),'debounce':f'{d.debounce:g} '+self.t('unit.seconds'),'fps':f'{d.sample_fps:g}','detail':f'{d.analysis_width} px',
            'area':Text('area.full'),'speed':'5×'}.items():self.rows[key].default=value;self.rows[key].translate(self.language)

    def analysis_changed(self,*_):
        if self._updating:return
        if self.samples:
            self.dirty=self.capture_settings is None or self.signature(self.settings(),self.detection.currentIndex())!=self.signature(self.capture_settings,self.capture_area_mode)
            if not self.dirty:self.replan()
        self.invalidate_preview();self.render()

    def temporal_changed(self,*_):
        if self._updating:return
        if self.samples and not self.dirty:self.replan()
        self.invalidate_preview();self.render()

    def replan(self,*_):
        if not self.samples or self.dirty:return
        if self.manual_changed:self.plan_reset_notice=True
        self.report=analyze_plan(self.samples,self.video.duration,self.settings(),'remove' if self.mode.currentIndex()==0 else 'speed',self.speed_value())
        self.automatic=list(self.report.plan);self.plan=list(self.automatic);self.manual_changed=False
        self.invalidate_preview();self.render_plan()

    def speed_changed(self,*_):
        if self._updating:return
        self.plan=[replace(s,speed=self.speed_value()) for s in self.plan];self.automatic=[replace(s,speed=self.speed_value()) for s in self.automatic]
        self.invalidate_preview();self.render_plan()

    def reset_area(self):
        self.area=(0,0,1,1);self.detection.setCurrentIndex(0);self.view.outline.hide();self.analysis_changed()

    def area_mode(self,index):
        if self._updating:return
        if index==0:self.area=(0,0,1,1);self.view.outline.hide()
        else:self.preview_mode.setCurrentIndex(0);self.player.pause()
        self.analysis_changed()

    def set_area(self,area):self.area=area;self.analysis_changed()

    def retranslate(self):
        install_qt_language(self.language)
        for widget,method,key in self.bindings:getattr(widget,method)(self.t(key))
        for combo,keys in ((self.profile,('profile.drawing','profile.screen','profile.general')),
            (self.mode,('mode.remove','mode.speed')),(self.detection,('area.full','area.custom')),
            (self.preview_mode,('original','edited')),(self.action,('keep','remove','speed_up'))):
            index=combo.currentIndex();combo.blockSignals(True)
            if combo.count()!=len(keys):combo.clear();combo.addItems([self.t(key) for key in keys]);combo.setCurrentIndex(max(0,index))
            else:
                for i,key in enumerate(keys):combo.setItemText(i,self.t(key))
            combo.blockSignals(False)
        for fold in (self.advanced,self.manual,self.analysis_details):fold.translate(self.language)
        for card in (self.raw_card,self.pause_card,self.removed_card):card.translate(self.language)
        for spin in (self.minimum,self.padding,self.debounce,self.start,self.end):spin.setSuffix(' '+self.t('unit.seconds'))
        self.table.setHorizontalHeaderLabels([self.t('start'),self.t('end'),self.t('action')]);self.timeline.language=self.language
        self.update_defaults();self.render_plan();self.render_play();self.render()

    def set_language(self,language):
        self.language=language
        if self.remember:save_language(language)
        self.retranslate()

    def settings_dialog(self):
        dialog=QDialog(self);dialog.setWindowTitle(self.t('settings'));form=QFormLayout(dialog)
        label=QLabel(self.t('language'));combo=QComboBox();combo.addItems(['Українська','English']);combo.setCurrentIndex(0 if self.language=='uk' else 1);form.addRow(label,combo)
        close=QPushButton(self.t('close'));close.clicked.connect(dialog.accept);form.addRow(close)
        def change(index):
            self.set_language('uk' if index==0 else 'en');dialog.setWindowTitle(self.t('settings'));label.setText(self.t('language'));close.setText(self.t('close'))
        combo.currentIndexChanged.connect(change);dialog.exec()

    def question(self,title,message):
        box=QMessageBox(self);box.setWindowTitle(self.t(title));box.setText(self.t(message));box.setIcon(QMessageBox.Question)
        yes=box.addButton(self.t('yes'),QMessageBox.YesRole);box.addButton(self.t('no'),QMessageBox.NoRole);box.exec();return box.clickedButton()==yes

    def file_details(self):
        if not self.video:return
        v=self.video;box=QMessageBox(self);box.setWindowTitle(self.t('file.details'))
        box.setText(self.t('file.info',name=v.path.name,duration=clock(v.duration),width=v.width,height=v.height,fps=f'{v.fps:.2f}',size=f'{v.size/1024**2:.1f}',format=v.format,audio=self.t('file.audio' if v.audio else 'file.silent')))
        box.addButton(self.t('close'),QMessageBox.AcceptRole);box.exec()

    def open_dialog(self):
        path,_=QFileDialog.getOpenFileName(self,self.t('open'),'',self.t('file.filter'),options=QFileDialog.DontUseNativeDialog)
        if path:self.open_video(path)

    def open_video(self,path):
        if self.worker or self.closing:return
        if self.plan and not self.question('discard.title','discard'):return
        self.work(lambda job,p:probe(path,job),self.loaded,Text('state.loading'),'loading')

    def loaded(self,video):
        self.player.stop();self.player.setSource(QUrl());self.video=video
        self.samples=[];self.plan=[];self.automatic=[];self.report=None;self.capture_settings=None;self.dirty=False;self.manual_changed=False;self.plan_reset_notice=False
        self.selected_box.hide();self.graph.set_samples([],video.duration)
        self.preview_path=self.proxy_path=None;self.timeline.index=-1;self._updating=True
        self.area=(0,0,1,1);self.detection.setCurrentIndex(0);self._updating=False;self.view.dimensions(video.display_width,video.height)
        self.cleanup_cache();self.preview_mode.blockSignals(True);self.preview_mode.setCurrentIndex(0);self.preview_mode.blockSignals(False)
        self.codec_notice.hide();self.player.setSource(QUrl.fromLocalFile(str(video.path)))
        self.workflow.state=State.READY;self.last_analysis_elapsed=None;self.analysis_estimate=None;self.render_plan();self.render()

    def work(self,operation,done,stage,kind='analysis',analysis=None):
        if self.worker:return
        if analysis is True:kind='analysis'
        self.player.pause();generation=self.workflow.begin(kind);self.current_stage=stage;self.cancelling=False
        if kind=='analysis':self.analysis_estimate=AnalysisEstimate();self.eta_timer.start()
        worker=Worker(operation,self);self.worker=worker
        worker.result.connect(lambda result:self.operation_result(generation,result,done))
        worker.failure.connect(lambda error:self.operation_failed(generation,error))
        worker.cancelled.connect(lambda:self.operation_cancelled(generation))
        worker.progress.connect(lambda f,s:self.on_progress(f,s,generation))
        worker.finished.connect(lambda:self.job_finished(generation));self.render();worker.start()

    def operation_result(self,generation,result,done):
        if generation!=self.workflow.generation:return
        self.workflow.finish('complete');self.current_stage=None;self.eta_timer.stop();done(result);self.render()

    def operation_failed(self,generation,error):
        if generation!=self.workflow.generation:return
        self.workflow.finish('failed');self.current_stage=None;self.eta_timer.stop();self.render();self.show_error(error)

    def operation_cancelled(self,generation=None):
        if generation is not None and generation!=self.workflow.generation:return
        self.workflow.finish('cancelled');self.current_stage=None;self.eta_timer.stop();self.render()

    def on_progress(self,fraction,stage,generation=None):
        generation=self.workflow.generation if generation is None else generation
        if not self.workflow.update(generation,fraction):return
        if isinstance(stage,Text):self.current_stage=stage
        if self.workflow.kind=='analysis':self.analysis_estimate.update(fraction)
        self.render_operation()

    def job_finished(self,generation=None):
        if generation is not None and generation!=self.workflow.generation:return
        old=self.worker;self.worker=None
        if old:old.deleteLater()
        self.eta_timer.stop();self.cancelling=False
        if self.preview_mode.currentIndex()==1 and self.preview_path is None:self.preview_mode.setCurrentIndex(0)
        self.render()
        if self.closing:self.close()

    def cancel_job(self):
        if self.worker:
            self.cancelling=True;self.worker.job.cancel();self.eta_timer.stop();self.render_operation()

    def show_error(self,error):
        if self.closing:return
        message,details=user_error(error,self.language)
        box=QMessageBox(self);box.setWindowTitle(self.t('error.title'));box.setText(message);box.setIcon(QMessageBox.Warning)
        if details:box.setDetailedText(details[-12000:])
        box.addButton(self.t('close'),QMessageBox.AcceptRole);box.exec()

    def start_analysis(self):
        if not self.video or self.worker:return
        if self.detection.currentIndex()==1 and self.area==(0,0,1,1):self.show_error(AppError('error.draw_area'));return
        self.pending_settings=self.settings();self.pending_area_mode=self.detection.currentIndex()
        self.work(lambda job,p:analyze(self.video,self.pending_settings,job,p),self.analyzed,Text('state.analyzing'),'analysis')

    def analyzed(self,samples):
        self.samples=samples;self.capture_settings=self.pending_settings or self.settings();self.capture_area_mode=self.pending_area_mode
        self.dirty=False;self.manual_changed=False;self.plan_reset_notice=False;self.selected_box.hide();self.timeline.index=-1
        if self.analysis_estimate:self.last_analysis_elapsed=self.analysis_estimate.elapsed()
        self.replan();self.graph.set_samples(samples,self.video.duration)

    def render_plan(self):
        self.timeline.set_plan(self.plan);self.table.setRowCount(len(self.plan))
        for i,s in enumerate(self.plan):
            for j,text in enumerate((clock(s.start),clock(s.end),self.t({'keep':'keep','remove':'remove','speed':'speed_up'}[s.action]))):self.table.setItem(i,j,QTableWidgetItem(text))
        if self.plan:
            index=min(max(0,self.timeline.index),len(self.plan)-1);self.populate_segment(index,select=False)
        self.render()

    def render_results(self):
        if not self.video:return
        st=statistics(self.plan)
        self.stats.setText(f"{self.t('metric.original')}: {clock(self.video.duration)}  →  {self.t('metric.output')}: {clock(st['output'])}  ·  {self.t('metric.reduction')}: {st['reduction']:.1f}%")
        if not self.samples:return
        raw=detection_statistics(self.samples,self.video.duration,self.minimum.value())
        self.raw_card.value.setText(f"{clock(raw['still'])} · {1-raw['motion_fraction']:.1%}")
        pauses=self.report.pauses if self.report else ()
        self.pause_card.value.setText(self.t('metric.pause_value',time=clock(sum(b-a for a,b in pauses)),count=len(pauses)))
        self.removed_card.value.setText(clock(st['removed']))
        self.motion_value.setText(f"{self.t('metric.original')}: {clock(self.video.duration)}\n{self.t('metric.motion')}: {clock(raw['motion'])} · {raw['motion_fraction']:.1%}")
        if self.dirty:message=self.t('result.stale')
        elif st['output']<.02:message=self.t('result.empty')
        elif st['sections']>0:message=self.t('result.changed')
        elif not self.report or not self.report.pauses:
            message=self.t('result.none',idle=clock(raw['still'])) if raw['still']>0 else self.t('result.constant')
        elif self.report.lost_to_padding:message=self.t('result.padding')
        else:message=self.t('result.manual')
        previous=self.workflow.state in (State.ANALYSIS_CANCELLED,State.ANALYSIS_FAILED)
        if self.plan_reset_notice:message=self.t('result.reset')+'\n'+message
        if previous:message=self.t('result.previous')+'\n'+message
        self.result_message.setText(message);self.result_message.setProperty('warning',self.dirty or st['sections']==0 or previous);self.result_message.style().unpolish(self.result_message);self.result_message.style().polish(self.result_message)
        self.suggestions.setVisible(st['sections']==0 and not self.dirty)
        if self.report:
            report=self.report
            self.details_text.setText('\n'.join(self.t(key,value=value) for key,value in (
                ('details.raw',len(report.raw_idle)),('details.static',len(report.stable_idle)),('details.longest',clock(report.longest)),
                ('details.short',report.rejected_short),('details.padding',report.lost_to_padding),
                ('details.bursts',report.merged_bursts),('details.remove',clock(st['removed'])))))
            if st['sections']==0:self.details_text.setText(self.details_text.text()+'\n'+self.t('details.reason',reason=message))
            top='\n'.join(f'{clock(a)} → {clock(b)} · {clock(b-a)}' for a,b in report.top_five)
            self.top_pauses.setText(self.t('details.top')+'\n'+(top or self.t('details.none')))

    def render(self):
        self.render_results()
        loaded=self.video is not None;busy=self.worker is not None
        self.sidebar.setVisible(loaded);self.view.setVisible(loaded);self.player_controls.setVisible(loaded);self.seek.setVisible(loaded);self.file_details_button.setVisible(loaded)
        self.drop_zone.setMinimumHeight(42 if loaded else 240)
        if loaded:self.metadata.setText(f'{self.video.path.name}  ·  {clock(self.video.duration)}  ·  {self.video.width} × {self.video.height}')
        else:self.metadata.setText(self.t('formats'))
        self.open_button.setEnabled(not busy);self.drop_zone.setEnabled(not busy);self.settings_scroll.setEnabled(not busy)
        self.analyze_button.setEnabled(loaded and not busy);self.analyze_button.setText(self.t('reanalyze' if self.samples else 'analyze'))
        valid=bool(self.plan) and not self.dirty;output=statistics(self.plan)['output'] if self.plan else 0
        self.export_button.setEnabled(valid and output>=.02 and not busy)
        self.export_button.setText(self.t('export.unchanged' if self.plan and statistics(self.plan)['sections']==0 else 'export'))
        if valid and self.samples and self.workflow.state in (State.ANALYSIS_CANCELLED,State.ANALYSIS_FAILED):self.export_button.setText(self.t('export.previous'))
        self.preview_button.setEnabled(valid and output>=.02 and not busy);self.preview_mode.setEnabled(valid and output>=.02 and not busy)
        self.apply.setEnabled(valid and not busy);self.reset.setEnabled(valid and not busy);self.manual.setEnabled(not busy);self.timeline.setEnabled(not busy)
        self.selected_box.setEnabled(valid and not busy)
        self.play.setEnabled(loaded);self.proxy_button.setEnabled(loaded and not busy)
        self.view.custom=loaded and not busy and self.detection.currentIndex()==1 and self.preview_mode.currentIndex()==0
        self.results_scroll.setVisible(bool(self.samples));self.review_actions.setVisible(bool(self.samples))
        self.timeline.setVisible(bool(self.samples));self.timeline_legend.setVisible(bool(self.samples))
        self.rows['sensitivity'].name.setText(f"{self.t('sensitivity')} · {self.sensitivity.value()}%")
        if self.detection.currentIndex()==1:
            x,y,w,h=self.area;self.area_label.setText(self.t('area.selected',x=f'{x*100:.0f}',y=f'{y*100:.0f}',w=f'{w*100:.0f}',h=f'{h*100:.0f}'))
        else:self.area_label.clear()
        step=0 if not loaded else (2 if self.workflow.kind=='analysis' else 3 if self.samples else 1)
        for i,label in enumerate(self.steps):
            label.setProperty('current',i==step);label.style().unpolish(label);label.style().polish(label)
        self.render_operation()

    def render_operation(self):
        active=self.workflow.busy
        if self.cancelling and active:self.phase_label.setText(self.t('cancel.pending'))
        elif active and self.current_stage:self.phase_label.setText(self.t(self.current_stage.key,**self.current_stage.params))
        else:self.phase_label.setText(self.t('state.'+self.workflow.state.value,path=self.last_export_path))
        self.progress.setVisible(active)
        if not active:self.progress.setRange(0,1000);self.progress.setValue(0)
        elif self.workflow.progress is None:self.progress.setRange(0,0)
        else:self.progress.setRange(0,1000);self.progress.setValue(round(self.workflow.progress*1000))
        self.cancel.setEnabled(self.worker is not None and not self.cancelling);self.render_eta()

    def render_eta(self):
        if self.workflow.kind=='analysis' and self.analysis_estimate and not self.cancelling:
            remaining=self.analysis_estimate.remaining()
            self.eta_label.setText(self.t('eta.calibrating') if remaining is None else self.t('eta.remaining',remaining=clock(remaining),elapsed=clock(self.analysis_estimate.elapsed())))
        elif self.workflow.state==State.ANALYSIS_COMPLETE and self.last_analysis_elapsed is not None:self.eta_label.setText(self.t('eta.done',elapsed=clock(self.last_analysis_elapsed)))
        else:self.eta_label.clear()

    def populate_segment(self,index,select=True):
        if not self.plan:return
        s=self.plan[index];self.start.setValue(s.start);self.end.setValue(s.end);self.start.setEnabled(index>0);self.end.setEnabled(index<len(self.plan)-1)
        self.action.setCurrentIndex({'keep':0,'remove':1,'speed':2}[s.action])
        if select:
            self.timeline.index=index;self.timeline.update();self.table.selectRow(index);self.selected_box.show()
            self.selected_label.setText(self.t('segment.selected',start=clock(s.start),end=clock(s.end)))

    def select_segment(self,index):
        if not self.plan or self.worker:return
        self.preview_mode.setCurrentIndex(0);self.populate_segment(index);self.player.setPosition(round(self.plan[index].start*1000))

    def set_action(self,action):
        index=self.timeline.index
        if self.worker or self.dirty or not 0<=index<len(self.plan):return
        self.plan[index]=replace(self.plan[index],action=action,speed=self.speed_value());self.manual_changed=True;self.plan_reset_notice=False;self.invalidate_preview();self.render_plan();self.populate_segment(index)

    def apply_edit(self):
        index=max(0,self.timeline.index)
        try:
            plan=move_boundary(self.plan,index,self.start.value(),self.end.value(),self.video.duration)
            plan[index]=replace(plan[index],action=('keep','remove','speed')[self.action.currentIndex()],speed=self.speed_value())
            self.plan=plan;self.manual_changed=True;self.plan_reset_notice=False;self.invalidate_preview();self.render_plan();self.populate_segment(index)
        except ValueError as error:self.show_error(error)

    def reset_plan(self):self.plan=list(self.automatic);self.manual_changed=False;self.plan_reset_notice=False;self.invalidate_preview();self.render_plan()

    def render_play(self):
        active=self.player.playbackState()==QMediaPlayer.PlayingState
        self.play.setText(self.t('pause' if active else 'play'));self.play.setIcon(icon('pause' if active else 'play'))

    def toggle_play(self):
        if self.player.playbackState()==QMediaPlayer.PlayingState:self.player.pause()
        else:self.player.play()

    def position_changed(self,position):
        if not hasattr(self,'seek'):return
        duration=self.player.duration()
        if not self.seek.isSliderDown():self.seek.setValue(round(position/max(1,duration)*10000))
        self.time.setText(f'{clock(position/1000)} / {clock(duration/1000)}')
        source_time=position/1000
        if self.preview_mode.currentIndex()==1:
            cursor=0.
            for s in self.plan:
                if s.output_duration and source_time<=cursor+s.output_duration:
                    source_time=s.start+(source_time-cursor)*(s.speed if s.action=='speed' else 1);break
                cursor+=s.output_duration
        self.timeline.position=source_time;self.timeline.update()

    def media_error(self,error,message):
        if error!=QMediaPlayer.NoError and self.video and not self.closing:self.codec_notice.show()

    def invalidate_preview(self):
        self.preview_mode.setCurrentIndex(0);self.preview_path=None;self.cleanup_cache()

    def cleanup_cache(self):
        for path in self.cache.glob('*.mp4'):
            if path not in (self.preview_path,self.proxy_path):
                try:path.unlink()
                except OSError:pass

    def preview_result(self):
        if not self.plan or self.dirty:return
        if statistics(self.plan)['sections']==0:self.preview_mode.setCurrentIndex(0);self.player.play()
        else:self.preview_mode.setCurrentIndex(1)

    def switch_preview(self,index):
        if not self.video:return
        self.player.pause()
        if index==0:
            self.view.dimensions(self.video.display_width,self.video.height);self.player.setSource(QUrl.fromLocalFile(str(self.proxy_path or self.video.path)))
            if self.detection.currentIndex()==1:
                x,y,w,h=self.area;self.view.outline.setRect(x*self.video.display_width,y*self.video.height,w*self.video.display_width,h*self.video.height);self.view.outline.show()
            self.render()
        elif self.preview_path:self.player.setSource(QUrl.fromLocalFile(str(self.preview_path)));self.view.outline.hide();self.render()
        else:
            destination=self.cache/f'edited-{uuid.uuid4().hex}.mp4'
            self.work(lambda job,p:export(self.video,list(self.plan),destination,job,p,preview=True),self.preview_ready,Text('state.preview'),'preview')

    def preview_ready(self,path):self.preview_path=path;self.player.setSource(QUrl.fromLocalFile(str(path)));self.view.outline.hide();self.codec_notice.hide()

    def create_proxy(self):
        destination=self.cache/f'original-{uuid.uuid4().hex}.mp4'
        self.work(lambda job,p:proxy(self.video,destination,job,p),self.proxy_ready,Text('state.preview'),'preview')

    def proxy_ready(self,path):self.proxy_path=path;self.player.setSource(QUrl.fromLocalFile(str(path)));self.codec_notice.hide()

    def start_export(self):
        if not self.plan or self.dirty or self.worker:return
        if statistics(self.plan)['sections']==0 and not self.question('unchanged.title','unchanged.confirm'):return
        dialog=ExportDialog(self)
        if dialog.exec()!=QDialog.Accepted:return
        suggested=str(self.video.path.with_name(self.video.path.stem+'_smartcut.mp4'))
        destination,_=QFileDialog.getSaveFileName(self,self.t('export.title'),suggested,'MP4 (*.mp4)',options=QFileDialog.DontConfirmOverwrite|QFileDialog.DontUseNativeDialog)
        if not destination:return
        quality=(18,23,28)[dialog.quality.currentIndex()];mute=dialog.audio.currentIndex()==1;encoder=('libx264','h264_nvenc','h264_qsv','h264_amf')[dialog.encoder.currentIndex()]
        self.work(lambda job,p:export(self.video,list(self.plan),destination,job,p,crf=quality,mute=mute,encoder=encoder),self.exported,Text('state.exporting'),'export')

    def exported(self,path):self.last_export_path=str(path)

    def highlight_drop(self,active):
        if self.drop_zone.property('dragActive')!=active:
            self.drop_zone.setProperty('dragActive',active);self.drop_zone.style().unpolish(self.drop_zone);self.drop_zone.style().polish(self.drop_zone)

    def closeEvent(self,event):
        self.closing=True
        if self.worker:self.cancel_job();event.ignore();return
        self.eta_timer.stop();self.player.stop();self.player.setSource(QUrl());shutil.rmtree(self.cache,ignore_errors=True);event.accept()


STYLE='''
QWidget { background: #171c24; color: #e0e6ee; font-family: 'Segoe UI', sans-serif; font-size: 12px; }
QLabel { background: transparent; }
QLabel#brand { font-size: 22px; font-weight: 650; color: #f4f6fa; }
QLabel#sectionTitle { font-size: 16px; font-weight: 600; padding: 4px 0; }
QLabel#muted { color: #a2afbf; font-size: 11px; }
QLabel#settingName { font-weight: 600; }
QLabel#step { padding: 8px; color: #93a0b0; background: #1d242f; border-radius: 6px; }
QLabel#step[current="true"] { color: #8fe0c2; background: #233b35; }
QFrame#statCard { background: #202834; border-radius: 7px; }
QLabel#statValue { font-size: 17px; font-weight: 600; }
QLabel#resultMessage { padding: 9px; background: #20362f; border-radius: 6px; color: #b0ddca; }
QLabel#resultMessage[warning="true"] { background: #383124; color: #ead5a3; }
QPushButton,QComboBox,QDoubleSpinBox { background: #293342; border: 1px solid #3b4859; border-radius: 6px; padding: 8px; }
QPushButton:hover { background: #354558; }
QPushButton:disabled { color: #677589; background: #202835; }
QPushButton#primary { background: #69d4ae; color: #112c23; font-weight: 650; border: 0; padding: 11px; }
QPushButton#dropZone { border: 1px dashed #52647b; background: #1a2330; color: #bfcee0; padding: 14px; }
QPushButton#dropZone[dragActive="true"] { border: 2px solid #73dcba; background: #223d34; }
QToolButton { border: 0; background: transparent; padding: 4px; }
QToolButton:hover { background: #2c394a; border-radius: 4px; }
QToolButton#foldToggle { padding: 8px 0; font-weight: 600; }
QSlider::groove:horizontal { background: #354155; height: 5px; border-radius: 2px; }
QSlider::handle:horizontal { background: #70d7b5; width: 14px; margin: -5px 0; border-radius: 7px; }
QSlider::sub-page:horizontal { background: #59b396; }
QProgressBar { background: #303b4b; border: 0; height: 12px; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #5cb99b; border-radius: 4px; }
QTableWidget { background: #202835; gridline-color: #384556; border: 0; selection-background-color: #34584d; }
QHeaderView::section { background: #293443; border: 0; padding: 6px; }
QScrollArea { border: 0; }
QScrollBar:vertical { background: #1a222d; width: 8px; margin: 0; border: 0; }
QScrollBar::handle:vertical { background: #435269; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background: transparent; }
'''


def main():
    app=QApplication(sys.argv);app.setApplicationName('Smart Video Cutter');app.setStyleSheet(STYLE)
    window=Window();window.show();sys.exit(app.exec())


if __name__=='__main__':main()
