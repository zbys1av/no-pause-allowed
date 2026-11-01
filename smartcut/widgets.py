"""Reusable Qt views; all editing policy lives in model/controller modules."""
from bisect import bisect_right
from math import log1p
from PySide6.QtCore import Qt, Signal, QObject, QEvent, QTimer, QRectF, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QPainterPath
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (QWidget,QGraphicsView,QGraphicsScene,QGraphicsRectItem,
    QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QToolButton,QFrame,QToolTip,QGraphicsOpacityEffect)
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


def video_drop_path(mime):
    if not mime.hasUrls() or len(mime.urls()) != 1 or not mime.urls()[0].isLocalFile():
        return None
    path = mime.urls()[0].toLocalFile()
    return path if Path(path).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"} else None


def video_drop_paths(mime):
    if not mime.hasUrls():return []
    paths=[]
    for url in mime.urls():
        if not url.isLocalFile():return []
        path=url.toLocalFile()
        if Path(path).suffix.lower() not in {'.mp4','.mov','.mkv','.webm','.avi'}:return []
        if path not in paths:paths.append(path)
    return paths


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
            if len(paths)==1:QTimer.singleShot(0, lambda: self.window.open_video(paths[0]))
            else:QTimer.singleShot(0, lambda: self.window.open_videos(paths))
        return True


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
        self.effect=QGraphicsOpacityEffect(self.body);self.body.setGraphicsEffect(self.effect)
        self.animation=QPropertyAnimation(self.effect,b'opacity',self)
        self.animation.setDuration(140);self.animation.setEasingCurve(QEasingCurve.OutCubic)
        self.toggle.toggled.connect(self.set_open)
        self.translate(language)

    def translate(self,language):
        self.language=language;self.toggle.setText(tr(language,self.key))
        self.toggle.setIcon(icon('chevron-down' if self.toggle.isChecked() else 'chevron-right'))

    def set_open(self,opened):
        self.animation.stop()
        self.toggle.setChecked(opened);self.body.setVisible(opened)
        if opened:
            self.animation.setStartValue(.25);self.animation.setEndValue(1.);self.animation.start()
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


class Timeline(QWidget):
    selected=Signal(int)
    def __init__(self):
        super().__init__();self.plan=[];self.reviews=[];self.index=-1;self.position=0.;self.language='uk';self.ends=[]
        self.setMinimumHeight(42);self.setMouseTracking(True)

    def set_plan(self,plan,reviews=()):
        self.plan=list(plan);self.reviews=list(reviews);self.ends=[s.end for s in plan];self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#242b35'))
        if not self.plan:return
        duration=self.plan[-1].end
        for i,s in enumerate(self.plan):
            rect=QRectF(s.start/duration*self.width(),4,(s.end-s.start)/duration*self.width(),self.height()-8)
            p.fillRect(rect,QColor({'keep':'#4bae91','remove':'#bd6679','speed':'#c1a15c'}[s.action]))
            p.setPen(QPen(QColor('#f3f6fb' if i==self.index else '#1a2029'),2 if i==self.index else 1));p.drawRect(rect)
        for span in self.reviews:
            left=span.start/duration*self.width();width=max(2,(span.end-span.start)/duration*self.width())
            # Keep a bottom strip in the action color; the purple body is a
            # separate review status, including after a manual action change.
            p.fillRect(QRectF(left,4,width,self.height()-12),QColor('#55416f'))
            p.fillRect(QRectF(left,7,width,self.height()-15),QBrush(QColor('#c2a1ff'),Qt.BDiagPattern))
            p.fillRect(QRectF(left,4,width,4),QColor('#b08aff'))
        p.setPen(QPen(QColor('#fff'),2));x=int(self.position/duration*self.width());p.drawLine(x,0,x,self.height())

    def at(self,x):
        if not self.plan:return None
        return min(len(self.plan)-1,bisect_right(self.ends,max(0,x)/max(1,self.width())*self.plan[-1].end))

    def mousePressEvent(self,event):
        index=self.at(event.position().x())
        if index is not None:self.selected.emit(index)

    def mouseMoveEvent(self,event):
        index=self.at(event.position().x())
        if index is not None:
            s=self.plan[index]
            tip=tr(self.language,'segment.tip',
                start=clock(s.start),end=clock(s.end),duration=clock(s.end-s.start),
                action=tr(self.language,{'keep':'keep','remove':'remove','speed':'speed_up'}[s.action]))
            review=review_tip(self.language,self.reviews,s.start,s.end)
            if review:tip+='\n\n'+review
            QToolTip.showText(event.globalPosition().toPoint(),tip,self)


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


