"""Background tasks: analysis, naming, refinement, charts and exports.

One task runs at a time (MainWindow.busy). Each start_* method checks that,
starts a worker, and the matching *_ready/*_finished slot updates the tabs.

Part of MainWindow (see main_window.py); not meant to be used on its own.
"""
import traceback
from pathlib import Path
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QMessageBox, QInputDialog)

from qualitative_themes.storage.documents import (get_document, list_documents)
from qualitative_themes.storage.history import segment_count
from qualitative_themes.analysis.labels import labeling_report, AUTO_NAMING_LIMIT
from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
from qualitative_themes.analysis.refinement import load_source, list_proposals, cancel_proposal
from qualitative_themes.gui.visualizations import VisualizationWorker
from qualitative_themes.gui.history import AnalysisHistoryDialog, ResearchExportWorker
from qualitative_themes.gui.refinement import RefinementSetupDialog, RefinementReviewDialog, RefinementWorker
import json
from qualitative_themes.storage.preferences import save_setup, result_summary


from qualitative_themes.gui.workers import AnalysisWorker


class TasksMixin:
    def name_selected_run(self):
        if self.workspace.run_id:
            count, _ = result_summary(self.project.directory, self.workspace.run_id)
            if count > AUTO_NAMING_LIMIT and QMessageBox.question(
                    self, 'Many themes to name',
                    f'This analysis has {count} themes. Naming them may take a long time. '
                    'You can keep keyword names, or combine similar themes first. Name all of them?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.start_refinement(operation="names")

    def refresh_visualization_runs(self, _index=None):
        if self.tabs.currentWidget() == self.visualizations and self.project and not self._analysis_worker:
            self.visualizations.reload_runs(self.workspace.run_id)

    def start_visualizations(self):
        if not self.project or self.busy:
            return
        selected = self.visualizations.run_choice.currentData()
        if not selected:
            return
        self.visualizations.invalidate()
        worker = VisualizationWorker(self.project.directory, selected, self)
        self._analysis_worker = worker
        worker.progress.connect(self.visualizations.status.setText)
        worker.result.connect(self.visualizations.display_result)
        worker.failed.connect(lambda message: self.visualizations.status.setText("Couldn't make the charts: " + message))
        worker.cancelled.connect(lambda: self.visualizations.status.setText("Charts cancelled. Nothing was changed."))
        worker.finished.connect(self.analysis_finished)
        for button in [self.import_button, self.analyze_button]:
            button.setEnabled(False)
        self.update_naming_controls()
        self.visualizations.cancel_button.setEnabled(True)
        self.cancel_analysis_button.setEnabled(True)
        self.track_task(worker)
        worker.start()

    def open_visualization_evidence(self, run_id, theme_id):
        if self.busy:
            return
        self.workspace.reload_runs(run_id)
        self.workspace.reload_themes(theme_id)
        self.tabs.setCurrentWidget(self.workspace)
        self.update_naming_controls()

    def show_analysis_history(self):
        if not self.project or self.busy:
            return
        dialog = AnalysisHistoryDialog(self.project.directory, self)
        dialog.exec()
        dialog.deleteLater()

    def start_research_export(self, kind):
        if not self.project or self.busy:
            return
        filters = "Codebook CSV (*.csv);;Codebook JSON (*.json)" if kind == "codebook" else "Coded passages CSV (*.csv)"
        title = "Export codebook" if kind == "codebook" else "Export coded passages"
        if not self.workspace.directory:
            self.workspace.set_project(self.project.directory)
        path, selected_filter = QFileDialog.getSaveFileName(self, title, f"{kind}.csv", filters)
        if not path:
            return
        suffix = ".json" if "JSON" in selected_filter else ".csv"
        destination = Path(path)
        if destination.suffix.lower() not in ({".csv", ".json"} if kind == "codebook" else {".csv"}):
            destination = destination.with_suffix(suffix)
            if destination.exists() and QMessageBox.question(
                    self, "Replace export?", f"Replace the existing file {destination.name}?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        self._analysis_worker = ResearchExportWorker(self.project.directory, self.workspace.run_id,
                                                     kind, destination, self)
        worker = self._analysis_worker
        worker.progress.connect(self.task_status)
        worker.failed.connect(lambda message: self.task_status("Export failed: " + message))
        worker.cancelled.connect(lambda: self.task_status("Export cancelled. Existing export files and research are unchanged."))
        worker.result.connect(lambda filename, count: self.task_status(
            f"Exported {count} {'themes' if kind == 'codebook' else 'unique passage ranges'} to {filename}"))
        worker.finished.connect(self.analysis_finished)
        for button in [self.import_button, self.analyze_button]:
            button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)
        self.workspace.cancel_task_button.setEnabled(True)
        self.update_naming_controls()
        self.task_status("Gathering your coding for the export…")
        self.track_task(worker)
        worker.start()

    def start_refinement(self, operation=None):
        if not self.project or not self.workspace.run_id or self.busy:
            return
        try:
            source = load_source(self.project.directory, self.workspace.run_id)
            models = [self.ollama_model_choice.itemData(i) for i in range(self.ollama_model_choice.count())]
            source["settings"] = replace(source["settings"], ollama_model=self.ollama_model_choice.currentData() or source["settings"].ollama_model)
            setup = RefinementSetupDialog(source, models, self, operation)
            if setup.exec() != QDialog.DialogCode.Accepted:
                setup.deleteLater()
                return
            operation = setup.operation.currentData()
            settings = setup.settings()
            unit = source["unit"]
            setup.deleteLater()
            if operation == "discover":
                available = {doc for doc, _, status in list_documents(self.project.directory) if status == "ready"}
                if not set(source["documents"]).issubset(available):
                    raise ValueError("This analysis includes documents you've removed. Add them again first, "
                                     "or find themes in a new selection of documents.")
                settings = replace(settings, requested_theme_count=None)
                chosen = [(doc, get_document(self.project.directory, doc)[0]) for doc in source["documents"]]
                dialog = AnalysisSetupDialog(self.project.directory, chosen, settings, unit, [m for m in models if m], self)
                dialog.setWindowTitle("Find themes again with new settings")
                dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Prepare changes")
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    dialog.deleteLater()
                    return
                settings, unit = dialog.settings(), dialog.unit.currentText()
                dialog.deleteLater()
            self._analysis_worker = RefinementWorker(self.project.directory, self.workspace.run_id,
                                                     operation, settings, unit, self)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.warning(self, "Could not prepare the changes", str(exc))
            return
        worker = self._analysis_worker
        worker.progress.connect(self.task_status)
        worker.failed.connect(lambda message: self.task_status("Couldn't adjust the themes: " + message))
        worker.cancelled.connect(lambda: self.task_status("Cancelled. Your analysis and coding are unchanged."))
        worker.result.connect(self.refinement_ready)
        worker.finished.connect(self.analysis_finished)
        for button in [self.import_button, self.analyze_button]:
            button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)
        self.workspace.cancel_task_button.setEnabled(True)
        self.update_naming_controls()
        self.task_status("Preparing the changes for you to review…")
        self.track_task(worker)
        worker.start()

    def refinement_ready(self, proposal_id, _count=0):
        if self._close_after_analysis or (self._analysis_worker and self._analysis_worker.isInterruptionRequested()):
            cancel_proposal(self.project.directory, proposal_id)
            return
        self.tabs.setCurrentWidget(self.workspace)
        try:
            dialog = RefinementReviewDialog(self.project.directory, proposal_id, self)
            dialog.exec()
            applied = dialog.applied
            dialog.deleteLater()
            if applied:
                self.analysis_ready(proposal_id, segment_count(self.project.directory, proposal_id))
                self.task_status("Changes applied. Your earlier analyses and coding are kept.")
            else:
                self.task_status("Cancelled. Nothing was changed.")
        except Exception as exc:
            traceback.print_exc()
            self.task_status("The adjusted themes are still saved for review: " + str(exc))
        self.update_naming_controls()

    def review_pending_refinement(self):
        if not self.project or self.busy:
            return
        proposals = list_proposals(self.project.directory)
        if not proposals:
            return
        from qualitative_themes.analysis.refinement import OPERATIONS
        displays = [f"{i+1}. {started[:19]} · {OPERATIONS[json.loads(info)['refinement_operation']][0]}"
                    for i, (_, started, info) in enumerate(proposals)]
        choice, ok = QInputDialog.getItem(self, "Review adjusted themes", "Choose the changes to review", displays, 0, False)
        if ok:
            self.refinement_ready(proposals[displays.index(choice)][0])

    def start_analysis(self, _checked=False, run_id=None):
        """Open the setup dialog and start an AnalysisWorker (or name an existing run if run_id is set).

        Pre: a project is open, nothing else is running, and documents are selected
        (not needed for naming).
        Post: the setup is saved to the project before the worker starts, so it's
        remembered even if the analysis fails.
        """
        if not self.project or self.busy:
            return
        documents = [item.data(Qt.ItemDataRole.UserRole) for item in self.checked_document_items()]
        if not run_id and not documents:
            self.import_status.setText("Select the documents to include, then choose Find themes.")
            return
        settings = self.analysis_settings
        if not run_id:
            chosen = [(item.data(Qt.ItemDataRole.UserRole), item.text()) for item in self.checked_document_items()]
            models = [self.ollama_model_choice.itemData(i) for i in range(self.ollama_model_choice.count())]
            initial = replace(settings, ollama_model=self.ollama_model_choice.currentData() or settings.ollama_model)
            dialog = AnalysisSetupDialog(self.project.directory, chosen, initial,
                                         self.analysis_unit, models, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                dialog.deleteLater()
                return
            settings = dialog.settings()
            self.analysis_unit = dialog.unit.currentText()
            dialog.deleteLater()
            try:
                save_setup(self.project.directory, settings, self.analysis_unit)
            except Exception as exc:
                traceback.print_exc()
                QMessageBox.warning(self, "Could not save setup", str(exc))
                return
            self.analysis_settings = settings
            if settings.ollama_model and self.ollama_model_choice.findData(settings.ollama_model) < 0:
                self.ollama_model_choice.addItem(settings.ollama_model, settings.ollama_model)
            self.ollama_model_choice.setCurrentIndex(self.ollama_model_choice.findData(settings.ollama_model))
        self._analysis_worker = AnalysisWorker(self.project.directory, self.analysis_unit, documents,
                                               parent=self,
                                               name_themes=settings.name_themes,
                                               ollama_model=(self.ollama_model_choice.currentData() if run_id else settings.ollama_model),
                                               run_id=run_id, settings=settings)
        self.update_naming_controls()
        worker = self._analysis_worker
        worker.progress.connect(self.task_status)
        worker.failed.connect(lambda message: self.task_status("Analysis/setup failed: " + message))
        worker.cancelled.connect(lambda: self.task_status("Cancelled. Completed analysis and researcher work remain saved."))
        worker.result.connect(lambda run, count: self.analysis_ready(run, count)
                              if self.worker_matches_project(worker) else None)
        worker.naming_complete.connect(lambda run: self.naming_ready(worker, run))
        worker.finished.connect(self.analysis_finished)
        for button in [self.import_button, self.analyze_button]:
            button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)
        self.workspace.cancel_task_button.setEnabled(True)
        self.task_status("Starting…")
        self.track_task(worker)
        worker.start()

    def track_task(self, worker):
        """Wire a worker's progress/failed/cancelled signals into the shared progress bar."""
        titles = {"VisualizationWorker": "Preparing visualizations", "ResearchExportWorker": "Exporting research",
                  "RefinementWorker": "Preparing refinement"}
        title = titles.get(type(worker).__name__, "Analyzing documents")
        if isinstance(worker, AnalysisWorker):
            title = "Naming themes" if worker.run_id else title
        self.task_progress.begin(title)
        worker.progress.connect(self.task_progress.update_stage)
        worker.failed.connect(lambda message: self.task_progress.terminal("failed", "Failed · " + message))
        worker.cancelled.connect(lambda: self.task_progress.terminal("cancelled", "Cancelled · Saved analyses and your coding are unchanged."))

    def cancel_analysis(self):
        """Ask the current worker to stop. It stops at its next check_cancel(), not immediately."""
        if self._analysis_worker:
            self._analysis_worker.requestInterruption()
            self.task_progress.request_cancel()
            self.visualizations.cancel_button.setEnabled(False)
            self.cancel_analysis_button.setEnabled(False)
            self.workspace.cancel_task_button.setEnabled(False)
            self.task_status("Cancellation requested; waiting for the current computation or request…")

    def task_status(self, message):
        self.task_progress.update_stage(message)
        self.import_status.setText(message)
        self.workspace.status.setText(message)

    def analysis_ready(self, run_id, count):
        self.workspace.reload_runs(run_id)
        self.overview.set_run(self.project.directory, run_id)
        self.tabs.setCurrentWidget(self.overview)
        themes, uncategorized = result_summary(self.project.directory, run_id)
        self.task_status(f"Themes found: {themes} · {count} passages · {uncategorized} not in any theme.")
        self.workspace.naming_status.setPlainText(labeling_report(self.project.directory, run_id))

    def worker_matches_project(self, worker):
        """Guard against results from a worker that started on a different project."""
        return (worker is self._analysis_worker and self.project is not None
                and Path(worker.directory).resolve() == self.project.directory.resolve())

    def naming_ready(self, worker, run_id):
        if not self.worker_matches_project(worker) or self.workspace.run_id != run_id:
            return
        self.workspace.refresh_theme_labels()
        self.workspace.naming_status.setPlainText(labeling_report(self.project.directory, run_id))
        self.overview.set_run(self.project.directory, run_id)
        self.task_status('Theme naming finished. Your reading position and researcher edits are preserved.')

    def analysis_finished(self):
        """Clean up after any worker in the _analysis_worker slot (success, failure or cancel)."""
        self.task_progress.finish()
        worker = self._analysis_worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self._analysis_worker = None
        self.update_naming_controls()
        self.import_button.setEnabled(self.project is not None)
        self.update_document_actions()
        self.cancel_analysis_button.setEnabled(False)
        self.workspace.cancel_task_button.setEnabled(False)
        if self._close_after_analysis:
            self.close()
