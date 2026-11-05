"""Streaming RGB analysis with sparse-motion and gradual-stroke protection."""
from collections import deque
from typing import NamedTuple
import queue
import re
import subprocess
import threading
import math
import time
from itertools import groupby
from bisect import bisect_left
import numpy as np
from .process import child, tool
from .model import MotionSample
from .errors import AppError
from .i18n import Text
from dataclasses import replace
from .repetition import LocalCycles
from .adaptive import AdaptiveAnalysis
from .parallel import PairPipeline,choose_workers


def change_masks(previous, current, sensitivity, difference=None):
    # Compare color too: two different brush colors can have the same luminance.
    difference = np.subtract(current,previous,dtype=np.int16) if difference is None else difference
    offset = np.median(difference[::4, ::4], axis=(0, 1))
    # Compensate only tiny capture jitter, never erase a real canvas fill/change.
    if np.all(np.abs(offset) <= 2):
        difference -= np.asarray(offset, dtype=np.int16)
    pixel_threshold = max(4., 16 - sensitivity * .12)
    if difference.ndim == 3:
        luminance=np.multiply(difference[:,:,0],77,dtype=np.int32)
        luminance+=np.multiply(difference[:,:,1],150,dtype=np.int32)
        luminance+=np.multiply(difference[:,:,2],29,dtype=np.int32)
        np.abs(luminance,out=luminance)
        # Three channel-wise ufuncs avoid a tiny reduction for every RGB pixel.
        # Integer operations and thresholds are identical to abs(delta).max(2).
        magnitude = np.maximum(np.maximum(np.abs(difference[:,:,0]),np.abs(difference[:,:,1])),np.abs(difference[:,:,2]))
        # Chroma noise grows when YUV420 is expanded to RGB. Use a higher
        # chromatic floor while retaining the lower luma floor for pencil lines.
        changed = (luminance > pixel_threshold*256) | (magnitude > pixel_threshold*3)
        strong_mask = (luminance >= max(24, pixel_threshold*3)*256) | (magnitude >= max(32, pixel_threshold*4))
    else:
        magnitude = np.abs(difference)
        changed = magnitude > pixel_threshold
        strong_mask = magnitude >= max(24, pixel_threshold*3)
    return difference, changed, strong_mask


def significant_pixels(changed, strong_mask, sensitivity):
    support = np.zeros(changed.shape, dtype=np.uint8)
    support[1:] += changed[:-1]
    support[:-1] += changed[1:]
    support[:, 1:] += changed[:, :-1]
    support[:, :-1] += changed[:, 1:]
    supported = changed & (support >= 1)
    coherent = np.count_nonzero(supported)
    strong = np.count_nonzero(strong_mask)
    # A cursor must not disappear merely because the rest of a 4K canvas is still.
    # At high sensitivity, preserve even one strong pixel or two adjacent pixels.
    minimum = max(2, math.ceil(changed.size*.002*(1-sensitivity/100)**3),
                  round(2+(100-sensitivity)*.08))
    moving = coherent >= minimum or strong >= (1 if sensitivity >= 90 else minimum)
    return float(max(coherent, strong))/changed.size, bool(moving), supported | strong_mask


def motion_score(previous, current, sensitivity):
    pair=prepare_pair(previous,current,sensitivity,details=False)
    return pair.score,pair.moving


class PairEvidence(NamedTuple):
    score: float
    moving: bool
    bounds: tuple | None
    pixels: tuple
    bitmap: bytes | None = None
    quiet: str = ''


def prepare_pair(previous, frame, sensitivity, details=True):
    """Pure adjacent-frame work; may run ahead, without temporal state."""
    if previous is None:return None
    if np.array_equal(previous,frame):return PairEvidence(0.,False,None,(),None,'exact')
    delta=np.subtract(frame,previous,dtype=np.int16)
    bound=math.floor(max(4.,16-sensitivity*.12)-2)
    probe=delta[::16,::16]
    # A sparse probe can only reject this proof. Acceptance checks every pixel.
    if probe.min()>=-bound and probe.max()<=bound and delta.min()>=-bound and delta.max()<=bound:
        return PairEvidence(0.,False,None,(),None,'bounded')
    _,changed,strong=change_masks(previous,frame,sensitivity,difference=delta)
    score,moving,pixels=significant_pixels(changed,strong,sensitivity)
    bounds=None;points=()
    if details and moving and np.count_nonzero(pixels)<=64:
        ys,xs=np.nonzero(pixels)
        if len(xs) and xs.max()-xs.min()<32 and ys.max()-ys.min()<32:
            bounds=(int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1))
            points=tuple(zip(xs.tolist(),ys.tolist()))
    bitmap=np.packbits(pixels).tobytes() if details and moving and not points else None
    return PairEvidence(score,moving,bounds,points,bitmap)


