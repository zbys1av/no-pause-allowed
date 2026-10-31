"""Regression for the reported two-pixel event every two seconds at the tail."""
from dataclasses import replace
import numpy as np
import pytest
from smartcut.motion import MotionTracker, cursor_or_return
from smartcut.model import Settings,analyze_plan,statistics
from smartcut.repetition import LocalCycles


def blink_tail(kind='blink',duration=29):
    tracker=MotionTracker(95)
    for i in range(duration*10):
        t=i/10
        frame=np.full((100,160,3),200,np.uint8)
        # Five seconds of real activity followed by an otherwise idle canvas.
        x=10+min(i,49)%30
        frame[70:80,x:x+10]=30
        if t>=7:
            n=int((t-7)//2)+1
            if kind=='blink':frame[30,40:42]=30 if n%2 else 200
            elif kind=='noisy_pulse':frame[30,40:42]=30 if (i-70)%20==0 else 190 if n%2 else 200
            elif kind=='stroke':
                for j in range(n):frame[30,40+j*2:42+j*2]=30
            elif kind=='fade':frame[30,40:42]=200-n*8
        tracker.add(t,frame)
    return tracker


@pytest.mark.parametrize('kind',['blink','noisy_pulse'])
def test_two_pixel_periodic_blink_no_longer_keeps_idle_tail(kind):
    tracker=blink_tail(kind)
    old=[replace(s,transient=False,persistent=s.active) for s in tracker.samples]
    legacy=analyze_plan(old,29,Settings())
    assert len(legacy.plan)==1 and legacy.plan[0].action=='keep'
    report=analyze_plan(tracker.samples,29,Settings())
    assert report.merged_bursts>=9
    assert report.plan[-1].action=='remove'
    assert report.plan[-1].start==pytest.approx(5.5,abs=.2)
    assert statistics(report.plan)['removed']>23
    assert any(s.evidence=='repeated_local_cycle' and s.bounds==(40,30,42,31) for s in tracker.samples)
    # Spatial recurrence is evidence only. Turning debounce off keeps all motion.
    disabled=analyze_plan(tracker.samples,29,Settings(debounce=0))
    assert disabled.plan==legacy.plan
    assert disabled.raw_idle==report.raw_idle


@pytest.mark.parametrize('kind',['stroke','fade'])
def test_equally_small_and_regular_lasting_brush_changes_are_preserved(kind):
    tracker=blink_tail(kind)
    assert not any(s.evidence=='repeated_local_cycle' for s in tracker.samples)
    report=analyze_plan(tracker.samples,29,Settings())
    assert len(report.plan)==1 and report.plan[0].action=='keep'
    assert any(s.persistent for s in tracker.samples if s.time>7)


def test_nonperiodic_toggles_and_large_periodic_animation_stay_active():
    for large in (False,True):
        tracker=MotionTracker(95)
        times=[2,3,6,7,11,12,17,18] if not large else list(range(2,20,2))
        for i in range(210):
            t=i/10;frame=np.full((100,160,3),200,np.uint8)
            if sum(t>=v for v in times)%2:
                if large:frame[20:60,20:60]=30
                else:frame[30,40:42]=30
            tracker.add(t,frame)
        assert not any(s.evidence=='repeated_local_cycle' for s in tracker.samples)


def test_weak_isolated_codec_residual_does_not_invalidate_cursor_return():
    before=np.full((100,160,3),200,np.uint8)
    after=before.copy();after[10,10]=190  # weak, isolated residual, no coherent support
    assert cursor_or_return(before,after,95)
    after[60,60]=30  # genuine lasting strong one-pixel mark
    assert not cursor_or_return(before,after,95)


def test_recurrence_memory_is_bounded_to_small_patches():
    cycles=LocalCycles();frame=np.zeros((320,320,3),np.uint8)
    for j in range(16):
        x=(j%8)*32;y=(j//8)*32
        for i in range(15):cycles.observe((x,y,x+2,y+1),frame,frame,i*2,i*2+.2,i,i+1)
    assert len(cycles.locations)==8
    assert len(cycles.backgrounds)==8
    assert all(len(history)==9 for history in cycles.locations.values())
    assert all(e.before.shape[:2]==(32,32) for history in cycles.locations.values() for e in history)


def test_growing_faint_stroke_outside_blink_patch_invalidates_cycle():
    tracker=MotionTracker(95)
    for i in range(260):
        t=i/10;frame=np.full((100,160,3),200,np.uint8)
        if int(t//2)%2:frame[30,40:42]=30
        # Each adjacent pair is too weak. Without an independent background
        # anchor the blink would reset gradual detection every two seconds.
        frame[70,100:102]=200-min(20,int(t*2))
        tracker.add(t,frame)
    assert any(s.evidence=='background_changed' for s in tracker.samples)
    report=analyze_plan(tracker.samples,26,Settings())
    assert report.plan[0].action=='keep' and report.plan[0].end>8
    assert report.plan[-1].action=='remove' and report.plan[-1].start<16


def test_cycle_evidence_survives_debug_report_and_replay(tmp_path):
    import csv,io,json,zipfile
    from PySide6.QtWidgets import QApplication
    from smartcut.app import Window
    from smartcut.debug import snapshot,write_report
    from smartcut.debug_replay import replay
    from smartcut.video import Video
    from smartcut.process import Job
    app=QApplication.instance() or QApplication([])
    window=Window(language='uk',remember=False)
    window.video=Video(tmp_path/'drawing.mp4',29,160,100,10,1,'mp4',False)
    window.analyzed(blink_tail('noisy_pulse').samples)
    target=tmp_path/'cycles.zip';write_report(snapshot(window),target,Job())
    with zipfile.ZipFile(target) as archive:
        rows=list(csv.DictReader(io.StringIO(archive.read('samples.csv').decode())))
        repeated=[r for r in rows if r['evidence']=='repeated_local_cycle']
        assert repeated and json.loads(repeated[0]['bounds'])==[40,30,42,31]
        data=json.loads(archive.read('report.json'))
        assert any('repeated_local_cycle' in b['evidence'] for b in data['burst_decisions'])
    assert replay(target)['matches_recorded_automatic_plan']
    window.close();app.processEvents()
