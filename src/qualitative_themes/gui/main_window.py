"""Project shell and import/segmentation view."""
from pathlib import Path
from dataclasses import replace

from PySide6.QtCore import Qt, QThread, Signal, QItemSelectionModel
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMainWindow, QMessageBox, QInputDialog,
                               QPlainTextEdit, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from qualitative_themes.domain.models import Project
from qualitative_themes.storage.database import create_project, open_project
from qualitative_themes.storage.documents import (ImportOutcome, get_document,
                                                   import_document, list_documents, remove_documents, restore_documents)
from qualitative_themes.analysis.segment import UNITS
from qualitative_themes.storage.segmentation import (SegmentationCancelled,
                                                      create_segmentation_run,
                                                      list_segments)
from qualitative_themes.gui.coding_workspace import CodingWorkspace
from qualitative_themes.analysis.topics import (AnalysisCancelled, analyze,
                                                download_embedding_model)
from qualitative_themes.analysis.labels import label_run, list_local_models, labeling_report
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.gui.analysis_setup import AnalysisSetupDialog
from qualitative_themes.analysis.refinement import load_source, list_proposals, cancel_proposal
from qualitative_themes.gui.visualizations import VisualizationTab, VisualizationWorker
from qualitative_themes.gui.history import AnalysisHistoryDialog, ResearchExportWorker
from qualitative_themes.gui.refinement import RefinementSetupDialog, RefinementReviewDialog, RefinementWorker
import json
from qualitative_themes.storage.preferences import load_setup, save_setup, result_summary


class OllamaModelsWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            self.result.emit(list_local_models())
        except Exception as exc:
            self.failed.emit(str(exc))


class AnalysisWorker(QThread):
    progress = Signal(str)
    result = Signal(str, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, directory, unit, document_ids, download=False, parent=None,
                 name_themes=False, ollama_model=None, run_id=None, settings=None):
        super().__init__(parent)
        self.directory, self.unit, self.document_ids = directory, unit, document_ids
        self.download = download
        self.name_themes, self.ollama_model, self.run_id = name_themes, ollama_model, run_id
        self.settings = settings or AnalysisSettings()

    def run(self):
        try:
            if self.download:
                self.progress.emit("Downloading model weights for offline use…")
                download_embedding_model(self.directory, self.settings)
                if self.isInterruptionRequested():
                    raise AnalysisCancelled()
                self.progress.emit("Embedding model is ready. Select documents and click Analyze.")
            elif self.run_id:
                label_run(self.directory, self.run_id, self.ollama_model,
                          progress=self.progress.emit, should_cancel=self.isInterruptionRequested)
                self.result.emit(self.run_id, len(list_segments(self.directory, self.run_id)))
            else:
                run_id, count = analyze(self.directory, self.unit, self.document_ids,
                    settings=self.settings, progress=self.progress.emit, should_cancel=self.isInterruptionRequested)
                if self.name_themes:
                    try:
                        label_run(self.directory, run_id, self.ollama_model,
                                  progress=self.progress.emit, should_cancel=self.isInterruptionRequested)
                    except AnalysisCancelled:
                        self.result.emit(run_id, count)
                        raise
                    except Exception as exc:
                        self.progress.emit(f"Analysis saved; theme naming failed: {exc}")
                self.result.emit(run_id, count)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class ImportWorker(QThread):
    result = Signal(object)

    def __init__(self, directory: Path, files: list[Path], parent=None):
        super().__init__(parent)
        self.directory = directory
        self.files = files

    def run(self):
        for path in self.files:
            if self.isInterruptionRequested():
                break
            self.result.emit(import_document(self.directory, path))


