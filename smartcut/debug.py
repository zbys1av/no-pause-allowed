"""Portable, versioned support reports. No frames, audio or environment secrets."""
from collections import deque
from dataclasses import asdict
from datetime import datetime, timezone
import csv
import io
import json
import os
from pathlib import Path
import platform
import tempfile
import time
import zipfile
from . import __version__
from .model import stabilize, activity_intervals, statistics
from .review import review_spans,overlapping_reviews,review_sample_reasons,review_policy


class Journal:
    def __init__(self):
        self.events = deque(maxlen=2000)
        self.total = 0
        self.started = time.monotonic()

    def add(self, event, **details):
        self.total += 1
        self.events.append(dict(utc=datetime.now(timezone.utc).isoformat(),
                                elapsed_s=time.monotonic()-self.started, event=event, **details))


REASONS = {
    'isolated_local_change': 'Short local event surrounded by long stillness on both sides; retained for review. Small size alone is not ambiguous.',
    'background_conflict': 'Background guard conflicts with the local-cycle interpretation; inspect source pixels.',
    'small_change': 'Small visual change could be ink, cursor, interface or codec noise; retained conservatively, needs review.',
    'gradual_change': 'Accumulated faint change, not semantic recognition; needs review.',
    'missing_evidence': 'No conclusive visual evidence available; needs review.',
    'unfinished_motion': 'Final active burst has no subsequent frame to confirm its end; needs review.',
    'certified_quiet': 'Previous-pair evidence and the checked idle anchor certify the same zero-score inactive sample; ordered tracker work safely skipped.',
    'merged_transient': 'Short interior cursor/return burst confirmed by pixels; merged by debounce.',
    'repeated_local_cycle': 'At least three matching repetitions of small local before/after pixel states at a regular cadence; treated as an interface animation, not accumulating ink.',
    'background_changed': 'Scene changed outside the repeating patch; earlier burst classification revoked to protect drawing.',
    'lasting_change': 'Accumulated change or burst not confirmed as a small cursor/return change; protected, not semantic stroke recognition.',
    'movement_preserved': 'Movement retained: duration, boundary or visual evidence does not permit debouncing.',
    'below_motion_threshold': 'No significant change after spatial noise filtering.',
    'short_pause': 'Still interval shorter than minimum pause; kept.',
    'padding_covered': 'Qualifying pause fully covered by padding around retained activity.',
    'eligible_pause': 'Qualifying pause with time available after padding.',
    'manual_edit': 'Final action or boundaries differ from automatic plan; user decision.',
    'automatic_keep': 'Activity, protected short pause or padding; see interval coverage.',
    'automatic_idle': 'Remaining eligible pause after minimum-length filtering and padding.',
}


