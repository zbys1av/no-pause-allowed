import subprocess
import pytest
from smartcut.video import probe
from smartcut.model import Settings, Segment
from smartcut.motion import analyze
from smartcut.export import export
from smartcut.process import Job, tool


def ff(*args):
    subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True, capture_output=True)


def test_rotation_and_anamorphic_aspect(tmp_path):
    source = tmp_path/"source.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=2", "-vf", "setsar=2/1", "-c:v", "libx264", source)
    video = probe(source)
    assert video.sar == 2 and video.display_width == 320
    output = tmp_path/"aspect.mp4"
    export(video, [Segment(0, 2)], output, Job())
    assert probe(output).sar == pytest.approx(2)
    rotated = tmp_path/"rotated.mp4"
    ff("-display_rotation", "90", "-i", source, "-c", "copy", rotated)
    video = probe(rotated)
    assert (video.width, video.height) == (90, 160)
    assert video.sar == pytest.approx(.5)
    samples = analyze(video, Settings(area=(0, 0, .5, 1)), Job())
    assert len(samples) > 5 and any(s[2] for s in samples)
    result = tmp_path/"rotated-result.mp4"
    export(video, [Segment(0, 2)], result, Job())
    info = probe(result)
    assert (info.width, info.height) == (90, 160)
    assert info.sar == pytest.approx(.5)
