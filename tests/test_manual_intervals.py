import pytest
import re
import subprocess
from smartcut.process import tool
from smartcut.manual_intervals import ManualInterval,assign,automatic_intervals,analysis_windows,measured_intersections,ffmpeg_window_filter


def test_overwrite_and_restore_auto_preserve_other_confirmations():
    intervals=assign([],2,8,'pause',10)
    intervals=assign(intervals,4,6,'motion',10)
    assert intervals==[ManualInterval(2,4,'pause'),ManualInterval(4,6,'motion'),ManualInterval(6,8,'pause')]
    intervals=assign(intervals,3,7,'auto',10)
    assert intervals==[ManualInterval(2,3,'pause'),ManualInterval(7,8,'pause')]
    assert automatic_intervals(intervals,10)==[(0.,2),(3,7),(8,10)]


def test_adjacent_confirmations_merge_and_full_coverage_has_no_auto():
    intervals=assign([],0,4,'motion',10)
    intervals=assign(intervals,4,10,'motion',10)
    assert intervals==[ManualInterval(0,10,'motion')]
    assert automatic_intervals(intervals,10)==[]


def test_context_windows_merge_without_counting_confirmations_as_measured():
    manual=[ManualInterval(2,3,'pause'),ManualInterval(5,9,'motion')]
    assert analysis_windows(manual,12,1)==[(0,6,[(0.,2),(3,5)]),(8,12,[(9,12)])]
    assert analysis_windows([ManualInterval(0,12,'pause')],12,1)==[]
    assert analysis_windows([],12,1)==[(0,12,[(0.,12)])]
    with pytest.raises(ValueError):analysis_windows(manual,12,float('nan'))


def test_measured_statistics_exclude_context_inside_manual_intervals():
    automatic=[(0,2),(3,5),(9,12)]
    assert measured_intersections([(1,4),(4.5,10)],automatic)==[(1,2),(3,4),(4.5,5),(9,10)]
    assert measured_intersections([(2,3),(5,9)],automatic)==[]


def test_ffmpeg_window_filter_preserves_original_pts_expression():
    assert ffmpeg_window_filter([(0,2,[]),(4,6,[])]) == "select='between(t,0.000000000,2.000000000)+between(t,4.000000000,6.000000000)'"
    assert ffmpeg_window_filter([])=="select='0'"
    with pytest.raises(ValueError):ffmpeg_window_filter([(2,4,[]),(3,5,[])])


def test_window_filter_preserves_vfr_source_timestamps():
    # Create an irregular sequence by dropping frames without resetting PTS.
    base="select='not(mod(n,3))+not(mod(n,7))'"
    def timestamps(extra):
        proc=subprocess.run([tool('ffmpeg'),'-hide_banner','-nostdin','-f','lavfi',
            '-i','testsrc=size=32x32:rate=10:duration=3','-vf',base+extra+',showinfo',
            '-fps_mode','vfr','-f','null','-'],capture_output=True,check=True)
        return [float(value) for value in re.findall(r'\bn:\s*\d+.*?pts_time:([\d.eE+\-]+)',proc.stderr.decode(errors='replace'))]
    original=timestamps('')
    selected=timestamps(','+ffmpeg_window_filter([(0.4,1.1,[]),(2,2.6,[])]))
    assert selected==[t for t in original if .4<=t<=1.1 or 2<=t<=2.6]


@pytest.mark.parametrize('start,end,kind',[(1,1,'pause'),(-1,2,'pause'),(0,11,'motion'),(0,2,'remove'),(float('nan'),2,'pause')])
def test_invalid_ranges_are_rejected(start,end,kind):
    with pytest.raises(ValueError):assign([],start,end,kind,10)
