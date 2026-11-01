from dataclasses import replace
from pathlib import Path
import json,zipfile
import numpy as np
import pytest
from smartcut.model import MotionSample,Settings,analyze_plan
from smartcut.motion import MotionTracker
from smartcut.review import review_spans,review_reasons


def test_long_motion_does_not_evict_its_initial_scene_change():
    tracker=MotionTracker(95)
    for i in range(60*8):
        t=i/8;frame=np.full((100,200,3),200,np.uint8)
        if int(t//2)%2:frame[30,40:42]=30
        if t>=20.5:frame[70:90,160:190]=30
        if 20.5<=t<46:
            x=60+(i%10)*6;frame[70:78,x:x+8]=30
        tracker.add(t,frame)
    assert tracker.cycle_stats['explained_changes']>0
    assert not any(s.evidence=='background_changed' for s in tracker.samples)
    plan=analyze_plan(tracker.samples,60,Settings()).plan
    assert plan[0].action=='remove' and plan[0].end>19
    assert any(s.action=='keep' and s.start<21 and s.end>45 for s in plan)
    assert plan[-1].action=='remove' and plan[-1].start<48
    assert len(tracker.background_coverage)<=8
    assert all(mask.nbytes==(100*200+7)//8 for _,mask in tracker.background_coverage.values())
    assert tracker.burst_pixels is None


def test_review_uses_source_intervals_clips_and_does_not_change_plan():
    samples=[MotionSample(0,0,False),MotionSample(2,.00002,True,persistent=True,evidence='adjacent_change',bounds=(4,4,6,5)),
             MotionSample(4,0,False),MotionSample(6,.5,True,persistent=True,evidence='adjacent_change'),
             MotionSample(8,0,False),MotionSample(9,.00002,True,persistent=True,evidence='background_changed')]
    before=analyze_plan(samples,10,Settings())
    spans=review_spans(samples,10,960*540)
    assert [(s.start,s.end,s.reasons) for s in spans]==[(8,10,('background_conflict',))]
    assert review_reasons(spans,2,8)==[]
    assert review_reasons(spans,8.5,9)==['background_conflict']
    assert analyze_plan(samples,10,Settings())==before
    assert review_spans([MotionSample(0,0,False),MotionSample(2,.00001,True,True,False,'repeated_local_cycle'),MotionSample(3,0,False)],4,960*540)==[]


def test_review_gui_localization_manual_action_and_debug(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from smartcut.app import Window
    from smartcut.video import Video
    from smartcut.debug import snapshot,write_report
    from smartcut.debug_replay import replay
    from smartcut.process import Job
    app=QApplication.instance() or QApplication([])
    window=Window(language='uk',remember=False)
    window.video=Video(tmp_path/'source.mp4',10,320,180,8,1,'mp4',False)
    samples=[MotionSample(0,0,False),MotionSample(3,.00001,True,persistent=True,evidence='background_changed',bounds=(4,4,6,5)),MotionSample(4,0,False)]
    window.analyzed(samples)
    assert window.timeline.reviews
    assert '⚠' in window.table.item(0,2).text()
    assert window.table.item(0,2).background().color().name()=='#43365b'
    image=window.timeline.grab().toImage()
    assert image.pixelColor(5,5).name()=='#b08aff'
    assert image.pixelColor(5,image.height()-6).name()=='#4bae91'
    assert 'Потребує перевірки' in window.result_message.text()
    window.populate_segment(0)
    assert 'Зміна фону' in window.selected_label.text()
    assert '00:00:03.00 із 00:00:03.60' in window.selected_label.text()
    window.set_language('en')
    assert 'Background change' in window.selected_label.text()
    assert window.table.item(0,0).background().style()==Qt.NoBrush
    window.set_action('remove')
    assert window.plan[0].action=='remove' and window.timeline.reviews
    target=tmp_path/'report.zip';write_report(snapshot(window),target,Job())
    with zipfile.ZipFile(target) as z:
        report=json.loads(z.read('report.json'))
        assert report['review']['seconds']==3
        assert report['segment_decisions'][0]['needs_review']
        assert report['segment_decisions'][0]['action']=='remove'
        assert 'review_reasons' in z.read('samples.csv').decode()
    assert replay(target)['matches_recorded_automatic_plan']
    window.close();app.processEvents()


def tiny(time,evidence='adjacent_change',persistent=True):
    return MotionSample(time,2/518400,True,persistent=persistent,evidence=evidence,bounds=(4,4,6,5))


def test_small_strokes_accumulation_legacy_and_final_motion_are_not_warnings():
    drawing=[MotionSample(0,0,False)]+[tiny(i/8) for i in range(1,81)]+[MotionSample(11,0,False)]
    assert not review_spans(drawing,20,518400)
    drawing[20]=tiny(20/8,'accumulated_change')
    assert not review_spans(drawing,20,518400)
    assert not review_spans(drawing[:-1],10,518400)
    assert not review_spans([(0,0,False),(1,.0001,True),(2,0,False)],10,518400)
    assert not review_spans([MotionSample(0,0,False),tiny(10,persistent=False)],10,518400)


def test_only_short_isolated_local_events_need_review_and_context_matters():
    samples=[MotionSample(0,0,False),MotionSample(5,0,False),tiny(5.125),MotionSample(10,0,False)]
    assert [(s.start,s.end,s.reasons) for s in review_spans(samples,10,518400)]==[(5,5.125,('isolated_local_change',))]
    assert review_spans(samples,10,518400,min_idle=6)==[]
    # Actual drawing nearby supplies context even when the queried mark is tiny.
    near=[MotionSample(0,0,False),tiny(4),MotionSample(5,0,False),tiny(5.125),MotionSample(10,0,False)]
    assert not review_spans(near,10,518400)
    # A slow stroke, a fill, and missing legacy metadata are not local impulses.
    assert not review_spans([samples[0],samples[1],replace(tiny(5.125),score=.2,bounds=None),samples[-1]],10,518400)
    assert not review_spans([samples[0],samples[1],tiny(5.5),samples[-1]],10,518400)


def test_confirmed_blinks_do_not_count_as_drawing_context():
    samples=[MotionSample(0,0,False)]
    for i in range(1,81):
        t=i/8
        if i in (16,32,48,64):s=replace(tiny(t,'repeated_local_cycle',False),transient=True)
        elif i==41:s=tiny(t)
        else:s=MotionSample(t,0,False)
        samples.append(s)
    flags=review_spans(samples,10,518400)
    assert len(flags)==1 and flags[0].start==5 and flags[0].end==5.125
    assert not review_spans(samples,10,518400,debounce=0)


def test_explicit_conflicts_remain_visible_during_other_activity():
    samples=[MotionSample(0,0,False),tiny(1),tiny(2,'background_changed'),tiny(3),tiny(4,'unconfirmed_change'),MotionSample(5,0,False)]
    assert [s.reasons for s in review_spans(samples,6,518400)]==[('background_conflict',),('missing_evidence',)]


def test_contextual_flags_match_gui_json_and_csv(tmp_path):
    import csv,io
    from PySide6.QtWidgets import QApplication
    from smartcut.app import Window
    from smartcut.video import Video
    from smartcut.debug import snapshot,write_report
    from smartcut.process import Job
    app=QApplication.instance() or QApplication([])
    w=Window(language='uk',remember=False)
    w.video=Video(tmp_path/'source.mp4',10,960,540,8,1,'mp4',False)
    cases=[([MotionSample(0,0,False),MotionSample(5,0,False),tiny(5.125),MotionSample(10,0,False)],True),
           ([MotionSample(0,0,False)]+[tiny(i/8) for i in range(1,65)]+[MotionSample(9,0,False)],False)]
    for n,(samples,expected) in enumerate(cases):
        w.analyzed(samples)
        assert bool(w.timeline.reviews)==expected
        path=tmp_path/f'review-{n}.zip';write_report(snapshot(w),path,Job())
        with zipfile.ZipFile(path) as z:
            report=json.loads(z.read('report.json'))
            rows=list(csv.DictReader(io.StringIO(z.read('samples.csv').decode())))
        assert bool(report['review']['intervals'])==expected
        assert report['review']['policy']['version']==2
        flagged=[r for r in rows if json.loads(r['review_reasons'])]
        assert bool(flagged)==expected
        if expected:
            assert [json.loads(r['review_reasons']) for r in flagged]==[['isolated_local_change']]
            assert report['review']['seconds']==.125
    w.close();app.processEvents()
