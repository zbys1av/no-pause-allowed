from PySide6.QtWidgets import QApplication

from smartcut.app import Window
from smartcut.model import Segment, MotionSample, statistics
from smartcut.video import Video
from pathlib import Path
import time
import pytest


def test_filters_are_visual_and_show_all_restores_them():
    app = QApplication.instance() or QApplication([])
    window = Window(language='en', remember=False)
    try:
        plan = [Segment(0, 2), Segment(2, 4, 'remove'), Segment(4, 6, 'speed', 2)]
        window.timeline.set_plan(plan)
        before = statistics(plan)
        for button in window.filter_buttons.values():
            button.setChecked(False)
        window.timeline.hidden_segments.add(window.timeline.segment_key(plan[1]))
        assert not window.timeline.visible_layers
        assert window.timeline.plan == plan
        assert statistics(window.timeline.plan) == before
        window.show_all_timeline()
        assert window.timeline.visible_layers == {'keep', 'remove', 'speed', 'review'}
        assert not window.timeline.hidden_segments
        assert window.timeline.plan == plan
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize('duration',[3268.733333,3268.7338])
def test_displayed_eof_is_exact_source_end(duration):
    from smartcut.manual_intervals import automatic_intervals
    app=QApplication.instance() or QApplication([])
    window=Window(language='en',remember=False)
    try:
        window.loaded(Video(Path('eof.mp4'),duration,320,180,30,1,'mp4',False))
        window.set_range(2588.808,duration)
        assert window.remove_range_button.isEnabled()
        assert window.range_coordinates()==(2588.808,duration)
        window.confirm_range('pause')
        assert window.manual_intervals[-1].end==duration
        assert automatic_intervals(window.manual_intervals,duration)==[(0,2588.808)]
        assert window.plan[-1].end==duration and window.plan[-1].action=='remove'
        window.set_range(10,20)
        assert window.range_coordinates()==(10,20)
    finally:
        window.close();app.processEvents()


def test_visibility_is_independent_per_file_and_review_layer():
    app=QApplication.instance() or QApplication([])
    window=Window(language='en',remember=False)
    try:
        segment=Segment(0,2,'remove');hidden_one={window.timeline.segment_key(segment)};hidden_two=set()
        assert not window.timeline.segment_visible(segment,hidden_one)
        assert window.timeline.segment_visible(segment,hidden_two)
        window.timeline.set_layer_visible('remove',False)
        assert not window.timeline.segment_visible(segment,hidden_two)
        assert window.timeline.segment_visible(segment,hidden_two,'review')
        window.timeline.set_layer_visible('review',False)
        assert not window.timeline.segment_visible(segment,hidden_two,'review')
        hidden_one.discard(window.timeline.segment_key(segment))
        window.timeline.set_layer_visible('remove',True)
        assert window.timeline.segment_visible(segment,hidden_one)
    finally:
        window.close();app.processEvents()


def test_hidden_key_survives_file_switch_but_not_plan_split():
    app=QApplication.instance() or QApplication([])
    window=Window(language='en',remember=False)
    try:
        one=Video(Path('one.mp4'),10,320,180,24,1,'mp4',False)
        two=Video(Path('two.mp4'),8,320,180,24,1,'mp4',False)
        window.project=[dict(video=one,state=None),dict(video=two,state=None)]
        window.loaded(one);window.refresh_project()
        key=window.timeline.segment_key(window.plan[0]);window.timeline.hidden_segments.add(key)
        window.switch_file(1);window.switch_file(0)
        assert key in window.timeline.hidden_segments
        window.set_range(2,4);window.apply_range_action('remove')
        assert key not in window.timeline.hidden_segments
    finally:
        window.close();app.processEvents()


def test_confirmations_are_separate_from_edit_actions_and_reset():
    app = QApplication.instance() or QApplication([])
    window = Window(language='en', remember=False)
    try:
        window.loaded(Video(Path('source.mp4'), 10, 320, 180, 24, 1, 'mp4', False))
        window.set_range(2, 5);window.confirm_range('pause')
        assert [(x.start,x.end,x.kind) for x in window.manual_intervals] == [(2,5,'pause')]
        assert any(s.start == 2 and s.end == 5 and s.action == 'remove' for s in window.plan)
        window.reset_plan()
        assert any(s.action == 'remove' for s in window.plan)
        window.set_range(3,4);window.confirm_range('auto')
        assert [(x.start,x.end) for x in window.manual_intervals] == [(2,3),(4,5)]
        assert any(s.start == 3 and s.end == 4 and s.action == 'keep' for s in window.plan)
    finally:
        window.close();app.processEvents()


