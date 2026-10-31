"""Analysis estimates based on observed source progress and monotonic wall time."""
from math import isfinite
import time


class AnalysisEstimate:
    def __init__(self, started=None):
        self.started = time.monotonic() if started is None else started
        self.fraction = 0.
        self.updates = 0

    def update(self, fraction):
        if isfinite(fraction) and fraction > self.fraction:
            self.fraction = min(1., fraction)
            self.updates += 1

    def elapsed(self, now=None):
        return max(0., (time.monotonic() if now is None else now)-self.started)

    def remaining(self, now=None):
        elapsed = self.elapsed(now)
        if self.fraction >= 1:
            return 0.
        if elapsed < 1 or self.fraction <= 0 or self.updates < 3:
            return None
        # Include stalled time: an estimate must not count down blindly if
        # decoding stops making progress or a later scene is more expensive.
        return elapsed*(1-self.fraction)/self.fraction
