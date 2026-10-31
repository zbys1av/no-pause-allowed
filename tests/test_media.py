import hashlib
import json
from pathlib import Path
import subprocess
import numpy as np
import pytest
from smartcut.video import probe
from smartcut.motion import analyze
from smartcut.model import Settings, Segment, make_plan, statistics
from smartcut.export import export, proxy
from smartcut.process import Job, Cancelled, tool


def ff(*args):
    return subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "2", *map(str, args)],
                          check=True, capture_output=True).stdout


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    directory = tmp_path_factory.mktemp("media")
    static = directory / "static.mp4"
    moving = directory / "motion.mp4"
    colors = directory / "colors.mp4"
    ff("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=24:d=10", "-c:v", "libx264", "-threads", "2", static)
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=15", "-vf",
       "drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='between(t,5,10)'", "-c:v", "libx264", "-threads", "2", moving)
    # Time-separated colors and tones let tests detect both ordering and A/V drift.
    ff("-f", "lavfi", "-i", "color=c=red:s=160x90:r=25:d=2",
       "-f", "lavfi", "-i", "color=c=green:s=160x90:r=25:d=2",
       "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=25:d=2",
       "-f", "lavfi", "-i", "aevalsrc='sin(2*PI*if(lt(t,2),440,if(lt(t,4),660,880))*t)':s=48000:d=6",
       "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
       "-c:v", "libx264", "-threads", "2", "-c:a", "aac", colors)
    return directory, static, moving, colors


def test_static_10_seconds(clips):
    video = probe(clips[1])
    samples = analyze(video, Settings(), Job())
    assert 60 < len(samples) <= 81
    assert not any(s[2] for s in samples)
    assert make_plan(samples, video.duration, Settings()) == [Segment(0, 10, "remove")]


def test_five_motion_five_static_five_motion(clips):
    video = probe(clips[2])
    settings = Settings(padding=.4)
    samples = analyze(video, settings, Job())
    plan = make_plan(samples, video.duration, settings)
    idle = [s for s in plan if s.action == "remove"]
    assert len(idle) == 1
    assert idle[0].start == pytest.approx(5.4, abs=.4)
    assert idle[0].end == pytest.approx(9.6, abs=.4)
    assert plan[0].start == 0 and plan[-1].end == 15


def test_roi_ignores_motion_outside(clips):
    directory = clips[0]
    path = directory / "roi.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=320x180:r=24:d=3", "-vf",
       "drawbox=x=0:y=0:w=160:h=180:color=gray:t=fill", "-c:v", "libx264", path)
    video = probe(path)
    full = analyze(video, Settings(), Job())
    roi = analyze(video, Settings(area=(0, 0, .45, 1)), Job())
    assert any(s[2] for s in full)
    assert not any(s[2] for s in roi)


def test_small_motion_sensitivity_video(clips):
    path = clips[0] / "small.mp4"
    ff("-f", "lavfi", "-i", "nullsrc=s=320x180:r=24:d=3",
       "-vf", "geq=lum='100+10*between(X,50,55)*between(Y,50,55)*mod(floor(T*6),2)':cb=128:cr=128",
       "-c:v", "libx264", "-crf", "0", path)
    video = probe(path)
    assert any(s[2] for s in analyze(video, Settings(sensitivity=100), Job()))
    assert not any(s[2] for s in analyze(video, Settings(sensitivity=0), Job()))


def test_noise_video(clips):
    path = clips[0] / "noise.mp4"
    ff("-f", "lavfi", "-i", "color=c=gray:s=320x180:r=24:d=3", "-vf", "noise=alls=2:allf=t", "-c:v", "libx264", path)
    assert not any(s[2] for s in analyze(probe(path), Settings(sensitivity=100), Job()))


def color_at(path, time):
    raw = ff("-ss", time, "-i", path, "-frames:v", "1", "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1")
    return np.frombuffer(raw, np.uint8)


def tone_at(path, time):
    raw = ff("-ss", time, "-i", path, "-t", "0.25", "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1")
    audio = np.frombuffer(raw, np.float32)
    assert len(audio) > 10000
    return np.argmax(abs(np.fft.rfft(audio)))*48000/len(audio)


