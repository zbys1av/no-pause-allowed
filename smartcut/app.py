"""Four-step desktop controller. Detection, planning and encoding stay independent."""
from dataclasses import replace,asdict
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from PySide6.QtCore import Qt,QTimer,QUrl
from PySide6.QtGui import QShortcut,QKeySequence
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QComboBox,QSlider,QDoubleSpinBox,QProgressBar,QScrollArea,QSplitter,
    QTableView,QAbstractItemView,QHeaderView,QFileDialog,QMessageBox,
    QDialog,QFormLayout,QFrame,QCheckBox,QToolButton,QMenu,QStackedWidget,QWidgetAction)
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from . import __version__
from .model import Settings,Segment,analyze_plan,move_boundary,statistics,detection_statistics,split_plan,edit_range
from .manual_intervals import assign as assign_manual, analysis_windows
from .motion import analyze,analyze_selected,analysis_dimensions
from .review import review_spans,review_reasons
from .video import probe
from .export import export,proxy
from .batch import export_project
from .progress import AnalysisEstimate
from .profiles import PROFILES
from .state import State,Workflow
from .i18n import tr,Text,install_qt_language
from .errors import AppError,user_error
from .preferences import read_language,save_language
from .icons import icon
from .worker import Worker
from .debug import Journal,snapshot,write_report
from .widgets import VideoView,Timeline,TimelineViewport,VideoDropFilter,video_drop_path,Fold,SettingRow,StatCard,MotionGraph,clock,review_tip,PlanTableModel


class ExportDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent);t=parent.t
        self.setWindowTitle(t('export.title'));layout=QFormLayout(self)
        self.quality=QComboBox();self.quality.addItems([t('quality.high'),t('quality.balanced'),t('quality.small')]);self.quality.setCurrentIndex(1)
        self.audio=QComboBox();self.audio.addItems([t('audio.keep'),t('audio.mute')])
        self.encoder=QComboBox();self.encoder.addItems([t('encoder.cpu'),'NVIDIA · NVENC','Intel · QSV','AMD · AMF'])
        layout.addRow(t('quality'),self.quality);layout.addRow(t('audio'),self.audio);layout.addRow(t('encoder'),self.encoder)
        self.output=QComboBox();self.output.addItems([t('batch.merged'),t('batch.separate')])
        if len(parent.project)>1:
            layout.addRow(t('batch.output'),self.output)
            merge_note=QLabel(t('batch.merge_note'));merge_note.setWordWrap(True);layout.addRow(merge_note)
        note=QLabel(t('encoder.note'));note.setWordWrap(True);layout.addRow(note)
        buttons=QHBoxLayout();cancel=QPushButton(t('cancel'));save=QPushButton(t('save'));save.setObjectName('primary')
        cancel.clicked.connect(self.reject);save.clicked.connect(self.accept);buttons.addWidget(cancel);buttons.addWidget(save);layout.addRow(buttons)


