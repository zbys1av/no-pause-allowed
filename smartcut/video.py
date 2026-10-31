from dataclasses import dataclass
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess
from .process import Job, child, tool
from .errors import AppError


@dataclass(frozen=True)
class Video:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    size: int
    format: str
    audio: bool
    rotation: int = 0
    sar: float = 1.

    @property
    def display_width(self):
        return self.width * self.sar


def probe(path, job=None):
    job = job or Job()
    path = Path(path).resolve(strict=True)
    if path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
        raise AppError("error.format")
    with child([tool("ffprobe"), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
               job, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as p:
        try:
            out, err = p.communicate(timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise AppError("error.probe_timeout") from exc
        job.check()
        if p.returncode:
            raise AppError("error.video", err.decode(errors="replace")[-2000:])
    data = json.loads(out)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
    if not streams:
        raise AppError("error.no_video")
    s = streams[0]
    duration = float(s.get("duration") or data["format"].get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise AppError("error.duration")
    rotation = int(round(float(next((v["rotation"] for v in s.get("side_data_list", []) if "rotation" in v), s.get("tags", {}).get("rotate", 0))))) % 360
    w, h = int(s["width"]), int(s["height"])
    if rotation in (90, 270):
        w, h = h, w
    try:
        sar = float(Fraction(s.get("sample_aspect_ratio", "1:1").replace(":", "/")))
        if not math.isfinite(sar) or sar <= 0:
            sar = 1.
    except (ValueError, ZeroDivisionError):
        sar = 1.
    if rotation in (90, 270):
        sar = 1/sar
    try:
        fps = float(Fraction(s.get("avg_frame_rate", "0/1")))
    except (ValueError, ZeroDivisionError):
        fps = 0
    return Video(path, duration, w, h, fps, path.stat().st_size,
                 data["format"].get("format_name", path.suffix),
                 any(v.get("codec_type") == "audio" for v in data["streams"]), rotation, sar)
