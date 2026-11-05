"""Reusable Qt views; all editing policy lives in model/controller modules."""
from bisect import bisect_right,bisect_left
from math import log1p
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer, QRectF, QAbstractTableModel,QPropertyAnimation,QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPainterPath
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QWidget,QGraphicsView,QGraphicsScene,QGraphicsRectItem,
    QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QToolButton,QFrame,QToolTip,QMenu,QScrollArea)
from pathlib import Path
from .i18n import tr,Text
from .icons import icon
from .review import overlapping_reviews


def clock(value):
    value=max(0,value)
    return f"{int(value)//3600:02}:{int(value)//60%60:02}:{value%60:05.2f}"


def review_tip(language,spans,start,end):
    overlaps=overlapping_reviews(spans,start,end)
    if not overlaps:return ''
    reasons=sorted({r for s in overlaps for r in s.reasons})
    return tr(language,'review.tip',time=clock(sum(s.end-s.start for s in overlaps)),
              duration=clock(end-start),reasons='; '.join(tr(language,'review.'+r) for r in reasons))


def video_drop_paths(mime):
    if not mime.hasUrls():return []
    paths=[]
    for url in mime.urls():
        if not url.isLocalFile():return []
        path=url.toLocalFile()
        if Path(path).suffix.lower() not in {'.mp4','.mov','.mkv','.webm','.avi'}:return []
        paths.append(path)
    return paths