def cursor_or_return(before, after, sensitivity):
    """Conservative visual evidence, not semantic object recognition.

    A short lasting mark is protected. A small, exactly translated pair of
    opposite shapes is likely a cursor. Unmatched pixels protect the entire burst.
    """
    delta, changed, strong = change_masks(before, after, sensitivity)
    _, moving, changed = significant_pixels(changed,strong,sensitivity)
    if not moving:return True
    count = np.count_nonzero(changed)
    if not count:
        return True
    if count > max(32, min(1024, changed.size*.005)):
        return False
    if delta.ndim == 3:
        channel = int(np.abs(delta[changed]).sum(axis=0).argmax())
        delta = delta[:, :, channel]
    positive, negative = changed & (delta > 0), changed & (delta < 0)
    if not positive.any() or positive.sum() != negative.sum() or positive.sum()+negative.sum()!=count:
        return False
    def crop(mask):
        ys, xs = np.nonzero(mask)
        return mask[ys.min():ys.max()+1, xs.min():xs.max()+1]
    a, b = crop(positive), crop(negative)
    return max(a.shape) <= 64 and a.shape == b.shape and np.array_equal(a, b)


def analysis_dimensions(video, settings):
    _, _, w, h = settings.area
    source_w, source_h = video.width*w, video.height*h
    scale = min(1., settings.analysis_width/max(source_w, source_h))
    return max(2, round(source_w*scale)), max(2, round(source_h*scale))


