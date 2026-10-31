"""Streaming RGB analysis with sparse-motion and gradual-stroke protection."""
from collections import deque
import queue
import re
import subprocess
import threading
import math
import time
import numpy as np
from .process import child, tool
from .model import MotionSample
from .errors import AppError
from .i18n import Text
from dataclasses import replace


def change_masks(previous, current, sensitivity):
    # Compare color too: two different brush colors can have the same luminance.
    difference = current.astype(np.int16) - previous.astype(np.int16)
    offset = np.median(difference[::4, ::4], axis=(0, 1))
    # Compensate only tiny capture jitter, never erase a real canvas fill/change.
    if np.all(np.abs(offset) <= 2):
        difference -= np.asarray(offset, dtype=np.int16)
    pixel_threshold = max(4., 16 - sensitivity * .12)
    if difference.ndim == 3:
        luminance = np.abs(np.einsum('...c,c->...', difference,
                          np.array([77, 150, 29], dtype=np.int32), dtype=np.int32))
        magnitude = np.abs(difference).max(axis=2)
        # Chroma noise grows when YUV420 is expanded to RGB. Use a higher
        # chromatic floor while retaining the lower luma floor for pencil lines.
        changed = (luminance > pixel_threshold*256) | (magnitude > pixel_threshold*3)
        strong_mask = (luminance >= max(24, pixel_threshold*3)*256) | (magnitude >= max(32, pixel_threshold*4))
    else:
        magnitude = np.abs(difference)
        changed = magnitude > pixel_threshold
        strong_mask = magnitude >= max(24, pixel_threshold*3)
    return difference, changed, strong_mask


def motion_score(previous, current, sensitivity):
    _, changed, strong_mask = change_masks(previous, current, sensitivity)
    support = np.zeros(changed.shape, dtype=np.uint8)
    support[1:] += changed[:-1]
    support[:-1] += changed[1:]
    support[:, 1:] += changed[:, :-1]
    support[:, :-1] += changed[:, 1:]
    coherent = np.count_nonzero(changed & (support >= 1))
    strong = np.count_nonzero(strong_mask)
    # A cursor must not disappear merely because the rest of a 4K canvas is still.
    # At high sensitivity, preserve even one strong pixel or two adjacent pixels.
    minimum = max(2, math.ceil(changed.size*.002*(1-sensitivity/100)**3),
                  round(2+(100-sensitivity)*.08))
    moving = coherent >= minimum or strong >= (1 if sensitivity >= 90 else minimum)
    return float(max(coherent, strong))/changed.size, bool(moving)


def cursor_or_return(before, after, sensitivity):
    """Conservative visual evidence, not semantic object recognition.

    A short lasting mark is protected. A small, exactly translated pair of
    opposite shapes is likely a cursor. Unmatched pixels protect the entire burst.
    """
    delta, changed, _ = change_masks(before, after, sensitivity)
    count = np.count_nonzero(changed)
    if not count:
        return True
    if count > max(32, min(1024, changed.size*.005)):
        return False
    if delta.ndim == 3:
        channel = int(np.abs(delta[changed]).sum(axis=0).argmax())
        delta = delta[:, :, channel]
    positive, negative = changed & (delta > 0), changed & (delta < 0)
    if not positive.any() or positive.sum() != negative.sum() or positive.sum()+negative.sum()!=count:
        return False
    def crop(mask):
        ys, xs = np.nonzero(mask)
        return mask[ys.min():ys.max()+1, xs.min():xs.max()+1]
    a, b = crop(positive), crop(negative)
    return max(a.shape) <= 64 and a.shape == b.shape and np.array_equal(a, b)


def analysis_dimensions(video, settings):
    _, _, w, h = settings.area
    source_w, source_h = video.width*w, video.height*h
    scale = min(1., settings.analysis_width/max(source_w, source_h))
    return max(2, round(source_w*scale)), max(2, round(source_h*scale))


