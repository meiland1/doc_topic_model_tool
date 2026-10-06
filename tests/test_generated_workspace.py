"""Offscreen Qt checks with fake model output, not a downloaded model."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from qualitative_themes.analysis.topics import AnalysisSettings, TopicOutput, analyze
from qualitative_themes.gui.coding_workspace import CodingWorkspace
from qualitative_themes.storage.coding import assignment_origin, list_notes
from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import import_document
from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
from qualitative_themes.gui.main_window import MainWindow
from qualitative_themes.storage.database import open_project
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QDialogButtonBox


@pytest.fixture
def generated_workspace(tmp_path):
    app = QApplication.instance() or QApplication([])
    project = create_project(tmp_path/"project", "Qt smoke")
    docs = []
    for number in range(2):
        source = tmp_path/f"{number}.txt"
        source.write_text("\n\n".join(f"Café 😀 document {number} passage {i}." for i in range(6)), encoding="utf-8")
        docs.append(import_document(project.directory, source).document_id)
    def fake(passages, *args):
        return TopicOutput([i % 2 - 1 for i in range(len(passages))], {0: [("observations", .5)], -1: []})
    run, _ = analyze(project.directory, "Paragraphs", docs, backend=fake)
    workspace = CodingWorkspace()
    workspace.set_project(project.directory)
    yield app, workspace, docs, project.directory, run
    workspace.close()


def test_generated_highlights_cross_document_and_utf16(generated_workspace):
    app, workspace, docs, directory, run = generated_workspace
    assert workspace.run_id == run
    assert workspace.matches
    workspace.hits.setCurrentRow(len(workspace.matches)-1)
    app.processEvents()
    match = workspace.matches[workspace.match_index]
    assert workspace.doc_id == docs[1] == match[1]
    assert workspace.viewer.textCursor().selectedText() == match[5]
    assert "😀" in match[5]
    assert workspace.viewer.extraSelections()
    assert "Model suggestion" in workspace.range_themes.text()


def test_note_accepts_suggestion_and_removal_stays_removed(generated_workspace):
    app, workspace, docs, directory, run = generated_workspace
    before = len(workspace.matches)
    workspace.note_editor.setPlainText("Research memo on a suggestion")
    workspace.save_new_note()
    coding = workspace.matches[workspace.match_index][0]
    assert assignment_origin(directory, coding) == "researcher"
    assert list_notes(directory, coding_id=coding)[0][1] == "Research memo on a suggestion"
    assert len(workspace.matches) == before
    workspace.remove_selected_coding()
    assert len(workspace.matches) == before-1
    workspace.reload_runs(run)
    assert len(workspace.matches) == before-1


def test_setup_inspects_corpus_and_validates_presets(generated_workspace):
    app, workspace, docs, directory, run = generated_workspace
    dialog = AnalysisSetupDialog(directory, [(doc, f"Document {i}") for i, doc in enumerate(docs)], unit="Paragraphs")
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if dialog._inspection is None else None)
    poll.start(10)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    poll.stop()
    try:
        assert dialog._inspection is None
        assert dialog.summary["count"] == 12
        analyze_button = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert analyze_button.isEnabled()
        dialog.style.setCurrentText("Broad")
        assert analyze_button.isEnabled()
        assert "Small corpus" in dialog.validation.text()
        dialog.style.setCurrentText("Detailed")
        assert dialog.minimum.value() == 3
        assert analyze_button.isEnabled()
        dialog.number_mode.setCurrentIndex(1)
        dialog.number.setValue(50)
        assert not analyze_button.isEnabled()
    finally:
        dialog.reject()


def test_saved_run_naming_controls_are_in_research_workspace(generated_workspace):
    app, workspace, docs, directory, run = generated_workspace
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        assert window.rename_run_button is window.workspace.name_run_button
        assert window.workspace.isAncestorOf(window.rename_run_button)
        assert window.workspace.isAncestorOf(window.refresh_models_button)
        assert window.rename_run_button.isEnabled()
        assert window.workspace.run_id == run
    finally:
        window.close()


def test_run_scope_and_new_empty_theme(generated_workspace, monkeypatch):
    from qualitative_themes.storage.coding import create_theme, add_coding
    from PySide6.QtWidgets import QInputDialog
    app, workspace, docs, directory, _ = generated_workspace
    theme = create_theme(directory, "Earlier manual theme")
    add_coding(directory, docs[0], 0, 5, theme)
    def fake(passages, *args):
        return TopicOutput([0] * len(passages), {0: [("observations", .5)]})
    run, _ = analyze(directory, "Paragraphs", [docs[1]], settings=AnalysisSettings(min_topic_size=2), backend=fake)
    workspace.reload_runs(run)
    assert workspace.document_choice.count() == 1
    assert workspace.doc_id == docs[1]
    assert theme not in {row[0] for row in workspace.visible_themes()}
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("New draft theme", True))
    workspace.new_theme()
    draft = workspace.current_theme_id()
    assert draft in {row[0] for row in workspace.visible_themes()}
    workspace.reload_runs(run)
    assert draft not in {row[0] for row in workspace.visible_themes()}
    workspace.run_choice.setCurrentIndex(workspace.run_choice.findData(None))
    assert workspace.document_choice.count() == 2
    assert {theme, draft} <= {row[0] for row in workspace.visible_themes()}


def test_manual_only_list_excludes_unaccepted_model_themes(generated_workspace):
    from qualitative_themes.storage.coding import create_theme, add_coding
    app, workspace, docs, directory, run = generated_workspace
    manual = create_theme(directory, "Manual only")
    suggestion = workspace.matches[0]
    accepted = workspace.current_theme_id()
    add_coding(directory, suggestion[1], suggestion[3], suggestion[4], accepted)
    workspace.run_choice.setCurrentIndex(workspace.run_choice.findData(None))
    app.processEvents()
    assert {row[0] for row in workspace.visible_themes()} == {manual, accepted}
    workspace.run_choice.setCurrentIndex(workspace.run_choice.findData(run))
    assert len(workspace.visible_themes()) >= 2


def test_unaccepted_generated_theme_can_be_renamed(generated_workspace, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from qualitative_themes.storage.database import connect
    app, workspace, docs, directory, run = generated_workspace
    theme = workspace.current_theme_id()
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Researcher renamed theme", True))
    workspace.rename_selected_theme()
    with connect(directory) as conn:
        assert conn.execute("SELECT user_label FROM themes WHERE id=?", (theme,)).fetchone()[0] == "Researcher renamed theme"


def test_refinement_review_cancel_and_apply(generated_workspace):
    from qualitative_themes.analysis.refinement import propose_refinement, review_proposal, list_proposals
    from qualitative_themes.analysis.topics import list_analysis_runs
    from qualitative_themes.gui.refinement import RefinementReviewDialog
    app, workspace, docs, directory, run = generated_workspace
    def offline():
        raise ValueError("No local service for this test")
    before = [row[0] for row in list_analysis_runs(directory)]
    proposal, _ = propose_refinement(directory, run, "names", client_factory=offline)
    dialog = RefinementReviewDialog(directory, proposal)
    try:
        assert not dialog.buttons.button(dialog.buttons.StandardButton.Apply).isEnabled()
        assert dialog.evidence.toPlainText()
        dialog.reject()
        assert [row[0] for row in list_analysis_runs(directory)] == before
        assert not list_proposals(directory)
    finally:
        dialog.deleteLater()
    proposal, _ = propose_refinement(directory, run, "names", client_factory=offline)
    dialog = RefinementReviewDialog(directory, proposal)
    try:
        dialog.confirm.setChecked(True)
        assert dialog.buttons.button(dialog.buttons.StandardButton.Apply).isEnabled()
        dialog.apply()
        assert dialog.applied
        workspace.reload_runs(proposal)
        assert workspace.run_id == proposal
        assert workspace.matches
    finally:
        dialog.deleteLater()


def test_main_window_refinement_cancel_keeps_selected_run(generated_workspace, monkeypatch):
    from qualitative_themes.analysis.refinement import propose_refinement, list_proposals
    from qualitative_themes.gui.refinement import RefinementReviewDialog
    from PySide6.QtWidgets import QDialog
    app, workspace, docs, directory, run = generated_workspace
    def offline():
        raise ValueError("No local service")
    proposal, _ = propose_refinement(directory, run, "names", client_factory=offline)
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        assert window.workspace.refine_button.isEnabled()
        assert window.workspace.review_proposal_button.isEnabled()
        def cancel(dialog):
            dialog.reject()
            return QDialog.DialogCode.Rejected
        monkeypatch.setattr(RefinementReviewDialog, "exec", cancel)
        window.refinement_ready(proposal)
        assert window.workspace.run_id == run
        assert not list_proposals(directory)
    finally:
        window.close()


def _run_passage_preview(window, all_documents=False):
    """Wait for the real segmentation worker with a bounded offscreen event loop."""
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    window.preview_passages(all_documents=all_documents)
    assert window._segment_worker is not None
    window._segment_worker.finished.connect(loop.quit)
    timer.start(5000)
    loop.exec()
    timer.stop()
    assert window._segment_worker is None, "Preview worker did not finish in time"


def test_preview_uses_current_selected_document_and_clears_stale_passages(generated_workspace):
    from PySide6.QtCore import QItemSelectionModel, Qt
    app, workspace, docs, directory, _ = generated_workspace
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        window.unit_selector.setCurrentText("Paragraphs")
        for row in [1, 0, 1]:
            window.document_list.setCurrentRow(row, QItemSelectionModel.SelectionFlag.ClearAndSelect)
            assert not window.segment_list.count()
            assert not window.segment_excerpt.toPlainText()
            _run_passage_preview(window)
            assert window.segment_list.count() == 6
            assert {window.segment_list.item(i).data(Qt.ItemDataRole.UserRole)[0]
                    for i in range(window.segment_list.count())} == {docs[row]}
            assert f"document {row}" in window.segment_excerpt.toPlainText()
            assert window.document_list.currentRow() == row
            assert window.document_list.isEnabled() and window.unit_selector.isEnabled()
    finally:
        window.close()


def test_preview_multi_selection_keeps_selection_and_current_document(generated_workspace):
    from PySide6.QtCore import QItemSelectionModel, Qt
    app, workspace, docs, directory, _ = generated_workspace
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        window.unit_selector.setCurrentText("Paragraphs")
        for row in range(window.document_list.count()):
            window.document_list.item(row).setSelected(True)
        window.document_list.setCurrentItem(window.document_list.item(1), QItemSelectionModel.SelectionFlag.NoUpdate)
        _run_passage_preview(window)
        assert window.segment_list.count() == 12
        assert "document 1" in window.segment_excerpt.toPlainText()
        window.segment_list.setCurrentRow(0)
        assert "document 0" in window.segment_excerpt.toPlainText()
        assert {item.data(Qt.ItemDataRole.UserRole) for item in window.document_list.selectedItems()} == set(docs)
        assert window.segment_list.count() == 12
    finally:
        window.close()


def test_preview_without_selected_documents_does_not_start_worker(generated_workspace):
    app, workspace, docs, directory, _ = generated_workspace
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        window.document_list.clearSelection()
        window.preview_passages()
        assert window._segment_worker is None
        assert "Select documents to preview" in window.import_status.text()
    finally:
        window.close()


def test_preview_all_includes_unselected_documents_and_excludes_removed(generated_workspace):
    from PySide6.QtCore import QItemSelectionModel, Qt
    from qualitative_themes.storage.documents import remove_documents
    app, workspace, docs, directory, _ = generated_workspace
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        window.unit_selector.setCurrentText("Paragraphs")
        window.document_list.setCurrentRow(1, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        _run_passage_preview(window, all_documents=True)
        assert window.segment_list.count() == 12
        assert "document 1" in window.segment_excerpt.toPlainText()
        assert len(window.document_list.selectedItems()) == 1
        window.document_list.clearSelection()
        _run_passage_preview(window, all_documents=True)
        assert window.segment_list.count() == 12
        remove_documents(directory, [docs[0]])
        window.refresh_documents(docs[1])
        _run_passage_preview(window, all_documents=True)
        assert window.segment_list.count() == 6
        assert {window.segment_list.item(i).data(Qt.ItemDataRole.UserRole)[0]
                for i in range(window.segment_list.count())} == {docs[1]}
    finally:
        window.close()


def test_remove_restore_controls_and_saved_workspace_evidence(generated_workspace, monkeypatch):
    from PySide6.QtCore import QItemSelectionModel
    from PySide6.QtWidgets import QDialog, QListWidget, QMessageBox
    from qualitative_themes.storage.documents import list_documents
    app, workspace, docs, directory, run = generated_workspace
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        window.document_list.setCurrentRow(0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.No)
        window.remove_selected_documents()
        assert window.document_list.count() == 2
        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
        window.remove_selected_documents()
        assert window.document_list.count() == 1
        assert len(list_documents(directory)) == 1
        assert window.workspace.run_id == run
        assert window.workspace.document_choice.findData(docs[0]) >= 0
        assert "[removed]" in window.workspace.document_choice.itemText(window.workspace.document_choice.findData(docs[0]))
        assert window.restore_documents_button.isEnabled()
        def restore(dialog):
            choices = dialog.findChild(QListWidget)
            choices.setCurrentRow(0)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QDialog, "exec", restore)
        window.restore_removed_documents()
        assert window.document_list.count() == 2
        assert not window.restore_documents_button.isEnabled()
        assert window.workspace.matches
    finally:
        window.close()


def test_removing_all_documents_leaves_import_and_restore_available(generated_workspace, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    app, workspace, docs, directory, run = generated_workspace
    window = MainWindow()
    try:
        window._show_project(open_project(directory))
        for row in range(window.document_list.count()):
            window.document_list.item(row).setSelected(True)
        monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
        window.remove_selected_documents()
        assert not window.document_list.count()
        assert not window.preview_all_button.isEnabled()
        assert not window.remove_documents_button.isEnabled()
        assert window.import_button.isEnabled()
        assert window.restore_documents_button.isEnabled()
        assert window.workspace.run_id == run
        assert window.workspace.matches
    finally:
        window.close()


def test_five_passage_docx_enables_analyze_with_small_corpus_warning(tmp_path):
    from docx import Document
    app = QApplication.instance() or QApplication([])
    directory = create_project(tmp_path / "project", "Small DOCX").directory
    path = tmp_path / "reviews.docx"
    document = Document()
    for number in range(5):
        document.add_paragraph(f"Review {number}. " + "Research evidence describes application usage experiences. " * 17)
    document.save(path)
    doc = import_document(directory, path).document_id
    dialog = AnalysisSetupDialog(directory, [(doc, path.name)],
        AnalysisSettings(min_topic_size=2, name_themes=True, ollama_model="llama3.1"),
        "Short passages", ["llama3.1"])
    loop = QEventLoop()
    timer = QTimer()
    timer.timeout.connect(lambda: loop.quit() if dialog._inspection is None else None)
    timer.start(10)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    timeout.start(5000)
    loop.exec()
    timer.stop()
    timeout.stop()
    try:
        assert dialog._inspection is None
        assert dialog.summary["count"] == 5
        assert dialog.style.currentText() == "Balanced"
        assert dialog.minimum.value() == 2
        assert dialog.auto_names.isChecked()
        assert dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        assert "Small corpus" in dialog.validation.text()
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_analysis_title_rename_reopen_and_history(generated_workspace, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from qualitative_themes.gui.history import AnalysisHistoryDialog
    app, workspace, docs, directory, run = generated_workspace
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs: ('Reviews — café 😀', True))
    workspace.rename_selected_analysis()
    app.processEvents()
    assert workspace.run_id == run
    assert workspace.run_choice.currentText() == 'Reviews — café 😀'
    reopened = CodingWorkspace()
    reopened.set_project(directory)
    assert reopened.run_choice.currentText() == 'Reviews — café 😀'
    history = AnalysisHistoryDialog(directory)
    assert 'Reviews — café 😀' in history.widgets['runs']['details'].toPlainText()
    assert 'Passages: 12' in history.widgets['runs']['details'].toPlainText()
    assert history.widgets['events']['rows'].count() > 0
    history.close()
    reopened.close()


def test_coded_passage_export_worker_preserves_selected_run(generated_workspace, tmp_path, monkeypatch):
    import csv
    from PySide6.QtWidgets import QFileDialog
    app, workspace, docs, directory, run = generated_workspace
    window = MainWindow()
    window._show_project(open_project(directory))
    destination = tmp_path/'passages.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *args, **kwargs: (str(destination), 'Coded passages CSV (*.csv)'))
    window.start_research_export('passages')
    worker = window._analysis_worker
    assert worker is not None
    assert not window.workspace.export_passages_button.isEnabled()
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    app.processEvents()
    assert window._analysis_worker is None
    assert window.workspace.run_id == run
    assert window.workspace.export_passages_button.isEnabled()
    with destination.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12
    assert all(row['analysis_id'] == run for row in rows)
    window.close()


def test_selected_theme_keywords_match_export_and_clear_for_manual_theme(generated_workspace):
    from qualitative_themes.export.research import build_export
    from qualitative_themes.storage.coding import create_theme
    from PySide6.QtCore import Qt
    app, workspace, docs, directory, run = generated_workspace
    entries = build_export(directory, run)['themes']
    entry = next(row for row in entries if row['keywords'])
    for i in range(workspace.themes.count()):
        item = workspace.themes.item(i)
        if item.data(Qt.ItemDataRole.UserRole) == entry['theme_id']:
            workspace.themes.setCurrentItem(item)
            break
    app.processEvents()
    from qualitative_themes.storage.theme_keywords import theme_keyword_scores
    scores = theme_keyword_scores(directory, entry['theme_id'], run)
    assert workspace.theme_keywords_label.text() == 'Keywords: ' + ', '.join(f'{word} ({score:.2%})' for word, score in scores)
    manual = create_theme(directory, 'No model keywords')
    workspace._draft_theme_ids.add(manual)
    workspace.reload_themes(manual)
    assert workspace.theme_keywords_label.text() == 'Keywords: No model keywords saved for this theme.'
    workspace.themes.clear()
    assert workspace.theme_keywords_label.text() == 'Keywords: Select a theme.'


def test_visualization_tab_worker_and_evidence_navigation(generated_workspace, monkeypatch):
    from qualitative_themes.analysis.visualizations import visualization_snapshot
    from qualitative_themes.gui.visualizations import VisualizationTab
    import qualitative_themes.gui.visualizations as visual_gui
    from PySide6.QtWidgets import QWidget
    app, workspace, docs, directory, run = generated_workspace
    class Viewer(QWidget):
        def load(self, url): self.url = url
        def setUrl(self, url): self.url = url
        def stop(self): pass
        def page(self): return self
    def viewer(tab, key):
        if key not in tab.viewers:
            tab.viewers[key] = Viewer(tab)
        return tab.viewers[key]
    monkeypatch.setattr(VisualizationTab, '_viewer', viewer)
    def fake(directory, run, *args):
        return {'snapshot': visualization_snapshot(directory, run), 'html': {'map':'<html>Map</html>',
            'heatmap':'<html>Heatmap</html>', 'hierarchy':'<html>Hierarchy</html>'}, 'messages':{}, 'tree':'Readable topic tree'}
    monkeypatch.setattr(visual_gui, 'build_visualizations', fake)
    window = MainWindow()
    window._show_project(open_project(directory))
    window.tabs.setCurrentWidget(window.visualizations)
    assert window.visualizations.plots.count() == 4
    assert window.visualizations.run_choice.currentData() == run
    window.visualizations.build_button.click()
    worker = window._analysis_worker
    assert worker is not None
    assert not window.visualizations.run_choice.isEnabled()
    loop = QEventLoop(); worker.finished.connect(loop.quit)
    QTimer.singleShot(10000, loop.quit); loop.exec(); app.processEvents()
    assert window._analysis_worker is None
    assert window.visualizations.tree.toPlainText() == 'Readable topic tree'
    assert window.visualizations.themes.count() > 0
    from PySide6.QtCore import Qt
    selected = window.visualizations.themes.currentItem().data(Qt.ItemDataRole.UserRole)
    window.visualizations.evidence_button.click()
    assert window.tabs.currentWidget() == window.workspace
    assert window.workspace.run_id == run
    assert window.workspace.current_theme_id() == selected
    window.close()


def test_visualization_tab_without_analyses_is_disabled(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    project = create_project(tmp_path/'empty', 'Empty visualizations')
    window._show_project(project)
    assert not window.visualizations.build_button.isEnabled()
    assert not window.visualizations.evidence_button.isEnabled()
    window.close()
