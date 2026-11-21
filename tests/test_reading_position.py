from PySide6.QtGui import QTextCursor

from qualitative_themes.storage.database import create_project, connect
from qualitative_themes.storage.documents import import_document, remove_documents
from qualitative_themes.storage.preferences import load_workspace_view, save_workspace_view
from qualitative_themes.gui.main_window import MainWindow


def project_with_text(tmp_path):
    project = create_project(tmp_path / 'study', 'Reading')
    source = tmp_path / 'text.txt'
    source.write_text('First line.\nCafé 😀 and a second paragraph.\nLast line.', encoding='utf-8')
    doc = import_document(project.directory, source).document_id
    return project, doc


def test_close_reopen_restores_unicode_selection_and_tab(tmp_path, qt_test_application):
    project, doc = project_with_text(tmp_path)
    from qualitative_themes.storage.coding import create_theme, add_coding, rename_theme
    theme = create_theme(project.directory, 'Initial label')
    add_coding(project.directory, doc, 0, 10, theme)
    window = MainWindow()
    window._show_project(project)
    window.tabs.setCurrentWidget(window.workspace)
    window.workspace.document_choice.setCurrentIndex(window.workspace.document_choice.findData(doc))
    window.workspace.view_mode.setCurrentIndex(0)
    cursor = window.workspace.viewer.textCursor()
    start, end = window.workspace.coords.qt_range(12, 18)
    cursor.setPosition(start)
    cursor.setPosition(end, QTextCursor.KeepAnchor)
    window.workspace.viewer.setTextCursor(cursor)
    selected = cursor.selectedText()
    window.close()
    rename_theme(project.directory, theme, 'Renamed code')
    reopened = MainWindow()
    reopened._show_project(project)
    assert reopened.tabs.currentWidget() is reopened.workspace
    assert reopened.workspace.doc_id == doc
    assert reopened.workspace.current_theme_id() == theme
    assert 'Renamed code' in reopened.workspace.themes.currentItem().text()
    assert reopened.workspace.viewer.textCursor().selectedText() == selected
    reopened.close()
    with connect(project.directory) as conn:
        assert conn.execute("SELECT count(*) FROM history_events WHERE event_type='workspace_view_saved'").fetchone()[0] == 1


def test_switch_preserves_each_project_and_missing_document_falls_back(tmp_path, qt_test_application):
    first, doc = project_with_text(tmp_path)
    second = create_project(tmp_path / 'other', 'Other')
    window = MainWindow()
    window._show_project(first)
    window.tabs.setCurrentWidget(window.workspace)
    window._show_project(second)
    assert load_workspace_view(first.directory)['document_id'] == doc
    window._show_project(first)
    assert window.workspace.doc_id == doc
    window._show_project(second)
    remove_documents(first.directory, [doc])
    window._show_project(first)
    # Removed documents remain readable as evidence in the existing workspace.
    assert window.workspace.doc_id == doc
    assert '[removed]' in window.workspace.document_choice.currentText()
    window.close()


def test_invalid_and_old_view_preferences_are_ignored(tmp_path, qt_test_application):
    project, doc = project_with_text(tmp_path)
    save_workspace_view(project.directory, {'version': 99, 'position': 'bad'})
    assert load_workspace_view(project.directory) == {}
    save_workspace_view(project.directory, {'version': 1, 'run_id': 'missing', 'theme_id': 'removed',
                                          'document_id': doc, 'position': 999999, 'anchor': [],
                                          'view_mode': 'bad', 'organization': -1, 'tab': 'Gone'})
    window = MainWindow()
    window._show_project(project)
    assert window.workspace.doc_id == doc
    assert window.workspace.run_id is None
    window.workspace.restore_view({'version': 1, 'document_id': 'no-longer-present', 'position': 12, 'anchor': 0})
    assert window.workspace.doc_id == doc
    window.close()


def test_hub_separates_optional_setup(monkeypatch, qt_test_application):
    from qualitative_themes.gui.hub import Hub
    from qualitative_themes.runtime.health import Check
    monkeypatch.setattr('qualitative_themes.runtime.health.run_checks', lambda: [
        Check('embedding', 'Topics', True, ''), Check('naming', 'Naming', False, '', required=False)])
    hub = Hub()
    hub.refresh_setup()
    assert hub.setup.isHidden()
    assert 'Ready to find themes' in hub.readiness.text()
    assert "naming isn't set up" in hub.readiness.text()
    hub.advanced_toggle.setChecked(True)
    assert not hub.setup.isHidden()
    hub.close()


def test_reading_preferences_do_not_change_research_history(tmp_path):
    from qualitative_themes.storage.history import history_events
    from qualitative_themes.analysis.view_cache import coding_fingerprint
    project, _ = project_with_text(tmp_path)
    before = history_events(project.directory)
    fingerprint = coding_fingerprint(project.directory, 'test-run')
    save_workspace_view(project.directory, {'version': 1, 'position': 12, 'anchor': 12})
    assert history_events(project.directory) == before
    assert coding_fingerprint(project.directory, 'test-run') == fingerprint
