"""Ordered project export with staged outputs and cancellable media processing."""
import os
import tempfile
from pathlib import Path
from .export import export
from .model import validate_plan
from .process import run, tool, publish
from .errors import AppError
from .i18n import Text


def output_paths(videos, directory):
    """Numbered names avoid collisions for equal stems from different folders."""
    return [Path(directory) / f'{i:03d}_{v.path.stem}_smartcut.mp4'
            for i,v in enumerate(videos,1)]


def export_project(items, destination, job, progress=lambda *_: None, merged=True, **options):
    if not items:
        raise AppError('error.empty')
    destination=Path(destination).absolute()
    targets=[destination] if merged else output_paths([v for v,_ in items],destination)
    sources={v.path.resolve() for v,_ in items}
    for target in targets:
        if target.resolve() in sources:raise AppError('error.original')
        if target.exists():raise AppError('error.exists')
        if target.suffix.lower()!='.mp4':raise AppError('error.mp4')
    for video,plan in items:
        validate_plan(plan,video.duration)
        if sum(s.output_duration for s in plan)<.02:raise AppError('error.empty')
    parent=destination.parent if merged else destination
    canvas=(max(2,round(items[0][0].display_width/2)*2),max(2,round(items[0][0].height/2)*2))
    audio=any(v.audio for v,_ in items)
    published=[]
    with tempfile.TemporaryDirectory(prefix='smartcut-project-',dir=parent) as directory:
        temp=Path(directory)
        try:
            for i,(video,plan) in enumerate(items):
                job.check()
                export(video,plan,temp/f'clip{i}.mp4',job,
                       lambda f,s,i=i:progress(.9*(i+f)/len(items),Text('batch.progress',{'index':i+1,'count':len(items)})),
                       **options,**({'canvas':canvas,'force_audio':audio} if merged else {}))
            if merged:
                manifest=temp/'clips.txt'
                manifest.write_text(''.join(f"file 'clip{i}.mp4'\n" for i in range(len(items))),encoding='utf-8')
                pending=temp/'merged.mp4'
                duration=sum(s.output_duration for _,plan in items for s in plan)
                run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-n',
                     '-f','concat','-safe','1','-i',str(manifest),'-map','0:v:0','-map','0:a?',
                     '-c','copy','-movflags','+faststart','-progress','pipe:1',str(pending)],
                    job,duration,lambda f,s:progress(.9+.1*f,s),Text('stage.finalizing'))
                staged=[pending]
            else:staged=[temp/f'clip{i}.mp4' for i in range(len(items))]
            for source,target in zip(staged,targets):
                job.check();publish(source,target);published.append(target)
        except BaseException:
            for target in published:target.unlink(missing_ok=True)
            raise
    progress(1.,Text('stage.export_complete'))
    return destination
