"""The shared editing plan, independent of the GUI and media processes."""
from dataclasses import dataclass, replace
from math import isfinite
from .errors import AppError


@dataclass(frozen=True)
class Settings:
    sensitivity: float = 95
    min_idle: float = 3
    padding: float = .6
    sample_fps: float = 8
    area: tuple[float, float, float, float] = (0, 0, 1, 1)
    analysis_width: int = 960
    debounce: float = .3
    adaptive: bool = True

    def validate(self):
        if not (0 <= self.sensitivity <= 100 and .5 <= self.min_idle <= 10
                and 0 <= self.padding <= 2 and 1 <= self.sample_fps <= 12
                and self.analysis_width in (320, 640, 960, 1280) and 0 <= self.debounce <= 2
                and isinstance(self.adaptive,bool)):
            raise AppError("error.settings")
        x, y, w, h = self.area
        if not all(isfinite(v) for v in self.area) or not (
            0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0
            and x + w <= 1.000001 and y + h <= 1.000001
        ):
            raise AppError("error.area")


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    action: str = "keep"
    speed: float = 5

    @property
    def output_duration(self):
        return (self.end - self.start) / (self.speed if self.action == "speed" else 1) if self.action != "remove" else 0


@dataclass(frozen=True)
class MotionSample:
    time: float
    score: float
    active: bool
    transient: bool = False
    persistent: bool = False
    evidence: str = ''
    bounds: tuple | None = None

    def __iter__(self):
        return iter((self.time, self.score, self.active))

    def __getitem__(self, index):
        return (self.time, self.score, self.active)[index]


def stabilize(samples, duration, debounce):
    """Suppress only brief bursts whose pixels confirm a reversible/cursor change.

    Legacy triples carry no visual evidence and are conservatively preserved.
    A persistent stroke remains active regardless of its duration.
    """
    result = list(samples)
    merged = 0
    i = 0
    while i < len(samples):
        if not samples[i][2]:
            i += 1
            continue
        begin = i
        while i < len(samples) and samples[i][2]:
            i += 1
        start = samples[begin-1][0] if begin else 0.
        end = samples[i-1][0] if i < len(samples) else duration
        if (debounce > 0 and begin > 0 and i < len(samples)
                and end-start <= debounce+1e-8
                and all(getattr(s, 'transient', False) and not getattr(s, 'persistent', False)
                        for s in samples[begin:i])):
            for j in range(begin, i):
                result[j] = replace(samples[j], active=False)
            merged += 1
    return result, merged


@dataclass(frozen=True)
class AnalysisReport:
    raw_idle: tuple
    stable_idle: tuple
    pauses: tuple
    available: tuple
    merged_bursts: int
    rejected_short: int
    lost_to_padding: int
    plan: tuple

    @property
    def idle_time(self):
        return sum(b-a for a, b in self.raw_idle)

    @property
    def pause_time(self):
        return sum(b-a for a, b in self.pauses)

    @property
    def longest(self):
        return max((b-a for a, b in self.stable_idle), default=0.)

    @property
    def top_five(self):
        return sorted(self.stable_idle, key=lambda p:p[1]-p[0], reverse=True)[:5]


def validate_plan(segments, duration):
    if not segments or not isfinite(duration) or duration <= 0:
        raise AppError("error.no_segments")
    previous = 0.
    for s in segments:
        if not all(isfinite(v) for v in (s.start, s.end, s.speed)) or (
            abs(s.start - previous) > 1e-5 or s.end <= s.start
            or s.end > duration + 1e-5 or s.action not in {"keep", "remove", "speed"}
            or s.speed not in {2, 5, 10, 20}
        ):
            raise AppError("error.plan")
        previous = s.end
    if abs(previous - duration) > 1e-5:
        raise AppError("error.coverage")


def make_plan(samples, duration, settings, mode="remove", speed=5):
    """Samples are (source timestamp, score, active); preserve short idle runs.

    A changed pair conservatively marks the interval since the previous sample.
    Padding expands activity only after minimum-idle classification.
    """
    return list(analyze_plan(samples, duration, settings, mode, speed).plan)


def analyze_plan(samples, duration, settings, mode="remove", speed=5):
    settings.validate()
    raw_idle = complement(activity_intervals(samples, duration), duration)
    filtered, merged_bursts = stabilize(samples, duration, settings.debounce)
    merged = activity_intervals(filtered, duration)
    idle = complement(merged, duration)
    short = [(a, b) for a, b in idle if b - a < settings.min_idle]
    pauses = [(a,b) for a,b in idle if b-a >= settings.min_idle]
    merged = merge(merged + short)
    padded = merge([(max(0, a - settings.padding), min(duration, b + settings.padding)) for a, b in merged])
    result = []
    cursor = 0.
    for a, b in padded:
        if a > cursor:
            result.append(Segment(cursor, a, mode, speed))
        result.append(Segment(a, b, 'keep', speed))
        cursor = b
    if cursor < duration:
        result.append(Segment(cursor, duration, mode, speed))
    validate_plan(result, duration)
    available = [(s.start, s.end) for s in result if s.action != 'keep']
    lost, index = 0, 0
    for a, b in pauses:
        while index < len(available) and available[index][1] <= a+1e-8:
            index += 1
        if index == len(available) or min(b, available[index][1])-max(a, available[index][0]) <= 1e-8:
            lost += 1
    return AnalysisReport(tuple(raw_idle), tuple(idle), tuple(pauses), tuple(available),
                          merged_bursts, len(short), lost, tuple(result))


def activity_intervals(samples, duration):
    """Estimated motion in source time, before short-pause retention or padding."""
    active = []
    previous = 0.
    for time, _score, moving in samples:
        time = min(duration, max(previous, time))
        if moving:
            active.append((previous, time))
        previous = time
    if samples and samples[-1][2]:
        active.append((previous, duration))
    return merge(active)


def detection_statistics(samples, duration, min_idle):
    active = activity_intervals(samples, duration)
    still = complement(active, duration)
    long_pauses = [(a, b) for a, b in still if b-a >= min_idle]
    motion = sum(b-a for a, b in active)
    return dict(original=duration, motion=motion, still=duration-motion,
                motion_fraction=motion/duration if duration else 0,
                long_idle=sum(b-a for a, b in long_pauses), sections=len(long_pauses))


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
                removed=sum(s.end-s.start for s in plan if s.action == 'remove'),
                accelerated=sum(s.end-s.start for s in plan if s.action == 'speed'),
                sections=sum(s.action != "keep" for s in plan))
