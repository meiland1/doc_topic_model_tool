"""Background workers the main window starts. Each runs one job off the GUI thread."""
import traceback
from pathlib import Path

from PySide6.QtCore import Signal

from qualitative_themes.storage.documents import import_document
from qualitative_themes.analysis.topics import AnalysisCancelled, analyze, prepare_passages
from qualitative_themes.analysis.labels import label_run, list_local_models
from qualitative_themes.analysis.settings import AnalysisSettings


from qualitative_themes.gui.worker import WorkerThread


class OllamaModelsWorker(WorkerThread):
    """List installed naming models off the GUI thread (the engine may be slow to answer)."""
    result = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            self.result.emit(list_local_models())
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))


class EngineStartWorker(WorkerThread):
    """Install any bundled model pack, then start the private local AI engine."""
    ready = Signal(object)

    def run(self):
        from qualitative_themes.runtime.engine import EngineStatus, get_engine
        from qualitative_themes.runtime.model_pack import install_bundled_packs
        try:
            install_bundled_packs()
        except Exception:
            traceback.print_exc()
        try:
            self.ready.emit(get_engine().start())
        except Exception as exc:
            traceback.print_exc()
            self.ready.emit(EngineStatus("failed", message=str(exc)))


class PackInstallWorker(WorkerThread):
    """Install a .tmtpack (or split pack) chosen from the start screen.

    done(ok, message) is emitted once, on success or failure.
    """
    progress = Signal(str)
    done = Signal(bool, str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        from qualitative_themes.runtime.model_pack import install_model_pack
        try:
            info = install_model_pack(self.path, progress=self.progress.emit)
            self.done.emit(True, f"Added the theme-naming model {info['model']}.")
        except Exception as exc:
            traceback.print_exc()
            self.done.emit(False, str(exc))


class AnalysisWorker(WorkerThread):
    """Runs a new analysis, or names the themes of an existing run when run_id is given.

    Signals: result(run_id, passages) as soon as the analysis is saved;
    naming_complete(run_id) later if naming ran; failed(message); cancelled().
    The analysis is already saved before naming starts, so a naming failure or
    cancel never loses it.
    """
    progress = Signal(str)
    result = Signal(str, int)
    naming_complete = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, directory, unit, document_ids, parent=None,
                 name_themes=False, ollama_model=None, run_id=None, settings=None):
        super().__init__(parent)
        self.directory, self.unit, self.document_ids = directory, unit, document_ids
        self.name_themes, self.ollama_model, self.run_id = name_themes, ollama_model, run_id
        self.settings = settings or AnalysisSettings()

    def run(self):
        try:
            if self.run_id:
                label_run(self.directory, self.run_id, self.ollama_model,
                          progress=self.progress.emit, should_cancel=self.isInterruptionRequested)
                self.naming_complete.emit(self.run_id)
            else:
                run_id, count = analyze(self.directory, self.unit, self.document_ids,
                    settings=self.settings, progress=self.progress.emit, should_cancel=self.isInterruptionRequested)
                self.result.emit(run_id, count)
                if self.name_themes:
                    self.progress.emit('Analysis saved. You can read and code while optional theme naming finishes…')
                    try:
                        label_run(self.directory, run_id, self.ollama_model,
                                  progress=self.progress.emit, should_cancel=self.isInterruptionRequested)
                    except AnalysisCancelled:
                        raise
                    except Exception as exc:
                        traceback.print_exc()
                        self.progress.emit(f"Analysis saved; theme naming failed: {exc}")
                    else:
                        self.naming_complete.emit(run_id)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(str(exc))


class ImportWorker(WorkerThread):
    """Import files one at a time, emitting an ImportOutcome for each. Stops between files on interrupt."""
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


class InspectionWorker(WorkerThread):
    """Preview passages for the current choices without saving anything.

    Runs prepare_passages() and emits result(unit, summary) with counts, the
    passages themselves and segmentation stats. The dialog restarts it every
    time the passage size or interviewer options change.
    """
    result = Signal(str, object)
    failed = Signal(str)

    def __init__(self, directory, documents, unit, parent=None, exclude_interviewer=False, labels=None, settings=None):
        super().__init__(parent)
        self.directory, self.documents, self.unit = directory, documents, unit
        self.exclude_interviewer = exclude_interviewer
        self.labels = tuple(labels or ())
        self.analysis_settings = settings

    def run(self):
        try:
            diagnostics = {}
            passages = prepare_passages(self.directory, self.unit, self.documents, self.exclude_interviewer,
                                        list(self.labels), settings=self.analysis_settings, diagnostics=diagnostics,
                                        should_cancel=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                words = sum(len(p.text.split()) for p in passages)
                self.result.emit(self.unit, {"count": len(passages), "words": words,
                    "unique": len({p.text for p in passages}), "average": round(words / len(passages)) if passages else 0,
                    "duplicates": len(passages) - len({p.text for p in passages}),
                    "exclude_interviewer": self.exclude_interviewer, "labels": self.labels, "passages": passages,
                    "segmentation": diagnostics})
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelListWorker(WorkerThread):
    """Fetch installed naming models for the dropdown off the GUI thread."""
    result = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            self.result.emit(list_local_models())
        except Exception as exc:
            self.failed.emit(str(exc))