class Window(QMainWindow):
    def __init__(self,language=None,remember=True):
        super().__init__();self.language=language or read_language();self.remember=remember
        self.setWindowTitle(f'Smart Video Cutter · {__version__}');self.resize(1366,850)
        self.video_expanded=False;self.sidebar_visible=True;self._review_cache=None;self._raw_cache=None
        self.project=[];self.project_index=0;self._hidden_by_video={}
        self.video=None;self.samples=[];self.plan=[];self.automatic=[];self.report=None
        self.manual_intervals=[];self.coverage_dirty=False
        self.area=(0,0,1,1);self.worker=None;self.closing=False;self.dirty=False;self.manual_changed=False;self.plan_reset_notice=False
        self.capture_settings=None;self.capture_area_mode=0;self.pending_settings=None;self.pending_area_mode=0
        self.preview_path=self.proxy_path=None;self.last_export_path='';self.current_stage=None
        self.workflow=Workflow();self.analysis_estimate=None;self.last_analysis_elapsed=None;self.cancelling=False
        self.journal=Journal();self.journal.add('application_started',version=__version__)
        self.last_job_debug={};self.last_analysis_debug={};self.plan_settings=None;self.last_report_path='';self.operation_kind=None
        self._updating=False;self.bindings=[];self.rows={}
        self.cache=Path(tempfile.mkdtemp(prefix='smartcut-preview-'))
        self.player=QMediaPlayer(self);self.audio_output=QAudioOutput(self);self.player.setAudioOutput(self.audio_output)
        self.view=VideoView();self.player.setVideoOutput(self.view.item)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda _:self.position_changed(self.player.position()))
        self.player.mediaStatusChanged.connect(self.pending_project_seek)
        self.player.playbackStateChanged.connect(lambda _:self.render_play())
        self.player.errorOccurred.connect(self.media_error)
        self.build_ui()
        self.view.expand_requested.connect(self.toggle_video_expanded)
        self.expand_shortcut=QShortcut(QKeySequence('Ctrl+Return'),self);self.expand_shortcut.activated.connect(self.toggle_video_expanded)
        self.escape_shortcut=QShortcut(QKeySequence('Escape'),self);self.escape_shortcut.activated.connect(lambda:self.set_video_expanded(False))
        self.command_shortcuts=[]
        for keys,command in (('Ctrl+O',self.open_button.click),('Ctrl+E',self.export_button.click),
            ('Ctrl+Shift+A',self.analyze_all_button.click),('Ctrl+L',self.range_button.click),
            ('Ctrl+=',lambda:self.zoom.setValue(self.zoom.value()+1)),('Ctrl+-',lambda:self.zoom.setValue(self.zoom.value()-1)),
            ('Ctrl+0',lambda:self.zoom.setValue(1))):
            shortcut=QShortcut(QKeySequence(keys),self);shortcut.activated.connect(command);self.command_shortcuts.append(shortcut)
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
        root=QWidget();self.setCentralWidget(root);layout=QVBoxLayout(root);self.root_layout=layout;layout.setContentsMargins(18,12,18,12);layout.setSpacing(8)
        self.header_widget=QWidget();header=QHBoxLayout(self.header_widget);header.setContentsMargins(0,0,0,0);brand=QLabel('Smart Video Cutter');brand.setObjectName('brand');header.addWidget(brand);header.addStretch()
        self.open_button=self.button('open',self.open_dialog,'folder-open');header.addWidget(self.open_button)
        self.debug_button=self.button('debug.save',self.save_debug_report,'download');header.addWidget(self.debug_button)
        self.settings_button=self.button('settings',self.settings_dialog,'settings');header.addWidget(self.settings_button);layout.addWidget(self.header_widget)
        self.metadata_widget=QWidget();meta=QHBoxLayout(self.metadata_widget);meta.setContentsMargins(0,0,0,0);self.metadata=QLabel();self.metadata.setWordWrap(True);meta.addWidget(self.metadata,1)
        self.file_details_button=self.button('file.details',self.file_details,'film');meta.addWidget(self.file_details_button);layout.addWidget(self.metadata_widget)
        self.project_widget=QWidget();project_bar=QHBoxLayout(self.project_widget);project_bar.setContentsMargins(0,0,0,0);self.project_selector=QComboBox();self.project_selector.currentIndexChanged.connect(self.switch_file);project_bar.addWidget(self.project_selector,1)
        self.add_files_button=self.button('batch.add',lambda:self.open_dialog(append=True),'folder-open');project_bar.addWidget(self.add_files_button)
        self.project_menu=QMenu(self);self.up_file=self.project_menu.addAction(self.t('batch.up'),self.move_file_up)
        self.remove_file_button=self.project_menu.addAction(self.t('batch.remove'),self.remove_file)
        self.project_tools=QToolButton();self.project_tools.setText('...');self.project_tools.setMenu(self.project_menu);self.project_tools.setPopupMode(QToolButton.InstantPopup);project_bar.addWidget(self.project_tools)
        self.analyze_all_button=self.button('batch.analyze',self.analyze_all,'activity');project_bar.addWidget(self.analyze_all_button);layout.addWidget(self.project_widget)
        self.project_help=self.text('batch.help','muted');self.project_help.hide()
        self.splitter=QSplitter();layout.addWidget(self.splitter,1)
        center=QWidget();editor=QVBoxLayout(center);editor.setContentsMargins(0,0,0,0);editor.setSpacing(6)
        self.drop_zone=self.button('drop',self.open_dialog,'upload');self.drop_zone.setObjectName('dropZone');self.drop_zone.setMinimumHeight(110);editor.addWidget(self.drop_zone)
        editor.addWidget(self.view,1)
        self.player_controls=QWidget();controls=QHBoxLayout(self.player_controls);controls.setContentsMargins(0,0,0,0)
        self.play=self.button('play',self.toggle_play,'play');controls.addWidget(self.play)
        self.time=QLabel('00:00:00.00 / 00:00:00.00');controls.addWidget(self.time);controls.addStretch()
        self.preview_mode=QComboBox();self.preview_mode.currentIndexChanged.connect(self.switch_preview);controls.addWidget(self.preview_mode)
        self.sidebar_button=self.button('ui.parameters',self.toggle_sidebar);self.sidebar_button.setCheckable(True);self.sidebar_button.setChecked(True);controls.addWidget(self.sidebar_button)
        self.expand_button=self.button('ui.expand',self.toggle_video_expanded);controls.addWidget(self.expand_button);editor.addWidget(self.player_controls)
        self.seek=QSlider(Qt.Horizontal);self.seek.setRange(0,10000);self.seek.sliderMoved.connect(lambda v:self.player.setPosition(round(v/10000*self.player.duration())));editor.addWidget(self.seek)
        self.codec_notice=QWidget();notice=QVBoxLayout(self.codec_notice);notice.setContentsMargins(0,0,0,0)
        notice.addWidget(self.text('codec.offer','muted'));self.proxy_button=self.button('codec.prepare',self.create_proxy,'film');notice.addWidget(self.proxy_button);self.codec_notice.hide();editor.addWidget(self.codec_notice)
        self.results_scroll=QScrollArea();self.results_scroll.setWidgetResizable(True);self.results_scroll.setFrameShape(QFrame.NoFrame);self.results_scroll.setMaximumHeight(240)
        self.results=QWidget();result=QVBoxLayout(self.results);result.setContentsMargins(0,4,4,4);result.setSpacing(10)
        self.stats=QLabel();self.stats.setWordWrap(True);self.stats.setObjectName('muted')
        cards=QHBoxLayout();self.raw_card=StatCard('metric.raw','metric.raw_help');self.pause_card=StatCard('metric.pauses','metric.pause_help');self.removed_card=StatCard('metric.removed','metric.removed_help')
        for card in (self.raw_card,self.pause_card,self.removed_card):cards.addWidget(card,1)
        self.still_value=self.raw_card.value;result.addLayout(cards)
        self.result_message=QLabel();self.result_message.setWordWrap(True);self.result_message.setObjectName('resultMessage');
        self.suggestions=QWidget();suggest=QHBoxLayout(self.suggestions);suggest.setContentsMargins(0,0,0,0)
        self.suggest_minimum=self.button('suggest.minimum',lambda:self.minimum.setValue(max(.5,self.minimum.value()-.5)))
        self.suggest_sensitivity=self.button('suggest.sensitivity',lambda:self.sensitivity.setValue(max(0,self.sensitivity.value()-10)))
        self.suggest_debounce=self.button('suggest.debounce',lambda:self.debounce.setValue(min(2,self.debounce.value()+.2)))
        for b in (self.suggest_minimum,self.suggest_sensitivity,self.suggest_debounce):suggest.addWidget(b)
        result.addWidget(self.suggestions)
        self.timeline_legend=self.text('legend','muted');self.timeline=Timeline();self.timeline.selected.connect(self.select_segment)
        self.timeline.range_selected.connect(self.set_range)
        self.timeline.project_seek.connect(self.seek_project)
        self.range_box=QWidget();range_outer=QVBoxLayout(self.range_box);range_outer.setContentsMargins(0,0,0,0);range_layout=QHBoxLayout();range_outer.addLayout(range_layout)
        self.range_start=QDoubleSpinBox();self.range_end=QDoubleSpinBox()
        for key,spin in (('range.from',self.range_start),('range.to',self.range_end)):
            range_layout.addWidget(self.text(key));spin.setDecimals(3);spin.setRange(0,1e9);range_layout.addWidget(spin,1)
            spin.valueChanged.connect(self.range_values_changed)
        range_actions=QHBoxLayout();range_outer.addLayout(range_actions)
        self.remove_range_button=self.button('range.remove',lambda:self.apply_range_action('remove'));range_actions.addWidget(self.remove_range_button)
        self.keep_range_button=self.button('keep',lambda:self.apply_range_action('keep'));range_actions.addWidget(self.keep_range_button);range_actions.addStretch()
        confirmations=QHBoxLayout();range_outer.addLayout(confirmations)
        for key,kind in (('confirm.pause','pause'),('confirm.motion','motion'),('confirm.auto','auto')):
            button=self.button(key,lambda checked=False,k=kind:self.confirm_range(k))
            self.bindings.append((button,'setToolTip','confirm.help'));confirmations.addWidget(button)
        self.range_box.hide()
        self.selected_box=QWidget();selected=QHBoxLayout(self.selected_box);selected.setContentsMargins(0,0,0,0)
        self.selected_label=QLabel();self.selected_label.setWordWrap(True);selected.addWidget(self.selected_label,1)
        for key,action in (('keep','keep'),('remove','remove'),('speed_up','speed')):
            selected.addWidget(self.button(key,lambda checked=False,a=action:self.set_action(a)))
        self.selected_box.hide()
        self.manual=Fold('manual');self.table=QTableView();self.table_model=PlanTableModel(self.table);self.table.setModel(self.table_model);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows);self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers);self.table.setMaximumHeight(150)
        self.table.clicked.connect(lambda index:self.select_segment(index.row()));self.manual.content.addWidget(self.table)
        edit=QHBoxLayout();self.start=QDoubleSpinBox();self.end=QDoubleSpinBox()
        for spin in (self.start,self.end):spin.setRange(0,1e9);spin.setDecimals(3);edit.addWidget(spin)
        self.action=QComboBox();edit.addWidget(self.action);self.apply=self.button('apply',self.apply_edit);edit.addWidget(self.apply);self.manual.content.addLayout(edit)
        self.reset=self.button('reset.plan',self.reset_plan,'rotate-ccw');self.manual.content.addWidget(self.reset);result.addWidget(self.manual)
        self.analysis_details=Fold('details');self.motion_value=QLabel();self.motion_value.setWordWrap(True);self.analysis_details.content.addWidget(self.motion_value)
        self.details_text=QLabel();self.details_text.setWordWrap(True);self.analysis_details.content.addWidget(self.details_text)
        self.top_pauses=QLabel();self.top_pauses.setWordWrap(True);self.analysis_details.content.addWidget(self.top_pauses)
        self.analysis_details.content.addWidget(self.text('details.graph','muted'));self.graph=MotionGraph();self.analysis_details.content.addWidget(self.graph);result.addWidget(self.analysis_details)
        self.results_scroll.setWidget(self.results)
        editor.addWidget(self.stats);editor.addWidget(self.result_message)
        self.results_fold=Fold('ui.results');self.results_fold.content.addWidget(self.results_scroll);editor.addWidget(self.results_fold)
        self.timeline_filters=QWidget();filters=QHBoxLayout(self.timeline_filters);filters.setContentsMargins(0,0,0,0);filters.setSpacing(5)
        self.timeline_mode=QComboBox();self.timeline_mode.currentIndexChanged.connect(self.update_timeline_mode);self.timeline_mode.currentIndexChanged.connect(lambda _:self.render());filters.addWidget(self.timeline_mode)
        self.filter_buttons={}
        for layer,color,key in (('keep','#4bae91','keep'),('remove','#bd6679','remove'),('speed','#c1a15c','speed_up'),('review','#b08aff','timeline.review')):
            button=QToolButton();button.setText('●');button.setCheckable(True);button.setChecked(True)
            button.setStyleSheet(f'QToolButton {{ color: {color}; padding: 4px 8px; }} QToolButton:!checked {{ color: #6a7079; }}')
            button.toggled.connect(lambda checked,l=layer:self.timeline.set_layer_visible(l,checked))
            self.bindings.append((button,'setToolTip',key));filters.addWidget(button);self.filter_buttons[layer]=button
        self.show_all_button=self.button('timeline.show_all',self.show_all_timeline);filters.addWidget(self.show_all_button);filters.addStretch()
        editor.addWidget(self.timeline_filters);editor.addWidget(self.timeline);editor.addWidget(self.range_box);editor.addWidget(self.selected_box)
        self.review_actions=QWidget();review=QHBoxLayout(self.review_actions);review.setContentsMargins(0,0,0,0)
        self.preview_button=self.button('preview',self.preview_result,'play');self.export_button=self.button('export',self.start_export,'download',True)
        self.range_button=self.button('range.select',lambda:None);self.range_button.setCheckable(True);self.range_button.toggled.connect(self.toggle_range);review.addWidget(self.range_button)
        review.addStretch();review.addWidget(self.preview_button);review.addWidget(self.export_button);editor.addWidget(self.review_actions)
        self.splitter.addWidget(center)
        self.sidebar=QWidget();self.sidebar.setMinimumWidth(320);side=QVBoxLayout(self.sidebar);side.setContentsMargins(10,0,0,0)
        self.settings_scroll=QScrollArea();self.settings_scroll.setWidgetResizable(True);self.settings_scroll.setFrameShape(QFrame.NoFrame);self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        settings=QWidget();settings_layout=QVBoxLayout(settings);settings_layout.setContentsMargins(0,0,7,0)
        settings_layout.addWidget(self.text('profile','sectionTitle'));self.profile=QComboBox();settings_layout.addWidget(self.profile)
        self.profile_note=self.text('profile.note','muted');self.profile_note.hide()
        self.sensitivity=QSlider(Qt.Horizontal);self.sensitivity.setRange(0,100)
        sensitivity_widget=QWidget();sl=QVBoxLayout(sensitivity_widget);sl.setContentsMargins(0,0,0,0);sl.addWidget(self.sensitivity);scale_hint=self.text('sensitivity.scale','muted');scale_hint.hide()
        self.minimum=self.spin(.5,10,3);self.mode=QComboBox()
        self.add_setting(settings_layout,'sensitivity',sensitivity_widget,lambda:self.sensitivity.setValue(int(self.defaults().sensitivity)))
        self.add_setting(settings_layout,'minimum',self.minimum,lambda:self.minimum.setValue(self.defaults().min_idle))
        self.add_setting(settings_layout,'mode',self.mode,lambda:self.mode.setCurrentIndex(0))
        self.advanced=Fold('advanced');settings_layout.addWidget(self.advanced)
        self.padding=self.spin(0,2,.6);self.debounce=self.spin(0,2,.3);self.frequency=self.spin(1,12,8,1)
        self.adaptive=QCheckBox();self.bindings.append((self.adaptive,'setText','enabled'))
        self.parallel=QComboBox();self.parallel.addItems([self.t('workers.auto'),'1','2','4','8'])
        self.detail=QComboBox();self.detail.addItems(['320 px','640 px','960 px','1280 px']);self.detection=QComboBox();self.speed=QComboBox();self.speed.addItems(['2×','5×','10×','20×']);self.speed.setCurrentIndex(1)
        for key,widget,reset in (
            ('padding',self.padding,lambda:self.padding.setValue(self.defaults().padding)),
            ('debounce',self.debounce,lambda:self.debounce.setValue(self.defaults().debounce)),
            ('fps',self.frequency,lambda:self.frequency.setValue(self.defaults().sample_fps)),
            ('detail',self.detail,lambda:self.detail.setCurrentIndex((320,640,960,1280).index(self.defaults().analysis_width))),
            ('area',self.detection,lambda:self.reset_area()),('speed',self.speed,lambda:self.speed.setCurrentIndex(1))):self.add_setting(self.advanced.content,key,widget,reset)
        self.area_label=QLabel();self.area_label.setWordWrap(True);self.advanced.content.addWidget(self.area_label)
        self.advanced.content.addWidget(self.text('performance','sectionTitle'))
        self.add_setting(self.advanced.content,'adaptive',self.adaptive,lambda:self.adaptive.setChecked(self.defaults().adaptive))
        self.add_setting(self.advanced.content,'workers',self.parallel,lambda:self.parallel.setCurrentIndex(0))
        settings_layout.addStretch();self.settings_scroll.setWidget(settings);side.addWidget(self.settings_scroll,1)
        self.analyze_button=self.button('analyze',self.start_analysis,'activity',True);side.addWidget(self.analyze_button)
        self.splitter.addWidget(self.sidebar);self.splitter.setSizes([900,340])
        self.footer_widget=QWidget();footer=QHBoxLayout(self.footer_widget);footer.setContentsMargins(0,0,0,0);labels=QVBoxLayout();self.phase_label=QLabel();self.phase_label.setWordWrap(True);self.status=self.phase_label;self.eta_label=QLabel();self.eta_label.setObjectName('muted');labels.addWidget(self.phase_label);labels.addWidget(self.eta_label)
        footer.addLayout(labels,3);self.progress=QProgressBar();self.progress.setRange(0,1000);footer.addWidget(self.progress,2)
        self.cancel=self.button('cancel',self.cancel_job,'x');footer.addWidget(self.cancel);layout.addWidget(self.footer_widget)
        self.sensitivity_label=self.rows['sensitivity'].name
        self.profile.currentIndexChanged.connect(self.apply_profile)
        self.adaptive.toggled.connect(self.analysis_changed)
        self.parallel.currentIndexChanged.connect(self.analysis_changed)
        for widget in (self.sensitivity,self.frequency,self.detail):
            (widget.valueChanged if hasattr(widget,'valueChanged') else widget.currentIndexChanged).connect(self.analysis_changed)
        for widget in (self.minimum,self.padding,self.debounce):widget.valueChanged.connect(self.temporal_changed)
        self.mode.currentIndexChanged.connect(self.temporal_changed);self.speed.currentIndexChanged.connect(self.speed_changed)
        self.detection.currentIndexChanged.connect(self.area_mode);self.view.area_changed.connect(self.set_area)
        self.organize_workspace(header,project_bar,editor,side,edit)

    def organize_workspace(self,header,project_bar,editor,side,edit):
        # Reuse the controller's existing commands/widgets, moving each to its
        # primary workspace instead of creating parallel UI state.
        header.itemAt(0).widget().hide()
        self.open_button.clicked.disconnect();self.open_button.clicked.connect(lambda:self.open_dialog(append=bool(self.project)))
        self.bindings=[(w,m,'workspace.add' if w is self.open_button else k) for w,m,k in self.bindings]
        header.insertWidget(0,self.project_selector,1)
        self.project_selector.setMinimumWidth(120);self.project_selector.setMaximumWidth(480)
        header.insertWidget(2,self.project_tools)
        header.insertWidget(3,self.analyze_button)
        header.insertWidget(4,self.export_button)
        self.export_button.setObjectName('')
        self.export_button.style().unpolish(self.export_button);self.export_button.style().polish(self.export_button)
        self.export_button.setIcon(icon('download'))
        self.project_menu.addSeparator()
        batch=QWidgetAction(self.project_menu);batch.setDefaultWidget(self.analyze_all_button);self.project_menu.addAction(batch)
        # Replacing a project and appending clips are distinct commands.
        replace_action=self.project_menu.addAction(self.t('open'),self.open_dialog)
        self.bindings.append((replace_action,'setText','open'))
        self.add_files_button.hide()
        menu=QMenu(self.settings_button);self.settings_button.clicked.disconnect()
        language=menu.addAction(self.t('language'),self.settings_dialog);self.bindings.append((language,'setText','language'))
        for widget in (self.file_details_button,self.debug_button):
            item=QWidgetAction(menu);item.setDefaultWidget(widget);menu.addAction(item)
        self.settings_button.setMenu(menu)

        self.context_mode=QComboBox();side.insertWidget(0,self.context_mode)
        self.context_stack=QStackedWidget();side.addWidget(self.context_stack,1)
        self.context_mode.currentIndexChanged.connect(self.context_stack.setCurrentIndex)
        pages=[]
        for _ in range(4):
            page=QWidget();page_layout=QVBoxLayout(page);page_layout.setContentsMargins(0,4,0,0);page_layout.setSpacing(12)
            self.context_stack.addWidget(page);pages.append(page_layout)
        pages[0].addWidget(self.settings_scroll)
        pages[1].addWidget(self.selected_box)
        # One action editor: direct choices use the same set_action command.
        while self.selected_box.layout().count()>1:
            item=self.selected_box.layout().takeAt(1)
            if item.widget():
                self.bindings=[binding for binding in self.bindings if binding[0] is not item.widget()]
                item.widget().deleteLater()
        self.manual.content.removeItem(edit)
        while edit.count():edit.takeAt(0)
        form=QFormLayout()
        for key,widget in (('start',self.start),('end',self.end),('action',self.action)):
            form.addRow(self.text(key),widget)
        pages[1].addLayout(form);pages[1].addWidget(self.apply)
        self.action.activated.connect(lambda index:self.set_action(('keep','remove','speed')[index]))
        pages[1].addWidget(self.reset);pages[1].addWidget(self.manual);pages[1].addStretch()
        pages[2].addWidget(self.result_message);pages[2].addWidget(self.results_fold);pages[2].addStretch()
        self.results_scroll.setMaximumHeight(16777215)
        self.context_mode.currentIndexChanged.connect(lambda index:self.results_fold.set_open(True) if index==2 else None)
        self.sidebar.setMinimumWidth(300);self.sidebar.setMaximumWidth(420)
        self.splitter.setStretchFactor(0,1);self.splitter.setStretchFactor(1,0);self.splitter.setSizes([1020,320])

        timeline_index=editor.indexOf(self.timeline);editor.removeWidget(self.timeline)
        self.timeline_viewport=TimelineViewport(self.timeline);editor.insertWidget(timeline_index,self.timeline_viewport)
        self.zoom=QSlider(Qt.Horizontal);self.zoom.setRange(1,16);self.zoom.setValue(1);self.zoom.setFixedWidth(100)
        self.zoom.valueChanged.connect(self.timeline_viewport.set_zoom)
        self.timeline_viewport.zoom_changed.connect(self.zoom.setValue)
        self.zoom.setToolTip(self.t('workspace.zoom'));self.bindings.append((self.zoom,'setToolTip','workspace.zoom'))
        self.timeline_filters.layout().addWidget(self.zoom)
        self.zoom_reset=QToolButton();self.zoom_reset.setText('1×');self.zoom_reset.setToolTip(self.t('workspace.zoom'))
        self.zoom_reset.clicked.connect(lambda:self.zoom.setValue(1));self.zoom.valueChanged.connect(lambda value:self.zoom_reset.setText(f'{value}×'))
        self.timeline_filters.layout().addWidget(self.zoom_reset)
        # Range tools occupy two compact rows; confirmations are clearly
        # separate from editing actions and reuse confirm_range.
        outer=self.range_box.layout();confirmations=outer.takeAt(2)
        if confirmations and confirmations.layout():
            while confirmations.layout().count():
                item=confirmations.layout().takeAt(0)
                if item.widget():
                    self.bindings=[binding for binding in self.bindings if binding[0] is not item.widget()]
                    item.widget().deleteLater()
        self.confirm_kind=QComboBox();self.confirm_apply=self.button('workspace.confirm',lambda:self.confirm_range(('pause','motion','auto')[self.confirm_kind.currentIndex()]))
        old=outer.takeAt(0).layout()
        while old.count():
            item=old.takeAt(0)
            if item.widget() not in (self.range_start,self.range_end) and item.widget():
                self.bindings=[binding for binding in self.bindings if binding[0] is not item.widget()];item.widget().deleteLater()
        bounds=QFormLayout();bounds.addRow(self.text('range.from'),self.range_start);bounds.addRow(self.text('range.to'),self.range_end)
        outer.insertLayout(0,bounds)
        confirm_row=QHBoxLayout();confirm_row.addWidget(self.confirm_kind,1);confirm_row.addWidget(self.confirm_apply);outer.addLayout(confirm_row)
        pages[3].addWidget(self.range_box);pages[3].addStretch()
        self.review_actions.layout().addWidget(self.sidebar_button)
        self.metadata_widget.hide();self.project_widget.hide()

    def add_setting(self,layout,key,widget,reset):
        row=SettingRow(key,widget,'',reset,self.language);self.rows[key]=row;layout.addWidget(row)

    def defaults(self):return PROFILES[('drawing','screen','general')[max(0,self.profile.currentIndex())]]

    def settings(self):
        return Settings(self.sensitivity.value(),self.minimum.value(),self.padding.value(),self.frequency.value(),self.area,
                        (320,640,960,1280)[self.detail.currentIndex()],self.debounce.value(),self.adaptive.isChecked(),(0,1,2,4,8)[self.parallel.currentIndex()])

    def speed_value(self):return (2,5,10,20)[self.speed.currentIndex()]

    @staticmethod
    def signature(settings,area_mode):return (settings.sensitivity,settings.sample_fps,settings.analysis_width,settings.area,area_mode,settings.adaptive,settings.workers)

    def apply_profile(self,index):
        if self.worker:return
        self._updating=True;settings=PROFILES[('drawing','screen','general')[max(0,index)]]
        self.sensitivity.setValue(int(settings.sensitivity));self.minimum.setValue(settings.min_idle);self.padding.setValue(settings.padding)
        self.debounce.setValue(settings.debounce);self.frequency.setValue(settings.sample_fps);self.detail.setCurrentIndex((320,640,960,1280).index(settings.analysis_width))
        self.adaptive.setChecked(settings.adaptive)
        self.parallel.setCurrentIndex(0)
        self.mode.setCurrentIndex(0);self.speed.setCurrentIndex(1)
        self.area=(0,0,1,1);self.detection.setCurrentIndex(0);self.view.outline.hide();self._updating=False
        self.update_defaults();self.analysis_changed()

    def update_defaults(self):
        d=self.defaults()
        for key,value in {'sensitivity':f'{d.sensitivity:g}%', 'minimum':f'{d.min_idle:g} '+self.t('unit.seconds'), 'mode':Text('mode.remove'),
            'padding':f'{d.padding:g} '+self.t('unit.seconds'),'debounce':f'{d.debounce:g} '+self.t('unit.seconds'),'fps':f'{d.sample_fps:g}','detail':f'{d.analysis_width} px',
            'area':Text('area.full'),'speed':'5×','adaptive':Text('enabled' if d.adaptive else 'disabled'),'workers':Text('workers.auto')}.items():self.rows[key].default=value;self.rows[key].translate(self.language)

    def analysis_changed(self,*_):
        if self._updating:return
        self.journal.add('detector_settings_changed',settings=asdict(self.settings()))
        if self.samples:
            self.dirty=self.coverage_dirty or self.capture_settings is None or self.signature(self.settings(),self.detection.currentIndex())!=self.signature(self.capture_settings,self.capture_area_mode)
            if not self.dirty:self.replan()
        self.invalidate_preview();self.render()

    def temporal_changed(self,*_):
        if self._updating:return
        self.journal.add('pause_settings_changed',settings=asdict(self.settings()),mode=self.mode.currentIndex())
        if hasattr(self.samples,'windows') and self.capture_settings:
            current=self.settings();captured=self.capture_settings
            old=max(2.,captured.min_idle+captured.padding+captured.debounce,2/captured.sample_fps)
            new=max(2.,current.min_idle+current.padding+current.debounce,2/current.sample_fps)
            if new>old+1e-9:self.coverage_dirty=True;self.dirty=True
        if self.samples and not self.dirty:self.replan()
        self.invalidate_preview();self.render()

    def replan(self,*_):
        if not self.samples or self.dirty:return
        if self.manual_changed:self.plan_reset_notice=True
        self.report=analyze_plan(self.samples,self.video.duration,self.settings(),'remove' if self.mode.currentIndex()==0 else 'speed',self.speed_value())
        self.plan_settings=self.settings();self.journal.add('plan_rebuilt',settings=asdict(self.plan_settings),mode='remove' if self.mode.currentIndex()==0 else 'speed',speed=self.speed_value(),merged_bursts=self.report.merged_bursts)
        self.plan_mode='remove' if self.mode.currentIndex()==0 else 'speed'
        reviews=self.get_review_spans()
        self.report=replace(self.report,plan=tuple(split_plan(self.report.plan,[t for r in reviews for t in (r.start,r.end)])))
        self.automatic=list(self.report.plan);self.plan=self.apply_confirmations(self.automatic);self.manual_changed=False
        self.invalidate_preview();self.render_plan()

    def speed_changed(self,*_):
        if self._updating:return
        self.journal.add('speed_changed',speed=self.speed_value())
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
        self.parallel.setItemText(0,self.t('workers.auto'))
        for widget,method,key in self.bindings:getattr(widget,method)(self.t(key))
        for combo,keys in ((self.profile,('profile.drawing','profile.screen','profile.general')),
            (self.mode,('mode.remove','mode.speed')),(self.detection,('area.full','area.custom')),
            (self.preview_mode,('original','edited')),(self.action,('keep','remove','speed_up')),
            (self.timeline_mode,('timeline.current','timeline.all')),
            (self.context_mode,('workspace.analysis','workspace.segment','workspace.details','workspace.range')),
            (self.confirm_kind,('confirm.pause','confirm.motion','confirm.auto'))):
            index=combo.currentIndex();combo.blockSignals(True)
            if combo.count()!=len(keys):combo.clear();combo.addItems([self.t(key) for key in keys]);combo.setCurrentIndex(max(0,index))
            else:
                for i,key in enumerate(keys):combo.setItemText(i,self.t(key))
            combo.blockSignals(False)
        for fold in (self.advanced,self.manual,self.analysis_details,self.results_fold):fold.translate(self.language)
        for card in (self.raw_card,self.pause_card,self.removed_card):card.translate(self.language)
        for spin in (self.minimum,self.padding,self.debounce,self.start,self.end,self.range_start,self.range_end):spin.setSuffix(' '+self.t('unit.seconds'))
        self.timeline.language=self.language
        self.range_button.setToolTip(self.t('range.help'));self.timeline.setToolTip(self.t('range.help'))
        self.up_file.setText(self.t('batch.up'));self.remove_file_button.setText(self.t('batch.remove'))
        self.project_tools.setToolTip(self.t('ui.project_menu'));self.project_tools.setAccessibleName(self.t('ui.project_menu'))
        self.project_selector.setToolTip(self.t('batch.help'));self.profile.setToolTip(self.t('profile.note'))
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

    # Per-file editor state is kept in memory while switching project clips.
    project_fields=('samples','plan','automatic','report','capture_settings','capture_area_mode',
                    'dirty','manual_changed','plan_reset_notice','plan_settings','plan_mode','area',
                    'last_analysis_elapsed','last_analysis_debug','last_job_debug','manual_intervals','coverage_dirty')
    project_controls=('profile','sensitivity','minimum','padding','debounce','frequency','detail',
                      'adaptive','parallel','mode','speed','detection')

    def editor_state(self):
        fields={name:getattr(self,name,None) for name in self.project_fields}
        controls={}
        for name in self.project_controls:
            w=getattr(self,name)
            controls[name]=w.currentIndex() if isinstance(w,QComboBox) else w.isChecked() if isinstance(w,QCheckBox) else w.value()
        return dict(fields=fields,controls=controls,settings=self.settings())

    def save_current(self):
        if self.project:self.project[self.project_index]['state']=self.editor_state()

    def restore_editor(self,entry):
        self.loaded(entry['video'])
        state=entry['state']
        if state:
            self._updating=True
            for name,value in state['controls'].items():
                w=getattr(self,name);w.blockSignals(True)
                if isinstance(w,QComboBox):w.setCurrentIndex(value)
                elif isinstance(w,QCheckBox):w.setChecked(value)
                else:w.setValue(value)
                w.blockSignals(False)
            for name,value in state['fields'].items():setattr(self,name,value)
            self._updating=False
            self.graph.set_samples(self.samples,self.video.duration)
            self.workflow.state=State.ANALYSIS_COMPLETE if self.samples and not self.dirty else State.READY
            self.update_defaults();self.render_plan();self.render()

    def refresh_project(self):
        self.project_selector.blockSignals(True);self.project_selector.clear()
        for i,entry in enumerate(self.project):
            self.project_selector.addItem(f"{i+1}. {entry['video'].path.name}")
            self.project_selector.setItemData(i,str(entry['video'].path),Qt.ToolTipRole)
        self.project_selector.setCurrentIndex(self.project_index);self.project_selector.blockSignals(False)
        self.update_timeline_mode()

    def switch_file(self,index):
        if self.worker or index<0 or index>=len(self.project) or index==self.project_index:return
        self.save_current();self.project_index=index;self.restore_editor(self.project[index])

    def move_file_up(self):
        if self.worker or self.project_index<1:return
        self.save_current();i=self.project_index
        self.project[i-1],self.project[i]=self.project[i],self.project[i-1]
        self.project_index-=1;self.refresh_project();self.render()

    def remove_file(self):
        if self.worker or len(self.project)<2:return
        if (self.manual_changed or self.manual_intervals) and not self.question('discard.title','discard'):return
        del self.project[self.project_index]
        self.project_index=min(self.project_index,len(self.project)-1)
        self.restore_editor(self.project[self.project_index]);self.refresh_project();self.render()

    def open_dialog(self,checked=False,append=False):
        paths,_=QFileDialog.getOpenFileNames(self,self.t('open'),'',self.t('file.filter'),options=QFileDialog.DontUseNativeDialog)
        if paths:self.open_videos(paths,append=append)

    def open_video(self,path):self.open_videos([path])

    def open_videos(self,paths,append=False):
        if self.worker or self.closing:return
        if not append and (self.samples or self.manual_changed or self.manual_intervals or any(e['state'] and (e['state']['fields']['samples'] or e['state']['fields']['manual_changed'] or e['state']['fields'].get('manual_intervals')) for e in self.project)) and not self.question('discard.title','discard'):return
        paths=list(dict.fromkeys(str(Path(path).resolve()) for path in paths))
        if append:
            existing={str(e['video'].path) for e in self.project}
            paths=[path for path in paths if path not in existing]
        if not paths:return
        def load(job,progress):
            videos=[]
            for i,path in enumerate(paths):
                job.check();videos.append(probe(path,job));progress((i+1)/len(paths),Text('state.loading'))
            return videos
        def done(videos):
            if append:self.save_current()
            else:self.project=[];self.project_index=0
            self.project.extend(dict(video=v,state=None) for v in videos)
            if not append or self.video is None:self.loaded(self.project[0]['video'])
            self.refresh_project()
        self.work(load,done,Text('state.loading'),'loading')

    def analyze_all(self):
        if self.worker or not self.project:return
        self.save_current()
        if any(e['state'] and e['state']['fields']['manual_changed'] for e in self.project):
            if not self.question('discard.title','batch.reanalyze'):return
        template=self.editor_state()
        states=[]
        for entry in self.project:
            if entry['state']:states.append(entry['state'])
            else:
                fresh=dict(template);fresh['fields']=dict(template['fields'],manual_intervals=[])
                states.append(fresh)
        if any(st['controls']['detection']==1 and st['settings'].area==(0,0,1,1) for st in states):
            self.show_error(AppError('error.draw_area'));return
        videos=[e['video'] for e in self.project]
        def operation(job,progress):
            results=[]
            for i,(video,st) in enumerate(zip(videos,states)):
                manual=st['fields'].get('manual_intervals',[])
                if manual and not analysis_windows(manual,video.duration,0):
                    job.check();mode='remove' if st['controls']['mode']==0 else 'speed'
                    speed=(2,5,10,20)[st['controls']['speed']]
                    plan=[Segment(0,video.duration)]
                    for interval in manual:
                        plan=edit_range(plan,interval.start,interval.end,mode if interval.kind=='pause' else 'keep',speed)
                    fields=dict(st['fields']);fields.update(samples=[],plan=plan,automatic=[Segment(0,video.duration)],report=None,
                        capture_settings=st['settings'],capture_area_mode=st['controls']['detection'],dirty=False,manual_changed=False,
                        plan_reset_notice=False,plan_settings=st['settings'],plan_mode=mode,last_analysis_elapsed=0.,area=st['settings'].area,
                        last_analysis_debug={},last_job_debug={},coverage_dirty=False)
                    results.append(dict(fields=fields,controls=dict(st['controls']),settings=st['settings']))
                    progress((i+1)/len(videos),Text('batch.progress',{'index':i+1,'count':len(videos)}))
                    continue
                callback=lambda f,s,i=i:progress((i+f)/len(videos),Text('batch.progress',{'index':i+1,'count':len(videos)}))
                samples=analyze_selected(video,st['settings'],job,manual,callback) if manual else analyze(video,st['settings'],job,callback)
                mode='remove' if st['controls']['mode']==0 else 'speed'
                report=analyze_plan(samples,video.duration,st['settings'],mode,(2,5,10,20)[st['controls']['speed']])
                width,height=analysis_dimensions(video,st['settings'])
                reviews=review_spans(samples,video.duration,width*height,st['settings'].min_idle,st['settings'].debounce)
                report=replace(report,plan=tuple(split_plan(report.plan,[t for r in reviews for t in (r.start,r.end)])))
                plan=list(report.plan)
                for interval in st['fields'].get('manual_intervals',[]):
                    action=mode if interval.kind=='pause' else 'keep'
                    plan=edit_range(plan,interval.start,interval.end,action,(2,5,10,20)[st['controls']['speed']])
                fields=dict(st['fields']);fields.update(samples=samples,plan=plan,automatic=list(report.plan),report=report,
                    capture_settings=st['settings'],capture_area_mode=st['controls']['detection'],dirty=False,manual_changed=False,
                    plan_reset_notice=False,plan_settings=st['settings'],plan_mode=mode,last_analysis_elapsed=None,area=st['settings'].area,
                    last_analysis_debug={},last_job_debug={},coverage_dirty=False)
                results.append(dict(fields=fields,controls=dict(st['controls']),settings=st['settings']))
            return results
        def done(results):
            for entry,state in zip(self.project,results):entry['state']=state
            self.restore_editor(self.project[self.project_index])
        self.work(operation,done,Text('state.analyzing'),'analysis')

    def loaded(self,video):
        self.journal.add('video_loaded',duration=video.duration,width=video.width,height=video.height)
        self.plan_settings=None
        self.player.stop();self.player.setSource(QUrl());self.video=video
        self.samples=[];self.plan=[];self.automatic=[];self.report=None;self.capture_settings=None;self.dirty=False;self.manual_changed=False;self.plan_reset_notice=False
        self.manual_intervals=[];self.coverage_dirty=False
        self.plan=[Segment(0,video.duration)];self.automatic=list(self.plan)
        if not hasattr(self,'_hidden_by_video'):self._hidden_by_video={}
        self.timeline.hidden_segments=self._hidden_by_video.setdefault(str(video.path),set())
        self.range_button.setChecked(False);self.timeline.selection=None
        for spin in (self.range_start,self.range_end):spin.blockSignals(True);spin.setRange(0,video.duration)
        self.range_start.setValue(0);self.range_end.setValue(min(1,video.duration))
        for spin in (self.range_start,self.range_end):spin.blockSignals(False)
        self.range_values_changed()
        self.selected_box.hide();self.graph.set_samples([],video.duration)
        self.preview_path=self.proxy_path=None;self.timeline.index=-1;self._updating=True
        self.area=(0,0,1,1);self.detection.setCurrentIndex(0);self._updating=False;self.view.dimensions(video.display_width,video.height)
        self.cleanup_cache();self.preview_mode.blockSignals(True);self.preview_mode.setCurrentIndex(0);self.preview_mode.blockSignals(False)
        self.codec_notice.hide();self.player.setSource(QUrl.fromLocalFile(str(video.path)))
        self.workflow.state=State.READY;self.last_analysis_elapsed=None;self.analysis_estimate=None;self.render_plan(prune_hidden=False);self.render()

    def work(self,operation,done,stage,kind='analysis',analysis=None):
        if self.worker:return
        if analysis is True:kind='analysis'
        self.player.pause();generation=self.workflow.begin(kind);self.current_stage=stage;self.cancelling=False
        self.operation_kind=kind;self.progress_bucket=-1;self.journal.add('operation_started',kind=kind,generation=generation)
        self.operation_settings=asdict(self.settings())
        self.operation_video=dict(filename=self.video.path.name,duration=self.video.duration,width=self.video.width,height=self.video.height) if self.video else None
        if kind=='analysis':self.analysis_estimate=AnalysisEstimate();self.eta_timer.start()
        worker=Worker(operation,self);self.worker=worker
        worker.result.connect(lambda result:self.operation_result(generation,result,done))
        worker.failure.connect(lambda error:self.operation_failed(generation,error))
        worker.cancelled.connect(lambda:self.operation_cancelled(generation))
        worker.progress.connect(lambda f,s:self.on_progress(f,s,generation))
        worker.finished.connect(lambda:self.job_finished(generation));self.render();worker.start()

    def operation_result(self,generation,result,done):
        if generation!=self.workflow.generation:return
        self.journal.add('operation_completed',kind=self.operation_kind,generation=generation)
        self.workflow.finish('complete');self.current_stage=None;self.eta_timer.stop();done(result);self.render()

    def operation_failed(self,generation,error):
        if generation!=self.workflow.generation:return
        import traceback
        self.journal.add('operation_failed',kind=self.operation_kind,error_type=type(error).__name__,error=str(error),traceback=''.join(traceback.format_exception(error))[-16000:])
        self.workflow.finish('failed');self.current_stage=None;self.eta_timer.stop();self.render();self.show_error(error)

    def operation_cancelled(self,generation=None):
        if generation is not None and generation!=self.workflow.generation:return
        self.journal.add('operation_cancelled',kind=self.operation_kind)
        self.workflow.finish('cancelled');self.current_stage=None;self.eta_timer.stop();self.render()

    def on_progress(self,fraction,stage,generation=None):
        generation=self.workflow.generation if generation is None else generation
        if not self.workflow.update(generation,fraction):return
        bucket=int(fraction*20)
        if bucket!=getattr(self,'progress_bucket',-1):
            self.progress_bucket=bucket;self.journal.add('progress',fraction=fraction,stage=stage.key if isinstance(stage,Text) else str(stage))
        if isinstance(stage,Text):self.current_stage=stage
        if self.workflow.kind=='analysis':self.analysis_estimate.update(fraction)
        self.render_operation()

    def job_finished(self,generation=None):
        if generation is not None and generation!=self.workflow.generation:return
        old=self.worker;self.worker=None
        if old and self.operation_kind!='report':
            self.last_job_debug=dict(kind=self.operation_kind,state=self.workflow.state.value,settings=self.operation_settings,input_video=self.operation_video,commands=old.job.debug_commands,
                                     performance=getattr(old.job,'debug_analysis_performance',{}),
                                     samples=list(getattr(old.job,'debug_samples',[])) if self.workflow.state in (State.ANALYSIS_FAILED,State.ANALYSIS_CANCELLED) else [])
            if self.operation_kind=='analysis':self.last_analysis_debug=self.last_job_debug
        if old:old.deleteLater()
        self.eta_timer.stop();self.cancelling=False
        if self.preview_mode.currentIndex()==1 and self.preview_path is None:self.preview_mode.setCurrentIndex(0)
        self.render()
        if self.closing:self.close()

    def cancel_job(self):
        if self.worker:
            self.cancelling=True;self.worker.job.cancel();self.eta_timer.stop();self.render_operation()

    def show_error(self,error):
        self.journal.add('error_shown',error_type=type(error).__name__,error=str(error))
        if self.closing:return
        message,details=user_error(error,self.language)
        box=QMessageBox(self);box.setWindowTitle(self.t('error.title'));box.setText(message);box.setIcon(QMessageBox.Warning)
        if details:box.setDetailedText(details[-12000:])
        box.addButton(self.t('close'),QMessageBox.AcceptRole);box.exec()

    def start_analysis(self):
        if not self.video or self.worker:return
        if self.manual_changed and not self.question('discard.title','batch.reanalyze'):return
        if self.detection.currentIndex()==1 and self.area==(0,0,1,1):self.show_error(AppError('error.draw_area'));return
        self.pending_settings=self.settings();self.pending_area_mode=self.detection.currentIndex()
        skip=bool(self.manual_intervals) and not analysis_windows(self.manual_intervals,self.video.duration,0)
        def operation(job,progress):
            if skip:
                job.check();job.debug_analysis_performance=dict(frames=0,full_checks=0,manual_only=True)
                progress(1.,Text('stage.analysis_complete'));return None
            if self.manual_intervals:return analyze_selected(self.video,self.pending_settings,job,list(self.manual_intervals),progress)
            return analyze(self.video,self.pending_settings,job,progress)
        self.work(operation,self.analyzed,Text('state.analyzing'),'analysis')

    def analyzed(self,samples):
        manual_only=samples is None
        self.samples=[] if manual_only else samples;self.capture_settings=self.pending_settings or self.settings();self.capture_area_mode=self.pending_area_mode
        self.dirty=False;self.coverage_dirty=False;self.manual_changed=False;self.plan_reset_notice=False;self.selected_box.hide();self.timeline.index=-1
        if self.analysis_estimate:self.last_analysis_elapsed=self.analysis_estimate.elapsed()
        if manual_only:
            self.report=None;self.automatic=[Segment(0,self.video.duration)];self.plan=self.apply_confirmations(self.automatic)
            self.plan_settings=self.capture_settings;self.render_plan()
        else:self.replan()
        self.graph.set_samples(self.samples,self.video.duration)

    def toggle_sidebar(self):
        self.sidebar_visible=not self.sidebar_visible;self.render()

    def toggle_video_expanded(self):self.set_video_expanded(not self.video_expanded)

    def set_video_expanded(self,expanded):
        if expanded and not self.video:return
        if expanded==self.video_expanded:return
        if expanded:
            self._focus_selected_visible=not self.selected_box.isHidden()
            self._focus_codec_visible=not self.codec_notice.isHidden()
        else:
            self.selected_box.setVisible(self._focus_selected_visible)
            self.codec_notice.setVisible(self._focus_codec_visible)
        self.video_expanded=expanded;self.render()

    def get_review_spans(self):
        dimensions=analysis_dimensions(self.video,self.capture_settings or self.settings()) if self.video else (0,0)
        token=(id(self.samples),len(self.samples),self.video,dimensions,self.minimum.value(),self.debounce.value())
        if self._review_cache is None or self._review_cache[0]!=token:
            reviews=review_spans(self.samples,self.video.duration,dimensions[0]*dimensions[1],
                                 self.minimum.value(),self.debounce.value()) if self.video else []
            self._review_cache=(token,reviews,self.samples)
        return self._review_cache[1]

    def render_plan(self,prune_hidden=True):
        if prune_hidden:
            self.timeline.hidden_segments.intersection_update(self.timeline.segment_key(s) for s in self.plan)
        reviews=self.get_review_spans()
        self.timeline.confirmations=self.manual_intervals
        self.timeline.set_plan(self.plan,reviews);self.table_model.set_plan(self.plan,reviews,self.language)
        self.update_timeline_mode()
        if self.plan:
            index=min(max(0,self.timeline.index),len(self.plan)-1);self.populate_segment(index,select=False)
        self.render()

    def update_timeline_mode(self,*_):
        if not hasattr(self,'timeline_mode'):return
        if self.timeline_mode.currentIndex()!=1 or not self.project:
            self.timeline.set_project([])
            return
        clips=[]
        for i,entry in enumerate(self.project):
            video=entry['video'];state=entry['state']
            if i==self.project_index:
                plan=self.plan;reviews=self.get_review_spans();confirmations=self.manual_intervals
            elif state:
                fields=state['fields'];plan=fields['plan'];samples=fields['samples'];settings=fields['capture_settings'] or state['settings']
                width,height=analysis_dimensions(video,settings)
                token=(id(samples),len(samples),width,height,settings.min_idle,settings.debounce)
                cache=entry.get('_timeline_review_cache')
                if cache is None or cache[0]!=token:
                    reviews=review_spans(samples,video.duration,width*height,settings.min_idle,settings.debounce) if samples else []
                    entry['_timeline_review_cache']=(token,reviews,samples)
                else:reviews=cache[1]
                confirmations=fields.get('manual_intervals',[])
            else:plan=[Segment(0,video.duration)];reviews=[];confirmations=[]
            hidden=self._hidden_by_video.setdefault(str(video.path),set())
            clips.append((video.path.name,video.duration,plan,reviews,hidden,confirmations))
        self.timeline.project_index=self.project_index;self.timeline.set_project(clips)
        if self.range_button.isChecked():self.range_button.setChecked(False)

    def seek_project(self,index,position):
        if index!=self.project_index:
            self._pending_project_position=position
            self.project_selector.setCurrentIndex(index)
        else:self.player.setPosition(round(position*1000))

    def pending_project_seek(self,status):
        if status in (QMediaPlayer.LoadedMedia,QMediaPlayer.BufferedMedia) and hasattr(self,'_pending_project_position'):
            position=self._pending_project_position;del self._pending_project_position
            self.player.setPosition(round(position*1000))

    def render_results(self):
        if not self.video:return
        st=statistics(self.plan)
        self.stats.setText(f"{self.t('metric.original')}: {clock(self.video.duration)}  →  {self.t('metric.output')}: {clock(st['output'])}  ·  {self.t('metric.reduction')}: {st['reduction']:.1f}%")
        if self.timeline.project_clips:
            clips=self.timeline.project_clips
            original=sum(c[1] for c in clips);output=sum(statistics(c[2])['output'] for c in clips)
            affected=sum(s.end-s.start for c in clips for s in c[2] if s.action in ('remove','speed'))
            review_count=sum(len(c[3]) for c in clips)
            pauses=0.
            for i,entry in enumerate(self.project):
                report=self.report if i==self.project_index else (entry['state']['fields']['report'] if entry['state'] else None)
                if report:pauses+=report.pause_time
            self.stats.setText(self.t('timeline.summary',count=len(clips),original=clock(original),pauses=clock(pauses),affected=clock(affected),output=clock(output),reviews=review_count))
        if not self.samples:return st
        token=(id(self.samples),len(self.samples),self.video.duration,self.minimum.value())
        if self._raw_cache is None or self._raw_cache[0]!=token:
            self._raw_cache=(token,detection_statistics(self.samples,self.video.duration,self.minimum.value()),self.samples)
        raw=self._raw_cache[1]
        self.raw_card.value.setText(f"{clock(raw['still'])} · {1-raw['motion_fraction']:.1%}")
        pauses=self.report.pauses if self.report else ()
        self.pause_card.value.setText(self.t('metric.pause_value',time=clock(sum(b-a for a,b in pauses)),count=len(pauses)))
        self.removed_card.value.setText(clock(st['removed']))
        coverage=self.t('metric.measured') if hasattr(self.samples,'windows') else self.t('metric.original')
        self.motion_value.setText(f"{coverage}: {clock(raw['original'])}\n{self.t('metric.motion')}: {clock(raw['motion'])} · {raw['motion_fraction']:.1%}")
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
        if self.timeline.reviews:
            message+='\n'+self.t('review.summary',time=clock(sum(s.end-s.start for s in self.timeline.reviews)))
        self.result_message.setText(message)
        warning=self.dirty or st['sections']==0 or previous
        if self.result_message.property('warning')!=warning:
            self.result_message.setProperty('warning',warning);self.result_message.style().unpolish(self.result_message);self.result_message.style().polish(self.result_message)
        self.suggestions.setVisible(st['sections']==0 and not self.dirty)
        if self.report:
            report=self.report
            self.details_text.setText('\n'.join(self.t(key,value=value) for key,value in (
                ('details.raw',len(report.raw_idle)),('details.static',len(report.stable_idle)),('details.longest',clock(report.longest)),
                ('details.short',report.rejected_short),('details.padding',report.lost_to_padding),
                ('details.bursts',report.merged_bursts),('details.remove',clock(st['removed'])))))
            if st['sections']==0:self.details_text.setText(self.details_text.text()+'\n'+self.t('details.reason',reason=message))
            performance=self.last_analysis_debug.get('performance',{})
            if performance.get('frames') and self.last_analysis_debug.get('state')==State.ANALYSIS_COMPLETE.value:
                self.details_text.setText(self.details_text.text()+'\n'+self.t('details.performance',
                    full=performance['full_checks'],total=performance['frames'],fast=performance['certified_quiet']))
                self.details_text.setText(self.details_text.text()+'\n'+self.t('details.parallel',count=performance.get('effective_workers',1)))
            top='\n'.join(f'{clock(a)} → {clock(b)} · {clock(b-a)}' for a,b in report.top_five)
            self.top_pauses.setText(self.t('details.top')+'\n'+(top or self.t('details.none')))
        return st

    def render(self):
        st=self.render_results() or statistics(self.plan)
        loaded=self.video is not None;busy=self.worker is not None;expanded=self.video_expanded and loaded
        self.header_widget.setVisible(not expanded);self.metadata_widget.hide()
        self.project_widget.hide();self.footer_widget.setVisible(not expanded)
        self.root_layout.setContentsMargins(*((6,6,6,6) if expanded else (18,12,18,12)))
        self.expand_button.setText(self.t('ui.restore' if expanded else 'ui.expand'))
        self.expand_button.setToolTip(self.t('ui.expand_help'));self.expand_button.setEnabled(loaded)
        self.sidebar_button.setVisible(not expanded);self.sidebar_button.setChecked(self.sidebar_visible)
        self.project_tools.setVisible(loaded);self.project_tools.setEnabled(not busy)
        self.project_selector.setVisible(bool(self.project));self.project_selector.setEnabled(not busy)
        self.add_files_button.setEnabled(not busy)
        self.up_file.setVisible(len(self.project)>1);self.up_file.setEnabled(not busy and self.project_index>0)
        self.remove_file_button.setVisible(len(self.project)>1);self.remove_file_button.setEnabled(not busy)
        self.analyze_all_button.setVisible(len(self.project)>1);self.analyze_all_button.setEnabled(not busy)
        self.project_help.hide()
        self.debug_button.setEnabled(not busy)
        self.debug_button.setToolTip(self.t('debug.help'))
        self.sidebar.setVisible(loaded and self.sidebar_visible and not expanded);self.view.setVisible(loaded);self.player_controls.setVisible(loaded);self.seek.setVisible(loaded);self.file_details_button.setVisible(loaded)
        self.drop_zone.setVisible(not loaded);self.drop_zone.setMinimumHeight(240)
        if loaded:self.metadata.setText(f'{self.video.path.name}  ·  {clock(self.video.duration)}  ·  {self.video.width} × {self.video.height}')
        else:self.metadata.setText(self.t('formats'))
        self.open_button.setEnabled(not busy);self.drop_zone.setEnabled(not busy);self.settings_scroll.setEnabled(not busy)
        self.analyze_button.setEnabled(loaded and not busy);self.analyze_button.setText(self.t('reanalyze' if self.samples else 'analyze'))
        valid=bool(self.plan) and not self.dirty;output=st['output']
        self.export_button.setEnabled((len(self.project)>1 or (valid and output>=.02)) and not busy)
        self.export_button.setText(self.t('export.unchanged' if self.plan and st['sections']==0 else 'export'))
        if valid and self.samples and self.workflow.state in (State.ANALYSIS_CANCELLED,State.ANALYSIS_FAILED):self.export_button.setText(self.t('export.previous'))
        if len(self.project)>1:self.export_button.setText(self.t('batch.export'))
        self.preview_button.setEnabled(valid and output>=.02 and not busy);self.preview_mode.setEnabled(valid and output>=.02 and not busy)
        self.apply.setEnabled(valid and not busy);self.reset.setEnabled(valid and not busy);self.manual.setEnabled(not busy);self.timeline.setEnabled(not busy)
        self.selected_box.setEnabled(valid and not busy)
        self.play.setEnabled(loaded);self.proxy_button.setEnabled(loaded and not busy)
        self.view.custom=loaded and not busy and self.detection.currentIndex()==1 and self.preview_mode.currentIndex()==0
        self.results_scroll.setVisible(bool(self.plan));self.results_fold.setVisible(bool(self.plan) and not expanded)
        self.stats.setVisible(bool(self.plan) and not expanded)
        self.result_message.setVisible(bool(self.samples) and not expanded)
        self.review_actions.setVisible(loaded and not expanded)
        self.range_button.setEnabled(loaded and not busy and not self.dirty and self.timeline_mode.currentIndex()==0)
        self.range_box.setVisible(loaded and self.range_button.isChecked() and not expanded)
        self.range_box.setEnabled(not busy and not self.dirty)
        for card in (self.raw_card,self.pause_card,self.removed_card):card.setVisible(bool(self.samples))
        self.analysis_details.setVisible(bool(self.samples))
        if expanded:self.selected_box.hide();self.codec_notice.hide()
        self.timeline.setVisible(loaded and not expanded);self.timeline_legend.hide();self.timeline_filters.setVisible(loaded and not expanded)
        self.timeline_viewport.setVisible(loaded and not expanded)
        self.context_mode.setEnabled(not busy)
        self.rows['sensitivity'].name.setText(f"{self.t('sensitivity')} · {self.sensitivity.value()}%")
        if self.detection.currentIndex()==1:
            x,y,w,h=self.area;self.area_label.setText(self.t('area.selected',x=f'{x*100:.0f}',y=f'{y*100:.0f}',w=f'{w*100:.0f}',h=f'{h*100:.0f}'))
        else:self.area_label.clear()
        self.render_operation()

    def render_operation(self):
        active=self.workflow.busy
        if self.cancelling and active:self.phase_label.setText(self.t('cancel.pending'))
        elif active and self.current_stage:self.phase_label.setText(self.t(self.current_stage.key,**self.current_stage.params))
        else:self.phase_label.setText(self.t('state.'+self.workflow.state.value,path=self.last_export_path,report_path=self.last_report_path))
        self.progress.setVisible(active)
        if not active:self.progress.setRange(0,1000);self.progress.setValue(0)
        elif self.workflow.progress is None:self.progress.setRange(0,0)
        else:self.progress.setRange(0,1000);self.progress.setValue(round(self.workflow.progress*1000))
        self.cancel.setVisible(active)
        self.cancel.setEnabled(self.worker is not None and not self.cancelling);self.render_eta()

    def render_eta(self):
        if self.workflow.kind=='analysis' and self.analysis_estimate and not self.cancelling:
            remaining=self.analysis_estimate.remaining()
            self.eta_label.setText(self.t('eta.calibrating') if remaining is None else self.t('eta.remaining',remaining=clock(remaining),elapsed=clock(self.analysis_estimate.elapsed())))
        elif self.workflow.state==State.ANALYSIS_COMPLETE and self.last_analysis_elapsed is not None:self.eta_label.setText(self.t('eta.done',elapsed=clock(self.last_analysis_elapsed)))
        else:self.eta_label.clear()

    def toggle_range(self,enabled):
        self.timeline.range_mode=enabled
        if enabled:
            self.context_mode.setCurrentIndex(3);self.sidebar_visible=True
        elif self.context_mode.currentIndex()==3:self.context_mode.setCurrentIndex(0)
        if not enabled:self.timeline.selection=None;self.timeline.update()
        else:self.range_values_changed()
        self.render()

    def show_all_timeline(self):
        for button in self.filter_buttons.values():button.setChecked(True)
        for hidden in getattr(self,'_hidden_by_video',{}).values():hidden.clear()
        self.timeline.show_hidden()

    def set_range(self,start,end):
        self.range_button.setChecked(True)
        self.range_start.blockSignals(True);self.range_end.blockSignals(True)
        self.range_start.setValue(start);self.range_end.setValue(end)
        self.range_start.blockSignals(False);self.range_end.blockSignals(False)
        self.range_values_changed();self.render()

    def range_values_changed(self,*_):
        if not hasattr(self,'remove_range_button'):return
        start,end=self.range_coordinates()
        valid=bool(self.video) and 0<=start<end<=self.video.duration
        self.remove_range_button.setEnabled(valid);self.keep_range_button.setEnabled(valid)
        self.timeline.selection=(start,end) if valid and self.range_button.isChecked() else None
        self.timeline.update()

    def range_coordinates(self):
        start,end=self.range_start.value(),self.range_end.value()
        # Spin boxes round their maximum to the display precision. The maximum
        # still means EOF, including when the displayed number rounds upward.
        if self.video and end==self.range_end.maximum():
            tolerance=.5*10**(-self.range_end.decimals())+1e-9
            if abs(end-self.video.duration)<=tolerance:end=self.video.duration
        return start,end

    def apply_range_action(self,action):
        if not self.video or self.worker or self.dirty:return
        start,end=self.range_coordinates()
        try:self.plan=edit_range(self.plan,start,end,action,self.speed_value())
        except ValueError as error:self.show_error(error);return
        self.manual_changed=True;self.plan_reset_notice=False
        self.journal.add('manual_range',start=start,end=end,action=action)
        self.invalidate_preview();self.range_button.setChecked(False);self.render_plan()
        index=next(i for i,s in enumerate(self.plan) if s.start<=start<s.end)
        self.populate_segment(index)

    def apply_confirmations(self,plan):
        result=list(plan)
        for interval in self.manual_intervals:
            action=('remove' if self.mode.currentIndex()==0 else 'speed') if interval.kind=='pause' else 'keep'
            result=edit_range(result,interval.start,interval.end,action,self.speed_value())
        return result

    def confirm_range(self,kind):
        if not self.video or self.worker or self.dirty:return
        start,end=self.range_coordinates()
        try:
            intervals=assign_manual(self.manual_intervals,start,end,kind,self.video.duration)
            # Restore the automatic baseline only in the selected range, then
            # apply remaining confirmations. Other editing actions are retained.
            plan=list(self.plan)
            for segment in self.automatic:
                a=max(start,segment.start);b=min(end,segment.end)
                if a<b:plan=edit_range(plan,a,b,segment.action,segment.speed)
            self.manual_intervals=intervals
            for interval in intervals:
                a=max(start,interval.start);b=min(end,interval.end)
                if a<b:
                    action=('remove' if self.mode.currentIndex()==0 else 'speed') if interval.kind=='pause' else 'keep'
                    plan=edit_range(plan,a,b,action,self.speed_value())
            self.plan=plan
        except ValueError as error:self.show_error(error);return
        self.journal.add('manual_confirmation',start=start,end=end,kind=kind,
                         intervals=[asdict(item) for item in self.manual_intervals])
        if kind=='auto' and hasattr(self.samples,'windows'):
            self.coverage_dirty=True;self.dirty=True
        self.invalidate_preview();self.render_plan()

    def populate_segment(self,index,select=True):
        if not self.plan:return
        s=self.plan[index];self.start.setValue(s.start);self.end.setValue(s.end);self.start.setEnabled(index>0);self.end.setEnabled(index<len(self.plan)-1)
        self.action.setCurrentIndex({'keep':0,'remove':1,'speed':2}[s.action])
        if select:
            self.context_mode.setCurrentIndex(1);self.sidebar_visible=True
            self.timeline.index=index;self.timeline.update();self.table.selectRow(index);self.selected_box.show()
        label=self.t('segment.selected',start=clock(s.start),end=clock(s.end))
        reasons=review_reasons(self.timeline.reviews,s.start,s.end)
        if reasons:label+='\n'+review_tip(self.language,self.timeline.reviews,s.start,s.end)
        self.selected_label.setText(label)

    def select_segment(self,index):
        if not self.plan or self.worker:return
        self.preview_mode.setCurrentIndex(0);self.populate_segment(index);self.player.setPosition(round(self.plan[index].start*1000))

    def set_action(self,action):
        index=self.timeline.index
        if self.worker or self.dirty or not 0<=index<len(self.plan):return
        self.journal.add('manual_action',index=index,start=self.plan[index].start,end=self.plan[index].end,action=action,speed=self.speed_value())
        self.plan[index]=replace(self.plan[index],action=action,speed=self.speed_value());self.manual_changed=True;self.plan_reset_notice=False;self.invalidate_preview();self.render_plan();self.populate_segment(index)

    def apply_edit(self):
        index=max(0,self.timeline.index)
        try:
            plan=move_boundary(self.plan,index,self.start.value(),self.end.value(),self.video.duration)
            plan[index]=replace(plan[index],action=('keep','remove','speed')[self.action.currentIndex()],speed=self.speed_value())
            self.plan=plan;self.manual_changed=True;self.plan_reset_notice=False;self.invalidate_preview();self.render_plan();self.populate_segment(index)
            self.journal.add('manual_boundaries',index=index,start=self.start.value(),end=self.end.value(),action=self.plan[index].action)
        except ValueError as error:self.show_error(error)

    def reset_plan(self):
        self.journal.add('manual_plan_reset');self.plan=self.apply_confirmations(self.automatic);self.manual_changed=False;self.plan_reset_notice=False;self.invalidate_preview();self.render_plan()

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
        if error!=QMediaPlayer.NoError and self.video and not self.closing:
            if self.video_expanded:self._focus_codec_visible=True
            else:self.codec_notice.show()

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
        if len(self.project)>1:
            self.start_project_export();return
        if not self.plan or self.dirty or self.worker:return
        if statistics(self.plan)['sections']==0 and not self.question('unchanged.title','unchanged.confirm'):return
        dialog=ExportDialog(self)
        if dialog.exec()!=QDialog.Accepted:return
        suggested=str(self.video.path.with_name(self.video.path.stem+'_smartcut.mp4'))
        destination,_=QFileDialog.getSaveFileName(self,self.t('export.title'),suggested,'MP4 (*.mp4)',options=QFileDialog.DontConfirmOverwrite|QFileDialog.DontUseNativeDialog)
        if not destination:return
        quality=(18,23,28)[dialog.quality.currentIndex()];mute=dialog.audio.currentIndex()==1;encoder=('libx264','h264_nvenc','h264_qsv','h264_amf')[dialog.encoder.currentIndex()]
        self.work(lambda job,p:export(self.video,list(self.plan),destination,job,p,crf=quality,mute=mute,encoder=encoder),self.exported,Text('state.exporting'),'export')

    def start_project_export(self):
        if self.worker:return
        self.save_current()
        states=[e['state'] for e in self.project]
        if any(not st or not st['fields']['plan'] or st['fields']['dirty'] for st in states):
            self.show_error(AppError('batch.not_ready'));return
        if any(statistics(st['fields']['plan'])['output']<.02 for st in states):
            self.show_error(AppError('batch.empty'));return
        if any(statistics(st['fields']['plan'])['sections']==0 for st in states):
            if not self.question('unchanged.title','unchanged.confirm'):return
        dialog=ExportDialog(self)
        if dialog.exec()!=QDialog.Accepted:return
        merged=dialog.output.currentIndex()==0
        if merged:
            destination,_=QFileDialog.getSaveFileName(self,self.t('export.title'),str(self.video.path.with_name('project_smartcut.mp4')),'MP4 (*.mp4)',options=QFileDialog.DontConfirmOverwrite|QFileDialog.DontUseNativeDialog)
        else:destination=QFileDialog.getExistingDirectory(self,self.t('batch.directory'),str(self.video.path.parent),options=QFileDialog.DontUseNativeDialog)
        if not destination:return
        items=[(e['video'],list(st['fields']['plan'])) for e,st in zip(self.project,states)]
        options=dict(crf=(18,23,28)[dialog.quality.currentIndex()],mute=dialog.audio.currentIndex()==1,
                     encoder=('libx264','h264_nvenc','h264_qsv','h264_amf')[dialog.encoder.currentIndex()])
        self.work(lambda job,p:export_project(items,destination,job,p,merged=merged,**options),self.exported,Text('state.exporting'),'export')

    def exported(self,path):self.last_export_path=str(path)

    def save_debug_report(self):
        if self.worker:return
        path,_=QFileDialog.getSaveFileName(self,self.t('debug.save'),'SmartVideoCutter-debug.zip','ZIP (*.zip)',options=QFileDialog.DontConfirmOverwrite|QFileDialog.DontUseNativeDialog)
        if path:
            if not path.lower().endswith('.zip'):path+='.zip'
            captured=snapshot(self)
            self.work(lambda job,p:write_report(captured,path,job,p),self.report_saved,Text('state.report'),'report')

    def report_saved(self,path):self.last_report_path=str(path)

    def highlight_drop(self,active):
        if self.drop_zone.property('dragActive')!=active:
            self.drop_zone.setProperty('dragActive',active);self.drop_zone.style().unpolish(self.drop_zone);self.drop_zone.style().polish(self.drop_zone)

    def closeEvent(self,event):
        self.closing=True
        if self.worker:self.cancel_job();event.ignore();return
        self.eta_timer.stop();self.player.stop();self.player.setSource(QUrl());shutil.rmtree(self.cache,ignore_errors=True);event.accept()


