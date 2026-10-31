import csv
import io
import json
from pathlib import Path
import zipfile
import pytest
from PySide6.QtWidgets import QApplication
from smartcut.app import Window
from smartcut.debug import snapshot,write_report
from smartcut.model import MotionSample,Settings,analyze_plan,Segment
from smartcut.process import Job,Cancelled
from smartcut.video import Video
from smartcut.debug_replay import replay


def test_report_replays_plan_and_explains_manual_change_and_dirty_settings(tmp_path):
    app=QApplication.instance() or QApplication([])
    w=Window(language='uk',remember=False)
    w.video=Video(tmp_path/'private-user'/'drawing.mp4',18,320,180,10,1,'mp4',False)
    w.analyzed([MotionSample(0,0,False),MotionSample(5,1,True,persistent=True),MotionSample(13,0,False),MotionSample(18,1,True,persistent=True)])
    original=list(w.automatic)
    w.populate_segment(1);w.set_action('keep');w.sensitivity.setValue(80)
    w.journal.add('test_error',error=str(w.video.path))
    captured=snapshot(w)
    output=tmp_path/'репорт.zip';write_report(captured,output,Job())
    with zipfile.ZipFile(output) as archive:
        report=json.loads(archive.read('report.json'))
        rows=list(csv.DictReader(io.StringIO(archive.read('samples.csv').decode())))
        assert str(tmp_path) not in archive.read('report.json').decode()
        assert report['dirty'] and report['current_settings']['sensitivity']==80
        assert report['plan_settings']['sensitivity']==95
        assert report['pause_decisions'][0]['reason']=='eligible_pause'
        assert report['burst_decisions'][0]['reason']=='longer_than_debounce'
        assert report['burst_decisions'][-1]['reason']=='boundary_burst_preserved'
        assert report['segment_decisions'][1]['reason']=='manual_edit'
        assert report['statistics']['removed']==0
        settings=Settings(**report['plan_settings'])
        samples=[MotionSample(float(r['time_s']),float(r['score']),bool(int(r['raw_active'])),bool(int(r['transient'])),bool(int(r['persistent']))) for r in rows]
        assert list(analyze_plan(samples,report['video']['duration'],settings).plan)==original
        assert len(report['journal'])>0 and report['schema_version']==1
    with pytest.raises(FileExistsError):write_report(captured,output,Job())
    assert replay(output)['matches_recorded_automatic_plan']
    job=Job();job.cancel()
    with pytest.raises(Cancelled):write_report(captured,tmp_path/'cancel.zip',job)
    assert not (tmp_path/'cancel.zip').exists()
    assert not list(tmp_path.glob('smartcut-report-*'))
    w.close();app.processEvents()


def test_gui_saves_report_in_background_without_losing_analysis(tmp_path,monkeypatch):
    import time
    from PySide6.QtWidgets import QFileDialog
    from smartcut.state import State
    app=QApplication.instance() or QApplication([])
    w=Window(language='uk',remember=False)
    w.video=Video(tmp_path/'input.mp4',10,320,180,10,1,'mp4',False)
    w.analyzed([MotionSample(0,0,False),MotionSample(5,1,True),MotionSample(10,0,False)])
    before=list(w.plan)
    output=tmp_path/'report.zip'
    monkeypatch.setattr(QFileDialog,'getSaveFileName',lambda *a,**kw:(str(output),'ZIP'))
    errors=[];w.show_error=errors.append
    w.save_debug_report()
    deadline=time.monotonic()+10
    while w.worker is not None and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert not errors and w.worker is None and output.is_file()
    assert w.workflow.state==State.REPORT_COMPLETE and w.plan==before
    assert w.debug_button.isEnabled() and w.progress.isHidden()
    assert str(output) in w.phase_label.text()
    w.close();app.processEvents()


def test_report_without_video_and_with_partial_failed_samples(tmp_path):
    app=QApplication.instance() or QApplication([])
    w=Window(language='en',remember=False)
    w.last_job_debug={'state':'analysis_failed','samples':[MotionSample(0,0,False),MotionSample(1,.1,True)]}
    target=tmp_path/'failed.zip';write_report(snapshot(w),target,Job())
    with zipfile.ZipFile(target) as archive:
        report=json.loads(archive.read('report.json'))
        assert report['video'] is None and report['analysis'] is None
        assert report['last_operation']['state']=='analysis_failed'
        assert len(archive.read('partial_samples.csv').decode().splitlines())==3
        assert len(archive.read('samples.csv').decode().splitlines())==1
    w.close();app.processEvents()
