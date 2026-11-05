"""Measure analysis with different manual coverage on generated video."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time

from smartcut.manual_intervals import assign
from smartcut.model import Settings, analyze_plan, edit_range
from smartcut.motion import analyze,analyze_selected
from smartcut.process import Job, tool
from smartcut.video import probe


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=12)
    parser.add_argument('--output',type=Path,default=Path('build/manual-benchmark.json'))
    parser.add_argument('--fragments',type=int,default=1)
    parser.add_argument('--policy',choices=['auto','legacy','full'],default='auto')
    parser.add_argument('--vfr',action='store_true')
    parser.add_argument('--codec',choices=['h264','av1'],default='h264')
    args=parser.parse_args()
    if args.seconds<4:parser.error('Use at least four seconds')
    if args.fragments<1:parser.error('Fragments must be positive')
    records=[]
    with tempfile.TemporaryDirectory(prefix='smartcut-manual-benchmark-') as temp:
        path=Path(temp)/'source.mp4'
        subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',
            '-f','lavfi','-i',f'testsrc2=s=640x360:r=30:d={args.seconds}',
            *(['-vf',"select='not(mod(n,3))+not(mod(n,7))'",'-fps_mode','vfr'] if args.vfr else []),
            *(['-c:v','libaom-av1','-cpu-used','8','-crf','40','-g','120'] if args.codec=='av1' else ['-c:v','libx264','-preset','veryfast']),
            '-threads','2',str(path)],check=True)
        video=probe(path);settings=Settings(analysis_width=640)
        for coverage in (0,.3,.6,.9):
            manual=[]
            if coverage:
                for index in range(args.fragments):
                    a=index*video.duration/args.fragments;b=a+video.duration*coverage/args.fragments
                    manual=assign(manual,a,b,'pause',video.duration)
            job=Job();cpu=time.process_time();start=time.perf_counter()
            selective=bool(manual) and args.policy!='full'
            samples=analyze_selected(video,settings,job,manual,decode_policy=args.policy) if selective else analyze(video,settings,job)
            report=analyze_plan(samples,video.duration,settings)
            plan=list(report.plan)
            for item in manual:plan=edit_range(plan,item.start,item.end,'remove')
            performance=job.debug_analysis_performance
            records.append(dict(manual_fraction=coverage,seconds=time.perf_counter()-start,
                python_cpu_seconds=time.process_time()-cpu,selected_frames=performance['frames'],
                full_checks=performance['full_checks'],pair_checks=(sum(item['parallel']['pairs'] for item in performance['window_details']) if selective else performance['parallel']['pairs']),
                codec=args.codec,strategy=performance.get('decode_strategy','continuous'),ffmpeg_processes=performance.get('ffmpeg_processes',1),
                ffmpeg_s=performance.get('ffmpeg_s'),fragments=args.fragments,policy=args.policy,
                output_duration=sum(s.output_duration for s in plan)))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    for record in records:record['speedup']=records[0]['seconds']/record['seconds']
    args.output.write_text(json.dumps(dict(records=records,note='Python CPU excludes FFmpeg. Selected frames are not the decoder internal frame count. FFmpeg wall time includes pipe backpressure; auto wall time also includes packet inspection.'),indent=2)+'\n')
    print(json.dumps(records,indent=2))


if __name__=='__main__':main()