def test_project_time_keeps_clip_boundaries():
    app = QApplication.instance() or QApplication([])
    window = Window(language='en', remember=False)
    try:
        timeline=window.timeline;timeline.resize(300,42)
        timeline.set_project([('one',10,[Segment(0,10)],[],set(),[]),('two',20,[Segment(0,20)],[],set(),[])])
        assert timeline.project_time(timeline.width()/3) == pytest.approx((1,0))
        assert timeline.project_time(timeline.width()*2/3) == pytest.approx((1,10))
        assert timeline.project_time(timeline.width()) == pytest.approx((1,20))
    finally:
        window.close();app.processEvents()


def test_confirmations_survive_reanalysis_and_file_switch():
    app = QApplication.instance() or QApplication([])
    window = Window(language='en', remember=False)
    try:
        one=Video(Path('one.mp4'),10,320,180,24,1,'mp4',False)
        two=Video(Path('two.mp4'),8,320,180,24,1,'mp4',False)
        window.project=[dict(video=one,state=None),dict(video=two,state=None)]
        window.loaded(one);window.refresh_project()
        window.set_range(2,5);window.confirm_range('pause')
        window.analyzed([MotionSample(0,0,False),MotionSample(10,0,False)])
        assert len(window.manual_intervals)==1
        assert all(s.action=='remove' for s in window.plan if 2<=s.start and s.end<=5)
        window.switch_file(1);assert not window.manual_intervals
        window.set_range(1,3);window.confirm_range('motion')
        window.switch_file(0)
        assert [(x.start,x.end,x.kind) for x in window.manual_intervals]==[(2,5,'pause')]
        window.timeline_mode.setCurrentIndex(1)
        assert len(window.timeline.project_clips)==2
        assert window.timeline.project_time(window.timeline.width()*10/18)==(1,0)
        window.show();app.processEvents()
        assert not window.timeline.grab().isNull()
    finally:
        window.close();app.processEvents()


def test_fully_confirmed_video_skips_detector(monkeypatch):
    app=QApplication.instance() or QApplication([])
    window=Window(language='en',remember=False)
    try:
        window.loaded(Video(Path('manual.mp4'),10,320,180,24,1,'mp4',False))
        window.set_range(0,4);window.confirm_range('pause')
        window.set_range(4,10);window.confirm_range('motion')
        def unexpected(*args,**kwargs):raise AssertionError('Detector must not run')
        monkeypatch.setattr('smartcut.app.analyze',unexpected)
        window.start_analysis();deadline=time.monotonic()+5
        while window.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert window.worker is None
        assert not window.samples and window.report is None
        assert [(s.start,s.end,s.action) for s in window.plan]==[(0,4,'remove'),(4,10,'keep')]
        assert window.last_analysis_debug['performance']['frames']==0
    finally:
        window.close();app.processEvents()


def test_batch_skips_fully_confirmed_clips(monkeypatch):
    app=QApplication.instance() or QApplication([])
    window=Window(language='en',remember=False)
    try:
        one=Video(Path('one.mp4'),10,320,180,24,1,'mp4',False)
        two=Video(Path('two.mp4'),8,320,180,24,1,'mp4',False)
        window.project=[dict(video=one,state=None),dict(video=two,state=None)]
        window.loaded(one);window.refresh_project()
        window.set_range(0,10);window.confirm_range('pause')
        window.switch_file(1);window.set_range(0,8);window.confirm_range('motion')
        def unexpected(*args,**kwargs):raise AssertionError('Detector must not run')
        monkeypatch.setattr('smartcut.app.analyze',unexpected)
        window.analyze_all();deadline=time.monotonic()+5
        while window.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert window.worker is None
        first,second=[entry['state']['fields'] for entry in window.project]
        assert first['report'] is None and second['report'] is None
        assert first['plan'][0].action=='remove' and second['plan'][0].action=='keep'
    finally:
        window.close();app.processEvents()


def test_reopening_unmeasured_range_requires_analysis():
    from smartcut.motion import WindowedSamples
    app=QApplication.instance() or QApplication([])
    window=Window(language='en',remember=False)
    try:
        window.loaded(Video(Path('coverage.mp4'),10,320,180,24,1,'mp4',False))
        window.set_range(3,7);window.confirm_range('pause')
        samples=WindowedSamples([(0,10,[(0,3),(7,10)],
            [MotionSample(0,0,False),MotionSample(10,0,False)])])
        window.analyzed(samples)
        window.set_range(4,6);window.confirm_range('auto')
        assert window.coverage_dirty and window.dirty
        assert not window.export_button.isEnabled()
    finally:
        window.close();app.processEvents()