def snapshot(window):
    """Capture on the GUI thread; writer must not read live widgets or workers."""
    from PySide6 import __version__ as qt_version
    video = window.video
    settings = getattr(window, 'plan_settings', None) or window.capture_settings
    samples = list(window.samples)
    report = window.report
    data = dict(schema='smartcut-debug', schema_version=1,
        created_utc=datetime.now(timezone.utc).isoformat(),
        application=dict(version=__version__, python=platform.python_version(),
                         os=platform.system(), os_release=platform.release(), pyside=qt_version),
        state=window.workflow.state.value, dirty=window.dirty,
        language=window.language, current_settings=asdict(window.settings()),
        capture_settings=asdict(window.capture_settings) if window.capture_settings else None,
        plan_settings=asdict(settings) if settings else None,
        mode='remove' if window.mode.currentIndex()==0 else 'speed',
        plan_mode=getattr(window,'plan_mode','remove'),
        speed=window.speed_value(), manual_changed=window.manual_changed,
        video={k:v for k,v in asdict(video).items() if k!='path'} if video else None,
        source_filename=video.path.name if video else None,
        automatic_plan=[asdict(s) for s in window.automatic],
        final_plan=[asdict(s) for s in window.plan], statistics=statistics(window.plan),
        analysis=asdict(report) if report else None,
        journal=list(window.journal.events), journal_events_dropped=max(0,window.journal.total-len(window.journal.events)),
        last_operation={k:v for k,v in window.last_job_debug.items() if k!='samples'}, reasons=REASONS,
        last_analysis_attempt={k:v for k,v in window.last_analysis_debug.items() if k!='samples'},
        analysis_performance=window.last_analysis_debug.get('performance',{}),
        sample_semantics='A moving sample marks the interval since the preceding timestamp. Last active sample extends to video end.',
        detector_rules=dict(pixel_threshold='max(4,16-sensitivity*0.12)',
            motion='Coherent adjacent changed pixels OR strong pixels; at sensitivity >=90 one strong pixel is enough.',
            chroma='Chroma uses 3x pixel threshold; tiny global RGB offsets <=2 are compensated.',
            debouncing='Only interior runs <= debounce with all samples transient and none persistent are suppressed.',
            adaptive_guard='All base samples checked; exact equality to the previously checked frame, or every channel delta <= floor(pixel_threshold-2) against previous and anchor, certifies inactivity. Audit stride doubles after four quiet audits up to the configured sample/time cap; possible change restores base precision. Confirmed transient bursts can resume the preceding idle stride.',
            transient='No significant net change, small translated opposite pixel shapes, or confirmed repeated local pixel states. Heuristic, not semantic recognition.',
            local_cycles=dict(max_pixels=64,max_extent=32,max_burst_s=.5,minimum_repetitions=3,
                max_phase_events=3,min_event_period_s=.25,max_event_period_s=5,pixel_tolerance=2,
                max_locations=8,max_events_per_location=9),
            padding='Activity and short retained pauses expand by padding, then merge; complement becomes idle actions.'),
        limitations=['No media pixels included: visual classification cannot be independently verified from this report.',
                     'Partial cancelled/failed samples are not a completed analysis and must not be used as its plan.'])
    if report and video and settings:
        from .motion import analysis_dimensions
        width,height=analysis_dimensions(video,window.capture_settings or settings)
        pixel_count=width*height
        reviews=review_spans(samples,video.duration,pixel_count,settings.min_idle,settings.debounce)
        data['review']=dict(intervals=[asdict(s) for s in reviews],
            seconds=sum(s.end-s.start for s in reviews),
            semantics='Heuristic visual ambiguity, not a calibrated probability. Annotation never changes the editing action.',
            policy=review_policy(settings.min_idle))
        filtered, _ = stabilize(samples,video.duration,settings.debounce)
        active = activity_intervals(filtered,video.duration)
        data['stabilized_activity'] = active
        bursts=[];i=0
        while i<len(samples):
            if not samples[i][2]:i+=1;continue
            begin=i
            while i<len(samples) and samples[i][2]:i+=1
            a=samples[begin-1][0] if begin else 0.
            b=samples[i-1][0] if i<len(samples) else video.duration
            evidence=samples[begin:i]
            if settings.debounce==0:reason='debounce_disabled'
            elif begin==0 or i==len(samples):reason='boundary_burst_preserved'
            elif b-a>settings.debounce+1e-8:reason='longer_than_debounce'
            elif any(getattr(s,'persistent',False) for s in evidence):reason='protected_visual_change'
            elif not all(getattr(s,'transient',False) for s in evidence):reason='insufficient_cursor_or_return_evidence'
            else:reason='merged_transient'
            bursts.append(dict(start=a,end=b,duration=b-a,sample_begin=begin,sample_end_exclusive=i,
                transient_samples=sum(getattr(s,'transient',False) for s in evidence),
                protected_samples=sum(getattr(s,'persistent',False) for s in evidence),
                evidence=sorted({s.evidence for s in evidence if getattr(s,'evidence','')}),reason=reason))
        data['burst_decisions']=bursts
        decisions = []
        available = list(report.available)
        index = 0
        for a,b in report.stable_idle:
            while index<len(available) and available[index][1]<=a:index+=1
            has_cut=index<len(available) and min(b,available[index][1])>max(a,available[index][0])+1e-8
            reason='short_pause' if b-a<settings.min_idle else 'eligible_pause' if has_cut else 'padding_covered'
            decisions.append(dict(start=a,end=b,duration=b-a,reason=reason))
        data['pause_decisions'] = decisions
        # A final segment may overlap several automatic ones after manual boundary edits.
        segments=[];index=0
        for segment in window.plan:
            while index<len(window.automatic) and window.automatic[index].end<=segment.start:index+=1
            matches=[];j=index
            while j<len(window.automatic) and window.automatic[j].start<segment.end:
                auto=window.automatic[j]
                matches.append(dict(start=max(segment.start,auto.start),end=min(segment.end,auto.end),action=auto.action))
                j+=1
            unchanged=index<len(window.automatic) and segment==window.automatic[index]
            overlaps=overlapping_reviews(reviews,segment.start,segment.end)
            segments.append(dict(**asdict(segment),reason=('automatic_keep' if segment.action=='keep' else 'automatic_idle') if unchanged else 'manual_edit',automatic_coverage=matches,
                needs_review=bool(overlaps),review_intervals=[asdict(s) for s in overlaps]))
        data['segment_decisions']=segments
    else:filtered=samples;pixel_count=0
    data['review_pixel_count']=pixel_count
    # Remove known user directory prefixes from error text. No environment dump.
    redactions=[str(Path.home()),str(window.cache)]
    if video:redactions.extend([str(video.path),str(video.path.parent)])
    def scrub(value):
        if isinstance(value,str):
            for path in sorted(redactions,key=len,reverse=True):
                if len(path)>3:value=value.replace(path,'<local-path>')
            return value
        if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
        if isinstance(value,list):return [scrub(v) for v in value]
        return value
    return scrub(data), samples, filtered, list((window.last_analysis_debug or window.last_job_debug).get('samples',[]))


