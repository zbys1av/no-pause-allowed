"""The shared editing plan, independent of the GUI and media processes."""
from dataclasses import dataclass, replace
from math import isfinite


@dataclass(frozen=True)
class Settings:
    sensitivity: float = 80
    min_idle: float = 1.5
    padding: float = .4
    sample_fps: float = 6
    area: tuple[float, float, float, float] = (0, 0, 1, 1)

    def validate(self):
        if not (0 <= self.sensitivity <= 100 and .5 <= self.min_idle <= 10
                and 0 <= self.padding <= 2 and 1 <= self.sample_fps <= 12):
            raise ValueError("Invalid analysis settings")
        x, y, w, h = self.area
        if not all(isfinite(v) for v in self.area) or not (
            0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0
            and x + w <= 1.000001 and y + h <= 1.000001
        ):
            raise ValueError("Detection area must lie inside the video")


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    action: str = "keep"
    speed: float = 5

    @property
    def output_duration(self):
        return (self.end - self.start) / (self.speed if self.action == "speed" else 1) if self.action != "remove" else 0


def validate_plan(segments, duration):
    if not segments or not isfinite(duration) or duration <= 0:
        raise ValueError("No video segments")
    previous = 0.
    for s in segments:
        if not all(isfinite(v) for v in (s.start, s.end, s.speed)) or (
            abs(s.start - previous) > 1e-5 or s.end <= s.start
            or s.end > duration + 1e-5 or s.action not in {"keep", "remove", "speed"}
            or s.speed not in {2, 5, 10, 20}
        ):
            raise ValueError("The editing plan has a gap, overlap or invalid segment")
        previous = s.end
    if abs(previous - duration) > 1e-5:
        raise ValueError("The editing plan must cover the entire video")


def make_plan(samples, duration, settings, mode="remove", speed=5):
    """Samples are (source timestamp, score, active); preserve short idle runs.

    A changed pair conservatively marks the interval since the previous sample.
    Padding expands activity only after minimum-idle classification.
    """
    settings.validate()
    active = []
    previous = 0.
    for time, _score, moving in samples:
        time = min(duration, max(previous, time))
        if moving:
            active.append((previous, time))
        previous = time
    if samples and samples[-1][2]:
        active.append((previous, duration))
    merged = merge(active)
    idle = complement(merged, duration)
    short = [(a, b) for a, b in idle if b - a < settings.min_idle]
    merged = merge(merged + short)
    padded = merge([(max(0, a - settings.padding), min(duration, b + settings.padding)) for a, b in merged])
    boundaries = sorted({0., duration, *(v for pair in padded for v in pair)})
    result = []
    for a, b in zip(boundaries, boundaries[1:]):
        if b - a < 1e-8:
            continue
        keep = any(x <= (a+b)/2 <= y for x, y in padded)
        result.append(Segment(a, b, "keep" if keep else mode, speed))
    validate_plan(result, duration)
    return result


def merge(intervals):
    result = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if result and a <= result[-1][1] + 1e-8:
            result[-1] = (result[-1][0], max(b, result[-1][1]))
        else:
            result.append((a, b))
    return result


def complement(intervals, duration):
    result, cursor = [], 0.
    for a, b in intervals:
        if a > cursor:
            result.append((cursor, a))
        cursor = b
    if cursor < duration:
        result.append((cursor, duration))
    return result


def move_boundary(plan, index, start, end, duration):
    """Move shared boundaries, never introduce gaps or zero-length neighbors."""
    updated = list(plan)
    if index == 0:
        start = 0.
    if index == len(plan)-1:
        end = duration
    updated[index] = replace(plan[index], start=start, end=end)
    if index:
        updated[index-1] = replace(plan[index-1], end=start)
    if index+1 < len(plan):
        updated[index+1] = replace(plan[index+1], start=end)
    validate_plan(updated, duration)
    return updated


def statistics(plan):
    original = sum(s.end-s.start for s in plan)
    active = sum(s.end-s.start for s in plan if s.action == "keep")
    output = sum(s.output_duration for s in plan)
    return dict(original=original, active=active, idle=original-active, output=output,
                reduction=100*(1-output/original) if original else 0,
                sections=sum(s.action != "keep" for s in plan))
