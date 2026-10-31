from dataclasses import replace
import numpy as np
import pytest
from smartcut.model import Settings, MotionSample, analyze_plan, statistics, validate_plan
from smartcut.motion import MotionTracker


def series(duration, bursts, persistent=False):
    return [MotionSample(i/10, .0001 if any(a<i/10<=b for a,b in bursts) else 0,
                any(a<i/10<=b for a,b in bursts), transient=not persistent, persistent=persistent)
            for i in range(int(duration*10)+1)]


def test_reproduces_fragmented_static_time_with_no_eligible_pause():
    samples = series(20, [(i, i+.2) for i in range(2,20,2)])
    old = analyze_plan(samples,20,Settings(debounce=0))
    assert old.idle_time > 17 and not old.pauses
    assert statistics(old.plan)['removed'] == 0
    report = analyze_plan(samples,20,Settings(debounce=.3))
    assert report.merged_bursts == 9
    assert report.pauses == ((0.,20),)
    assert statistics(report.plan)['removed'] == 20


def test_short_stroke_protected_and_unknown_evidence_preserved():
    samples=series(10.2,[(5,5.2)],persistent=True)
    report=analyze_plan(samples,10.2,Settings(padding=.3))
    assert report.merged_bursts==0
    assert any(s.action=='keep' and s.start<=5 and s.end>=5.2 for s in report.plan)
    triples=[tuple(s) for s in samples]
    assert analyze_plan(triples,10.2,Settings()).merged_bursts==0


def test_actual_pixels_cursor_translation_vs_permanent_brush():
    base=np.full((100,160,3),200,np.uint8)
    old=base.copy();old[40:44,40:44]=0
    moved=base.copy();moved[40:44,45:49]=0
    tracker=MotionTracker(95)
    for t,frame in [(0,old),(1,old),(2,moved),(2.1,moved)]:tracker.add(t,frame)
    assert tracker.samples[-2].transient
    brush=moved.copy();brush[60,60:65]=0
    for t,frame in [(3,brush),(3.1,brush)]:tracker.add(t,frame)
    assert tracker.samples[-2].persistent and not tracker.samples[-2].transient


def test_motion_pause_motion_and_padding_diagnostics():
    samples=series(18,[(0,5),(13,18)],persistent=True)
    report=analyze_plan(samples,18,Settings(padding=.6))
    assert report.pauses==((5.,13.),)
    assert report.available[0]==pytest.approx((5.6,12.4))
    assert statistics(report.plan)['output']==pytest.approx(11.2)
    validate_plan(report.plan,18)
    lost=analyze_plan(samples,18,Settings(min_idle=.5,padding=2))
    assert lost.lost_to_padding==0
    tiny=analyze_plan(series(3,[(0,1),(1.6,3)],True),3,Settings(min_idle=.5,padding=.6))
    assert tiny.lost_to_padding==1 and statistics(tiny.plan)['removed']==0


def test_continuous_motion_and_many_short_pauses():
    assert not analyze_plan(series(10,[(0,10)],True),10,Settings()).pauses
    report=analyze_plan(series(10,[(i,i+.5) for i in range(10)],True),10,Settings())
    assert not report.pauses and report.rejected_short>0
