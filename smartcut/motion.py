"""Two-frame streaming analysis. No full-size frames or video blobs in memory."""
from collections import deque
import queue
import re
import subprocess
import threading
import numpy as np
from .process import child, tool


def motion_score(previous, current, sensitivity):
    # Remove global brightness shifts, then suppress low-amplitude compression noise.
    difference = current.astype(np.int16) - previous.astype(np.int16)
    difference -= int(np.median(difference))
    pixel_threshold = 16 - sensitivity * .12  # 16..4 luminance levels
    changed = np.abs(difference) > pixel_threshold
    # Require local support to reject isolated hot pixels, preserve tiny brush strokes.
    support = np.zeros_like(difference, dtype=np.uint8)
    support[1:] += changed[:-1]
    support[:-1] += changed[1:]
    support[:, 1:] += changed[:, :-1]
    support[:, :-1] += changed[:, 1:]
    score = float(np.count_nonzero(changed & (support >= 1))) / changed.size
    threshold = .00015 + .012 * (1 - sensitivity/100)**2
    return score, score >= threshold


def analyze(video, settings, job, progress=lambda *_: None):
    settings.validate()
    x, y, w, h = settings.area
    width = 320
    height = max(2, round(width * video.height * h / (video.width * w)))
    # Cap pathological narrow crops to bound memory and work.
    if height > 360:
        width, height = max(2, round(width*360/height)), 360
    crop = f"crop=w=iw*{w}:h=ih*{h}:x=iw*{x}:y=ih*{y},"
    filters = ("setpts=PTS-STARTPTS,select='isnan(prev_selected_t)+gte(t-prev_selected_t,"
               f"{1/settings.sample_fps-1e-6})'," + crop +
               f"scale={width}:{height}:flags=area,format=gray,showinfo")
    args = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-filter_threads", "2", "-threads", "2", "-i", str(video.path),
            "-map", "0:v:0", "-an", "-sn", "-vf", filters, "-fps_mode", "vfr",
            "-threads", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    timestamps = queue.Queue()
    errors = deque(maxlen=30)
    samples, previous = [], None
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
            raw = p.stdout.read(width*height)
            if not raw:
                break
            job.check()
            if len(raw) != width*height:
                raise RuntimeError("Incomplete decoded frame")
            timestamp = timestamps.get(timeout=30)
            if timestamp is None:
                raise RuntimeError("Missing source frame timestamps")
            current = np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
            score, moving = motion_score(previous, current, settings.sensitivity) if previous is not None else (0., False)
            samples.append((timestamp, score, moving))
            previous = current
            progress(min(.999, timestamp/video.duration), "Analyzing motion")
        code = p.wait()
        reader.join()
        job.check()
        if code:
            raise RuntimeError("Video decoding failed: " + "\n".join(errors))
    if not samples:
        raise ValueError("FFmpeg could not decode any video frames")
    progress(1., "Analysis complete")
    return samples
