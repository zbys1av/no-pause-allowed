from dataclasses import replace
import subprocess
import numpy as np
from smartcut.model import Settings,analyze_plan
from smartcut.motion import analyze,analyze_selected
from smartcut.process import Job,Cancelled,tool
from smartcut.video import probe
import pytest


def test_selected_window_preserves_original_sample_timestamps(tmp_path):
    source=tmp_path/'window.mkv'
    subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
        '-f','lavfi','-i','testsrc2=size=160x100:rate=16:duration=6',
        '-vf',"select='not(mod(n,3))+not(mod(n,7))'",'-fps_mode','vfr',
        '-c:v','ffv1',str(source)],check=True)
    video=probe(source);settings=Settings(analysis_width=320)
    from smartcut.decode_strategy import cfr_period
    assert cfr_period(video,Job()) is None
    full=analyze(video,settings,Job())
    partial=analyze(video,settings,Job(),window=(2.,4.))
    assert [s.time for s in partial]==[s.time for s in full if 2<=s.time<=4]
    assert partial and all(2<=s.time<=4 for s in partial)


def test_partial_manual_analysis_excludes_manual_time_from_statistics(tmp_path):
    from smartcut.manual_intervals import ManualInterval
    from smartcut.model import detection_statistics
    source=tmp_path/'partial.mkv'
    subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
        '-f','lavfi','-i','color=gray:size=160x100:rate=8:duration=30',
        '-c:v','ffv1',str(source)],check=True)
    video=probe(source);settings=Settings(analysis_width=320)
    job=Job();samples=analyze_selected(video,settings,job,[ManualInterval(4,26,'pause')])
    report=analyze_plan(samples,video.duration,settings)
    measured=detection_statistics(samples,video.duration,settings.min_idle)
    assert measured['original']==pytest.approx(8)
    assert all(b<=4 or a>=26 for a,b in report.raw_idle)
    assert job.debug_analysis_performance['frames']<30*settings.sample_fps
    assert job.debug_analysis_performance['decode_strategy']=='seek_verified_cfr'


def test_activity_near_manual_boundaries_is_kept(tmp_path):
    from smartcut.manual_intervals import ManualInterval
    source=tmp_path/'boundary.mkv'
    cmd=[tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-f','rawvideo',
         '-pixel_format','rgb24','-video_size','160x100','-framerate','8','-i','pipe:0',
         '-c:v','ffv1',str(source)]
    with subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        for i in range(160):
            frame=np.full((100,160,3),180,np.uint8)
            if i>=30:frame[30:40,20:20+min(80,i-29)]=0
            if i>=130:frame[70:80,30:30+min(80,i-129)]=0
            process.stdin.write(frame.tobytes())
        process.stdin.close();error=process.stderr.read();assert process.wait()==0,error
    video=probe(source);settings=Settings(analysis_width=320)
    measured=analyze_selected(video,settings,Job(),[ManualInterval(4,16,'pause')])
    plan=analyze_plan(measured,video.duration,settings).plan
    for position in (3.8,16.4):
        assert next(s.action for s in plan if s.start<=position<s.end)=='keep'


def test_cfr_seek_retains_sampling_grid_with_b_frames(tmp_path):
    from smartcut.decode_strategy import cfr_period
    source=tmp_path/'seek.mp4'
    subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
        '-f','lavfi','-i','testsrc2=size=160x100:rate=30:duration=30',
        '-c:v','libx264','-g','240',str(source)],check=True)
    video=probe(source);settings=Settings(analysis_width=320)
    period=cfr_period(video,Job());assert period is not None
    baseline=analyze(video,settings,Job(),window=(20,25))
    seek=analyze(video,settings,Job(),window=(20,25),seek_at=period*4*135)
    assert [s.time for s in seek]==[s.time for s in baseline]


