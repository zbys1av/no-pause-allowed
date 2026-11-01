"""Cross-platform, fresh-process benchmark of 1/2/4/8/Auto pair workers.

Run with the development Python environment; outputs actual RAM/CPU where
the OS exposes them. Includes an old-kernel active reference, not old app code.
"""
import argparse,hashlib,json,os,subprocess,sys,tempfile,time
from pathlib import Path
from dataclasses import asdict
import numpy as np
from smartcut.model import Settings,analyze_plan,statistics
from smartcut.motion import analyze
from smartcut.video import probe
from smartcut.process import Job,tool


def legacy_masks(a,b,sensitivity,difference=None):
    d=b.astype(np.int16)-a.astype(np.int16);offset=np.median(d[::4,::4],axis=(0,1))
    if np.all(np.abs(offset)<=2):d-=np.asarray(offset,np.int16)
    t=max(4.,16-sensitivity*.12)
    l=np.abs(np.einsum('...c,c->...',d,np.array([77,150,29],np.int32),dtype=np.int32))
    m=np.abs(d).max(axis=2)
    return d,(l>t*256)|(m>t*3),(l>=max(24,t*3)*256)|(m>=max(32,t*4))


def memory():
    if os.name!='nt':
        import resource
        scale=1024**2 if sys.platform=='darwin' else 1024
        return dict(app_peak_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/scale,
                    largest_child_peak_mb=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss/scale,
                    note='Separate peaks; not simultaneous combined RSS.')
    import ctypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ('peak','working','peak_paged','paged','peak_nonpaged','nonpaged','page','peak_page')]
    data=Counters();data.cb=ctypes.sizeof(data)
    ctypes.windll.kernel32.GetCurrentProcess.restype=ctypes.c_void_p
    handle=ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes=[ctypes.c_void_p,ctypes.POINTER(Counters),ctypes.c_ulong]
    if ctypes.windll.psapi.GetProcessMemoryInfo(handle,ctypes.byref(data),data.cb):return dict(app_peak_mb=data.peak/1024**2,note='Application peak; decoder RSS not available.')
    return dict(note='Peak RSS unavailable.')


def run_one(args):
    if args.legacy:
        import smartcut.motion
        smartcut.motion.change_masks=legacy_masks
        def legacy_pair(a,b,sensitivity,details=True):
            if a is None:return None
            _,changed,strong=legacy_masks(a,b,sensitivity)
            score,moving,pixels=smartcut.motion.significant_pixels(changed,strong,sensitivity)
            bounds=None;points=()
            if details and moving and np.count_nonzero(pixels)<=64:
                ys,xs=np.nonzero(pixels)
                if len(xs) and xs.max()-xs.min()<32 and ys.max()-ys.min()<32:
                    bounds=(int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1));points=tuple(zip(xs.tolist(),ys.tolist()))
            bitmap=np.packbits(pixels).tobytes() if details and moving and not points else None
            return smartcut.motion.PairEvidence(score,moving,bounds,points,bitmap)
        smartcut.motion.prepare_pair=legacy_pair
    video=probe(args.run);job=Job();settings=Settings(workers=args.workers)
    cpu=time.process_time();children=os.times();start=time.perf_counter()
    samples=analyze(video,settings,job)
    elapsed=time.perf_counter()-start;cpu=time.process_time()-cpu;after=os.times()
    child_cpu=(after.children_user+after.children_system-children.children_user-children.children_system) if os.name!='nt' else None
    plan=analyze_plan(samples,video.duration,settings)
    rows=[(s.time,s.score,s.active,s.transient,s.persistent,s.bounds,s.evidence) for s in samples]
    result=dict(workers=args.workers,legacy_kernel=args.legacy,seconds=elapsed,app_cpu_s=cpu,
        decoder_cpu_s=child_cpu,cpu_one_core_percent=100*(cpu+(child_cpu or 0))/elapsed,
        memory=memory(),samples=len(samples),signature=hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
        pauses=plan.pauses,pause_seconds=plan.pause_time,plan=[asdict(s) for s in plan.plan],
        statistics=statistics(plan.plan),performance=job.debug_analysis_performance)
    print(json.dumps(result))


def generate(directory,case,long_seconds):
    path=directory/(case+'.mp4');duration=long_seconds if case=='long' else 8 if case=='short' else 24
    generator='color=c=gray:s' if case=='long' else 'testsrc2=s'
    args=[tool('ffmpeg'),'-hide_banner','-loglevel','error','-f','lavfi','-i',f'{generator}=1280x720:r=24:d={duration}']
    if case=='short':args+=['-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='gte(t,2)*lt(t,6)'"]
    if case=='vfr':args+=['-vf',"select='if(lt(t,12),not(mod(n,3)),1)'",'-fps_mode','vfr']
    if case=='sparse':
        path=directory/'sparse.mkv'
        command=[tool('ffmpeg'),'-hide_banner','-loglevel','error','-f','rawvideo','-pixel_format','gray','-video_size','960x540','-framerate','8','-i','pipe:0','-c:v','ffv1',str(path)]
        with subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE) as process:
            for i in range(24*8):
                frame=np.full((540,960),200,np.uint8)
                frame[200,300:300+min(i,70)]=30
                if i>=120:frame[300,400:400+i-119]=30
                if (i//16)%2:frame[30,40:42]=30
                process.stdin.write(frame.tobytes())
            process.stdin.close();errors=process.stderr.read().decode(errors='replace')
            if process.wait():raise RuntimeError(errors)
    else:subprocess.run(args+['-c:v','libx264','-preset','veryfast','-threads','2',str(path)],check=True)
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',type=Path);parser.add_argument('--workers',type=int,default=1)
    parser.add_argument('--legacy',action='store_true')
    parser.add_argument('--long-seconds',type=float,default=120)
    parser.add_argument('--cases',nargs='+',default=['short','long','active','sparse','vfr'])
    parser.add_argument('--output',type=Path,default=Path('build/parallel-benchmark.json'))
    args=parser.parse_args()
    if args.run:run_one(args);return
    results=[]
    with tempfile.TemporaryDirectory(prefix='smartcut-parallel-bench-') as folder:
        for case in args.cases:
            source=generate(Path(folder),case,args.long_seconds);reference=None;records=[]
            for count in ([1,2,4,8,0]+[0,8,4,2,1]+([-1] if case=='active' else [])):
                command=[sys.executable,__file__,'--run',str(source),'--workers',str(max(0,count))]
                if count==-1:command=[sys.executable,__file__,'--run',str(source),'--workers','1','--legacy']
                record=json.loads(subprocess.run(command,check=True,capture_output=True,text=True).stdout)
                key=(record['signature'],record['plan'],record['pauses'])
                if reference is None:reference=key
                if key!=reference:raise AssertionError(f'{case}/{count}: result mismatch')
                records.append(record)
                print(case,count,round(record['seconds'],3),'s',record['performance']['effective_workers'],'effective workers',flush=True)
            from statistics import median
            base=median(r['seconds'] for r in records if r['workers']==1 and not r['legacy_kernel'])
            for r in records:r['speedup_vs_1']=base/r['seconds']
            results.append(dict(case=case,identical_results=True,runs=records))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(dict(cases=results,note='One fresh process per run; 100% CPU equals one core. Legacy is the old RGB kernel with current temporal logic. Timing depends on content/hardware.'),indent=2)+'\n')
    print('Saved',args.output,flush=True)


if __name__=='__main__':main()
