"""The local naming engine (Ollama) and model packs: starting, checking, installing.

Part of MainWindow (see main_window.py); not meant to be used on its own.
"""
from pathlib import Path

from PySide6.QtWidgets import (QFileDialog, QMessageBox)

from qualitative_themes.analysis.refinement import list_proposals


from qualitative_themes.gui.workers import (EngineStartWorker, OllamaModelsWorker, PackInstallWorker)
from qualitative_themes.gui.menus import update_menus


class LocalSetupMixin:
    def repair_setup(self):
        """Reinstall bundled model packs and restart the engine, in the background."""
        if getattr(self, "_engine_worker", None):
            return
        from qualitative_themes.runtime.engine import get_engine
        get_engine().stop()
        self.hub.refresh_setup("Checking and repairing local setup…")
        self.start_local_services()

    def add_model_pack(self):
        if getattr(self, "_pack_worker", None):
            return
        from qualitative_themes.runtime.model_pack import find_downloaded_packs
        found = find_downloaded_packs()
        start = str(found[0].parent) if found else str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose a theme-naming model pack", start,
                                              "Theme-naming model pack (*.tmtpack *.tmtpack.001)")
        if not path:
            return
        self._pack_worker = PackInstallWorker(Path(path), self)
        self._pack_worker.progress.connect(lambda message: self.hub.refresh_setup(message))
        self._pack_worker.done.connect(self._pack_installed)
        self._pack_worker.start()

    def _pack_installed(self, ok, message):
        worker, self._pack_worker = self._pack_worker, None
        if worker:
            worker.wait()
            worker.deleteLater()
        if not ok:
            QMessageBox.warning(self, "Could not add the naming model", message)
        self.hub.refresh_setup(message if ok else None)
        if ok:
            self.refresh_ollama_models()

    def update_naming_controls(self, _index=-1):
        busy = self.busy
        self.visualizations.set_busy(busy)
        self.rename_run_button.setEnabled(bool(self.project and self.workspace.run_id) and not busy)
        self.workspace.refine_button.setEnabled(bool(self.project and self.workspace.run_id) and not busy)
        self.workspace.review_proposal_button.setEnabled(bool(self.project and list_proposals(self.project.directory)) and not busy)
        self.workspace.run_choice.setEnabled(not busy)
        self.workspace.rename_analysis_button.setEnabled(bool(self.project and self.workspace.run_id) and not busy)
        self.workspace.history_button.setEnabled(bool(self.project) and not busy)
        self.refresh_models_button.setEnabled(not busy)
        self.ollama_model_choice.setEnabled(not busy)
        self.update_document_actions()
        update_menus(self)

    def start_local_services(self):
        """Start the bundled engine in the background; the UI stays usable meanwhile.

        Called once after the window is shown. engine_started() then refreshes the
        model list and the start screen's setup panel.
        """
        if getattr(self, "_engine_worker", None):
            return
        self.ollama_status.setText("Starting the local AI engine…")
        self._engine_worker = EngineStartWorker(self)
        self._engine_worker.ready.connect(self.engine_started)
        self._engine_worker.finished.connect(self._engine_worker_finished)
        self._engine_worker.start()

    def engine_started(self, status):
        self.engine_status = status
        self.ollama_status.setText(status.message)
        if status.state == "running":
            self.refresh_ollama_models()
        hub = getattr(self, "hub", None)
        if hub is not None:
            hub.refresh_setup()

    def _engine_worker_finished(self):
        worker = self._engine_worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self._engine_worker = None

    def refresh_ollama_models(self):
        if self.busy:
            return
        self._ollama_worker = OllamaModelsWorker(self)
        self._ollama_worker.result.connect(self.models_ready)
        self._ollama_worker.failed.connect(self.models_failed)
        self._ollama_worker.finished.connect(self.models_finished)
        self.ollama_status.setText("Checking which naming models are installed…")
        for button in [self.import_button, self.analyze_button]:
            button.setEnabled(False)
        self.update_naming_controls()
        self._ollama_worker.start()

    def models_ready(self, models):
        old = self.ollama_model_choice.currentData()
        self.ollama_model_choice.clear()
        for model in models:
            self.ollama_model_choice.addItem(model["name"], model["name"])
        index = self.ollama_model_choice.findData(old)
        if index < 0:
            from qualitative_themes.analysis.settings import DEFAULT_NAMING_MODEL
            index = self.ollama_model_choice.findData(DEFAULT_NAMING_MODEL)
        if index >= 0:
            self.ollama_model_choice.setCurrentIndex(index)
        self.ollama_status.setText(f"{len(models)} naming model(s) installed." if models else
                                   "No naming models installed, so themes will keep keyword names.")

    def models_failed(self, message):
        self.ollama_model_choice.clear()
        self.ollama_status.setText("The naming engine isn't available; themes will keep keyword names. " + message)

    def models_finished(self):
        worker = self._ollama_worker
        if worker:
            worker.wait()
            worker.deleteLater()
        self._ollama_worker = None
        self.import_button.setEnabled(self.project is not None)
        self.analyze_button.setEnabled(bool(self.project and self.document_list.count()))
        self.update_naming_controls()
        if self._close_after_analysis:
            self.close()