def video_drop_path(mime):
    if not mime.hasUrls() or len(mime.urls()) != 1 or not mime.urls()[0].isLocalFile():
        return None
    path = mime.urls()[0].toLocalFile()
    return path if Path(path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"} else None


class VideoDropFilter(QObject):
    """Handle file drops before graphics/table viewports consume drag events."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.DragLeave:
            self.window.highlight_drop(False)
        if kind not in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop):
            return False
        if not event.mimeData().hasUrls():
            return False
        paths = video_drop_paths(event.mimeData())
        if self.window.worker or not paths:
            event.ignore()
            return True
        event.acceptProposedAction()
        self.window.highlight_drop(kind != QEvent.Drop)
        if kind == QEvent.Drop:
            if len(paths)==1:QTimer.singleShot(0,lambda:self.window.open_video(paths[0]))
            else:QTimer.singleShot(0,lambda:self.window.open_videos(paths))
        return True


class VideoView(QGraphicsView):
    area_changed = Signal(object)
    expand_requested = Signal()

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

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.LeftButton and not self.custom:
            self.expand_requested.emit();event.accept()
        else:super().mouseDoubleClickEvent(event)

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


class Fold(QWidget):
    def __init__(self,key,language='uk'):
        super().__init__()
        self.key,self.language=key,language
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.toggle=QToolButton();self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setObjectName('foldToggle')
        self.body=QWidget();self.content=QVBoxLayout(self.body)
        self.content.setContentsMargins(2,8,2,8)
        self.body.hide();layout.addWidget(self.toggle);layout.addWidget(self.body)
        self.animation=QPropertyAnimation(self.body,b'maximumHeight',self);self.animation.setDuration(120)
        self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.animation.finished.connect(lambda:self.body.setMaximumHeight(16777215) if self.toggle.isChecked() else None)
        self.toggle.toggled.connect(self.set_open)
        self.translate(language)

    def translate(self,language):
        self.language=language;self.toggle.setText(tr(language,self.key))
        self.toggle.setIcon(icon('chevron-down' if self.toggle.isChecked() else 'chevron-right'))

    def set_open(self,opened):
        self.animation.stop();self.toggle.blockSignals(True);self.toggle.setChecked(opened);self.toggle.blockSignals(False)
        self.body.setVisible(opened)
        if opened:
            self.body.setMaximumHeight(0);self.animation.setStartValue(0);self.animation.setEndValue(max(1,self.body.sizeHint().height()));self.animation.start()
        self.toggle.setIcon(icon('chevron-down' if opened else 'chevron-right'))


class SettingRow(QWidget):
    def __init__(self,key,widget,default,reset,language='uk'):
        super().__init__()
        self.key,self.widget,self.default=key,widget,default
        layout=QVBoxLayout(self);layout.setContentsMargins(0,2,0,8);layout.setSpacing(5)
        row=QHBoxLayout();self.name=QLabel();self.name.setObjectName('settingName')
        self.info=QToolButton();self.info.setIcon(icon('info',size=14))
        self.reset=QToolButton();self.reset.setIcon(icon('rotate-ccw',size=14));self.reset.clicked.connect(reset)
        row.addWidget(self.name);row.addWidget(self.info);row.addStretch();row.addWidget(self.reset)
        self.help=QLabel();self.help.setWordWrap(True);self.help.setObjectName('muted')
        self.default_label=QLabel();self.default_label.setObjectName('muted')
        layout.addLayout(row);layout.addWidget(widget);layout.addWidget(self.help);layout.addWidget(self.default_label)
        self.help.hide();self.default_label.hide();self.info.setCheckable(True)
        self.info.toggled.connect(self.help.setVisible);self.info.toggled.connect(self.default_label.setVisible)
        self.translate(language)

    def translate(self,language):
        self.name.setText(tr(language,self.key))
        self.help.setText(tr(language,self.key+'.help'))
        self.info.setToolTip(self.help.text())
        self.info.setAccessibleName(self.help.text())
        value=tr(language,self.default.key,**self.default.params) if isinstance(self.default,Text) else self.default
        self.default_label.setText(tr(language,'default',value=value))
        self.reset.setToolTip(tr(language,'reset'))
        self.reset.setAccessibleName(tr(language,'reset'))


class PlanTableModel(QAbstractTableModel):
    """Format only requested cells; large hidden plans create no table items."""
    def __init__(self,parent=None):
        super().__init__(parent);self.plan=[];self.reviews=[];self.language='uk'
        self.review_starts=[];self.review_ends=[]

    def set_plan(self,plan,reviews,language):
        self.beginResetModel()
        self.plan=list(plan);self.reviews=list(reviews);self.language=language
        self.review_starts=[s.start for s in reviews];self.review_ends=[s.end for s in reviews]
        self.endResetModel()

    def rowCount(self,parent=None):return 0 if parent is not None and parent.isValid() else len(self.plan)
    def columnCount(self,parent=None):return 0 if parent is not None and parent.isValid() else 3

    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role==Qt.DisplayRole and orientation==Qt.Horizontal and 0<=section<3:
            return tr(self.language,('start','end','action')[section])
        return super().headerData(section,orientation,role)

    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid() or not 0<=index.row()<len(self.plan):return None
        s=self.plan[index.row()];column=index.column()
        if role==Qt.DisplayRole and column<2:return clock(s.start if column==0 else s.end)
        if role not in (Qt.DisplayRole,Qt.ToolTipRole,Qt.BackgroundRole):return None
        begin=bisect_right(self.review_ends,s.start);end=bisect_left(self.review_starts,s.end)
        overlaps=self.reviews[begin:end]
        if role==Qt.DisplayRole:
            return tr(self.language,{'keep':'keep','remove':'remove','speed':'speed_up'}[s.action])+(' ⚠' if overlaps else '')
        if role==Qt.BackgroundRole:return QBrush(QColor('#43365b')) if overlaps and column==2 else QBrush()
        if overlaps:return review_tip(self.language,overlaps,s.start,s.end)


class StatCard(QFrame):
    def __init__(self,key,tip=None):
        super().__init__();self.key,self.tip=key,tip;self.setObjectName('statCard')
        layout=QVBoxLayout(self);layout.setContentsMargins(12,9,12,9)
        self.title=QLabel();self.title.setObjectName('muted');self.title.setWordWrap(True)
        self.value=QLabel('—');self.value.setObjectName('statValue');self.value.setWordWrap(True)
        layout.addWidget(self.title);layout.addWidget(self.value)

    def translate(self,language):
        self.title.setText(tr(language,self.key))
        if self.tip:self.setToolTip(tr(language,self.tip))


class TimelineViewport(QScrollArea):
    """Native horizontal scrolling over the existing painted source timeline."""
    zoom_changed=Signal(int)
    def __init__(self,timeline):
        super().__init__();self.timeline=timeline;self.zoom=1
        self.setFrameShape(QFrame.NoFrame);self.setWidget(timeline)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(78);self.setMaximumHeight(78)
        timeline.setFixedHeight(56);timeline.setFixedWidth(640)

    def set_zoom(self,value):
        value=max(1,min(16,int(value)))
        bar=self.horizontalScrollBar();center=(bar.value()+self.viewport().width()/2)/max(1,self.timeline.width())
        self.zoom=value;self.timeline.setFixedWidth(max(1,self.viewport().width())*value)
        bar.setValue(round(center*self.timeline.width()-self.viewport().width()/2))
        self.zoom_changed.emit(value)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.set_zoom(self.zoom)

    def wheelEvent(self,event):
        if event.modifiers() & Qt.ControlModifier:
            self.set_zoom(self.zoom+(1 if event.angleDelta().y()>0 else -1));event.accept()
        else:super().wheelEvent(event)


class Timeline(QWidget):
    selected=Signal(int)
    range_selected=Signal(float,float)
    project_seek=Signal(int,float)
    def __init__(self):
        super().__init__();self.plan=[];self.reviews=[];self.index=-1;self.position=0.;self.language='uk';self.ends=[]
        self.setMinimumHeight(42);self.setMouseTracking(True)
        self.range_mode=False;self.drag_start=None;self.selection=None
        self.visible_layers={'keep','remove','speed','review'};self.hidden_segments=set()
        self.project_clips=[]
        self.project_index=0
        self.project_ends=[]
        self.confirmations=[]

    def set_project(self,clips):
        """Clips retain original-time boundaries; editing remains local to a clip."""
        self.project_clips=list(clips)
        self.project_ends=[[s.end for s in clip[2]] for clip in self.project_clips]
        self.update()

    def project_time(self,x):
        total=sum(clip[1] for clip in self.project_clips)
        t=min(total,max(0,x/max(1,self.width())*total));offset=0.
        for i,(_,duration,_,_,_,_) in enumerate(self.project_clips):
            if t<offset+duration or i==len(self.project_clips)-1:
                return i,min(duration,max(0.,t-offset))
            offset+=duration
        return None

    @staticmethod
    def segment_key(segment):
        return (segment.start,segment.end)

    def segment_visible(self,segment,hidden,layer=None):
        """Visibility affects painting only; hidden keys belong to one clip."""
        return self.segment_key(segment) not in hidden and (layer or segment.action) in self.visible_layers

    def set_layer_visible(self,layer,visible):
        if visible:self.visible_layers.add(layer)
        else:self.visible_layers.discard(layer)
        self.update()

    def show_hidden(self):
        self.hidden_segments.clear();self.update()

    def contextMenuEvent(self,event):
        if self.project_clips:
            point=self.project_time(event.pos().x())
            if point is None:return
            clip_index,position=point;_,_,plan,_,hidden,_=self.project_clips[clip_index]
            index=min(len(plan)-1,bisect_right([s.end for s in plan],position))
            if index<0:return
        else:
            index=self.at(event.pos().x());plan=self.plan;hidden=self.hidden_segments
            if index is None:return
        menu=QMenu(self)
        key=self.segment_key(plan[index]);is_hidden=key in hidden
        hide=menu.addAction(tr(self.language,'timeline.restore_segment' if is_hidden else 'timeline.hide_segment'))
        show=menu.addAction(tr(self.language,'timeline.show_hidden'))
        show.setEnabled(bool(hidden))
        chosen=menu.exec(event.globalPos())
        if chosen==hide:
            if is_hidden:hidden.discard(key)
            else:hidden.add(key)
            self.update()
        elif chosen==show:hidden.clear();self.update()

    def set_plan(self,plan,reviews=()):
        self.plan=list(plan);self.reviews=list(reviews);self.ends=[s.end for s in plan];self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#242b35'))
        if self.project_clips:
            self.paint_project(p,event);return
        if not self.plan:return
        duration=self.plan[-1].end
        left_time=max(0,event.rect().left()/max(1,self.width())*duration)
        right_time=min(duration,(event.rect().right()+1)/max(1,self.width())*duration)
        first=bisect_right(self.ends,left_time)
        for i in range(first,len(self.plan)):
            s=self.plan[i]
            if s.start>right_time:break
            if not self.segment_visible(s,self.hidden_segments):continue
            rect=QRectF(s.start/duration*self.width(),4,(s.end-s.start)/duration*self.width(),self.height()-8)
            p.fillRect(rect,QColor({'keep':'#4bae91','remove':'#bd6679','speed':'#c1a15c'}[s.action]))
            p.setPen(QPen(QColor('#f3f6fb' if i==self.index else '#1a2029'),2 if i==self.index else 1));p.drawRect(rect)
        for span in self.reviews if 'review' in self.visible_layers else ():
            if span.end<=left_time or span.start>right_time:continue
            index=bisect_right(self.ends,span.start)
            while index<len(self.plan) and self.plan[index].start<span.end:
                segment=self.plan[index];index+=1
                if not self.segment_visible(segment,self.hidden_segments,'review'):continue
                start=max(span.start,segment.start);end=min(span.end,segment.end)
                left=start/duration*self.width();width=(end-start)/duration*self.width()
                p.fillRect(QRectF(left,4,width,self.height()-12),QColor('#55416f'))
                p.fillRect(QRectF(left,7,width,self.height()-15),QBrush(QColor('#c2a1ff'),Qt.BDiagPattern))
                p.fillRect(QRectF(left,4,width,4),QColor('#b08aff'))
        for interval in self.confirmations:
            left=interval.start/duration*self.width();width=(interval.end-interval.start)/duration*self.width()
            p.fillRect(QRectF(left,0,width,3),QColor('#ffffff' if interval.kind=='motion' else '#ffb4c2'))
        p.setPen(QPen(QColor('#fff'),2));x=int(self.position/duration*self.width());p.drawLine(x,0,x,self.height())
        if self.selection:
            a,b=sorted(self.selection);left=a/duration*self.width();right=b/duration*self.width()
            rect=QRectF(left,1,right-left,self.height()-2)
            p.fillRect(rect,QColor(100,170,255,85));p.setPen(QPen(QColor('#b9ddff'),2,Qt.DashLine));p.drawRect(rect)

    def source_time(self,x):
        return min(1,max(0,x/max(1,self.width())))*self.plan[-1].end if self.plan else 0

    def at(self,x):
        if not self.plan:return None
        return min(len(self.plan)-1,bisect_right(self.ends,max(0,x)/max(1,self.width())*self.plan[-1].end))

    def mousePressEvent(self,event):
        if self.project_clips:
            if event.button()==Qt.LeftButton:
                point=self.project_time(event.position().x())
                if point:self.project_seek.emit(*point)
            return
        if self.plan and event.button()==Qt.LeftButton and (self.range_mode or event.modifiers()&Qt.ShiftModifier):
            self.drag_start=self.source_time(event.position().x());self.selection=(self.drag_start,self.drag_start);self.update();return
        index=self.at(event.position().x())
        if index is not None:self.selected.emit(index)

    def mouseMoveEvent(self,event):
        if self.project_clips:return
        if self.drag_start is not None:
            self.selection=(self.drag_start,self.source_time(event.position().x()));self.update();return
        index=self.at(event.position().x())
        if index is not None:
            s=self.plan[index]
            tip=tr(self.language,'segment.tip',
                start=clock(s.start),end=clock(s.end),duration=clock(s.end-s.start),
                action=tr(self.language,{'keep':'keep','remove':'remove','speed':'speed_up'}[s.action]))
            review=review_tip(self.language,self.reviews,s.start,s.end)
            if review:tip+='\n\n'+review
            QToolTip.showText(event.globalPosition().toPoint(),tip,self)

    def mouseReleaseEvent(self,event):
        if self.project_clips:return
        if self.drag_start is not None:
            a,b=sorted((self.drag_start,self.source_time(event.position().x())));self.drag_start=None
            self.selection=(a,b) if b-a>=.001 else None;self.update()
            if self.selection:self.range_selected.emit(a,b)
        else:super().mouseReleaseEvent(event)

    def paint_project(self,p,event):
        total=sum(clip[1] for clip in self.project_clips)
        if total<=0:return
        offset=0.
        for clip_index,(name,duration,plan,reviews,hidden,confirmations) in enumerate(self.project_clips):
            left=offset/total*self.width();right=(offset+duration)/total*self.width()
            if right>=event.rect().left() and left<=event.rect().right():
                ends=self.project_ends[clip_index]
                visible_start=max(0,event.rect().left()/max(1,self.width())*total-offset)
                visible_end=min(duration,(event.rect().right()+1)/max(1,self.width())*total-offset)
                for index in range(bisect_right(ends,visible_start),len(plan)):
                    s=plan[index]
                    if s.start>visible_end:break
                    if not self.segment_visible(s,hidden):continue
                    x=(offset+s.start)/total*self.width();w=(s.end-s.start)/total*self.width()
                    p.fillRect(QRectF(x,15,w,self.height()-19),QColor({'keep':'#4bae91','remove':'#bd6679','speed':'#c1a15c'}[s.action]))
                if 'review' in self.visible_layers:
                    for span in reviews:
                        if span.end<=visible_start or span.start>visible_end:continue
                        for index in range(bisect_right(ends,span.start),len(plan)):
                            s=plan[index]
                            if s.start>=span.end:break
                            if not self.segment_visible(s,hidden,'review'):continue
                            a=max(span.start,s.start);b=min(span.end,s.end)
                            x=(offset+a)/total*self.width();w=(b-a)/total*self.width()
                            p.fillRect(QRectF(x,15,w,self.height()-23),QColor('#55416f'))
                for interval in confirmations:
                    x=(offset+interval.start)/total*self.width();w=(interval.end-interval.start)/total*self.width()
                    p.fillRect(QRectF(x,12,w,3),QColor('#ffffff' if interval.kind=='motion' else '#ffb4c2'))
                p.setPen(QColor('#e5ebf2'));p.drawLine(int(left),0,int(left),self.height())
                p.save();p.setClipRect(QRectF(left,0,max(0,right-left),15));p.drawText(QRectF(left+3,0,max(0,right-left-4),15),Qt.AlignLeft|Qt.AlignVCenter,name);p.restore()
            offset+=duration
        if 0<=self.project_index<len(self.project_clips):
            position=sum(c[1] for c in self.project_clips[:self.project_index])+self.position
            x=round(position/total*self.width());p.setPen(QPen(QColor('#fff'),2));p.drawLine(x,0,x,self.height())


class MotionGraph(QWidget):
    def __init__(self):
        super().__init__();self.points=[];self.setMinimumHeight(70)

    def set_samples(self,samples,duration):
        bins={}
        for timestamp,score,_ in samples:
            index=min(999,int(timestamp/max(.001,duration)*1000))
            bins[index]=max(bins.get(index,0),log1p(score*100000))
        scale=max(bins.values(),default=1) or 1
        self.points=[(i/1000,value/scale) for i,value in sorted(bins.items())];self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#171d25'))
        path=QPainterPath()
        for i,(x,y) in enumerate(self.points):
            px,py=x*self.width(),self.height()-4-y*(self.height()-8)
            if i:path.lineTo(px,py)
            else:path.moveTo(px,py)
        painter.setPen(QPen(QColor('#71bfa9'),1));painter.drawPath(path)


