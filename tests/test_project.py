import hashlib
import subprocess
import time

import numpy as np
import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication

from smartcut.app import Window, ExportDialog
from smartcut.errors import AppError
from smartcut.model import Segment
from smartcut.process import Job, Cancelled, tool
from smartcut.project import export_project
from smartcut.video import probe
from smartcut.widgets import video_drop_paths


def ff(*args):
    return subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-y',
                           '-threads','2',*map(str,args)],check=True,capture_output=True).stdout


@pytest.fixture
def project(tmp_path):
    # Matching basenames, different shapes/rates and missing audio are intentional.
    a=tmp_path/'a';b=tmp_path/'b';a.mkdir();b.mkdir()
    red=a/'clip.mp4';blue=b/'clip.mp4'
    ff('-f','lavfi','-i','color=red:s=160x90:r=24:d=2','-f','lavfi','-i',
       'sine=frequency=440:duration=2','-c:v','libx264','-threads','2','-c:a','aac',red)
    ff('-f','lavfi','-i','color=blue:s=90x160:r=25:d=2','-c:v','libx264','-threads','2',blue)
    return [(probe(red),[Segment(0,1),Segment(1,2,'remove')]),
            (probe(blue),[Segment(0,2,'speed',2)])]


def pixels(path,at):
    return np.frombuffer(ff('-ss',at,'-i',path,'-frames:v','1','-vf','scale=16:16',
                            '-pix_fmt','rgb24','-f','rawvideo','pipe:1'),np.uint8).reshape(16,16,3)


def audio(path,at):
    return np.frombuffer(ff('-ss',at,'-i',path,'-t','0.25','-vn','-ac','1','-ar','48000',
                            '-f','f32le','pipe:1'),np.float32)


def test_project_combined_and_separate(project,tmp_path):
    before=[hashlib.sha256(v.path.read_bytes()).digest() for v,_ in project]
    output=tmp_path/'joined.mp4';progress=[]
    assert export_project(project,output,Job(),lambda f,s:progress.append(f))==[output]
    result=probe(output)
    assert result.duration==pytest.approx(2,abs=.15)
    assert (result.width,result.height,result.fps,result.audio)==(160,90,30,True)
    assert pixels(output,.4).mean(axis=(0,1)).argmax()==0
    assert pixels(output,1.5).mean(axis=(0,1)).argmax()==2
    assert np.max(abs(audio(output,.4)))>.02
    assert np.max(abs(audio(output,1.5)))<.002
    assert progress[-1]==1 and progress==sorted(progress)
    folder=tmp_path/'separate';folder.mkdir()
    outputs=export_project(project,folder,Job(),combined=False)
    assert [p.name for p in outputs]==['01_clip_smartcut.mp4','02_clip_smartcut.mp4']
    assert [(probe(p).width,probe(p).height,probe(p).audio) for p in outputs]==[(160,90,True),(90,160,False)]
    assert all(probe(p).duration==pytest.approx(1,abs=.12) for p in outputs)
    muted=tmp_path/'muted.mp4'
    export_project(list(reversed(project)),muted,Job(),mute=True)
    assert not probe(muted).audio
    assert (probe(muted).width,probe(muted).height)==(90,160)
    assert pixels(muted,.4).mean(axis=(0,1)).argmax()==2
    assert [hashlib.sha256(v.path.read_bytes()).digest() for v,_ in project]==before


def test_project_guards_and_cancellation(project,tmp_path):
    existing=tmp_path/'existing.mp4';existing.write_bytes(b'keep')
    with pytest.raises(AppError,match='already exists'):export_project(project,existing,Job())
    assert existing.read_bytes()==b'keep'
    with pytest.raises(AppError,match='original'):export_project(project,project[1][0].path,Job())
    empty=[(project[0][0],[Segment(0,2,'remove')])]
    with pytest.raises(AppError,match='No output'):export_project(empty,tmp_path/'empty.mp4',Job())
    for combined in (True,False):
        destination=tmp_path/'cancel.mp4' if combined else tmp_path/'out'
        if not combined:destination.mkdir()
        job=Job()
        with pytest.raises(Cancelled):
            export_project(project,destination,job,lambda f,s:job.cancel(),combined=combined)
        assert not list(tmp_path.glob('smartcut-project-*'))
        assert not destination.exists() if combined else not list(destination.iterdir())


