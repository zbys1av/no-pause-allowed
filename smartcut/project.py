"""Ordered multi-video export using the same editing plans as single-file export."""
import os
from pathlib import Path
import tempfile

from .errors import AppError
from .export import export
from .i18n import Text
from .model import statistics, validate_plan
from .process import run, tool


def export_project(items, destination, job, progress=lambda *_: None, *, combined=True,
                   crf=23, mute=False, encoder='libx264'):
    """Stage every output before publishing; never replace sources or existing files."""
    items = list(items)
    if not items:
        raise AppError('error.empty')
    destination = Path(destination).absolute()
    for video, plan in items:
        validate_plan(plan, video.duration)
        if statistics(plan)['output'] < .02:
            raise AppError('error.empty', video.path.name)
    outputs = ([destination] if combined else
               [destination / f'{i+1:02d}_{v.path.stem}_smartcut.mp4' for i,(v,_) in enumerate(items)])
    sources = {v.path.resolve() for v,_ in items}
    for path in outputs:
        if path.resolve() in sources:
            raise AppError('error.original')
        if path.exists():
            raise AppError('error.exists', str(path))
        if path.suffix.lower() != '.mp4':
            raise AppError('error.mp4')
    parent = destination.parent if combined else destination
    # First clip defines the joined canvas; letterbox other aspect ratios.
    first = items[0][0]
    canvas = (max(2, int(round(first.display_width / 2))*2), (first.height+1)//2*2)
    audio = any(v.audio for v,_ in items) and not mute
    with tempfile.TemporaryDirectory(prefix='smartcut-project-', dir=parent) as directory:
        temp = Path(directory)
        staged = []
        for i,(video,plan) in enumerate(items):
            job.check()
            part = temp / f'clip{i}.mov' if combined else temp / f'clip{i}.mp4'
            export(video, plan, part, job,
                   lambda f,s,i=i: progress(.9*(i+f)/len(items), s),
                   crf=crf, mute=mute, encoder=encoder,
                   canvas=canvas if combined else None, force_audio=combined and audio,
                   intermediate=combined)
            staged.append(part)
        if combined:
            manifest = temp / 'clips.txt'
            manifest.write_text(''.join(f"file 'clip{i}.mov'\n" for i in range(len(items))), encoding='utf-8')
            pending = temp / 'joined.mp4'
            args = [tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-nostdin', '-n',
                    '-f', 'concat', '-safe', '1', '-i', str(manifest), '-map', '0:v:0', '-c:v', 'copy']
            args += ['-map', '0:a:0', '-c:a', 'aac', '-b:a', '192k'] if audio else ['-an']
            args += ['-movflags', '+faststart', '-progress', 'pipe:1', str(pending)]
            run(args, job, sum(statistics(p)['output'] for _,p in items),
                lambda f,s: progress(.9+.1*f,s), Text('stage.finalizing'))
            staged = [pending]
        published = []
        try:
            for source,target in zip(staged,outputs):
                job.check()
                os.link(source,target)
                published.append(target)
        except BaseException:
            for path in published:
                path.unlink()
            raise
    progress(1.,Text('stage.export_complete'))
    return outputs