@pytest.mark.parametrize("action,speed", [("remove", 5), ("speed", 2), ("speed", 5), ("speed", 10), ("speed", 20)])
def test_export_duration_order_audio_and_source_preservation(clips, tmp_path, action, speed):
    video = probe(clips[3])
    source_hash = hashlib.sha256(video.path.read_bytes()).digest()
    plan = [Segment(0, 2), Segment(2, 4, action, speed), Segment(4, 6)]
    output = tmp_path / "result.mp4"
    progress = []
    export(video, plan, output, Job(), lambda fraction, stage: progress.append(fraction))
    result = probe(output)
    assert result.audio and (result.width, result.height) == (160, 90)
    assert result.duration == pytest.approx(statistics(plan)["output"], abs=.12)
    assert color_at(output, .5).argmax() == 0
    assert color_at(output, result.duration-.5).argmax() == 2
    assert tone_at(output, .5) == pytest.approx(440, abs=8)
    assert tone_at(output, result.duration-.6) == pytest.approx(880, abs=8)
    if action == "speed":
        assert color_at(output, 2+1/speed).argmax() == 1
    # Decode every frame: no black/empty video frames around joins.
    raw = ff("-i", output, "-vf", "scale=16:16", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1")
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 16, 16, 3)
    assert np.all(frames.mean(axis=(1, 2, 3)) > 15)
    assert progress[-1] == 1 and all(0 <= p <= 1 for p in progress)
    assert source_hash == hashlib.sha256(video.path.read_bytes()).digest()


def test_mute_proxy_and_guards(clips, tmp_path):
    video = probe(clips[3])
    plan = [Segment(0, 6)]
    output = tmp_path / "mute.mp4"
    export(video, plan, output, Job(), mute=True)
    assert not probe(output).audio
    with pytest.raises(ValueError, match="already exists"):
        export(video, plan, output, Job())
    with pytest.raises(ValueError, match="original"):
        export(video, plan, video.path, Job())
    with pytest.raises(ValueError, match="No output"):
        export(video, [Segment(0, 6, "remove")], tmp_path/"empty.mp4", Job())
    preview = tmp_path / "proxy.mp4"
    proxy(video, preview, Job())
    assert probe(preview).duration == pytest.approx(video.duration, abs=.1)


def test_cancel_cleans_temporary_output(clips, tmp_path):
    video = probe(clips[2])
    job = Job()
    output = tmp_path / "cancelled.mp4"
    def cancel(fraction, stage):
        job.cancel()
    with pytest.raises(Cancelled):
        export(video, [Segment(0, 15)], output, job, cancel)
    assert not output.exists()
    assert not list(tmp_path.glob("smartcut-*"))
    job = Job()
    with pytest.raises(Cancelled):
        analyze(video, Settings(), job, lambda *_: job.cancel())


def test_variable_frame_rate_uses_timestamps(clips, tmp_path):
    path = tmp_path/"vfr.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=4", "-vf",
       "select='if(lt(t,2),not(mod(n,4)),1)'", "-fps_mode", "vfr", "-c:v", "libx264", path)
    video = probe(path)
    samples = analyze(video, Settings(), Job())
    assert samples[-1][0] > 3.5
    assert len(samples) < 30
    output = tmp_path/"vfr-result.mp4"
    export(video, [Segment(0, video.duration)], output, Job())
    assert probe(output).duration == pytest.approx(video.duration, abs=.2)


def test_invalid_media_and_no_video(tmp_path):
    corrupt = tmp_path/"broken.mp4"
    corrupt.write_bytes(b"not a video")
    with pytest.raises(ValueError, match="Cannot read"):
        probe(corrupt)
    audio = tmp_path/"audio.mp4"
    ff("-f", "lavfi", "-i", "sine=d=1", "-c:a", "aac", audio)
    with pytest.raises(ValueError, match="no video"):
        probe(audio)


def test_delayed_audio_and_silent_cut(tmp_path):
    source = tmp_path/"delayed.mp4"
    ff("-f", "lavfi", "-i", "color=red:s=160x90:r=24:d=4", "-itsoffset", "1", "-f", "lavfi", "-i",
       "sine=frequency=440:sample_rate=48000:duration=2", "-c:v", "libx264", "-c:a", "aac", source)
    video = probe(source)
    output = tmp_path/"aligned.mp4"
    # The first cut contains no audio packets at all.
    plan = [Segment(0, .5), Segment(.5, 2), Segment(2, 4)]
    export(video, plan, output, Job())
    def rms(time):
        raw = ff("-ss", time, "-i", output, "-t", "0.2", "-vn", "-ac", "1", "-f", "f32le", "pipe:1")
        values = np.frombuffer(raw, np.float32)
        return np.sqrt(np.mean(values**2))
    assert rms(.2) < .001
    assert rms(.7) < .001
    assert rms(1.5) > .02
    assert rms(3.5) < .001
