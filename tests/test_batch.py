import time
from pathlib import Path
import subprocess
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData,QUrl
from smartcut.app import Window,ExportDialog
from smartcut.widgets import video_drop_paths
from smartcut.batch import export_project,output_paths
from smartcut.model import Segment
from smartcut.video import probe
from smartcut.process import Job,Cancelled,tool
from smartcut.errors import AppError


def ff(*args):
    return subprocess.run([tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin',*map(str,args)],check=True,capture_output=True).stdout


@pytest.fixture
def clips(tmp_path):
    a=tmp_path/'перша.mp4';b=tmp_path/'друга.mp4'
    ff('-f','lavfi','-i','color=red:s=160x90:r=24:d=2','-c:v','libx264',a)
    ff('-f','lavfi','-i','color=blue:s=96x128:r=25:d=2','-f','lavfi','-i','sine=frequency=440:duration=2',
       '-c:v','libx264','-c:a','aac',b)
    return [probe(a),probe(b)]


def test_merged_order_dimensions_audio_and_mute(clips,tmp_path):
    plans=[(v,[Segment(0,1),Segment(1,2,'remove')]) for v in clips]
    output=tmp_path/'merged.mp4';progress=[]
    export_project(plans,output,Job(),lambda f,s:progress.append(f))
    v=probe(output)
    assert v.duration==pytest.approx(2,abs=.15)
    assert (v.width,v.height)==(160,90) and v.audio
    for t,channel in ((.4,0),(1.4,2)):
        raw=ff('-ss',t,'-i',output,'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1')
        rgb=np.frombuffer(raw,dtype=np.uint8).reshape(v.height,v.width,3)
        assert rgb[v.height//2,v.width//2,channel]>200
    audio=np.frombuffer(ff('-i',output,'-f','f32le','-ac','1','-ar','48000','pipe:1'),dtype=np.float32)
    assert np.max(np.abs(audio[:24000]))<.01
    assert np.max(np.abs(audio[60000:84000]))>.03
    assert progress==sorted(progress) and progress[-1]==1
    muted=tmp_path/'muted.mp4'
    export_project(plans,muted,Job(),mute=True)
    assert not probe(muted).audio


def test_separate_names_original_sizes_and_collision(clips,tmp_path):
    folder=tmp_path/'separate';folder.mkdir()
    items=[(clips[0],[Segment(0,2,'speed',2)]),(clips[1],[Segment(0,1),Segment(1,2,'remove')])]
    export_project(items,folder,Job(),merged=False)
    outputs=output_paths(clips,folder)
    assert probe(outputs[0]).duration==pytest.approx(1,abs=.1)
    assert (probe(outputs[1]).width,probe(outputs[1]).height)==(96,128)
    before=[p.read_bytes() for p in outputs]
    with pytest.raises(AppError):export_project(items,folder,Job(),merged=False)
    assert before==[p.read_bytes() for p in outputs]


def test_batch_failure_and_cancel_publish_nothing(clips,tmp_path,monkeypatch):
    import smartcut.batch as batch
    items=[(v,[Segment(0,1),Segment(1,2,'remove')]) for v in clips]
    original=batch.export
    def fail(video,*args,**kwargs):
        if video==clips[1]:raise Cancelled()
        return original(video,*args,**kwargs)
    monkeypatch.setattr(batch,'export',fail)
    with pytest.raises(Cancelled):export_project(items,tmp_path/'failed.mp4',Job())
    assert not (tmp_path/'failed.mp4').exists()
    assert not list(tmp_path.glob('smartcut-project-*'))
    job=Job();job.cancel()
    with pytest.raises(Cancelled):export_project(items,tmp_path/'cancel.mp4',job)
    with pytest.raises(AppError):export_project([(clips[0],[Segment(0,2,'remove')])],tmp_path/'empty.mp4',Job())


def test_multiple_drop_validation():
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile('/tmp/a.mp4'),QUrl.fromLocalFile('/tmp/b.MOV')])
    assert len(video_drop_paths(mime))==2
    mime.setUrls([QUrl.fromLocalFile('/tmp/a.mp4'),QUrl('https://example.com/b.mp4')])
    assert not video_drop_paths(mime)


def test_project_editor_switch_append_reorder_and_analysis(clips):
    app=QApplication.instance() or QApplication([])
    w=Window(language='uk',remember=False);errors=[];w.show_error=errors.append
    def wait():
        deadline=time.monotonic()+25
        while w.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
        assert w.worker is None and not errors,errors
    try:
        w.open_videos([str(v.path) for v in clips]);wait()
        assert len(w.project)==2
        w.analyze_all();wait()
        assert all(e['state']['fields']['plan'] for e in w.project)
        w.plan=[Segment(0,2)];w.manual_changed=True;w.minimum.setValue(2)
        w.plan=[Segment(0,2)];w.manual_changed=True
        w.switch_file(1);w.switch_file(0)
        assert w.manual_changed and w.plan==[Segment(0,2)] and w.minimum.value()==2
        w.sensitivity.setValue(60);assert w.dirty
        w.switch_file(1);assert not w.dirty
        w.start_project_export();assert errors[-1].code=='batch.not_ready';errors.clear()
        w.move_file_up();assert w.project_index==0 and w.project[0]['video']==clips[1]
        w.open_videos([str(clips[0].path)],append=True);assert w.worker is None
        dialog=ExportDialog(w);assert dialog.output.count()==2
    finally:w.close();app.processEvents()