class MotionTracker:
    """Keep an idle-run anchor so gradual drawing cannot hide between samples."""
    def __init__(self, sensitivity):
        self.sensitivity = sensitivity
        self.previous = self.anchor = None
        self.quiet_start = None
        self.samples = []
        self.burst_start = None
        self.burst_reference = None
        self.burst_peak = 0.

    def add(self, timestamp, frame):
        if self.previous is None:
            self.samples.append(MotionSample(timestamp, 0., False))
            self.previous = self.anchor = frame
            return
        score, moving = motion_score(self.previous, frame, self.sensitivity)
        accumulated = False
        if not moving:
            anchor_score, accumulated = motion_score(self.anchor, frame, self.sensitivity)
            if accumulated:
                # Only backdate gradual changes. A sudden cursor return after a
                # real long break is detected by the adjacent pair above.
                if self.quiet_start is not None:
                    for i in range(self.quiet_start, len(self.samples)):
                        t, old_score, _ = self.samples[i]
                        self.samples[i] = MotionSample(t, max(old_score, anchor_score), True, persistent=True)
                moving, score = True, max(score, anchor_score)
        if moving:
            if self.burst_start is None:
                self.burst_start = len(self.samples)
                self.burst_reference = self.previous
                self.burst_peak = 0.
            self.burst_peak = max(self.burst_peak, score)
            self.anchor, self.quiet_start = frame, None
        elif self.quiet_start is None:
            self.quiet_start = len(self.samples)
        if not moving and self.burst_start is not None:
            burst = self.samples[self.burst_start:]
            small = self.burst_peak <= max(32/frame.shape[0]/frame.shape[1], .005)
            transient = small and not any(s.persistent for s in burst) and cursor_or_return(self.burst_reference, frame, self.sensitivity)
            for i in range(self.burst_start, len(self.samples)):
                self.samples[i] = replace(self.samples[i], transient=bool(transient), persistent=not transient)
            self.burst_start = self.burst_reference = None
        self.samples.append(MotionSample(timestamp, score, moving, persistent=accumulated))
        self.previous = frame


def analyze(video, settings, job, progress=lambda *_: None):
    settings.validate()
    x, y, w, h = settings.area
    width, height = analysis_dimensions(video, settings)
    crop = f"crop=w=iw*{w}:h=ih*{h}:x=iw*{x}:y=ih*{y},"
    filters = ("setpts=PTS-STARTPTS,select='isnan(prev_selected_t)+gte(t-prev_selected_t,"
               f"{1/settings.sample_fps-1e-6})'," + crop +
               f"scale={width}:{height}:flags=area,format=rgb24,showinfo")
    args = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-filter_threads", "2", "-threads", "2", "-i", str(video.path),
            "-map", "0:v:0", "-an", "-sn", "-vf", filters, "-fps_mode", "vfr",
            "-threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    timestamps = queue.Queue()
    errors = deque(maxlen=30)
    tracker = MotionTracker(settings.sensitivity)
    frame_bytes = width*height*3
    last_progress = -math.inf
    pattern = re.compile(r"\bn:\s*\d+.*?pts_time:([\d.eE+\-]+)")
    with child(args, job, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
        def read_log():
            for raw in p.stderr:
                line = raw.decode(errors="replace")
                match = pattern.search(line)
                if match:
                    timestamps.put(float(match[1]))
                elif "showinfo" not in line:
                    errors.append(line.strip())
            timestamps.put(None)
        reader = threading.Thread(target=read_log, daemon=True)
        reader.start()
        while True:
            # BufferedReader.read fills the requested size unless EOF.
            raw = p.stdout.read(frame_bytes)
            if not raw:
                break
            job.check()
            if len(raw) != frame_bytes:
                raise AppError("error.frame")
            timestamp = timestamps.get(timeout=30)
            if timestamp is None:
                raise AppError("error.frame")
            current = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            tracker.add(timestamp, current)
            now = time.monotonic()
            if now-last_progress >= .08:
                progress(min(.999, timestamp/video.duration), Text("stage.analysis"))
                last_progress = now
        code = p.wait()
        reader.join()
        job.check()
        if code:
            raise AppError("error.video", "\n".join(errors))
    if not tracker.samples:
        raise AppError("error.frames")
    progress(1., Text("stage.analysis_complete"))
    return tracker.samples