def test_av1_seek_preserves_presented_frames_and_evidence(tmp_path):
    from smartcut.decode_strategy import cfr_period
    source=tmp_path/'seek-av1.mp4'
    subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
        '-f','lavfi','-i','testsrc2=size=160x100:rate=30:duration=12',
        '-c:v','libaom-av1','-cpu-used','8','-crf','40','-g','120','-threads','2',str(source)],check=True)
    video=probe(source);settings=Settings(analysis_width=320);job=Job()
    period=cfr_period(video,job);assert period is not None,job.decode_probe
    full=analyze(video,settings,Job(),window=(8,11))
    selected=analyze(video,settings,Job(),window=(8,11),seek_at=period*4*48)
    assert selected==full
    from smartcut.manual_intervals import ManualInterval
    job=Job();result=analyze_selected(video,settings,job,[ManualInterval(0,9,'pause')])
    assert job.debug_analysis_performance['decode_strategy']=='seek_verified_cfr'
    assert job.debug_analysis_performance['window_details'][0]['seek_at']>0
    for a,b,_,items in result.windows:
        assert items==analyze(video,settings,Job(),window=(a,b))


def test_dense_intervals_choose_one_continuous_decoder():
    from smartcut.decode_strategy import choose_decode
    from smartcut.video import Video
    from pathlib import Path
    video=Video(Path('unused.mp4'),60,160,100,30,1,'mp4',False)
    windows=[(i*5,i*5+1,[(i*5,i*5+1)]) for i in range(12)]
    merged,cadence,strategy=choose_decode(video,Settings(),Job(),windows)
    assert merged==windows
    assert cadence is None and strategy=='continuous_dense_or_short'


def test_vfr_fallback_skips_manual_frames_in_one_process(tmp_path):
    from smartcut.manual_intervals import ManualInterval,analysis_windows
    source=tmp_path/'vfr-fallback.mkv'
    subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
        '-f','lavfi','-i','testsrc2=size=160x100:rate=16:duration=30',
        '-vf',"select='not(mod(n,3))+not(mod(n,7))'",'-fps_mode','vfr',
        '-c:v','ffv1',str(source)],check=True)
    video=probe(source);settings=Settings(analysis_width=320)
    full=analyze(video,settings,Job())
    manual=[ManualInterval(4,26,'motion')];job=Job()
    selected=analyze_selected(video,settings,job,manual)
    windows=analysis_windows(manual,video.duration,3.9)
    assert [s.time for s in selected]==[s.time for s in full if any(a<=s.time<=b for a,b,_ in windows)]
    stats=job.debug_analysis_performance
    assert stats['frames']<len(full) and stats['ffmpeg_processes']==1
    assert stats['seek_probe']['reason']=='irregular_packet_timestamps'
    for a,b,ranges,samples in selected.windows:
        reference=analyze(video,settings,Job(),window=(a,b))
        assert [(s.time,s.score,s.active,s.evidence) for s in samples]==[(s.time,s.score,s.active,s.evidence) for s in reference]


def test_real_decode_preserves_brief_cursor_and_single_pixel_stroke(tmp_path):
    source=tmp_path/'brief actions.mkv'
    command=[tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
        '-f','rawvideo','-pixel_format','rgb24','-video_size','160x100',
        '-framerate','16','-i','pipe:0','-an','-c:v','ffv1',str(source)]
    with subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        for i in range(160):
            frame=np.full((100,160,3),200,np.uint8)
            if i in (82,83):frame[30:34,40:44]=20
            if i>=108:frame[70,90]=20
            process.stdin.write(frame.tobytes())
        process.stdin.close();errors=process.stderr.read().decode(errors='replace')
        assert process.wait()==0,errors
    video=probe(source);settings=Settings(debounce=0)
    baseline=analyze(video,replace(settings,adaptive=False),Job())
    job=Job();samples=analyze(video,settings,job)
    key=lambda s:(s.time,s.score,s.active,s.transient,s.persistent,s.bounds)
    assert list(map(key,samples))==list(map(key,baseline))
    assert any(s.active for s in samples if 5<=s.time<=5.4)
    assert any(s.active and s.persistent for s in samples if 6.5<=s.time<=7)
    assert analyze_plan(samples,video.duration,settings).plan==analyze_plan(baseline,video.duration,settings).plan
    assert job.debug_analysis_performance['certified_quiet']>20
    cancelled=Job()
    with pytest.raises(Cancelled):analyze(video,settings,cancelled,lambda *_:cancelled.cancel())
    assert cancelled.debug_samples and cancelled.debug_analysis_performance['frames']>0