STYLE='''
QWidget { background: #171c24; color: #e0e6ee; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
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
QPushButton,QComboBox,QDoubleSpinBox { background: #252e3b; border: 1px solid #323e4e; border-radius: 6px; padding: 6px 9px; min-height: 20px; }
QPushButton:hover { background: #354558; }
QPushButton:disabled { color: #677589; background: #202835; }
QPushButton#primary { background: #69d4ae; color: #112c23; font-weight: 650; border: 0; padding: 7px 12px; }
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
QTableView { background: #202835; gridline-color: #384556; border: 0; selection-background-color: #34584d; }
QHeaderView::section { background: #293443; border: 0; padding: 6px; }
QScrollArea { border: 0; }
QMenu { background: #252e3b; border: 1px solid #394556; padding: 6px; }
QMenu::item { padding: 8px 16px; }
QMenu::item:selected { background: #354558; }
QScrollBar:horizontal { background: #1a222d; height: 10px; border: 0; }
QScrollBar::handle:horizontal { background: #435269; min-width: 28px; border-radius: 4px; }
QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar:vertical { background: #1a222d; width: 8px; margin: 0; border: 0; }
QScrollBar::handle:vertical { background: #435269; min-height: 24px; border-radius: 4px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical { background: transparent; }
'''


def main():
    app=QApplication(sys.argv);app.setApplicationName('Smart Video Cutter');app.setStyleSheet(STYLE)
    window=Window();window.show();sys.exit(app.exec())


if __name__=='__main__':main()
