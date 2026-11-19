from types import SimpleNamespace

import pytest

from qualitative_themes.analysis.topics import AnalysisCancelled
from qualitative_themes.gui.main_window import AnalysisWorker, MainWindow


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancel'])
def test_saved_result_delivered_before_naming(monkeypatch, tmp_path, qt_test_application, outcome):
    events = []
    monkeypatch.setattr('qualitative_themes.gui.main_window.analyze', lambda *a, **kw: ('saved-run', 12))
    def naming(*a, **kw):
        assert events == [('saved-run', 12)]
        if outcome == 'failure':
            raise RuntimeError('naming unavailable')
        if outcome == 'cancel':
            raise AnalysisCancelled()
    monkeypatch.setattr('qualitative_themes.gui.main_window.label_run', naming)
    worker = AnalysisWorker(tmp_path, 'Paragraphs', [], name_themes=True)
    worker.result.connect(lambda run, count: events.append((run, count)))
    worker.naming_complete.connect(lambda run: events.append(('named', run)))
    worker.cancelled.connect(lambda: events.append(('cancelled',)))
    worker.run()
    assert events[0] == ('saved-run', 12)
    assert len(events) == (1 if outcome == 'failure' else 2)
    if outcome == 'success':
        assert events[-1] == ('named', 'saved-run')


def test_late_naming_ignored_for_other_project_worker_or_run(tmp_path, qt_test_application, monkeypatch):
    window = MainWindow()
    worker = SimpleNamespace(directory=tmp_path / 'original')
    window._analysis_worker = worker
    window.project = SimpleNamespace(directory=tmp_path / 'other')
    window.workspace.run_id = 'run'
    monkeypatch.setattr(window.workspace, 'refresh_theme_labels', lambda: pytest.fail('Stale update'))
    window.naming_ready(worker, 'run')
    window.project.directory = worker.directory
    window.naming_ready(worker, 'different-run')
    window.naming_ready(SimpleNamespace(directory=worker.directory), 'run')
    window._analysis_worker = None
    window.project = None
    window.close()


def test_gui_receives_saved_result_while_naming_is_blocked(tmp_path, qt_test_application, monkeypatch):
    from threading import Event
    from PySide6.QtCore import QEventLoop, QTimer
    release, started = Event(), Event()
    monkeypatch.setattr('qualitative_themes.gui.main_window.analyze', lambda *a, **kw: ('saved', 3))
    def naming(*a, **kw):
        started.set()
        assert release.wait(10)
    monkeypatch.setattr('qualitative_themes.gui.main_window.label_run', naming)
    worker = AnalysisWorker(tmp_path, 'Paragraphs', [], name_themes=True)
    received = []
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    worker.result.connect(lambda run, count: (received.append((run, count)), loop.quit()))
    worker.start()
    try:
        timer.start(5000)
        loop.exec()
        assert received == [('saved', 3)] and worker.isRunning()
    finally:
        release.set()
        worker.wait(10000)
        timer.stop()
    assert started.is_set()
