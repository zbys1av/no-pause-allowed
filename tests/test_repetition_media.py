from dataclasses import replace
import subprocess
import numpy as np
import pytest
from smartcut.motion import analyze
from smartcut.model import Settings,analyze_plan,statistics
from smartcut.video import probe
from smartcut.process import Job,tool
from smartcut.export import export


def test_real_codec_two_pixel_noisy_pulses_and_idle_tail_export(tmp_path):
    source=tmp_path/'blink-tail.mkv'
    command=[tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
        '-f','rawvideo','-pixel_format','gray','-video_size','960x540',
        '-framerate','15','-i','pipe:0','-an','-c:v','ffv1',str(source)]
    with subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        for i in range(24*15):
            frame=np.full((540,960),200,np.uint8)
            x=100+min(i,29)*2
            frame[200:210,x:x+10]=30
            if i>=60:
                n=(i-60)//30+1
                # Return leaves a small local residual: the old exact return
                # classifier calls both changes persistent, just like the report.
                frame[30,40:42]=30 if (i-60)%30==0 else 190 if n%2 else 200
            process.stdin.write(frame.tobytes())
        process.stdin.close();errors=process.stderr.read().decode(errors='replace')
        assert process.wait()==0,errors
    video=probe(source);settings=Settings()
    samples=analyze(video,settings,Job())
    assert any(s.evidence=='repeated_local_cycle' for s in samples)
    old=[replace(s,transient=False,persistent=s.active) for s in samples]
    assert statistics(analyze_plan(old,video.duration,settings).plan)['removed']==0
    report=analyze_plan(samples,video.duration,settings)
    assert report.plan[-1].action=='remove' and report.plan[-1].end==video.duration
    assert statistics(report.plan)['removed']>20
    target=tmp_path/'edited.mp4';export(video,list(report.plan),target,Job())
    output=probe(target)
    assert 2<output.duration<3
    assert output.duration==pytest.approx(statistics(report.plan)['output'],abs=.15)
