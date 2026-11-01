from dataclasses import replace
import subprocess
import numpy as np
from smartcut.model import Settings,analyze_plan
from smartcut.motion import analyze
from smartcut.process import Job,Cancelled,tool
from smartcut.video import probe
import pytest


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
