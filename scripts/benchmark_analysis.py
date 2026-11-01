"""Compare fixed/adaptive analysis on generated H.264 recordings.

Run: python scripts/benchmark_analysis.py --seconds 16 --repeats 2
No timing assertions: compare median wall time and identical decision data.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import subprocess
import tempfile
import time
from smartcut.model import Settings,analyze_plan
from smartcut.motion import analyze
from smartcut.process import Job,tool
from smartcut.video import probe


def signature(samples):
    return [(s.time,s.score,s.active,s.transient,s.persistent,s.bounds) for s in samples]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=16)
    parser.add_argument('--repeats',type=int,default=2)
    parser.add_argument('--output',type=Path,default=Path('build/analysis-benchmark.json'))
    parser.add_argument('--cases',nargs='+',choices=['idle','mixed','active','blinking_idle'],default=['idle','mixed','active','blinking_idle'])
    args=parser.parse_args()
    if args.seconds<8 or args.repeats<1:parser.error('Use at least 8 seconds and one repeat')
    cases=[]
    with tempfile.TemporaryDirectory(prefix='smartcut-benchmark-') as temporary:
        for name in args.cases:
            source=Path(temporary)/(name+'.mp4')
            generator='color=c=gray:s' if name in ('idle','blinking_idle') else 'testsrc2=s'
            command=[tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-f','lavfi',
                '-i',f'{generator}=1920x1080:r=30:d={args.seconds}']
            if name=='mixed':command+=['-vf',f"drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='gte(t,2)*lt(t,{args.seconds-2})'"]
            if name=='blinking_idle':command+=['-vf',"drawbox=x=80:y=60:w=4:h=2:color=black:t=fill:enable='eq(mod(n,60),0)'"]
            command+=['-c:v','libx264','-preset','veryfast','-threads','2',str(source)]
            subprocess.run(command,check=True)
            video=probe(source);settings=Settings();records={False:[],True:[]};reference=None
            for repeat in range(args.repeats):
                for adaptive in ((False,True) if repeat%2==0 else (True,False)):
                    job=Job();start=time.perf_counter()
                    samples=analyze(video,replace(settings,adaptive=adaptive),job)
                    elapsed=time.perf_counter()-start
                    data=(signature(samples),analyze_plan(samples,video.duration,settings).plan)
                    if reference is None:reference=data
                    assert data==reference,f'{name}: decision mismatch'
                    records[adaptive].append(dict(seconds=elapsed,performance=job.debug_analysis_performance))
                    print(f'{name}: {"adaptive" if adaptive else "fixed"} {elapsed:.3f} s; full checks {job.debug_analysis_performance["full_checks"]}',flush=True)
            fixed=statistics.median(r['seconds'] for r in records[False])
            adaptive=statistics.median(r['seconds'] for r in records[True])
            cases.append(dict(case=name,source_seconds=video.duration,fixed_median_s=fixed,
                adaptive_median_s=adaptive,speedup=fixed/adaptive,identical_decisions=True,
                fixed=records[False],adaptive=records[True]))
    result=dict(cases=cases,note='Local generated 1080p H.264 clips, 960px analysis, 8 samples/s. Includes decode/scale/transfer time. Performance depends on content and hardware.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print('Saved',args.output,flush=True)


if __name__=='__main__':main()
