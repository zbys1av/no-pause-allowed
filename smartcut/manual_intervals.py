"""User-confirmed analysis intervals, independent from editing actions."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class ManualInterval:
    start: float
    end: float
    kind: str


def assign(intervals, start, end, kind, duration):
    """Replace a source-time range; 'auto' clears confirmations in that range."""
    if not all(isfinite(x) for x in (start,end,duration)) or not 0 <= start < end <= duration:
        raise ValueError('Invalid interval boundaries')
    if kind not in ('auto','pause','motion'):
        raise ValueError('Invalid interval kind')
    result=[]
    for item in intervals:
        if item.kind not in ('pause','motion') or not 0 <= item.start < item.end <= duration:
            raise ValueError('Invalid existing interval')
        if item.end<=start or item.start>=end:result.append(item);continue
        if item.start<start:result.append(ManualInterval(item.start,start,item.kind))
        if item.end>end:result.append(ManualInterval(end,item.end,item.kind))
    if kind!='auto':result.append(ManualInterval(start,end,kind))
    result.sort(key=lambda x:x.start)
    merged=[]
    for item in result:
        if merged and item.start<merged[-1].end:
            raise ValueError('Overlapping existing intervals')
        if merged and item.start==merged[-1].end and item.kind==merged[-1].kind:
            merged[-1]=ManualInterval(merged[-1].start,item.end,item.kind)
        else:merged.append(item)
    return merged


def automatic_intervals(intervals, duration):
    """Return original-time gaps requiring automatic analysis."""
    if not isfinite(duration) or duration<=0:raise ValueError('Invalid duration')
    cursor=0.;result=[]
    for item in intervals:
        if item.kind not in ('pause','motion') or not cursor<=item.start<item.end<=duration:
            raise ValueError('Invalid or overlapping intervals')
        if item.start>cursor:result.append((cursor,item.start))
        cursor=item.end
    if cursor<duration:result.append((cursor,duration))
    return result


def analysis_windows(intervals, duration, context):
    """Build merged decode windows and preserve the actual automatic ranges.

    Context is decoded only as evidence: it must not be counted as a measured
    result in a user-confirmed interval. Coordinates always refer to source time.
    """
    if not isfinite(context) or context < 0:
        raise ValueError('Invalid context')
    gaps=automatic_intervals(intervals,duration)
    windows=[]
    for start,end in gaps:
        left=max(0.,start-context);right=min(duration,end+context)
        if windows and left<=windows[-1][1]:
            previous=windows[-1]
            windows[-1]=(previous[0],max(previous[1],right),previous[2]+[(start,end)])
        else:
            windows.append((left,right,[(start,end)]))
    return windows


def measured_intersections(spans, automatic):
    """Clip measured source-time spans to automatic ranges, never manual ones."""
    result=[];index=0
    for start,end in spans:
        if not isfinite(start) or not isfinite(end) or end<start:
            raise ValueError('Invalid measured span')
        while index<len(automatic) and automatic[index][1]<=start:index+=1
        j=index
        while j<len(automatic) and automatic[j][0]<end:
            left=max(start,automatic[j][0]);right=min(end,automatic[j][1])
            if right>left:result.append((left,right))
            j+=1
    return result


def ffmpeg_window_filter(windows):
    """Select original-PTS frames in decode windows without changing timestamps.

    The filter is deliberately separate from seek and sample selection so that
    future integration can retain the existing VFR sampling cadence.
    """
    terms=[];previous=-1.
    for start,end,_ in windows:
        if not (isfinite(start) and isfinite(end) and 0<=start<end and start>=previous):
            raise ValueError('Invalid decode windows')
        terms.append(f'between(t,{start:.9f},{end:.9f})')
        previous=end
    return "select='"+('+'.join(terms) if terms else '0')+"'"