def wait(app,window):
    end=time.monotonic()+20
    while window.worker is not None and time.monotonic()<end:
        app.processEvents();time.sleep(.01)
    assert window.worker is None


def test_multi_video_ui_state_order_analysis_and_atomic_load(project,tmp_path):
    app=QApplication.instance() or QApplication([])
    window=Window(language='uk',remember=False);errors=[];window.show_error=errors.append
    # Headless machines have no physical output. Audio content is tested above;
    # avoid the PulseAudio device teardown race during rapid file switches.
    window.player.setAudioOutput(None)
    window.question=lambda *_:True
    try:
        paths=[str(v.path) for v,_ in project]
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        assert video_drop_paths(mime)==paths
        window.open_videos(paths);wait(app,window)
        assert len(window.project)==2 and not errors
        assert ExportDialog(window).output_mode.count()==3
        window.minimum.setValue(.5)
        window.start_project_analysis();wait(app,window)
        assert all(e['samples'] and e['plan'] for e in window.project)
        # Keep the whole first static clip manually; do not lose it on switching.
        window.select_segment(0);window.set_action('keep')
        first_plan=list(window.plan)
        window.sensitivity.setValue(77)
        assert window.dirty
        window.select_video(1)
        assert not window.dirty
        window.select_video(0)
        assert window.plan==first_plan and window.manual_changed and window.dirty
        assert window.sensitivity.value()==77
        window.move_video(1)
        assert window.project[1]['video'].path==project[0][0].path
        assert window.plan==first_plan
        # One bad input leaves the entire existing project intact.
        window.open_videos([paths[0],str(tmp_path/'missing.mp4')]);wait(app,window)
        assert len(errors)==1 and len(window.project)==2
        assert window.plan==first_plan
        window.open_videos([paths[0]],append=True)
        assert window.worker is None and len(window.project)==2
        window.remove_video()
        assert len(window.project)==1 and window.video.path==project[1][0].path
    finally:
        window.close();app.processEvents()


def test_publication_failure_rolls_back_completed_files(project,tmp_path,monkeypatch):
    import smartcut.project as module
    directory=tmp_path/'results';directory.mkdir()
    link=module.os.link
    def racing_link(source,target):
        if str(target).endswith('02_clip_smartcut.mp4'):
            raise FileExistsError('Concurrent file creation')
        return link(source,target)
    monkeypatch.setattr(module.os,'link',racing_link)
    with pytest.raises(FileExistsError):
        export_project(project,directory,Job(),combined=False)
    assert not list(directory.iterdir())


def test_gui_project_export_choices_and_stale_guard(project,tmp_path,monkeypatch):
    import smartcut.app as module
    from PySide6.QtWidgets import QDialog, QFileDialog
    app=QApplication.instance() or QApplication([])
    window=Window(language='en',remember=False);errors=[];window.show_error=errors.append
    window.player.setAudioOutput(None)
    window.question=lambda *_:True
    try:
        window.open_videos([v.path for v,_ in project]);wait(app,window)
        window.start_project_analysis();wait(app,window)
        for index in range(2):
            window.select_video(index);window.select_segment(0);window.set_action('keep')
        dialog=ExportDialog(window)
        monkeypatch.setattr(module,'ExportDialog',lambda parent:dialog)
        monkeypatch.setattr(QDialog,'exec',lambda self:QDialog.Accepted)
        target=tmp_path/'ui-joined.mp4'
        monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**kw:(str(target),''))
        dialog.output_mode.setCurrentIndex(2)
        window.start_export();wait(app,window)
        assert target.is_file() and probe(target).duration==pytest.approx(4,abs=.15)
        folder=tmp_path/'ui-separate';folder.mkdir()
        monkeypatch.setattr(QFileDialog,'getExistingDirectory',lambda *a,**kw:str(folder))
        dialog.output_mode.setCurrentIndex(1)
        window.start_export();wait(app,window)
        assert len(list(folder.glob('*.mp4')))==2 and not errors
        window.sensitivity.setValue(70);window.select_video(0)
        assert not window.dirty
        window.start_export()
        assert window.worker is None and errors[-1].code=='error.project_analysis'
    finally:
        window.close();app.processEvents()
