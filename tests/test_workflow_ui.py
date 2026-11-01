"""User-visible state, localization and stale-result regressions."""
from pathlib import Path
import threading
import time
import pytest
from PySide6.QtWidgets import QApplication
from smartcut.app import Window, STYLE
from smartcut.errors import AppError, user_error
from smartcut.i18n import Text
from smartcut.state import State
from smartcut.video import Video


def wait(app, predicate):
    deadline = time.monotonic()+8
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert predicate(), 'GUI operation timed out'


def editor():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(STYLE)
    window = Window(language='uk', remember=False)
    window.video = Video(Path('/tmp/drawing.mp4'), 18, 320, 180, 10, 1, 'mp4', False)
    window.view.dimensions(320, 180)
    window.workflow.state = State.READY
    window.render()
    return app, window


def analyzed(window):
    window.workflow.begin('analysis')
    window.workflow.finish('complete')
    window.analyzed([(0, 0, False), (5, 1, True), (13, 0, False), (18, 1, True)])


def test_presets_simple_mode_and_live_localization():
    app, window = editor()
    window.show()
    app.processEvents()

    assert not window.advanced.toggle.isChecked()
    assert not window.manual.toggle.isChecked()
    assert not window.analysis_details.toggle.isChecked()
    assert not window.debounce.isVisible() and window.minimum.isVisible()
    drawing = window.settings()
    assert drawing.debounce == .3 and drawing.sensitivity == 95
    window.profile.setCurrentIndex(1)
    screen = window.settings()
    window.profile.setCurrentIndex(2)
    general = window.settings()
    assert len({drawing, screen, general}) == 3
    window.minimum.setValue(8)
    window.rows['minimum'].reset.click()
    assert window.minimum.value() == general.min_idle
    assert window.rows['debounce'].info.toolTip()
    analyzed(window)
    window.set_language('en')
    assert window.analyze_button.text() == 'Analyze Again'
    assert window.advanced.toggle.text() == 'Advanced Settings'
    assert window.raw_card.title.text() == 'Little/no movement'
    assert window.mode.itemText(0) == 'Remove pauses'
    assert window.table.horizontalHeaderItem(2).text() == 'Action'
    assert window.rows['minimum'].help.text().startswith('At 3 s')
    assert 'Default:' in window.rows['minimum'].default_label.text()
    assert 'Actual removal:' in window.details_text.text()
    assert user_error(AppError('error.original'), 'uk')[0].startswith('Оригінальний')
    assert 'never be overwritten' in user_error(AppError('error.original'), 'en')[0]
    window.set_language('uk')
    assert window.preview_mode.itemText(1) == 'Після монтажу'
    assert window.minimum.suffix() == ' с'
    assert window.adaptive.isChecked()
    assert window.rows['adaptive'].help.text()
    window.adaptive.setChecked(False)
    assert not window.settings().adaptive and window.dirty
    window.rows['adaptive'].reset.click()
    assert window.settings().adaptive and not window.dirty
    window.close()
    app.processEvents()


def test_no_eligible_pause_explains_unchanged_export_and_padding_loss():
    app, window = editor()
    samples = [(i/10, 1 if i % 20 in (1, 2) else 0, i % 20 in (1, 2)) for i in range(181)]
    window.analyzed(samples)
    assert window.report.idle_time > 15 and not window.report.pauses
    assert window.report.rejected_short > 0
    assert window.removed_card.value.text() == '00:00:00.00'
    assert 'розділені рухами або коротші' in window.result_message.text()
    assert 'без скорочення' in window.export_button.text()
    assert not window.suggestions.isHidden()
    questions = []
    window.question = lambda title, message: questions.append((title, message)) or False
    window.start_export()
    assert questions == [('unchanged.title', 'unchanged.confirm')]
    window.minimum.setValue(1.5)
    assert window.report.pauses and float(window.removed_card.value.text().split(':')[-1]) > 0
    window.padding.setValue(2)
    assert window.report.lost_to_padding and window.removed_card.value.text() == '00:00:00.00'
    assert 'перекриті запасом' in window.result_message.text()
    window.close()
    app.processEvents()


def test_canonical_plan_manual_edit_and_dirty_detector():
    app, window = editor()
    analyzed(window)
    raw = window.raw_card.value.text()
    assert '00:00:08.00' in window.pause_card.value.text()
    assert window.removed_card.value.text() == '00:00:06.80'
    assert '00:00:11.20' in window.stats.text()
    window.populate_segment(1)
    window.set_action('keep')
    assert window.removed_card.value.text() == '00:00:00.00'
    assert window.raw_card.value.text() == raw
    assert 'без скорочення' in window.export_button.text()
    # Temporal changes re-use frames, explain the loss of manual edits and
    # restore the exact automatic cut. No worker or second decoding pass.
    window.debounce.setValue(.5)
    assert window.worker is None and not window.dirty
    assert window.plan_reset_notice and 'ручні правки скинуто' in window.result_message.text()
    assert window.removed_card.value.text() == '00:00:06.80'
    window.sensitivity.setValue(90)
    assert window.dirty and not window.export_button.isEnabled()
    assert 'повторіть аналіз' in window.result_message.text()
    window.sensitivity.setValue(95)
    assert not window.dirty and window.export_button.isEnabled()
    window.close()
    app.processEvents()


@pytest.mark.parametrize('previous,stale', [(False, False), (True, False), (True, True)])
def test_worker_cancellation_retains_only_valid_previous_result(previous, stale):
    app, window = editor()
    if previous:
        analyzed(window)
        if stale:
            window.sensitivity.setValue(90)
    plan, samples = list(window.plan), list(window.samples)
    started = threading.Event()
    gate = threading.Event()
    completed = []

    def processing(job, progress):
        progress(.4, Text('stage.analysis'))
        started.set()
        while True:
            job.check()
            gate.wait(.01)

    window.work(processing, completed.append, Text('state.analyzing'))
    generation = window.workflow.generation
    wait(app, lambda: started.is_set() and window.progress.value() == 400)
    assert not window.export_button.isEnabled()
    window.cancel_job()
    wait(app, lambda: window.worker is None)
    assert not completed
    assert window.workflow.state == State.ANALYSIS_CANCELLED
    assert window.workflow.progress is None and window.progress.value() == 0
    assert window.progress.isHidden() and not window.eta_label.text()
    assert not window.eta_timer.isActive() and not window.cancel.isEnabled()
    assert window.plan == plan and window.samples == samples
    assert window.export_button.isEnabled() == (previous and not stale)
    if previous:
        assert 'попередній коректний' in window.result_message.text()
    if previous and not stale:
        assert window.export_button.text() == 'Експортувати попередній результат'
    # Late queued progress cannot resurrect the cancelled operation.
    window.on_progress(.9, Text('stage.analysis'), generation)
    assert window.progress.value() == 0 and window.workflow.progress is None
    window.close()
    app.processEvents()


def test_failed_load_preserves_analysis_and_resets_progress():
    app, window = editor()
    analyzed(window)
    video, plan = window.video, list(window.plan)
    errors = []
    window.show_error = errors.append

    def broken(job, progress):
        progress(.5, Text('state.loading'))
        raise AppError('error.video')

    window.work(broken, window.loaded, Text('state.loading'), 'loading')
    wait(app, lambda: window.worker is None)
    assert errors and window.workflow.state == State.LOADING_FAILED
    assert window.video == video and window.plan == plan
    assert window.export_button.isEnabled()
    assert window.progress.value() == 0 and window.workflow.progress is None
    assert window.phase_label.text() == 'Не вдалося відкрити відео'
    window.close()
    app.processEvents()