def write_report(captured, destination, job, progress=lambda *_:None):
    from .i18n import Text
    data,samples,filtered,partial=captured
    destination=Path(destination)
    if destination.exists():raise FileExistsError(str(destination))
    # Same-volume, atomic publication. Cancellation never leaves a partial report.
    with tempfile.TemporaryDirectory(prefix='smartcut-report-',dir=destination.parent) as directory:
        pending=Path(directory)/'report.zip'
        with zipfile.ZipFile(pending,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            job.check()
            archive.writestr('report.json',json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
            for name,raw,stable in [('samples.csv',samples,filtered),('partial_samples.csv',partial,None)]:
                settings=data.get('plan_settings') or {}
                flags=review_sample_reasons(raw,data['video']['duration'],data.get('review_pixel_count',0),
                    settings.get('min_idle',3.),settings.get('debounce',.3)) if stable is not None and data.get('video') else None
                with archive.open(name,'w') as stream:
                    with io.TextIOWrapper(stream,encoding='utf-8',newline='') as text:
                        writer=csv.writer(text);writer.writerow(['time_s','score','raw_active','transient','persistent','stabilized_active','reason','evidence','bounds','review_reasons'])
                        for i,s in enumerate(raw):
                            if i%512==0:
                                job.check();progress(i/max(1,len(raw)),Text('state.report'))
                            t,score,active=s[:3] if isinstance(s,list) else tuple(s)
                            transient=getattr(s,'transient',s[3] if isinstance(s,list) and len(s)>3 else False)
                            persistent=getattr(s,'persistent',s[4] if isinstance(s,list) and len(s)>4 else False)
                            kept=bool(stable[i][2]) if stable is not None else ''
                            reason='merged_transient' if active and kept is False else 'lasting_change' if persistent else 'movement_preserved' if active else 'below_motion_threshold'
                            if getattr(s,'evidence','')=='certified_quiet':reason='certified_quiet'
                            review=flags[i] if flags is not None else ()
                            writer.writerow([t,score,int(active),int(transient),int(persistent),kept,reason,getattr(s,'evidence',''),json.dumps(getattr(s,'bounds',None)),json.dumps(review)])
            archive.writestr('README.txt','Smart Video Cutter diagnostic report / Діагностичний звіт\n'
                'Надішліть увесь ZIP для аналізу. Send this whole ZIP for investigation.\n'
                'report.json: settings, intervals, decision reasons, plans, events and errors.\n'
                'samples.csv: complete retained analysis; partial_samples.csv: last interrupted attempt only.\n'
                'Reasons are explained in report.json/reasons. Times refer to the source video.\n'
                'No video/audio/frames or environment credentials included. Filename and technical errors may identify the recording.\n')
        job.check();os.link(pending,destination)
    return destination
