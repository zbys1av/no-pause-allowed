from dataclasses import replace
import numpy as np
import pytest
from smartcut.adaptive import AdaptiveAnalysis
from smartcut.motion import MotionTracker,motion_score
from smartcut.model import Settings,analyze_plan


def compare(frames,times=None,settings=None,source_fps=30):
    settings=settings or Settings()
    baseline=MotionTracker(settings.sensitivity)
    engine=AdaptiveAnalysis(MotionTracker(settings.sensitivity),settings,source_fps)
    for i,frame in enumerate(frames):
        timestamp=times[i] if times is not None else i/settings.sample_fps
        baseline.add(timestamp,frame);engine.add(timestamp,frame)
    key=lambda s:(s.time,s.score,s.active,s.transient,s.persistent,s.bounds)
    assert [key(s) for s in baseline.samples]==[key(s) for s in engine.tracker.samples]
    duration=engine.tracker.samples[-1].time+1/settings.sample_fps
    assert analyze_plan(baseline.samples,duration,settings).plan==analyze_plan(engine.tracker.samples,duration,settings).plan
    assert engine.stats['frames']==engine.stats['full_checks']+engine.stats['certified_quiet']
    return engine


def test_static_scene_grows_audit_step_and_eliminates_heavy_work():
    frame=np.full((90,160,3),200,np.uint8)
    engine=compare([frame]*240)
    assert engine.step==16 and engine.stats['certified_quiet']>180
    assert engine.stats['full_checks']<50
    assert [e['step_samples'] for e in engine.stats['step_events']]==[2,4,8,16]
    assert engine.stats['max_full_gap_s']<=2


@pytest.mark.parametrize('sensitivity',[0,35,65,90,95,100])
def test_certified_rgb_noise_is_equivalent_at_all_thresholds(sensitivity):
    rng=np.random.default_rng(42)
    frames=[(200+rng.integers(-1,2,(50,80,3))).astype(np.uint8) for _ in range(100)]
    engine=compare(frames,settings=Settings(sensitivity=sensitivity))
    assert engine.stats['bounded_matches']>50


def test_transient_motion_between_identical_endpoints_is_not_missed():
    base=np.full((90,160,3),200,np.uint8)
    frames=[base]*160
    cursor=base.copy();cursor[40:44,50:54]=20
    frames[82]=cursor  # 10.25 s, between identical coarse endpoints
    engine=compare(frames,settings=Settings(debounce=0))
    assert any(s.active for s in engine.tracker.samples if 10<=s.time<=10.5)
    assert engine.stats['refinements']>=1
    assert any(e['time_s']==10.25 and e['step_samples']==1 for e in engine.stats['step_events'])


def test_one_pixel_stroke_and_gradual_accumulation_keep_base_precision():
    base=np.full((90,160,3),200,np.uint8)
    frames=[]
    for i in range(160):
        frame=base.copy()
        if i>=70:frame[60,70]=30
        if i>=90:frame[30:32,40:43]=200-min(30,i-89)
        frames.append(frame)
    engine=compare(frames)
    assert engine.tracker.samples[70].active
    assert all(s.active for s in engine.tracker.samples[90:110])


def test_vfr_and_source_fps_cap_do_not_change_intervals():
    base=np.full((50,80,3),200,np.uint8)
    frames=[base]*150
    changed=base.copy();changed[20,30:32]=30
    frames[80]=changed
    times=np.cumsum(np.tile([.08,.13,.2],50)).tolist()
    engine=compare(frames,times,source_fps=120)
    assert engine.max_span==.5 and engine.max_step==4
    assert engine.stats['max_full_gap_s']<=.5+1e-8


def test_fixed_mode_runs_every_full_check():
    base=np.full((50,80,3),200,np.uint8)
    engine=compare([base]*100,settings=Settings(adaptive=False))
    assert engine.stats['full_checks']==100 and engine.stats['guard_checks']==0


def test_repeating_blink_and_background_guard_keep_the_same_decisions():
    frames=[]
    for i in range(240):
        t=i/8;frame=np.full((100,160,3),200,np.uint8)
        if int(t//2)%2:frame[30,40:42]=30
        frame[70,100:102]=200-min(20,int(t*2))
        frames.append(frame)
    engine=compare(frames)
    assert any(s.evidence=='repeated_local_cycle' for s in engine.tracker.samples)
    assert any(s.evidence=='background_changed' for s in engine.tracker.samples)


def test_known_blinks_resume_fast_audits_but_real_ink_resets_them():
    frames=[]
    for i in range(320):
        t=i/8;frame=np.full((100,160,3),200,np.uint8)
        if int(t//2)%2:frame[30,40:42]=30
        frames.append(frame)
    engine=compare(frames)
    assert any(e['reason']=='confirmed_transient_resume' for e in engine.stats['step_events'])
    assert engine.stats['full_checks']<len(frames)*.6