class MotionTracker:
    """Keep an idle-run anchor so gradual drawing cannot hide between samples."""
    def __init__(self, sensitivity):
        self.sensitivity = sensitivity
        self.previous = self.anchor = None
        self.quiet_start = None
        self.samples = []
        self.burst_start = None
        self.burst_reference = None
        self.burst_peak = 0.
        self.cycles = LocalCycles(self.same_background)
        self.burst_bounds = None
        self.burst_cycle_candidate = False
        self.background_coverage={}
        self.burst_pixels=None
        self.burst_direct=True
        self.cycle_stats=dict(background_checks=0,unexplained_changes=0,explained_changes=0,events=[])

    def same_background(self, before, after, region, after_index=None):
        _,changed,strong=change_masks(before,after,self.sensitivity)
        left,top,right,bottom=region
        changed[top:bottom,left:right]=False;strong[top:bottom,left:right]=False
        _,moving,pixels=significant_pixels(changed,strong,self.sensitivity)
        self.cycle_stats['background_checks']+=1
        if moving:
            # A later, already recorded scene event must not turn the preceding
            # 18 seconds of a proved clock blink back into user activity.
            # Require exact sparse pixel coverage, not merely nearby rectangles.
            target=np.packbits(pixels)
            anchor,covered=self.background_coverage.get(region,(None,None))
            if anchor is not before:covered=np.zeros_like(target)
            explained=not np.any(target & ~covered)
            self.cycle_stats['explained_changes' if explained else 'unexplained_changes']+=1
            if len(self.cycle_stats['events'])<128:
                self.cycle_stats['events'].append(dict(time_s=self.samples[-1].time if self.samples else 0,
                    reason='scene_event_already_recorded' if explained else 'unexplained_background_change',region=region))
            if explained:
                # Advance the baseline past the recorded event. Otherwise its
                # pixels would look unexplained when it leaves the history.
                self.cycles.backgrounds[region]=after
                self.cycles.background_indices[region]=len(self.samples) if after_index is None else after_index
                self.background_coverage.pop(region,None)
                return True
        else:
            # A directly observed event which has returned to the baseline may
            # not explain a later gradual change on those same pixels. Keep the
            # original frame anchor, so subthreshold ink can still accumulate.
            self.background_coverage.pop(region,None)
        return not moving

    def record_burst_pixels(self, pair, shape):
        if self.burst_pixels is None:self.burst_pixels=np.zeros((shape[0]*shape[1]+7)//8,np.uint8)
        if pair.bitmap is not None:
            np.bitwise_or(self.burst_pixels,np.frombuffer(pair.bitmap,np.uint8),out=self.burst_pixels)
        elif pair.pixels:
            indices=np.fromiter((y*shape[1]+x for x,y in pair.pixels),dtype=np.int64)
            np.bitwise_or.at(self.burst_pixels,indices//8,(1<<(7-indices%8)).astype(np.uint8))

    def commit_burst_pixels(self):
        # One union bitmap per existing background, not a FIFO of frame masks.
        # Long movement cannot evict its first scene event from the evidence.
        self.background_coverage={k:v for k,v in self.background_coverage.items() if k in self.cycles.backgrounds}
        for region,anchor in self.cycles.backgrounds.items():
            old,mask=self.background_coverage.get(region,(None,None))
            if old is not anchor:mask=np.zeros_like(self.burst_pixels)
            np.bitwise_or(mask,self.burst_pixels,out=mask)
            self.background_coverage[region]=(anchor,mask)

    def add_certified_quiet(self, timestamp, frame):
        """Only AdaptiveAnalysis may call after proving an inactive zero-score sample."""
        assert self.burst_start is None and self.samples and not self.samples[-1].active
        if self.quiet_start is None:self.quiet_start=len(self.samples)
        self.samples.append(MotionSample(timestamp,0.,False,evidence='certified_quiet'))
        self.previous=frame

    def add(self, timestamp, frame, pair=None):
        if self.previous is None:
            self.samples.append(MotionSample(timestamp, 0., False))
            self.previous = self.anchor = frame
            return
        pair=pair if pair is not None else prepare_pair(self.previous,frame,self.sensitivity)
        score,moving,bounds=pair[:3]
        accumulated = False
        if not moving:
            anchor_score, accumulated = motion_score(self.anchor, frame, self.sensitivity)
            if accumulated:
                # Only backdate gradual changes. A sudden cursor return after a
                # real long break is detected by the adjacent pair above.
                if self.quiet_start is not None:
                    for i in range(self.quiet_start, len(self.samples)):
                        t, old_score, _ = self.samples[i]
                        self.samples[i] = replace(self.samples[i],score=max(old_score,anchor_score),active=True,persistent=True,evidence='accumulated_change')
                moving, score = True, max(score, anchor_score)
        if moving:
            if self.burst_start is None:
                self.burst_start = len(self.samples)
                self.burst_reference = self.previous
                self.burst_peak = 0.
                self.burst_bounds = bounds
                self.burst_cycle_candidate = bounds is not None
                self.burst_pixels=None;self.burst_direct=True
            if accumulated:self.burst_direct=False
            else:self.record_burst_pixels(pair,frame.shape)
            if bounds is None or accumulated:self.burst_cycle_candidate=False
            elif self.burst_bounds is not None:
                a,b,c,d=self.burst_bounds;x,y,u,v=bounds
                self.burst_bounds=(min(a,x),min(b,y),max(c,u),max(d,v))
                if self.burst_bounds[2]-self.burst_bounds[0]>32 or self.burst_bounds[3]-self.burst_bounds[1]>32:self.burst_cycle_candidate=False
            self.burst_peak = max(self.burst_peak, score)
            self.anchor, self.quiet_start = frame, None
        elif self.quiet_start is None:
            self.quiet_start = len(self.samples)
        if not moving and self.burst_start is not None:
            burst = self.samples[self.burst_start:]
            small = self.burst_peak <= max(32/frame.shape[0]/frame.shape[1], .005)
            transient = small and not any(s.persistent for s in burst) and cursor_or_return(self.burst_reference, frame, self.sensitivity)
            for i in range(self.burst_start, len(self.samples)):
                self.samples[i] = replace(self.samples[i], transient=bool(transient), persistent=not transient,
                    evidence='cursor_or_return' if transient else self.samples[i].evidence or 'unconfirmed_change')
            if not transient and self.burst_direct and self.burst_pixels is not None:self.commit_burst_pixels()
            if self.burst_cycle_candidate:
                begin=self.burst_start
                matches=self.cycles.observe(self.burst_bounds,self.burst_reference,frame,
                    self.samples[begin-1].time if begin else 0.,self.samples[-1].time,begin,len(self.samples))
                self.background_coverage={k:v for k,v in self.background_coverage.items()
                                          if self.cycles.backgrounds.get(k) is v[0]}
                for event in self.cycles.invalidated:
                    for i in range(event.begin,event.stop):
                        self.samples[i]=replace(self.samples[i],transient=False,persistent=True,evidence='background_changed')
                for event in matches:
                    for i in range(event.begin,event.stop):
                        self.samples[i]=replace(self.samples[i],transient=True,persistent=False,evidence='repeated_local_cycle')
            self.burst_start = self.burst_reference = None
            self.burst_pixels=None
        self.samples.append(MotionSample(timestamp, score, moving, persistent=accumulated,
            evidence='accumulated_change' if accumulated else 'adjacent_change' if moving else 'below_threshold',bounds=bounds))
        self.previous = frame


def analyze(video, settings, job, progress=lambda *_: None, *, window=None, seek_at=None, regions=None):
    settings.validate()
    if regions is not None:
        if window is not None or seek_at is not None:raise ValueError('Conflicting decode modes')
        if not regions:raise ValueError('Empty decode regions')
    if window is not None:
        start,end=window
        if not (math.isfinite(start) and math.isfinite(end) and 0<=start<end<=video.duration):
            raise ValueError('Invalid analysis window')
    x, y, w, h = settings.area
    width, height = analysis_dimensions(video, settings)
    workers,selection=choose_workers(settings.workers,video.duration,width*height)
    decoder_threads='1' if settings.workers>1 else '2'
    crop = f"crop=w=iw*{w}:h=ih*{h}:x=iw*{x}:y=ih*{y},"
    window_filter=''
    if window is not None or regions is not None:
        from .manual_intervals import ffmpeg_window_filter
        window_filter=ffmpeg_window_filter(regions if regions is not None else [(start,end,[])])+','
    prefix='setpts=PTS-STARTPTS,' if seek_at is None else ''
    filters = (prefix+"select='isnan(prev_selected_t)+gte(t-prev_selected_t,"
               f"{1/settings.sample_fps-1e-6})'," + window_filter + crop +
               f"scale={width}:{height}:flags=area,format=rgb24,showinfo")
    args = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-filter_threads", '1', "-threads", decoder_threads]
    if seek_at is not None:args += ['-copyts','-start_at_zero','-ss',f'{seek_at:.9f}']
    # Bound the input, not just the filtered output: a select filter can discard
    # every later frame, preventing an output -t from ever observing its limit.
    input_end=min(video.duration,(regions[-1][1] if regions is not None else end)+1.) if (window is not None or regions is not None) else None
    if input_end is not None:args += ['-t',f'{input_end-(seek_at or 0.):.9f}']
    args += ['-i',str(video.path)]
    args += ["-map", "0:v:0", "-an", "-sn", "-vf", filters, "-fps_mode", "vfr",
            "-threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    timestamps = queue.Queue()
    errors = deque(maxlen=30)
    tracker = MotionTracker(settings.sensitivity)
    engine = AdaptiveAnalysis(tracker,settings,video.fps)
    job.debug_samples = tracker.samples
    job.debug_analysis_performance = engine.stats
    engine.stats['cycles']=tracker.cycle_stats
    engine.stats.update(requested_workers=settings.workers,effective_workers=1 if settings.workers==0 else workers,worker_selection=selection,
                        decoder_threads=int(decoder_threads),pixel_kernel='rgb-channel-v2',
                        strategy='one_decoder_parallel_adjacent_pairs_ordered_temporal_state')
    analysis_started=time.perf_counter()
    metadata={k:engine.stats[k] for k in ('requested_workers','effective_workers','worker_selection','decoder_threads','pixel_kernel','strategy')}
    region_results=[];region_stats=[]
    frame_bytes = width*height*3
    last_progress = -math.inf
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
        def decoded():
            previous_time=-math.inf
            while True:
                raw=p.stdout.read(frame_bytes)
                if not raw:break
                job.check()
                if len(raw)!=frame_bytes:raise AppError('error.frame')
                timestamp=timestamps.get(timeout=30)
                if timestamp is None or timestamp<=previous_time:raise AppError('error.frame')
                previous_time=timestamp
                yield timestamp,np.frombuffer(raw,np.uint8).reshape(height,width,3)
        # showinfo formats pts_time with limited precision. Classify inside the
        # excluded gaps, rather than at inclusive window ends, so rounding of
        # an endpoint cannot assign its frame to the following window.
        cuts=[(regions[i][1]+regions[i+1][0])/2 for i in range(len(regions)-1)] if regions is not None else None
        groups=groupby(decoded(),key=lambda item:bisect_left(cuts,item[0])) if regions is not None else [(0,decoded())]
        for region_index,frames in groups:
            if regions is not None:
                tracker=MotionTracker(settings.sensitivity);engine=AdaptiveAnalysis(tracker,settings,video.fps)
                engine.stats.update(metadata);engine.stats['cycles']=tracker.cycle_stats
                job.debug_samples=tracker.samples;job.debug_analysis_performance=engine.stats
            with PairPipeline(workers,lambda a,b:prepare_pair(a,b,settings.sensitivity),job,auto=settings.workers==0) as pipeline:
                engine.stats['parallel']=pipeline.stats
                for timestamp,current,pair in pipeline.frames(frames):
                    engine.stats['effective_workers']=pipeline.stats['max_workers_used']
                    engine.add(timestamp,current,pair=pair)
                    now=time.monotonic()
                    if now-last_progress>=.08:
                        fraction=(timestamp-start)/(end-start) if window is not None else timestamp/video.duration
                        progress(min(.995,max(0.,fraction*.995)),Text('stage.analysis'))
                        last_progress=now
            if regions is not None:
                a,b,automatic=regions[region_index]
                region_results.append((a,b,automatic,tracker.samples));region_stats.append(engine.stats)
        code = p.wait()
        reader.join()
        job.check()
        if code:
            raise AppError("error.video", "\n".join(errors))
    if not tracker.samples:
        raise AppError("error.frames")
    progress(.999,Text('stage.validate_analysis'))
    from .model import analyze_plan
    if window is None and regions is None:analyze_plan(tracker.samples,video.duration,settings)
    job.check()
    engine.stats['validated_samples']=len(tracker.samples)
    progress(1., Text("stage.analysis_complete"))
    engine.stats['elapsed_s']=time.perf_counter()-analysis_started
    engine.stats['ffmpeg_s']=engine.stats['elapsed_s']
    engine.stats['seek_at']=seek_at
    engine.stats['input_end_limit']=input_end
    if regions is not None:
        measured=WindowedSamples(region_results);job.debug_samples=measured
        job.debug_analysis_performance=dict(frames=sum(s['frames'] for s in region_stats),
            full_checks=sum(s['full_checks'] for s in region_stats),certified_quiet=sum(s['certified_quiet'] for s in region_stats),
            effective_workers=max(s['effective_workers'] for s in region_stats),window_details=region_stats,
            ffmpeg_processes=1,elapsed_s=time.perf_counter()-analysis_started,ffmpeg_s=time.perf_counter()-analysis_started)
        return measured
    return tracker.samples


class WindowedSamples(list):
    """Measured samples grouped by decode window; context is not an auto range."""
    def __init__(self, windows):
        self.windows=windows
        super().__init__(sample for _,_,_,samples in windows for sample in samples)


def analyze_selected(video,settings,job,intervals,progress=lambda *_: None,*,decode_policy='auto'):
    from .manual_intervals import analysis_windows
    context=max(2.,settings.min_idle+settings.padding+settings.debounce,2/settings.sample_fps)
    windows=analysis_windows(intervals,video.duration,context)
    from .decode_strategy import choose_decode
    started=time.perf_counter()
    if decode_policy=='auto':windows,cadence,strategy=choose_decode(video,settings,job,windows)
    elif decode_policy=='legacy':cadence=None;strategy='legacy_repeated_prefixes'
    else:raise ValueError('Invalid decode policy')
    if cadence is None and decode_policy=='auto' and windows:
        measured=analyze(video,settings,job,progress,regions=windows)
        job.debug_analysis_performance.update(decode_strategy=strategy,windows=len(windows),
            seek_probe=getattr(job,'decode_probe',{}),elapsed_s=time.perf_counter()-started)
        return measured
    results=[];stats=[];total=sum(end-start for start,end,_ in windows);completed=0.
    for index,(start,end,automatic) in enumerate(windows):
        job.check()
        seek_at=max(0.,math.floor(max(0.,start-1.)/cadence)*cadence) if cadence else None
        samples=analyze(video,settings,job,
            lambda fraction,stage,completed=completed,span=end-start:progress((completed+fraction*span)/total,stage),window=(start,end),seek_at=seek_at)
        stats.append(job.debug_analysis_performance)
        results.append((start,end,automatic,samples));completed+=end-start
    measured=WindowedSamples(results)
    job.debug_samples=measured
    job.debug_analysis_performance=dict(frames=sum(s['frames'] for s in stats),
        full_checks=sum(s['full_checks'] for s in stats),certified_quiet=sum(s['certified_quiet'] for s in stats),
        windows=len(windows),effective_workers=max((s['effective_workers'] for s in stats),default=1),
        elapsed_s=time.perf_counter()-started,ffmpeg_s=sum(s['ffmpeg_s'] for s in stats),
        ffmpeg_processes=len(stats),decode_strategy=strategy,seek_probe=getattr(job,'decode_probe',{}),window_details=stats)
    return measured
