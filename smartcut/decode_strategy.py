"""Conservative seek eligibility and a bounded decode-cost decision."""
import json
import math
import subprocess
from .process import child,tool


def cfr_period(video,job):
    def reject(reason,**details):
        job.decode_probe=dict(reason=reason,**details);return None
    # Inspect packet PTS without decoding pixels. Missing, repeated or irregular
    # timestamps force the unchanged sequential sampler.
    with child([tool('ffprobe'),'-v','error','-select_streams','v:0',
                '-show_entries','packet=pts_time:format=start_time:stream=codec_name,field_order,nb_frames','-of','json',str(video.path)],
               job,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as process:
        out,_=process.communicate();job.check()
        if process.returncode:return reject('ffprobe_failed',returncode=process.returncode)
    try:
        data=json.loads(out);stream=data['streams'][0]
        if stream['codec_name'] not in ('h264','hevc','ffv1','mpeg4','av1'):return reject('unsupported_codec',codec=stream['codec_name'])
        if stream.get('field_order','unknown') not in ('progressive','unknown'):return reject('interlaced_source',field_order=stream['field_order'])
        pts=sorted(float(p['pts_time']) for p in data['packets'])
        # AV1 packets can contain hidden reference frames. Require the container
        # presentation-frame count to agree with the timestamped packet count;
        # unknown or multi-presentation packet layouts retain filtered decoding.
        if stream['codec_name']=='av1' and int(stream.get('nb_frames',0))!=len(pts):
            return reject('av1_presentation_count_unverified',container_frames=stream.get('nb_frames'),packets=len(pts))
        if len(pts)<3:return reject('insufficient_packet_timestamps',packets=len(pts))
        origin=float(data['format']['start_time'])
        if abs(origin)>1e-7 or abs(pts[0])>1e-7:return reject('nonzero_source_origin',format_start=origin,first_video_pts=pts[0])
        period=(pts[-1]-pts[0])/(len(pts)-1)
        if period<=0 or not math.isfinite(period):return reject('invalid_period')
        # FFprobe's decimal timestamps are rounded; accept at most 2 microseconds.
        for i,t in enumerate(pts):
            if not math.isfinite(t) or abs(t-i*period)>2e-6:
                return reject('irregular_packet_timestamps',index=i,pts=t,expected=i*period,period=period)
        job.decode_probe=dict(reason='verified_cfr',period=period,packets=len(pts))
        return period
    except (ValueError,KeyError,TypeError,IndexError) as error:return reject('incomplete_probe_metadata',error=type(error).__name__)


def choose_decode(video,settings,job,windows):
    automatic=[gap for _,_,gaps in windows for gap in gaps]
    continuous=windows
    # One second per process is a deliberately conservative launch/seek budget.
    # Dense markings use one decoder rather than repeatedly reading prefixes.
    cost=sum(b-a+1 for a,b,_ in windows)
    if not windows or len(windows)>8 or video.duration-cost<3 or video.duration*max(1,video.fps)>500000:
        job.decode_probe=dict(reason='seek_cost_or_scan_limit',windows=len(windows),estimated_window_cost_s=cost)
        return continuous,None,'continuous_dense_or_short'
    period=cfr_period(video,job)
    if period is None:return continuous,None,'continuous_irregular_or_unknown_pts'
    step=max(1,math.ceil((1/settings.sample_fps-1e-6)/period))
    return windows,period*step,'seek_verified_cfr'
