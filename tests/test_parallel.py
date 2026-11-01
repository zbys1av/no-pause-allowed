from dataclasses import replace
import threading,time,subprocess
import numpy as np
import pytest
from smartcut.parallel import PairPipeline,choose_workers
from smartcut.motion import change_masks,analyze,MotionTracker,prepare_pair,significant_pixels
from smartcut.model import Settings,analyze_plan
from smartcut.process import Job,Cancelled,tool
from smartcut.video import probe


def legacy_masks(a,b,sensitivity):
    d=b.astype(np.int16)-a.astype(np.int16)
    offset=np.median(d[::4,::4],axis=(0,1))
    if np.all(np.abs(offset)<=2):d-=np.asarray(offset,np.int16)
    t=max(4.,16-sensitivity*.12)
    l=np.abs(np.einsum('...c,c->...',d,np.array([77,150,29],np.int32),dtype=np.int32))
    m=np.abs(d).max(axis=2)
    return d,(l>t*256)|(m>t*3),(l>=max(24,t*3)*256)|(m>=max(32,t*4))


@pytest.mark.parametrize('sensitivity',[0,65,95,100])
def test_fast_rgb_kernel_is_bit_exact(sensitivity):
    rng=np.random.default_rng(9)
    for noise in (True,False):
        a=rng.integers(0,256,(51,83,3),dtype=np.uint8)
        b=np.clip(a.astype(np.int16)+rng.integers(-2,3,a.shape),0,255).astype(np.uint8) if noise else rng.integers(0,256,a.shape,dtype=np.uint8)
        assert all(np.array_equal(x,y) for x,y in zip(legacy_masks(a,b,sensitivity),change_masks(a,b,sensitivity)))
        _,changed,strong=legacy_masks(a,b,sensitivity)
        expected=significant_pixels(changed,strong,sensitivity)
        pair=prepare_pair(a,b,sensitivity)
        assert pair.score==expected[0] and pair.moving==expected[1]


@pytest.mark.parametrize('workers',[1,2,4,8])
def test_out_of_order_completion_merges_in_source_order(workers):
    def prepare(a,b):
        if int(b[0])%7==0:time.sleep(.005)
        return int(b[0])-int(a[0])
    with PairPipeline(workers,prepare,Job()) as pipeline:
        result=list(pipeline.frames((i/8,np.array([i])) for i in range(40)))
    assert [t for t,_,_ in result]==[i/8 for i in range(40)]
    assert [v for _,_,v in result]==[None]+[1]*39
    assert pipeline.stats['max_pending']<=2*workers


@pytest.mark.parametrize('error_type',[ValueError,TimeoutError])
def test_worker_failure_retries_synchronously_and_cancel_joins_threads(error_type):
    def flaky(a,b):
        if int(b[0])==7 and threading.current_thread().name.startswith('smartcut-pair'):raise error_type('worker failure')
        return int(b[0])
    job=Job()
    with PairPipeline(4,flaky,job) as pipeline:
        values=[v for _,_,v in pipeline.frames((i,np.array([i])) for i in range(30))]
    assert values==[None]+list(range(1,30)) and pipeline.stats['fallbacks']==1
    job=Job()
    with pytest.raises(Cancelled):
        with PairPipeline(4,flaky,job) as pipeline:
            for t,_,_ in pipeline.frames((i,np.array([i])) for i in range(100)):
                if t==5:job.cancel()
    assert not any(t.name.startswith('smartcut-pair') for t in threading.enumerate())


def test_auto_switches_for_expensive_pairs_and_keeps_trivial_work_serial():
    def expensive(a,b):
        if int(b[0])<65:time.sleep(.004)
        return int(b[0])
    with PairPipeline(2,expensive,Job(),auto=True) as pipeline:
        values=[v for _,_,v in pipeline.frames((i,np.array([i])) for i in range(140))]
    assert values==[None]+list(range(1,140))
    assert [e['workers'] for e in pipeline.stats['worker_events']]==[2,1]


def test_auto_is_conservative_on_short_video_and_low_resources(monkeypatch):
    monkeypatch.setattr('smartcut.parallel.resources',lambda:(4,8*1024**3))
    assert choose_workers(0,10,960*540)[0]==1
    assert choose_workers(0,600,960*540)[0]==2
    assert choose_workers(8,600,960*540)[0]==8
    monkeypatch.setattr('smartcut.parallel.resources',lambda:(2,64*1024**2))
    assert choose_workers(0,600,960*540)[0]==1
    assert choose_workers(8,600,960*540)[0]==1


@pytest.mark.parametrize('vfr',[False,True])
def test_real_video_workers_match_timestamps_strokes_and_boundary_pause(tmp_path,vfr):
    source=tmp_path/'source.mp4'
    filters="drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='gte(t,4)*lt(t,12)'"
    if vfr:filters+=",select='if(lt(t,6),not(mod(n,3)),1)'"
    subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-f','lavfi','-i',
        'testsrc2=s=320x180:r=24:d=16','-vf',filters,'-fps_mode','vfr','-c:v','libx264','-threads','2',str(source)],check=True)
    video=probe(source);settings=Settings();reference=None
    for workers in (1,2,4,8):
        job=Job();progress=[]
        samples=analyze(video,replace(settings,workers=workers),job,lambda p,s:progress.append((p,str(s))))
        data=([(s.time,s.score,s.active,s.transient,s.persistent,s.bounds) for s in samples],analyze_plan(samples,video.duration,settings).plan)
        if reference is None:reference=data
        assert data==reference
        assert job.debug_analysis_performance['effective_workers']==workers
        assert len(samples)==job.debug_analysis_performance['validated_samples']
        assert progress[-1][0]==1 and all(p<1 for p,_ in progress[:-1])
    assert any(s.action=='remove' and s.start<8<s.end for s in reference[1])


@pytest.mark.parametrize('large',[False,True])
def test_recorded_scene_event_does_not_revoke_old_confirmed_blinks(large):
    tracker=MotionTracker(95)
    for i in range(400):
        t=i/8;frame=np.full((100,160,3),200,np.uint8)
        if int(t//2)%2:frame[30,40:42]=30
        if t>=20.5:
            if large:frame[70:90,100:140]=30
            else:frame[70,100:103]=30
        tracker.add(t,frame)
    assert tracker.cycle_stats['explained_changes']>0
    assert not any(s.evidence=='background_changed' for s in tracker.samples)
    report=analyze_plan(tracker.samples,50,Settings())
    assert report.plan[0].action=='remove' and report.plan[0].end>19
    assert any(s.action=='keep' and s.start<20.5<s.end for s in report.plan)
    assert report.plan[-1].action=='remove' and report.plan[-1].start<22


def test_consumed_scene_event_cannot_hide_later_faint_ink_at_same_pixels():
    tracker=MotionTracker(95)
    for i in range(288):
        t=i/8;frame=np.full((100,160,3),200,np.uint8)
        if int(t//2)%2:frame[30,40:42]=30
        if t>=20.5:frame[70,100:103]=30+min(20,max(0,int((t-26)*2)))
        tracker.add(t,frame)
    assert tracker.cycle_stats['explained_changes']>0
    assert tracker.cycle_stats['unexplained_changes']>0
    report=analyze_plan(tracker.samples,36,Settings())
    assert any(s.action=='keep' and s.start<30 and s.end>34 for s in report.plan)
