"""Beginner setup with asynchronous corpus inspection and local model discovery."""
from dataclasses import replace
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from qualitative_themes.analysis.labels import list_local_models
from qualitative_themes.analysis.segment import UNITS
from qualitative_themes.analysis.settings import (PRESETS, STYLE_HELP, AnalysisSettings,
    parse_ignored_words, preset_settings, validate_settings, small_corpus_warning)
from qualitative_themes.analysis.topics import prepare_passages


UNIT_HELP = {
    "Short passages": "Specific ideas with less context; usually more passages to analyze.",
    "Standard passages": "A starting point that balances context and passage count.",
    "Long passages": "More context per passage; embedding models may truncate long text.",
    "Paragraphs": "Use each extracted paragraph as a passage; length varies with the source.",
    "Whole document": "One passage per document; may hide smaller ideas and truncate long text.",
}


class InspectionWorker(QThread):
    result = Signal(str, object)
    failed = Signal(str)

    def __init__(self, directory, documents, unit, parent=None):
        super().__init__(parent)
        self.directory, self.documents, self.unit = directory, documents, unit

    def run(self):
        try:
            passages = prepare_passages(self.directory, self.unit, self.documents)
            if not self.isInterruptionRequested():
                self.result.emit(self.unit, {"count": len(passages),
                    "words": sum(len(p.text.split()) for p in passages), "unique": len({p.text for p in passages})})
        except Exception as exc:
            self.failed.emit(str(exc))


class ModelListWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            self.result.emit(list_local_models())
        except Exception as exc:
            self.failed.emit(str(exc))


