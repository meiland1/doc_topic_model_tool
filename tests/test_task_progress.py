"""Stage counts, cancellation and task lifetime independent of active tab."""
from PySide6.QtCore import QObject, Signal
from qualitative_themes.gui.task_progress import TaskProgress
from qualitative_themes.gui.main_window import MainWindow


class FakeWorker(QObject):
    progress = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    def requestInterruption(self):
        self.interrupted = True


def test_stage_counts_reset_to_activity_for_clustering(qt_test_application):
    p = TaskProgress(); p.begin('Analysis')
    assert p.active and p.bar.maximum() == 0
    p.update_stage('Embedding passages: 32/100 new texts…')
    assert p.bar.maximum() == 100 and p.bar.value() == 32
    p.update_stage('Finding corpus-wide themes…')
    assert p.bar.maximum() == 0
    p.update_stage('Naming theme 2/5…')
    assert p.bar.maximum() == 5 and p.bar.value() == 2
    p.finish(); assert not p.active and not p.timer.isActive() and p.bar.isHidden()
    p.close()


def test_cancel_waits_for_worker_and_failure_does_not_become_success(qt_test_application):
    p = TaskProgress(); p.begin('Analysis'); p.request_cancel()
    p.update_stage('Embedding passages: 1/10')
    assert 'Cancellation requested' in p.stage.text() and p.active
    assert not p.cancel.isEnabled()
    p.terminal('failed', 'Failed · disk full'); p.finish()
    assert p.stage.text() == 'Failed · disk full' and p.outcome == 'failed'
    p.begin('Next run'); assert p.cancel.isEnabled() and not p.cancelling and not p.outcome
    p.close(); p.timer.stop()


def test_global_progress_survives_tab_switch_and_cancel_routes_to_worker(qt_test_application):
    w = MainWindow(); worker = FakeWorker(); w._analysis_worker = worker
    w.track_task(worker)
    worker.progress.emit('Finding corpus-wide themes…')
    for index in range(w.tabs.count()):
        w.tabs.setCurrentIndex(index)
        assert not w.task_progress.isHidden()
        assert w.task_progress.stage.text() == 'Finding corpus-wide themes…'
    w.task_progress.cancel.click()
    assert worker.interrupted and w.task_progress.cancelling
    worker.cancelled.emit(); w.task_progress.finish()
    assert w.task_progress.outcome == 'cancelled'
    assert not w.task_progress.cancel.isEnabled()
    w._analysis_worker = None; w.close()


def test_terminal_failure_and_elapsed_time_are_retained(qt_test_application):
    p = TaskProgress(); p.begin('Download')
    p.clock.start(); p.update_elapsed()
    assert p.elapsed.text().startswith('Elapsed ')
    p.terminal('failed', 'Failed · unavailable'); p.finish()
    p.update_stage('Late progress')
    assert p.stage.text() == 'Failed · unavailable'
    assert not p.timer.isActive()
    p.close()
