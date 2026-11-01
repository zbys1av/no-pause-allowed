"""Bounded pixel evidence for small, regularly repeating interface animations."""
from collections import OrderedDict, deque
from dataclasses import dataclass
import numpy as np


@dataclass
class CycleEvent:
    start: float
    end: float
    begin: int
    stop: int
    before: np.ndarray
    after: np.ndarray


class LocalCycles:
    """Three matching repetitions, never merely similar scalar motion scores.

    Store nine tiny before/after patches and one background anchor per location,
    for eight locations. No unbounded image history. Background comparison also
    protects slow drawing outside a blinking element's patch.
    """
    def __init__(self, background_compare=None):
        self.locations = OrderedDict()
        self.backgrounds = {}
        self.background_indices = {}
        self.background_compare = background_compare
        self.invalidated = []

    @staticmethod
    def same(a, b):
        return bool(np.max(np.abs(a.astype(np.int16)-b.astype(np.int16))) <= 2)

    def observe(self, bounds, before, after, start, end, begin, stop):
        self.invalidated = []
        if bounds is None or not 0 < end-start <= .5+1e-8:
            return []
        x0,y0,x1,y1=bounds
        # Tile alignment tolerates a tiny codec halo without following objects.
        left,top=x0//32*32,y0//32*32
        right,bottom=min(before.shape[1],((x1+31)//32)*32),min(before.shape[0],((y1+31)//32)*32)
        key=(left,top,right,bottom)
        history=self.locations.setdefault(key,deque(maxlen=9))
        self.locations.move_to_end(key)
        while len(self.locations)>8:
            removed,_=self.locations.popitem(last=False);self.backgrounds.pop(removed,None);self.background_indices.pop(removed,None)
        anchor=self.backgrounds.get(key)
        if anchor is not None and self.background_compare is not None:
            before_ok=self.background_compare(anchor,before,key,begin-1)
            anchor=self.backgrounds.get(key,anchor)
            if not before_ok or not self.background_compare(anchor,after,key,stop):
                self.invalidated=list(history);history.clear();anchor=None
        if anchor is None:
            self.backgrounds[key]=before;self.background_indices[key]=begin-1
        history.append(CycleEvent(start,end,begin,stop,before[top:bottom,left:right].copy(),after[top:bottom,left:right].copy()))
        events=list(history)
        for lag in (1,2,3):
            if len(events)<3*lag:continue
            # An interruption must not require nine new events before a valid
            # three-repetition suffix can be recognized again.
            for start in range(len(events)-3*lag+1):
                recent=events[start:]
                if recent[-1].end-recent[0].start<1.:continue
                gaps=np.diff([e.start for e in recent])
                period=float(np.median(gaps))
                if not .25 <= period <= 5 or np.max(np.abs(gaps-period)) > max(.15,period*.12):continue
                if all(abs(recent[i].end-recent[i].start-(recent[i-lag].end-recent[i-lag].start))<=.15
                       and self.same(recent[i].before,recent[i-lag].before)
                       and self.same(recent[i].after,recent[i-lag].after)
                       for i in range(lag,len(recent))):
                    return recent
        return []
