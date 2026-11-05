"""Explain ambiguous visual evidence without changing any editing decisions."""
from dataclasses import dataclass
from .model import stabilize


MAX_ISOLATED_BURST_S=.3
MAX_LOCAL_PIXELS=64


def review_policy(min_idle=3.):
    return dict(version=2,isolated_burst_max_s=MAX_ISOLATED_BURST_S,
                quiet_context_s=max(2.,min_idle),local_pixels_max=MAX_LOCAL_PIXELS,
                size_alone_is_ambiguous=False)


@dataclass(frozen=True)
class ReviewSpan:
    start: float
    end: float
    reasons: tuple[str, ...]


def sample_review_reasons(sample, pixel_count):
    """Only explicit conflicting evidence is ambiguous without time context."""
    if not sample[2]:return ()
    evidence=getattr(sample,'evidence','')
    if evidence in ('repeated_local_cycle','cursor_or_return'):return ()
    if evidence=='background_changed':return ('background_conflict',)
    if evidence=='unconfirmed_change' and not getattr(sample,'transient',False):return ('missing_evidence',)
    return ()


def review_sample_reasons(samples,duration,pixel_count,min_idle=3.,debounce=.3):
    reasons=[sample_review_reasons(s,pixel_count) for s in samples]
    filtered,_=stabilize(samples,duration,debounce)
    runs=[];i=0
    while i<len(filtered):
        if not filtered[i][2]:i+=1;continue
        begin=i
        while i<len(filtered) and filtered[i][2]:i+=1
        start=samples[begin-1][0] if begin else 0.
        end=samples[i-1][0] if i<len(samples) else duration
        runs.append((begin,i,start,end))
    quiet=review_policy(min_idle)['quiet_context_s']
    for n,(begin,stop,start,end) in enumerate(runs):
        left=start-(runs[n-1][3] if n else 0.)
        right=(runs[n+1][2] if n+1<len(runs) else duration)-end
        if end-start>MAX_ISOLATED_BURST_S+1e-8 or min(left,right)<quiet-1e-8:continue
        # Require a complete, short, local event with stillness on BOTH sides.
        # A small mark during drawing or a boundary run is not a warning.
        if not all(getattr(s,'evidence','')=='adjacent_change' and getattr(s,'persistent',False)
                   and not getattr(s,'transient',False)
                   and (getattr(s,'bounds',None) is not None or
                        (pixel_count and s[1]*pixel_count<=MAX_LOCAL_PIXELS))
                   for s in samples[begin:stop]):continue
        for j in range(begin,stop):reasons[j]=('isolated_local_change',)
    return reasons


def review_spans(samples, duration, pixel_count, min_idle=3., debounce=.3):
    if hasattr(samples,'windows'):
        from dataclasses import replace
        from .manual_intervals import measured_intersections
        result=[]
        for start,end,automatic,items in samples.windows:
            local=[replace(s,time=s.time-start) for s in items]
            for span in review_spans(local,end-start,pixel_count,min_idle,debounce):
                for a,b in measured_intersections([(span.start+start,span.end+start)],automatic):
                    result.append(ReviewSpan(a,b,span.reasons))
        return result
    flags=review_sample_reasons(samples,duration,pixel_count,min_idle,debounce)
    result=[];previous=0.
    for i,sample in enumerate(samples):
        timestamp=min(duration,max(previous,sample[0]))
        end=duration if i==len(samples)-1 and sample[2] else timestamp
        reasons=flags[i]
        if reasons and end>previous:
            if result and result[-1].reasons==reasons and abs(result[-1].end-previous)<1e-8:
                result[-1]=ReviewSpan(result[-1].start,end,reasons)
            else:result.append(ReviewSpan(previous,end,reasons))
        previous=timestamp
    return result


def overlapping_reviews(spans, start, end):
    return [ReviewSpan(max(start,s.start),min(end,s.end),s.reasons)
            for s in spans if s.start<end and s.end>start]


def review_reasons(spans, start, end):
    return sorted({r for s in overlapping_reviews(spans,start,end) for r in s.reasons})