class AnalysisSetupDialog(QDialog):
    def __init__(self, directory, documents, settings=None, unit="Standard passages", models=(), parent=None):
        super().__init__(parent)
        self.directory, self.documents = directory, documents
        self.initial = settings or AnalysisSettings()
        self._inspection = None
        self._inspection_restart = False
        self._models_worker = None
        self._reject_requested = False
        self.summary = None
        self.setWindowTitle("Analysis Setup")
        self.resize(650, 730)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout.addWidget(QLabel("Analyze these documents together"))
        selected = QListWidget()
        selected.addItems([name for _, name in documents])
        selected.setMaximumHeight(95)
        layout.addWidget(selected)
        form = QFormLayout()
        self.style = QComboBox()
        self.style.addItems(PRESETS)
        self.style.setCurrentText(self.initial.analysis_style)
        form.addRow("Analysis style", self.style)
        self.style_help = QLabel()
        self.style_help.setWordWrap(True)
        form.addRow(self.style_help)
        self.unit = QComboBox()
        self.unit.addItems(UNITS)
        self.unit.setCurrentText(unit)
        form.addRow("Analysis unit", self.unit)
        self.unit_help = QLabel()
        self.unit_help.setWordWrap(True)
        form.addRow(self.unit_help)
        self.number_mode = QComboBox()
        self.number_mode.addItems(["Let model decide", "Aim for approximately N"])
        self.number_mode.setCurrentIndex(int(self.initial.requested_theme_count is not None))
        form.addRow("Number of themes", self.number_mode)
        self.number = QSpinBox()
        self.number.setRange(1, 1000)
        self.number.setValue(self.initial.requested_theme_count or 5)
        form.addRow("Approximate goal", self.number)
        explanation = QLabel("A reduction goal for discovered themes, excluding Uncategorized. "
                             "It cannot create additional themes or guarantee an exact count. "
                             "Choose Detailed/Exploratory for a new discovery run with smaller groups.")
        explanation.setWordWrap(True)
        form.addRow(explanation)
        self.auto_names = QCheckBox("Automatically name themes")
        self.auto_names.setChecked(self.initial.name_themes)
        form.addRow(self.auto_names)
        layout.addLayout(form)
        self.advanced_toggle = QCheckBox("Advanced")
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        self.minimum = QSpinBox()
        self.minimum.setRange(2, 10000)
        self.minimum.setValue(self.initial.min_topic_size)
        self.minimum.setToolTip("Minimum passages supporting a discovered group; changing this overrides the style default.")
        advanced.addRow("Minimum evidence (passages)", self.minimum)
        self.ignored = QPlainTextEdit()
        self.ignored.setMaximumHeight(65)
        self.ignored.setPlainText(", ".join(self.initial.ignored_words))
        self.ignored.setPlaceholderText("Words separated by commas, spaces, or newlines; added to English stop words")
        self.ignored.setToolTip("Ignored words affect extracted keywords; passage embeddings still use the complete text.")
        advanced.addRow("Words to ignore", self.ignored)
        self.phrases = QComboBox()
        self.phrases.addItems(["Words and two-word phrases", "Words only"])
        self.phrases.setCurrentIndex(int(self.initial.ngram_range == (1, 1)))
        advanced.addRow("Keyword phrases", self.phrases)
        self.model = QComboBox()
        self.model.addItem("Keyword fallback (no model selected)", None)
        for name in models:
            self.model.addItem(name, name)
        if self.initial.ollama_model and self.model.findData(self.initial.ollama_model) < 0:
            self.model.addItem(self.initial.ollama_model + " (refresh to verify)", self.initial.ollama_model)
        self.model.setCurrentIndex(max(0, self.model.findData(self.initial.ollama_model)))
        advanced.addRow("Local Ollama model", self.model)
        self.refresh_models = QPushButton("Refresh installed models")
        self.refresh_models.clicked.connect(self.fetch_models)
        advanced.addRow(self.refresh_models)
        self.model_status = QLabel("Refresh checks your local Ollama service; naming failures use keywords.")
        self.model_status.setWordWrap(True)
        advanced.addRow(self.model_status)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)
        defaults = QPushButton("Restore Recommended Defaults")
        defaults.clicked.connect(self.restore_defaults)
        layout.addWidget(defaults)
        self.work = QLabel("Counting selected passages…")
        self.work.setWordWrap(True)
        layout.addWidget(self.work)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Analyze")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.style.currentTextChanged.connect(self.style_changed)
        self.unit.currentTextChanged.connect(self.inspect)
        for signal in [self.number_mode.currentIndexChanged, self.number.valueChanged,
                       self.minimum.valueChanged, self.ignored.textChanged, self.phrases.currentIndexChanged,
                       self.auto_names.toggled, self.model.currentIndexChanged]:
            signal.connect(self.update_validation)
        self.update_validation()
        self.inspect()

    def settings(self):
        # Preserve the configured internal embedding identity and execution seed.
        base = preset_settings(self.style.currentText())
        return replace(base, embedding_model=self.initial.embedding_model,
            embedding_revision=self.initial.embedding_revision, seed=self.initial.seed,
            batch_size=self.initial.batch_size, min_topic_size=self.minimum.value(),
            ignored_words=parse_ignored_words(self.ignored.toPlainText()),
            ngram_range=(1, 1) if self.phrases.currentIndex() else (1, 2),
            requested_theme_count=self.number.value() if self.number_mode.currentIndex() else None,
            name_themes=self.auto_names.isChecked(), ollama_model=self.model.currentData())

    def style_changed(self, _style):
        self.minimum.setValue(PRESETS[self.style.currentText()]["min_topic_size"])
        self.update_validation()

    def restore_defaults(self):
        self.initial = AnalysisSettings()
        self.style.setCurrentText("Balanced")
        self.minimum.setValue(5)
        self.unit.setCurrentText("Standard passages")
        self.number_mode.setCurrentIndex(0)
        self.number.setValue(5)
        self.ignored.clear()
        self.phrases.setCurrentIndex(0)
        self.auto_names.setChecked(False)
        self.model.setCurrentIndex(0)
        self.update_validation()

    def inspect(self, _unit=None):
        self.summary = None
        self.work.setText("Counting selected passages…")
        if self._inspection:
            self._inspection_restart = True
            self._inspection.requestInterruption()
            self.update_validation()
            return
        self._inspection = InspectionWorker(self.directory, [doc for doc, _ in self.documents], self.unit.currentText(), self)
        self._inspection.result.connect(self.inspected)
        self._inspection.failed.connect(lambda message: self.validation.setText(message))
        self._inspection.finished.connect(self.inspection_finished)
        self.update_validation()
        self._inspection.start()

    def inspected(self, unit, summary):
        if unit == self.unit.currentText():
            self.summary = summary
            self.work.setText(f"{summary['count']} passages · {summary['words']:,} words · {summary['unique']} unique passage texts. "
                              "Embedding work grows with passage count; cached texts may be reused. "
                              "Naming adds a local request per unprotected theme.")

    def inspection_finished(self):
        worker = self._inspection
        worker.wait()
        worker.deleteLater()
        self._inspection = None
        if self._reject_requested:
            self.finish_reject()
        elif self._inspection_restart or worker.unit != self.unit.currentText():
            self._inspection_restart = False
            self.inspect()
        else:
            self.update_validation()

    def update_validation(self, *_args):
        self.style_help.setText(STYLE_HELP[self.style.currentText()])
        self.unit_help.setText(UNIT_HELP[self.unit.currentText()])
        self.number.setEnabled(bool(self.number_mode.currentIndex()))
        valid = False
        try:
            settings = self.settings()
            if self.summary is not None:
                validate_settings(settings, self.summary["count"])
                ready = ("Ready. Naming will use keyword fallback if the selected local model is unavailable."
                         if settings.name_themes else "Ready to analyze.")
                warning = small_corpus_warning(settings, self.summary["count"])
                self.validation.setText(ready + ("\n" + warning if warning else ""))
                valid = True
        except ValueError as exc:
            self.validation.setText(str(exc))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(valid and not self._inspection and not self._models_worker)

    def fetch_models(self):
        if self._models_worker:
            return
        self._models_worker = ModelListWorker(self)
        self._models_worker.result.connect(self.models_ready)
        self._models_worker.failed.connect(lambda message: self.model_status.setText("Local Ollama unavailable; keywords remain available. " + message))
        self._models_worker.finished.connect(self.models_finished)
        self.refresh_models.setEnabled(False)
        self.model_status.setText("Checking local Ollama…")
        self.update_validation()
        self._models_worker.start()

    def models_ready(self, models):
        old = self.model.currentData()
        self.model.clear()
        self.model.addItem("Keyword fallback (no model selected)", None)
        for row in models:
            self.model.addItem(row["name"], row["name"])
        index = self.model.findData(old)
        self.model.setCurrentIndex(index if index >= 0 else (1 if models else 0))
        self.model_status.setText(f"{len(models)} local model(s) found.")

    def models_finished(self):
        self._models_worker.wait()
        self._models_worker.deleteLater()
        self._models_worker = None
        self.refresh_models.setEnabled(True)
        self.update_validation()
        if self._reject_requested:
            self.finish_reject()

    def accept(self):
        if self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            super().accept()

    def reject(self):
        self._reject_requested = True
        if self._inspection:
            self._inspection.requestInterruption()
        self.finish_reject()

    def finish_reject(self):
        if not self._inspection and not self._models_worker:
            super().reject()
        else:
            self.validation.setText("Waiting for the current setup check to finish…")
            self.buttons.setEnabled(False)

    def closeEvent(self, event):
        if self._inspection or self._models_worker:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
