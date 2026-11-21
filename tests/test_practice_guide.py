from qualitative_themes.storage.database import connect, create_project
from qualitative_themes.storage.practice import create_practice_project, is_practice_project
from qualitative_themes.storage.coding import create_theme


def test_practice_is_synthetic_and_fresh_copy_preserves_work(monkeypatch):
    monkeypatch.setattr('qualitative_themes.analysis.encoder.load_encoder',
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Practice must not load AI')))
    first = create_practice_project()
    code = create_theme(first.directory, 'My practice code')
    second = create_practice_project()
    assert first.directory != second.directory
    assert is_practice_project(first.directory) and is_practice_project(second.directory)
    with connect(first.directory) as conn:
        assert conn.execute('SELECT user_label FROM themes WHERE id=?', (code,)).fetchone()[0] == 'My practice code'
    with connect(second.directory) as conn:
        docs = conn.execute('SELECT canonical_text, extraction_status FROM documents').fetchall()
        assert len(docs) == 3 and all(status == 'ready' for _, status in docs)
        assert all('SYNTHETIC PRACTICE MATERIAL' in text for text, _ in docs)
        assert not conn.execute('SELECT id FROM analysis_runs').fetchall()
        assert not conn.execute('SELECT id FROM themes WHERE id=?', (code,)).fetchall()


def test_hub_practice_guide_routes_and_reopening(tmp_path, qt_test_application):
    from qualitative_themes.gui.main_window import MainWindow
    window = MainWindow()
    window.hub.guide_button.click()
    assert window.pages.currentWidget() is window.quick_guide
    assert not window.quick_guide.buttons['check'].isEnabled()  # waits for a project
    window.quick_guide.fresh.click()
    first = window.project
    assert window.tabs.currentWidget() is window.project_guide
    assert not window.project_guide.practice_help.isHidden()
    window.project_guide.buttons['check'].click()
    assert window.tabs.currentIndex() == 0
    assert window.document_list.currentRow() == 0
    window.project_guide.buttons['code'].click()
    assert window.tabs.currentWidget() is window.workspace
    assert window.workspace.directory == first.directory
    window.project_guide.fresh.click()
    assert window.project.directory != first.directory
    window.open_recent(str(first.directory))
    assert window.project.directory == first.directory
    assert not window.project_guide.practice_help.isHidden()
    real = create_project(tmp_path / 'real', 'Real study')
    window.open_recent(str(real.directory))
    assert window.project_guide.practice_help.isHidden()
    assert window.project_guide.fresh.text() == 'Open sample interviews'
    window.close()


def test_practice_cannot_switch_during_task_or_failed_comment_save(qt_test_application, monkeypatch):
    from qualitative_themes.gui.main_window import MainWindow
    from PySide6.QtWidgets import QMessageBox
    window = MainWindow()
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: None)
    monkeypatch.setattr('qualitative_themes.storage.practice.create_practice_project',
                        lambda: (_ for _ in ()).throw(AssertionError('Must not create a project')))
    window._analysis_worker = object()
    window.start_practice()
    window._analysis_worker = None
    monkeypatch.setattr(window, 'flush_comments', lambda: False)
    window.start_practice()
    monkeypatch.setattr(window, 'flush_comments', lambda: True)
    window.close()
