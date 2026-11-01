"""Explain ambiguous visual evidence without changing any editing decisions."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewSpan:
    start: float
    end: float
    reasons: tuple[str, ...]


def sample_review_reasons(sample, pixel_count):
    if not sample[2]:return ()
    evidence=getattr(sample,'evidence','')
    if evidence in ('repeated_local_cycle','cursor_or_return'):return ()
    if evidence=='background_changed':return ('background_conflict',)
    if evidence=='accumulated_change':return ('gradual_change',)
    if not evidence or evidence=='unconfirmed_change':return ('missing_evidence',)
    if evidence=='adjacent_change':
        if getattr(sample,'bounds',None) is not None or (pixel_count and sample[1]*pixel_count<=64):
            return ('small_change',)
        if not getattr(sample,'persistent',False):return ('unfinished_motion',)
    return ()


def review_spans(samples, duration, pixel_count):
    result=[];previous=0.
    for i,sample in enumerate(samples):
        timestamp=min(duration,max(previous,sample[0]))
        end=duration if i==len(samples)-1 and sample[2] else timestamp
        reasons=sample_review_reasons(sample,pixel_count)
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
