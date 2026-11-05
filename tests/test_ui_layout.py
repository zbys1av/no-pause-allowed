from pathlib import Path
from PySide6.QtCore import Qt,QPointF
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from smartcut.app import Window,STYLE
from smartcut.model import Segment
from smartcut.video import Video
from smartcut.widgets import PlanTableModel,Fold
from smartcut.review import ReviewSpan
import pytest


@pytest.mark.parametrize('width,height',[(1280,720),(1366,768),(1920,1080)])
def test_workspace_resize_zoom_and_context_preserve_plan(width,height):
    w=editor()
    try:
        w.resize(width,height);QApplication.processEvents()
        assert w.width()==width and w.height()==height
        before=list(w.plan);source=w.player.source();duration=w.timeline.plan[-1].end
        w.zoom.setValue(4);QApplication.processEvents()
        assert w.timeline.width()==w.timeline_viewport.viewport().width()*4
        assert w.timeline.source_time(w.timeline.width())==duration
        assert w.timeline_viewport.horizontalScrollBar().maximum()>0
        for index in (1,2,0):w.context_mode.setCurrentIndex(index)
        assert w.plan==before and w.player.source()==source
        keys=[s.key().toString() for s in w.command_shortcuts]+[w.expand_shortcut.key().toString(),w.escape_shortcut.key().toString()]
        assert len(keys)==len(set(keys))
        assert w.export_button.parentWidget() is w.header_widget
        assert w.analyze_button.parentWidget() is w.header_widget
    finally:w.close();QApplication.processEvents()


def editor():
    w=Window(language='uk',remember=False)
    w.setStyleSheet(STYLE)
    w.loaded(Video(Path('/tmp/input.mp4'),10,320,180,24,1,'mp4',False))
    w.analyzed([(0,0,False),(3,1,True),(8,0,False),(10,1,True)])
    w.show();QApplication.processEvents()
    return w


def test_expand_restores_editor_and_preserves_player_and_edits():
    w=editor()
    try:
        w.results_fold.set_open(True);w.populate_segment(0)
        w.sidebar_button.click();assert not w.sidebar.isVisible()
        original_plan=list(w.plan);original_source=w.player.source()
        original_area=w.area;small=w.view.size()
        w.expand_button.click();QApplication.processEvents()
        assert w.video_expanded and not w.header_widget.isVisible() and not w.results_fold.isVisible()
        assert w.view.width()>w.width()*.95 and w.view.height()>w.height()*.8
        assert w.view.width()>small.width()
        assert w.player.source()==original_source and w.plan==original_plan and w.area==original_area
        w.render();assert not w.timeline.isVisible()
        w.expand_button.click();QApplication.processEvents()
        assert not w.video_expanded and w.results_fold.toggle.isChecked()
        assert not w.sidebar.isVisible() and w.timeline.index==0
        w.sidebar_button.click();assert w.sidebar.isVisible()
        assert w.context_mode.currentIndex()==1 and w.selected_box.isVisible()
    finally:w.close();QApplication.processEvents()


def test_double_click_shortcut_and_roi_gesture():
    w=editor()
    try:
        w.activateWindow();QTest.qWait(30)
        QTest.mouseDClick(w.view.viewport(),Qt.LeftButton)
        assert w.video_expanded
        QTest.keyClick(w,Qt.Key_Escape);assert not w.video_expanded
        QTest.keyClick(w,Qt.Key_Return,Qt.ControlModifier);assert w.video_expanded
        w.set_video_expanded(False)
        w.detection.setCurrentIndex(1)
        QTest.mouseDClick(w.view.viewport(),Qt.LeftButton);assert not w.video_expanded
        w.view.origin=None
        a=w.view.mapFromScene(QPointF(30,20));b=w.view.mapFromScene(QPointF(200,120))
        QTest.mousePress(w.view.viewport(),Qt.LeftButton,pos=a)
        QTest.mouseMove(w.view.viewport(),b)
        QTest.mouseRelease(w.view.viewport(),Qt.LeftButton,pos=b)
        assert w.area!=(0,0,1,1)
    finally:w.close();QApplication.processEvents()


def test_compact_sections_open_immediately():
    w=editor()
    try:
        assert not w.drop_zone.isVisible() and not w.project_help.isVisible()
        assert not w.results_fold.body.isVisible() and not w.rows['minimum'].help.isVisible()
        w.rows['minimum'].info.click();assert w.rows['minimum'].help.isVisible()
        w.context_mode.setCurrentIndex(2)
        assert w.results_fold.body.isVisible()
        w.context_mode.setCurrentIndex(0)
        w.advanced.toggle.click();assert w.advanced.body.isVisible()
        assert w.advanced.body.graphicsEffect() is None
        w.set_language('en');assert w.expand_button.text()=='Expand video'
        assert w.results_fold.toggle.text()=='Analysis statistics'
    finally:w.close();QApplication.processEvents()


def test_manual_edits_and_menus_reuse_measurements(monkeypatch):
    import smartcut.app as module
    w=editor();calls=[]
    original=module.review_spans
    def counted(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(module,'review_spans',counted)
    try:
        w.render_plan();w.render();w.set_language('en');w.speed.setCurrentIndex(2)
        assert not calls
        w.plan[0]=Segment(w.plan[0].start,w.plan[0].end,'keep')
        w.render_plan();assert not calls
        w.minimum.setValue(2);assert len(calls)==1
    finally:w.close();QApplication.processEvents()


def test_large_plan_is_formatted_on_demand_and_review_boundaries():
    model=PlanTableModel()
    plan=[Segment(i,i+1) for i in range(20000)]
    model.set_plan(plan,[ReviewSpan(100,101,('background_conflict',))],'uk')
    assert model.rowCount()==20000 and model.columnCount()==3
    assert '⚠' not in model.index(99,2).data()
    assert '⚠' in model.index(100,2).data()
    assert '⚠' not in model.index(101,2).data()
    assert 'Зміна фону' in model.index(100,2).data(Qt.ToolTipRole)
    assert model.index(100,2).data(Qt.BackgroundRole).color().name()=='#43365b'
    assert model.index(19999,0).data()=='05:33:19.00'


def test_fast_worker_does_not_flood_ui_with_progress():
    from smartcut.worker import Worker
    updates=[];results=[]
    def operation(job,progress):
        for i in range(10000):progress(i/10000,'analysis')
        progress(1,'complete')
        return 'done'
    worker=Worker(operation)
    worker.progress.connect(lambda f,s:updates.append((f,s)));worker.result.connect(results.append)
    worker.run()
    assert len(updates)<100 and updates[-1]==(1,'complete') and results==['done']