class SegmentWorker(QThread):
    result = Signal(str, int)
    failed = Signal(str)

    def __init__(self, directory: Path, unit: str, parent=None, document_ids=None):
        super().__init__(parent)
        self.directory = directory
        self.unit = unit
        self.document_ids = list(document_ids) if document_ids is not None else None

    def run(self):
        try:
            run_id, count = create_segmentation_run(
                self.directory, self.unit, document_ids=self.document_ids,
                should_cancel=self.isInterruptionRequested)
            self.result.emit(run_id, count)
        except SegmentationCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self._worker: ImportWorker | None = None
        self._segment_worker: SegmentWorker | None = None
        self._segment_run_id: str | None = None
        self._analysis_worker = None
        self._ollama_worker = None
        self.analysis_settings = AnalysisSettings()
        self._close_after_analysis = False
        self.setWindowTitle("Topic Model Tool")
        self.resize(1200, 850)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        title = QLabel("Topic Model Tool")
        title.setStyleSheet("font-size: 20px; font-weight: 600")
        layout.addWidget(title)
        self.status = QLabel("Create or open a project to begin.")
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        documents_tab = QWidget()
        tab_layout = QVBoxLayout(documents_tab)
        self.tabs.addTab(documents_tab, "Documents & Passages")
        self.workspace = CodingWorkspace()
        self.tabs.addTab(self.workspace, "Research Workspace")
        self.visualizations = VisualizationTab()
        self.tabs.addTab(self.visualizations, "Visualizations")
        self.visualizations.build_requested.connect(self.start_visualizations)
        self.visualizations.cancel_requested.connect(self.cancel_analysis)
        self.visualizations.evidence_requested.connect(self.open_visualization_evidence)
        self.tabs.currentChanged.connect(self.refresh_visualization_runs)
        actions = QHBoxLayout()
        create = QPushButton("Create Project")
        create.clicked.connect(self.create)
        open_button = QPushButton("Open Project")
        open_button.clicked.connect(self.open)
        actions.addWidget(create)
        actions.addWidget(open_button)
        self.import_button = QPushButton("Import Documents")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.import_files)
        actions.addWidget(self.import_button)
        self.unit_selector = QComboBox()
        self.unit_selector.addItems(UNITS)
        self.unit_selector.setCurrentText("Standard passages")
        self.unit_selector.setToolTip("Short passages reveal specific ideas; long passages retain more context.")
        actions.addWidget(self.unit_selector)
        self.segment_button = QPushButton("Preview Passages")
        self.segment_button.setEnabled(False)
        self.segment_button.clicked.connect(self.preview_passages)
        self.segment_button.setToolTip("Preview the selected documents. Ctrl/Shift selects several; the current document opens first.")
        actions.addWidget(self.segment_button)
        self.preview_all_button = QPushButton("Preview all documents")
        self.preview_all_button.setEnabled(False)
        self.preview_all_button.setToolTip("Preview every available imported document, using the current passage size.")
        self.preview_all_button.clicked.connect(lambda: self.preview_passages(all_documents=True))
        actions.addWidget(self.preview_all_button)
        actions.addStretch()
        tab_layout.addLayout(actions)
        analysis_actions = QHBoxLayout()
        self.analyze_button = QPushButton("Analyze selected documents")
        self.analyze_button.clicked.connect(self.start_analysis)
        self.analyze_button.setEnabled(False)
        analysis_actions.addWidget(self.analyze_button)
        self.download_button = QPushButton("Download model (one-time setup)")
        self.download_button.clicked.connect(lambda: self.start_analysis(download=True))
        self.download_button.setEnabled(False)
        analysis_actions.addWidget(self.download_button)
        self.cancel_analysis_button = QPushButton("Cancel")
        self.cancel_analysis_button.clicked.connect(self.cancel_analysis)
        self.cancel_analysis_button.setEnabled(False)
        analysis_actions.addWidget(self.cancel_analysis_button)
        tab_layout.addLayout(analysis_actions)
        self.rename_run_button = self.workspace.name_run_button
        self.rename_run_button.clicked.connect(self.name_selected_run)
        self.ollama_model_choice = self.workspace.ollama_model_choice
        self.refresh_models_button = self.workspace.refresh_models_button
        self.refresh_models_button.clicked.connect(self.refresh_ollama_models)
        self.ollama_status = self.workspace.ollama_status
        self.workspace.cancel_task_button.clicked.connect(self.cancel_analysis)
        self.workspace.run_choice.currentIndexChanged.connect(self.update_naming_controls)
        self.workspace.refine_button.clicked.connect(lambda: self.start_refinement())
        self.workspace.review_proposal_button.clicked.connect(self.review_pending_refinement)
        self.workspace.history_button.clicked.connect(self.show_analysis_history)
        self.workspace.export_codebook_button.clicked.connect(lambda: self.start_research_export("codebook"))
        self.workspace.export_passages_button.clicked.connect(lambda: self.start_research_export("passages"))
        self.contents = QSplitter()
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.addWidget(QLabel("Documents"))
        self.document_list = QListWidget()
        self.document_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.document_list.setToolTip("Use Ctrl/Shift to select documents. Analyze fits one model across all selected documents.")
        self.document_list.currentItemChanged.connect(self.show_document)
        self.document_list.itemSelectionChanged.connect(self.clear_passage_preview)
        self.document_list.itemSelectionChanged.connect(self.update_document_actions)
        left_lay.addWidget(self.document_list)
        self.remove_documents_button = QPushButton("Remove selected documents")
        self.remove_documents_button.setEnabled(False)
        self.remove_documents_button.clicked.connect(self.remove_selected_documents)
        left_lay.addWidget(self.remove_documents_button)
        self.restore_documents_button = QPushButton("Restore removed documents…")
        self.restore_documents_button.setEnabled(False)
        self.restore_documents_button.clicked.connect(self.restore_removed_documents)
        left_lay.addWidget(self.restore_documents_button)
        self.contents.addWidget(left)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        self.preview_title = QLabel("Import documents to preview extracted text.")
        right_lay.addWidget(self.preview_title)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        right_lay.addWidget(self.preview)
        self.contents.addWidget(right)
        self.contents.setStretchFactor(1, 3)
        tab_layout.addWidget(self.contents, 1)
        tab_layout.addWidget(QLabel("Passage preview (exact source offsets)"))
        self.segment_list = QListWidget()
        self.segment_list.setMaximumHeight(125)
        self.segment_list.currentItemChanged.connect(self.show_segment)
        tab_layout.addWidget(self.segment_list)
        self.segment_excerpt = QPlainTextEdit()
        self.segment_excerpt.setReadOnly(True)
        self.segment_excerpt.setMaximumHeight(105)
        tab_layout.addWidget(self.segment_excerpt)
        self.import_status = QLabel("")
        self.import_status.setWordWrap(True)
        tab_layout.addWidget(self.import_status)
        self.import_log = QListWidget()
        self.import_log.setMaximumHeight(105)
        self.import_log.setAccessibleName("Document import results")
        tab_layout.addWidget(self.import_log)

    def _show_project(self, project: Project):
        self.project = project
        self.analysis_settings, unit = load_setup(project.directory)
        self.unit_selector.setCurrentText(unit if unit in UNITS else "Standard passages")
        self.ollama_model_choice.clear()
        if self.analysis_settings.ollama_model:
            self.ollama_model_choice.addItem(self.analysis_settings.ollama_model, self.analysis_settings.ollama_model)
        self.setWindowTitle(f"{project.name} — Topic Model Tool")
        self.status.setText(f"Project: {project.name}\nLocation: {project.directory}")
        self.import_button.setEnabled(True)
        self.download_button.setEnabled(True)
        self._segment_run_id = None
        self.segment_list.clear()
        self.segment_excerpt.clear()
        self.import_status.setText("Project ready. Import PDF, DOCX, or TXT documents.")
        self.refresh_documents()
        self.workspace.set_project(project.directory)
        self.visualizations.set_project(project.directory)
        self.update_naming_controls()

    def update_naming_controls(self, _index=-1):
        busy = bool(self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker)
        self.visualizations.set_busy(busy)
        self.rename_run_button.setEnabled(bool(self.project and self.workspace.run_id) and not busy)
        self.workspace.refine_button.setEnabled(bool(self.project and self.workspace.run_id) and not busy)
        self.workspace.review_proposal_button.setEnabled(bool(self.project and list_proposals(self.project.directory)) and not busy)
        self.workspace.run_choice.setEnabled(not busy)
        self.workspace.rename_analysis_button.setEnabled(bool(self.project and self.workspace.run_id) and not busy)
        for button in [self.workspace.history_button, self.workspace.export_codebook_button, self.workspace.export_passages_button]:
            button.setEnabled(bool(self.project) and not busy)
        self.refresh_models_button.setEnabled(not busy)
        self.ollama_model_choice.setEnabled(not busy)
        self.update_document_actions()

    def update_document_actions(self):
        busy = bool(self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker)
        self.preview_all_button.setEnabled(bool(self.project and self.document_list.count()) and not busy)
        self.remove_documents_button.setEnabled(bool(self.project and self.document_list.selectedItems()) and not busy)
        removed = bool(self.project and any(status == "removed" for _, _, status in
                       list_documents(self.project.directory, include_removed=True)))
        self.restore_documents_button.setEnabled(removed and not busy)

    def remove_selected_documents(self):
        if not self.project or self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        items = self.document_list.selectedItems()
        if not items:
            self.import_status.setText("Select documents to remove.")
            return
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in items]
        names = "\n".join(item.text() for item in items)
        answer = QMessageBox.question(self, "Remove imported documents",
            f"Remove these {len(ids)} document(s) from the import list and future analyses?\n\n{names}\n\n"
            "Saved analyses, source text, coding, and notes remain available. "
            "You can restore the documents later.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            count = remove_documents(self.project.directory, ids)
        except Exception as exc:
            QMessageBox.warning(self, "Could not remove documents", str(exc))
            return
        self.refresh_documents()
        self.workspace.reload_documents()
        self.workspace.reload_themes()
        self.update_document_actions()
        self.import_status.setText(f"Removed {count} document(s) from the import list. Saved analyses, coding, and notes remain available.")

    def restore_removed_documents(self):
        if not self.project or self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        rows = [row for row in list_documents(self.project.directory, include_removed=True) if row[2] == "removed"]
        if not rows:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Restore removed documents")
        dialog.resize(480, 400)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Select documents to return to the import list (Ctrl/Shift selects several)."))
        choices = QListWidget()
        choices.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        from PySide6.QtWidgets import QListWidgetItem
        for doc_id, name, _ in rows:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, doc_id)
            choices.addItem(item)
        layout.addWidget(choices)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Restore selected")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        choices.itemSelectionChanged.connect(lambda: buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(choices.selectedItems())))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            ids = [item.data(Qt.ItemDataRole.UserRole) for item in choices.selectedItems()]
            try:
                count = restore_documents(self.project.directory, ids)
                self.refresh_documents(ids[0])
                self.workspace.reload_documents()
                self.workspace.reload_themes()
                self.update_document_actions()
                self.import_status.setText(f"Restored {count} document(s) with their saved research decisions.")
            except Exception as exc:
                QMessageBox.warning(self, "Could not restore documents", str(exc))
        dialog.deleteLater()

    def refresh_ollama_models(self):
        if self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        self._ollama_worker = OllamaModelsWorker(self)
        self._ollama_worker.result.connect(self.models_ready)
        self._ollama_worker.failed.connect(self.models_failed)
        self._ollama_worker.finished.connect(self.models_finished)
        self.ollama_status.setText("Checking local Ollama…")
        for button in [self.import_button, self.segment_button, self.analyze_button, self.download_button]:
            button.setEnabled(False)
        self.update_naming_controls()
        self._ollama_worker.start()

    def models_ready(self, models):
        old = self.ollama_model_choice.currentData()
        self.ollama_model_choice.clear()
        for model in models:
            self.ollama_model_choice.addItem(model["name"], model["name"])
        index = self.ollama_model_choice.findData(old)
        if index >= 0:
            self.ollama_model_choice.setCurrentIndex(index)
        self.ollama_status.setText(f"{len(models)} local model(s) found." if models else
                                   "No local models found. Keyword names will be used.")

    def models_failed(self, message):
        self.ollama_model_choice.clear()
        self.ollama_status.setText("Local Ollama unavailable. Keyword fallback remains available. " + message)

    def models_finished(self):
        worker = self._ollama_worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self._ollama_worker = None
        self.import_button.setEnabled(self.project is not None)
        self.segment_button.setEnabled(bool(self.project and self.document_list.count()))
        self.analyze_button.setEnabled(bool(self.project and self.document_list.count()))
        self.download_button.setEnabled(self.project is not None)
        self.update_naming_controls()
        if self._close_after_analysis:
            self.close()

    def name_selected_run(self):
        if self.workspace.run_id:
            self.start_refinement(operation="names")

    def refresh_documents(self, select_id: str | None = None):
        self.document_list.clear()
        self.preview.clear()
        self.preview_title.setText("Import documents to preview extracted text.")
        if self.project is None:
            return
        for document_id, name, status in list_documents(self.project.directory):
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, document_id)
            self.document_list.addItem(item)
            if select_id == document_id:
                self.document_list.setCurrentItem(item)
        if self.document_list.count() and self.document_list.currentItem() is None:
            self.document_list.setCurrentRow(0)
        self.segment_button.setEnabled(self.document_list.count() > 0 and self._worker is None
                                       and self._segment_worker is None)
        self.analyze_button.setEnabled(self.document_list.count() > 0 and not self._analysis_worker
                                       and not self._worker and not self._segment_worker)
        self.update_document_actions()

    def show_document(self, current, _previous=None):
        if not current or not self.project:
            return
        from PySide6.QtCore import Qt
        name, text, spans = get_document(
            self.project.directory, current.data(Qt.ItemDataRole.UserRole))
        pages = sorted({x[0] for x in spans if x[0] is not None})
        self.preview_title.setText(f"{name} · {len(text):,} characters" +
                                   (f" · {len(pages)} text pages" if pages else ""))
        self.preview.setPlainText(text)

    def import_files(self):
        if self.project is None or self._worker is not None or self._analysis_worker or self._segment_worker or self._ollama_worker:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import documents", "", "Documents (*.pdf *.docx *.txt)")
        if not paths:
            return
        self.import_button.setEnabled(False)
        self.segment_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.import_status.setText(f"Importing {len(paths)} file(s)…")
        self._outcomes: list[ImportOutcome] = []
        self.import_log.clear()
        self._worker = ImportWorker(self.project.directory, [Path(p) for p in paths], self)
        self.update_naming_controls()
        self._worker.result.connect(self._on_import_result)
        self._worker.finished.connect(self._on_import_finished)
        self._worker.start()

    def _on_import_result(self, outcome: ImportOutcome):
        self._outcomes.append(outcome)
        details = f"{outcome.filename}: {outcome.status}. {outcome.message}"
        if outcome.warnings:
            details += " " + " ".join(outcome.warnings)
        self.import_status.setText(details)
        self.import_log.addItem(details)
        if outcome.status in {"imported", "restored"}:
            self._segment_run_id = None
            self.segment_list.clear()
            self.segment_excerpt.clear()
            self.refresh_documents(outcome.document_id)
            self.workspace.reload_documents()

    def _on_import_finished(self):
        # Defer dropping the QThread reference until Qt has fully finished.
        worker = self._worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self._worker = None
        self.update_naming_controls()
        self.import_button.setEnabled(self.project is not None)
        self.segment_button.setEnabled(self.document_list.count() > 0)
        self.analyze_button.setEnabled(self.document_list.count() > 0)
        self.download_button.setEnabled(self.project is not None)
        imported = sum(x.status == "imported" for x in self._outcomes)
        restored = sum(x.status == "restored" for x in self._outcomes)
        duplicates = sum(x.status == "duplicate" for x in self._outcomes)
        failures = [x for x in self._outcomes if x.status == "failed"]
        self.import_status.setText(
            f"Imported {imported}; restored {restored}; already present {duplicates}; failed {len(failures)}. " +
            " ".join(f"{x.filename}: {x.message}" for x in failures))

    def closeEvent(self, event):
        if self._ollama_worker:
            self._close_after_analysis = True
            event.ignore()
            return
        if self._analysis_worker:
            self._close_after_analysis = True
            self.cancel_analysis()
            self.import_status.setText("Waiting for the active analysis stage to finish before closing…")
            event.ignore()
            return
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait()
        if self._segment_worker and self._segment_worker.isRunning():
            self._segment_worker.requestInterruption()
            self._segment_worker.wait()
        self.visualizations.dispose()
        super().closeEvent(event)

    def clear_passage_preview(self):
        if self._segment_worker:
            return
        self._segment_run_id = None
        self.segment_list.clear()
        self.segment_excerpt.clear()

    def preview_passages(self, _checked=False, all_documents=False):
        if not self.project or self._worker or self._segment_worker or self._analysis_worker or self._ollama_worker:
            return
        documents = ([row[0] for row in list_documents(self.project.directory) if row[2] == "ready"]
                     if all_documents else [item.data(Qt.ItemDataRole.UserRole) for item in self.document_list.selectedItems()])
        if not documents:
            self.import_status.setText("Import documents to preview." if all_documents else "Select documents to preview (Ctrl/Shift selects several).")
            return
        self.clear_passage_preview()
        if self.unit_selector.currentText() == "Whole document":
            lengths = []
            for doc in documents:
                name, text, _ = get_document(self.project.directory, doc)
                lengths.append((name, len(text)))
            if any(length > 30000 for _name, length in lengths):
                QMessageBox.information(
                    self, "Long documents",
                    "Some documents are long. Treating each entire document as one passage "
                    "may hide specific ideas; Standard passages may work better.")
        self.segment_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.document_list.setEnabled(False)
        self.unit_selector.setEnabled(False)
        self._preview_scope = "all available" if all_documents else "selected"
        self.import_status.setText(f"Preparing passage preview for {self._preview_scope} documents…")
        self._segment_worker = SegmentWorker(self.project.directory,
                                             self.unit_selector.currentText(), self, document_ids=documents)
        self.update_naming_controls()
        self._segment_worker.result.connect(self._on_segments_ready)
        self._segment_worker.failed.connect(self._on_segments_failed)
        self._segment_worker.finished.connect(self._on_segments_finished)
        self._segment_worker.start()

    def _on_segments_ready(self, run_id: str, count: int):
        self._segment_run_id = run_id
        self.segment_list.clear()
        current = self.document_list.currentItem()
        focus_doc = current.data(Qt.ItemDataRole.UserRole) if current else None
        focus_row = None
        records = list_segments(self.project.directory, run_id)
        for doc_id, name, ordinal, start, end, excerpt in records:
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(f"{name} · passage {ordinal + 1} · [{start}, {end})")
            item.setData(Qt.ItemDataRole.UserRole, (doc_id, start, end, excerpt))
            self.segment_list.addItem(item)
            if focus_row is None and doc_id == focus_doc:
                focus_row = self.segment_list.count() - 1
        if count:
            self.segment_list.setCurrentRow(focus_row if focus_row is not None else 0)
        self.import_status.setText(
            f"{count} passages prepared from {len({row[0] for row in records})} {getattr(self, '_preview_scope', 'selected')} document(s) using {self.unit_selector.currentText()}. "
            "Select one below to inspect its exact extracted text and offsets.")

    def _on_segments_failed(self, message: str):
        self.import_status.setText(f"Could not prepare passages: {message}")

    def _on_segments_finished(self):
        worker = self._segment_worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self._segment_worker = None
        self.document_list.setEnabled(True)
        self.unit_selector.setEnabled(True)
        self.update_naming_controls()
        self.import_button.setEnabled(self.project is not None)
        self.segment_button.setEnabled(self.document_list.count() > 0)
        self.analyze_button.setEnabled(self.document_list.count() > 0)
        self.download_button.setEnabled(self.project is not None)

    def refresh_visualization_runs(self, _index=None):
        if self.tabs.currentWidget() == self.visualizations and self.project and not self._analysis_worker:
            self.visualizations.reload_runs(self.workspace.run_id)

    def start_visualizations(self):
        if not self.project or self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        selected = self.visualizations.run_choice.currentData()
        if not selected:
            return
        self.visualizations.invalidate()
        worker = VisualizationWorker(self.project.directory, selected, self)
        self._analysis_worker = worker
        worker.progress.connect(self.visualizations.status.setText)
        worker.result.connect(self.visualizations.display_result)
        worker.failed.connect(lambda message: self.visualizations.status.setText("Visualization failed: " + message))
        worker.cancelled.connect(lambda: self.visualizations.status.setText("Visualization cancelled. Saved research is unchanged."))
        worker.finished.connect(self.analysis_finished)
        for button in [self.import_button, self.segment_button, self.analyze_button, self.download_button]:
            button.setEnabled(False)
        self.update_naming_controls()
        self.visualizations.cancel_button.setEnabled(True)
        self.cancel_analysis_button.setEnabled(True)
        worker.start()

    def open_visualization_evidence(self, run_id, theme_id):
        if self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        self.workspace.reload_runs(run_id)
        self.workspace.reload_themes(theme_id)
        self.tabs.setCurrentWidget(self.workspace)
        self.update_naming_controls()

    def show_analysis_history(self):
        if not self.project or self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        dialog = AnalysisHistoryDialog(self.project.directory, self)
        dialog.exec()
        dialog.deleteLater()

    def start_research_export(self, kind):
        if not self.project or self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        filters = "Codebook CSV (*.csv);;Codebook JSON (*.json)" if kind == "codebook" else "Coded passages CSV (*.csv)"
        title = "Export codebook" if kind == "codebook" else "Export coded passages"
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
        for button in [self.import_button, self.segment_button, self.analyze_button, self.download_button]:
            button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)
        self.workspace.cancel_task_button.setEnabled(True)
        self.update_naming_controls()
        self.task_status("Preparing a read-only snapshot of effective coding…")
        worker.start()

    def start_refinement(self, operation=None):
        if not self.project or not self.workspace.run_id or self._worker or self._segment_worker or self._analysis_worker or self._ollama_worker:
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
                    raise ValueError("This saved analysis includes removed documents. Restore them before fresh discovery, or analyze a new selection of available documents.")
                settings = replace(settings, requested_theme_count=None)
                chosen = [(doc, get_document(self.project.directory, doc)[0]) for doc in source["documents"]]
                dialog = AnalysisSetupDialog(self.project.directory, chosen, settings, unit, [m for m in models if m], self)
                dialog.setWindowTitle("Fresh discovery proposal — Analysis Setup")
                dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Prepare proposal")
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    dialog.deleteLater()
                    return
                settings, unit = dialog.settings(), dialog.unit.currentText()
                dialog.deleteLater()
            self._analysis_worker = RefinementWorker(self.project.directory, self.workspace.run_id,
                                                     operation, settings, unit, self)
        except Exception as exc:
            QMessageBox.warning(self, "Could not prepare refinement", str(exc))
            return
        worker = self._analysis_worker
        worker.progress.connect(self.task_status)
        worker.failed.connect(lambda message: self.task_status("Refinement failed: " + message))
        worker.cancelled.connect(lambda: self.task_status("Refinement cancelled. Selected analysis and research decisions are unchanged."))
        worker.result.connect(self.refinement_ready)
        worker.finished.connect(self.analysis_finished)
        for button in [self.import_button, self.segment_button, self.analyze_button, self.download_button]:
            button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)
        self.workspace.cancel_task_button.setEnabled(True)
        self.update_naming_controls()
        self.task_status("Preparing a separate proposal…")
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
                self.analysis_ready(proposal_id, len(list_segments(self.project.directory, proposal_id)))
                self.task_status("Refinement applied. Earlier analyses and all research decisions remain saved.")
            else:
                self.task_status("Proposal cancelled. Selected analysis and research decisions are unchanged.")
        except Exception as exc:
            self.task_status("Proposal remains saved for review: " + str(exc))
        self.update_naming_controls()

    def review_pending_refinement(self):
        if not self.project or self._analysis_worker or self._worker or self._segment_worker or self._ollama_worker:
            return
        proposals = list_proposals(self.project.directory)
        if not proposals:
            return
        from qualitative_themes.analysis.refinement import OPERATIONS
        displays = [f"{i+1}. {started[:19]} · {OPERATIONS[json.loads(info)['refinement_operation']][0]}"
                    for i, (_, started, info) in enumerate(proposals)]
        choice, ok = QInputDialog.getItem(self, "Pending refinements", "Choose a saved proposal", displays, 0, False)
        if ok:
            self.refinement_ready(proposals[displays.index(choice)][0])

    def start_analysis(self, _checked=False, download=False, run_id=None):
        if not self.project or self._worker or self._segment_worker or self._analysis_worker or self._ollama_worker:
            return
        documents = [item.data(Qt.ItemDataRole.UserRole) for item in self.document_list.selectedItems()]
        if not download and not run_id and not documents:
            self.import_status.setText("Select documents to analyze together (Ctrl/Shift selects several).")
            return
        settings = self.analysis_settings
        if not download and not run_id:
            chosen = [(item.data(Qt.ItemDataRole.UserRole), item.text()) for item in self.document_list.selectedItems()]
            models = [self.ollama_model_choice.itemData(i) for i in range(self.ollama_model_choice.count())]
            initial = replace(settings, ollama_model=self.ollama_model_choice.currentData() or settings.ollama_model)
            dialog = AnalysisSetupDialog(self.project.directory, chosen, initial,
                                         self.unit_selector.currentText(), models, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                dialog.deleteLater()
                return
            settings = dialog.settings()
            self.unit_selector.setCurrentText(dialog.unit.currentText())
            dialog.deleteLater()
            try:
                save_setup(self.project.directory, settings, self.unit_selector.currentText())
            except Exception as exc:
                QMessageBox.warning(self, "Could not save setup", str(exc))
                return
            self.analysis_settings = settings
            if settings.ollama_model and self.ollama_model_choice.findData(settings.ollama_model) < 0:
                self.ollama_model_choice.addItem(settings.ollama_model, settings.ollama_model)
            self.ollama_model_choice.setCurrentIndex(self.ollama_model_choice.findData(settings.ollama_model))
        self._analysis_worker = AnalysisWorker(self.project.directory, self.unit_selector.currentText(), documents,
                                               download=download, parent=self,
                                               name_themes=settings.name_themes,
                                               ollama_model=(self.ollama_model_choice.currentData() if run_id else settings.ollama_model),
                                               run_id=run_id, settings=settings)
        self.update_naming_controls()
        worker = self._analysis_worker
        worker.progress.connect(self.task_status)
        worker.failed.connect(lambda message: self.task_status("Analysis/setup failed: " + message))
        worker.cancelled.connect(lambda: self.task_status("Cancelled. Completed analysis and researcher work remain saved."))
        worker.result.connect(self.analysis_ready)
        worker.finished.connect(self.analysis_finished)
        for button in [self.import_button, self.segment_button, self.analyze_button, self.download_button]:
            button.setEnabled(False)
        self.cancel_analysis_button.setEnabled(True)
        self.workspace.cancel_task_button.setEnabled(True)
        self.task_status("Starting…")
        worker.start()

    def cancel_analysis(self):
        if self._analysis_worker:
            self._analysis_worker.requestInterruption()
            self.cancel_analysis_button.setEnabled(False)
            self.workspace.cancel_task_button.setEnabled(False)
            self.task_status("Cancellation requested; waiting for the current computation or request…")

    def task_status(self, message):
        self.import_status.setText(message)
        self.workspace.status.setText(message)

    def analysis_ready(self, run_id, count):
        self.workspace.reload_runs(run_id)
        self.tabs.setCurrentWidget(self.workspace)
        themes, uncategorized = result_summary(self.project.directory, run_id)
        self.task_status(f"Analysis complete: {themes} discovered themes · {count} passages · {uncategorized} Uncategorized passages.")
        self.workspace.naming_status.setPlainText(labeling_report(self.project.directory, run_id))
        if self._analysis_worker and self._analysis_worker.run_id:
            self.import_status.setText("Theme naming finished. See Theme naming details in Research Workspace.")
            self.workspace.status.setText("Theme naming finished. Open Theme naming details for results and fallback reasons.")

    def analysis_finished(self):
        worker = self._analysis_worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self._analysis_worker = None
        self.update_naming_controls()
        self.import_button.setEnabled(self.project is not None)
        self.segment_button.setEnabled(self.document_list.count() > 0)
        self.analyze_button.setEnabled(self.document_list.count() > 0)
        self.download_button.setEnabled(self.project is not None)
        self.cancel_analysis_button.setEnabled(False)
        self.workspace.cancel_task_button.setEnabled(False)
        if self._close_after_analysis:
            self.close()

    def show_segment(self, current, _previous=None):
        if not current:
            return
        doc_id, start, end, excerpt = current.data(Qt.ItemDataRole.UserRole)
        # SQLite substr and Python slicing should agree for saved Unicode text.
        _, full_text, _ = get_document(self.project.directory, doc_id)
        actual = full_text[start:end]
        if excerpt != actual:
            self.segment_excerpt.setPlainText("Passage preview mismatch; check project data.")
            return
        self.segment_excerpt.setPlainText(actual)
        for row in range(self.document_list.count()):
            item = self.document_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == doc_id:
                self.document_list.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
                break

    def create(self):
        if self._worker is not None or self._segment_worker is not None or self._analysis_worker or self._ollama_worker:
            QMessageBox.information(self, "Task in progress", "Finish the current task before switching projects.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose an empty project folder")
        if not folder:
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Project name", "Name", QLineEdit.EchoMode.Normal, Path(folder).name)
        if not ok:
            return
        try:
            self._show_project(create_project(Path(folder), name))
        except Exception as exc:
            QMessageBox.warning(self, "Could not create project", str(exc))

    def open(self):
        if self._worker is not None or self._segment_worker is not None or self._analysis_worker or self._ollama_worker:
            QMessageBox.information(self, "Task in progress", "Finish the current task before switching projects.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Open a project folder")
        if folder:
            try:
                self._show_project(open_project(Path(folder)))
            except Exception as exc:
                QMessageBox.warning(self, "Could not open project", str(exc))
