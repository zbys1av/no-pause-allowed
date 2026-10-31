from pathlib import Path
import subprocess
import numpy as np
import pytest
from smartcut.motion import MotionTracker, motion_score, analyze, analysis_dimensions
from smartcut.model import Settings, Segment, make_plan, detection_statistics, statistics
from smartcut.process import Job, tool
from smartcut.video import Video, probe
from smartcut.progress import AnalysisEstimate


def test_sparse_cursor_does_not_depend_on_whole_frame_area():
    image = np.full((540, 960, 3), 200, np.uint8)
    cursor = image.copy()
    cursor[200:203, 400:402] = 30
    assert motion_score(image, cursor, Settings().sensitivity)[1]
    stroke = image.copy()
    stroke[250, 600] = 30
    assert motion_score(image, stroke, Settings().sensitivity)[1]


def test_equal_luminance_brush_color_and_jitter():
    original = np.full((100, 160, 3), (255, 0, 0), np.uint8)
    frame = original.copy()
    frame[40:42, 50:53] = (0, 130, 0)  # same grayscale intensity, different color
    assert motion_score(original, frame, 95)[1]
    rng = np.random.default_rng(4)
    a = (100+rng.integers(-2, 3, (100, 160, 3))).astype(np.uint8)
    b = (100+rng.integers(-2, 3, (100, 160, 3))).astype(np.uint8)
    assert not motion_score(a, b, 95)[1]


def test_gradual_stroke_is_backdated_but_real_long_break_is_not():
    base = np.full((90, 160, 3), 100, np.uint8)
    tracker = MotionTracker(95)
    tracker.add(0, base)
    for i in range(1, 11):
        frame = base.copy()
        frame[30:32, 50:53] += i
        tracker.add(i*.125, frame)
    assert all(moving for _, _, moving in tracker.samples[1:])
    final = frame.copy()
    for i in range(11, 51):
        tracker.add(i*.125, final)
    returned = final.copy()
    returned[40:45, 80:85] = 0
    tracker.add(6.375, returned)
    assert not any(s[2] for s in tracker.samples[11:51])
    assert tracker.samples[-1][2]


def test_source_activity_stats_ignore_padding_and_manual_keep():
    samples = [(0, 0, False), (1, 1, True), (2, 1, True), (3, 0, False),
               (6, 0, False), (7, 1, True), (8, 0, False), (10, 0, False)]
    raw = detection_statistics(samples, 10, 3)
    assert raw['motion'] == 3
    assert raw['still'] == 7
    assert raw['motion']+raw['still'] == raw['original'] == 10
    assert raw['long_idle'] == 7 and raw['sections'] == 2
    assert raw['motion_fraction'] == .3
    assert statistics([Segment(0, 10)])['active'] == 10
    assert detection_statistics(samples, 10, 3) == raw


def test_analysis_size_preserves_detail_without_upscaling():
    landscape = Video(Path('test.mp4'), 10, 1920, 1080, 30, 1, 'mp4', False)
    assert analysis_dimensions(landscape, Settings()) == (960, 540)
    assert analysis_dimensions(landscape, Settings(area=(0, 0, .25, .25))) == (480, 270)
    portrait = Video(Path('test.mp4'), 10, 1080, 1920, 30, 1, 'mp4', False)
    assert analysis_dimensions(portrait, Settings()) == (540, 960)


def test_estimate_uses_observed_progress_and_accounts_for_stalls():
    estimate = AnalysisEstimate(started=100)
    assert estimate.remaining(120) is None  # no invented estimate without progress
    for f in (.1, .2, .25): estimate.update(f)
    assert estimate.remaining(120) == pytest.approx(60)
    assert estimate.remaining(140) == pytest.approx(120)  # stall increases ETA
    estimate.update(.1)
    assert estimate.fraction == .25
    estimate.update(1)
    assert estimate.remaining(150) == 0


def test_1080p_tiny_cursor_thin_line_and_real_pause(tmp_path):
    source = tmp_path/'cursor and brush.mp4'
    command = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin',
               '-f', 'rawvideo', '-pixel_format', 'gray', '-video_size', '1920x1080',
               '-framerate', '8', '-i', 'pipe:0', '-an', '-c:v', 'libx264',
               '-threads', '2', '-crf', '0', str(source)]
    # Stream a lossless 1080p recording, never store its full frame sequence.
    with subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        for i in range(64):
            frame = np.full((1080, 1920), 200, np.uint8)
            cursor_x = 100+min(i, 15)*2
            frame[200:212, cursor_x:cursor_x+4] = 20
            if i >= 48:
                frame[500, 600:600+(i-47)*6] = 40  # one-pixel brush, no cursor movement
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        errors = process.stderr.read().decode(errors='replace')
        assert process.wait() == 0, errors
    video = probe(source)
    settings = Settings()
    samples = analyze(video, settings, Job())
    assert sum(s[2] for s in samples if .2 <= s[0] < 1.9) >= 10
    assert sum(s[2] for s in samples if 6.1 <= s[0] < 7.9) >= 10
    assert not any(s[2] for s in samples if 2.2 <= s[0] < 5.7)
    plan = make_plan(samples, video.duration, settings)
    cuts = [s for s in plan if s.action == 'remove']
    assert len(cuts) == 1
    assert 2.3 < cuts[0].start < 2.8 and 5 < cuts[0].end < 5.8
    raw = detection_statistics(samples, video.duration, settings.min_idle)
    assert raw['still'] == pytest.approx(4, abs=.3)
