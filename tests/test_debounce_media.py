"""Exercise visual evidence through actual FFmpeg decoding and final export."""
from dataclasses import replace
import subprocess
import numpy as np
import pytest
from smartcut.model import Settings, analyze_plan, statistics, validate_plan
from smartcut.motion import analyze
from smartcut.video import probe
from smartcut.process import Job, tool
from smartcut.export import export


def test_cursor_spike_joins_idle_but_short_brush_remains_in_export(tmp_path):
    source = tmp_path/'cursor and short stroke.mkv'
    command = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', '160x100',
        '-framerate', '10', '-i', 'pipe:0', '-an', '-c:v', 'ffv1', str(source)]
    # Two cursor translations in 0.2 s surrounded by stillness; a tiny lasting
    # stroke at 10 s must remain even though it is shorter than debouncing.
    with subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        for i in range(150):
            frame = np.full((100, 160, 3), 200, np.uint8)
            x = 40 if i < 50 else 45 if i == 50 else 50
            frame[40:44, x:x+4] = 0
            if i >= 100:
                frame[60, 60:65] = 0
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        errors = process.stderr.read().decode(errors='replace')
        assert process.wait() == 0, errors
    video = probe(source)
    settings = Settings(sample_fps=10, padding=.3)
    samples = analyze(video, settings, Job())
    old = analyze_plan(samples, video.duration, replace(settings, debounce=0))
    report = analyze_plan(samples, video.duration, settings)
    assert report.merged_bursts == 1
    assert len(old.stable_idle) == 3 and len(report.stable_idle) == 2
    assert report.stable_idle[0][1] == pytest.approx(9.9, abs=.11)
    assert any(s.action == 'keep' and s.start <= 9.9 and s.end >= 10 for s in report.plan)
    validate_plan(report.plan, video.duration)
    output = tmp_path/'cut.mp4'
    export(video, list(report.plan), output, Job())
    result = probe(output)
    assert .5 < result.duration < 1.1 < video.duration
    assert result.duration == pytest.approx(statistics(report.plan)['output'], abs=.12)
    # Decode the retained central frame: the actual short stroke survived.
    raw = subprocess.run([tool('ffmpeg'), '-hide_banner', '-loglevel', 'error',
        '-ss', '0.4', '-i', str(output), '-frames:v', '1', '-f', 'rawvideo',
        '-pix_fmt', 'rgb24', 'pipe:1'], check=True, capture_output=True).stdout
    frame = np.frombuffer(raw, np.uint8).reshape(100, 160, 3)
    assert frame[60, 60:65].mean() < 80


def test_real_five_motion_eight_idle_five_motion_export(tmp_path):
    source = tmp_path/'five-eight-five.mp4'
    subprocess.run([tool('ffmpeg'), '-hide_banner', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'testsrc2=s=160x100:r=10:d=18', '-vf',
        "drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='gte(t,5)*lt(t,13)'",
        '-c:v', 'libx264', '-threads', '2', str(source)], check=True)
    video = probe(source)
    settings = Settings(sample_fps=10, padding=.6)
    report = analyze_plan(analyze(video, settings, Job()), video.duration, settings)
    assert len(report.pauses) == 1
    assert report.pauses[0] == pytest.approx((5, 12.9), abs=.11)
    output = tmp_path/'shorter.mp4'
    export(video, list(report.plan), output, Job())
    result = probe(output)
    assert result.duration == pytest.approx(statistics(report.plan)['output'], abs=.12)
    assert result.duration < 12 < video.duration
