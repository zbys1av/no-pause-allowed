from pathlib import Path
import subprocess
import numpy as np
import pytest
from PySide6.QtCore import Qt,QPoint
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from smartcut.app import Window,STYLE
from smartcut.model import Segment,MotionSample,split_plan,edit_range,validate_plan,statistics
from smartcut.video import Video,probe
from smartcut.export import export
from smartcut.process import Job,tool


def test_exact_splits_and_manual_range_across_existing_actions():
    original=[Segment(0,3),Segment(3,7,'speed',5),Segment(7,10)]
    split=split_plan(original,[0,2,3,4,4,8,10])
    assert [(s.start,s.end) for s in split]==[(0,2),(2,3),(3,4),(4,7),(7,8),(8,10)]
    assert statistics(split)['output']==statistics(original)['output']
    edited=edit_range(original,2,8)
    assert all(s.action=='remove' for s in edited if s.start>=2 and s.end<=8)
    assert edited[0]==Segment(0,2) and edited[-1]==Segment(8,10)
    assert original==[Segment(0,3),Segment(3,7,'speed',5),Segment(7,10)]
    validate_plan(edited,10)
    assert statistics(edited)['output']==4
    assert statistics(edit_range(edited,2,8,'keep'))['output']==10
    for a,b in [(4,4),(6,2),(-1,4),(2,11),(float('nan'),3)]:
        with pytest.raises(ValueError):edit_range(original,a,b)


def editor():
    w=Window(language='uk',remember=False);w.setStyleSheet(STYLE)
    w.loaded(Video(Path('/tmp/source.mp4'),10,320,180,24,1,'mp4',False))
    w.show();QApplication.processEvents()
    return w


def test_review_intervals_are_individually_removable_sections():
    w=editor()
    try:
        w.analyzed([MotionSample(0,0,False),MotionSample(4,0,False),
                    MotionSample(4.2,.01,True,persistent=True,evidence='background_changed'),MotionSample(10,0,False)])
        index=next(i for i,s in enumerate(w.plan) if abs(s.start-4)<1e-8 and abs(s.end-4.2)<1e-8)
        before=list(w.plan);duration=statistics(before)['output']
        w.select_segment(index);w.set_action('remove')
        assert w.plan[index].action=='remove'
        assert w.plan[:index]==before[:index] and w.plan[index+1:]==before[index+1:]
        assert statistics(w.plan)['output']==pytest.approx(duration-.2)
        w.reset_plan();assert w.plan==before
    finally:w.close();QApplication.processEvents()


def test_drag_manual_range_before_analysis_and_restore():
    w=editor()
    try:
        assert not w.samples and w.timeline.isVisible()
        w.range_button.click()
        a=QPoint(round(.7*w.timeline.width()),20);b=QPoint(round(.2*w.timeline.width()),20)
        QTest.mousePress(w.timeline,Qt.LeftButton,pos=a)
        QTest.mouseMove(w.timeline,b);QTest.mouseRelease(w.timeline,Qt.LeftButton,pos=b)
        assert w.range_box.isVisible()
        assert w.range_start.value()==pytest.approx(2,abs=.03)
        assert w.range_end.value()==pytest.approx(7,abs=.03)
        w.range_start.setValue(2);w.range_end.setValue(7);w.remove_range_button.click()
        assert w.plan==[Segment(0,2),Segment(2,7,'remove'),Segment(7,10)]
        assert not w.samples and w.manual_changed and w.export_button.isEnabled()
        w.results_fold.set_open(True);w.manual.set_open(True);assert w.table.isVisible()
        w.set_range(2,7);w.keep_range_button.click()
        assert all(s.action=='keep' for s in w.plan)
        w.reset_plan();assert w.plan==[Segment(0,10)]
        w.set_range(7,2);assert not w.remove_range_button.isEnabled()
    finally:w.close();QApplication.processEvents()


def test_shift_drag_without_range_mode():
    w=editor()
    try:
        assert not w.range_button.isChecked()
        QTest.mousePress(w.timeline,Qt.LeftButton,Qt.ShiftModifier,QPoint(10,20))
        QTest.mouseRelease(w.timeline,Qt.LeftButton,Qt.ShiftModifier,QPoint(w.timeline.width()//2,20))
        assert w.range_button.isChecked() and w.range_end.value()>w.range_start.value()
    finally:w.close();QApplication.processEvents()


def test_manual_removal_exports_only_selected_source_intervals(tmp_path):
    def ff(*args):
        return subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',*map(str,args)],check=True,capture_output=True).stdout
    source=tmp_path/'source.mp4'
    ff('-f','lavfi','-i','color=red:s=160x90:r=24:d=1','-f','lavfi','-i','color=green:s=160x90:r=24:d=1',
       '-f','lavfi','-i','color=blue:s=160x90:r=24:d=1','-filter_complex','[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]',
       '-map','[v]','-c:v','libx264',source)
    video=probe(source);plan=edit_range([Segment(0,video.duration)],1,2)
    # Editable review cuts do not force tiny, redundant encoding pieces.
    plan=split_plan(plan,[.01,.02,.03,2.01,2.02])
    output=tmp_path/'manual.mp4';export(video,plan,output,Job())
    assert probe(output).duration==pytest.approx(2,abs=.1)
    for t,channel in ((.5,0),(1.5,2)):
        raw=ff('-ss',t,'-i',output,'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','pipe:1')
        assert np.frombuffer(raw,dtype=np.uint8).reshape(-1,3).mean(axis=0)[channel]>200
