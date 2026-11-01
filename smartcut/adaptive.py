"""Certified idle fast path. Every base sample is checked, even between audits."""
from math import floor
import time
import numpy as np


class AdaptiveAnalysis:
    def __init__(self, tracker, settings, source_fps=0):
        self.tracker=tracker
        self.enabled=settings.adaptive
        self.bound=floor(max(4.,16-settings.sensitivity*.12)-2)
        self.max_span=min(2.,60/source_fps) if source_fps>0 else 2.
        self.max_step=max(1,floor(self.max_span*settings.sample_fps))
        self.step=1;self.quiet_audits=0;self.since_audit=0
        self.resume_step=1
        self.last_full=None;self.last_time=None;self.scratch=None
        self.stats=dict(engine='certified-adaptive-v1' if self.enabled else 'fixed-v1',
            base_sample_fps=settings.sample_fps,max_audit_span_s=self.max_span,
            max_step_samples=self.max_step,frames=0,full_checks=0,guard_checks=0,
            certified_quiet=0,exact_matches=0,bounded_matches=0,refinements=0,
            full_seconds=0.,guard_seconds=0.,max_full_gap_s=0.,
            step_events=[],step_events_dropped=0,
            note='All selected RGB frames are examined. Guarded intervals need no seek/refinement: the resulting zero-score inactive sample is certified using the previous frame and idle anchor. Source decoding is sequential.')

    def set_step(self, value, timestamp, reason):
        value=max(1,min(self.max_step,value))
        if value==self.step:return
        self.step=value
        event=dict(time_s=timestamp,step_samples=value,reason=reason)
        if len(self.stats['step_events'])<500:self.stats['step_events'].append(event)
        else:self.stats['step_events_dropped']+=1

    def certified(self, frame):
        previous=self.tracker.previous
        if np.array_equal(frame,previous):
            # Invariant: the preceding frame was already checked against its
            # anchor, or became the anchor itself. Identical pixels add no ink.
            return 'exact'
        if self.scratch is None:self.scratch=np.empty(frame.shape,np.int16)
        for reference in (previous,self.tracker.anchor):
            if reference is None:return None
            np.subtract(frame,reference,out=self.scratch,dtype=np.int16)
            if self.scratch.min() < -self.bound or self.scratch.max() > self.bound:return None
            if previous is self.tracker.anchor:break
        # After the existing <=2 offset correction every RGB component is at
        # most pixel_threshold. Its weighted luma cannot exceed that threshold;
        # neither chroma nor strong/coherent-pixel tests can fire. This proves
        # both pair and anchor scores are zero, rather than estimating stillness.
        return 'bounded'

    def full(self, timestamp, frame):
        burst_begin=self.tracker.burst_start
        start=time.perf_counter();self.tracker.add(timestamp,frame)
        self.stats['full_seconds']+=time.perf_counter()-start
        self.stats['full_checks']+=1
        if self.last_full is not None:self.stats['max_full_gap_s']=max(self.stats['max_full_gap_s'],timestamp-self.last_full)
        self.last_full=timestamp;self.since_audit=0
        if self.tracker.samples[-1].active:
            self.resume_step=max(self.resume_step,self.step)
            self.quiet_audits=0;self.set_step(1,timestamp,'motion')
        else:
            if self.enabled and self.resume_step>1:
                closed=burst_begin is not None and self.tracker.burst_start is None
                transient=closed and all(s.transient and not s.persistent for s in self.tracker.samples[burst_begin:-1])
                if transient or burst_begin is None:
                    self.set_step(self.resume_step,timestamp,'confirmed_transient_resume' if transient else 'full_check_confirmed_quiet')
                self.resume_step=1
            self.quiet_audits+=1
            if self.enabled and self.quiet_audits>=4:
                self.set_step(self.step*2,timestamp,'four_quiet_audits');self.quiet_audits=0

    def add(self, timestamp, frame):
        self.stats['frames']+=1
        delta=timestamp-self.last_time if self.last_time is not None else 0.
        self.last_time=timestamp
        audit=(self.last_full is None or self.since_audit+1>=self.step
               or timestamp-self.last_full+delta>self.max_span+1e-8)
        if not self.enabled or self.step==1 or audit or self.tracker.burst_start is not None:
            self.full(timestamp,frame);return
        start=time.perf_counter();proof=self.certified(frame)
        self.stats['guard_seconds']+=time.perf_counter()-start
        self.stats['guard_checks']+=1
        if proof:
            self.tracker.add_certified_quiet(timestamp,frame)
            self.stats['certified_quiet']+=1
            self.stats['exact_matches' if proof=='exact' else 'bounded_matches']+=1
            self.since_audit+=1
        else:
            self.stats['refinements']+=1;self.quiet_audits=0
            self.resume_step=max(self.resume_step,self.step)
            self.set_step(1,timestamp,'possible_change_refine_at_base_resolution')
            self.full(timestamp,frame)
