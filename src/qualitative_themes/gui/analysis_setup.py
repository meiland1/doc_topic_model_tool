"""Beginner setup with asynchronous corpus inspection and local model discovery."""
from dataclasses import replace
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QTextEdit, QVBoxLayout, QWidget)

from qualitative_themes.analysis.labels import list_local_models
from qualitative_themes.analysis.segment import TOKEN_SAFE_UNITS, UNITS
from qualitative_themes.analysis.settings import (PRESETS, STYLE_HELP, AnalysisSettings,
    ANALYSIS_METHODS, parse_topic_definitions, parse_ignored_words, preset_settings, validate_settings, small_corpus_warning)
from qualitative_themes.analysis.speakers import count_interviewer_turns, detect_speakers, interviewer_ranges
from qualitative_themes.analysis.topics import prepare_passages
from qualitative_themes.domain.text_coordinates import TextCoordinates
from qualitative_themes.storage.documents import get_document

UNIT_BUTTONS = {"Automatic passages": "Auto", "Interview passages": "Interview", "Semantic passages": "Semantic*", "Short passages": "Short", "Standard passages": "Standard", "Long passages": "Long",
                "Paragraphs": "Paragraph", "Whole document": "Whole doc"}


UNIT_HELP = {
    "Automatic passages": "Keep sentences and paragraphs together where possible; fit every passage within the local model's token limit.",
    "Interview passages": "Keep different speakers separate and split long answers within the model limit. Uncertain speaker layouts use Auto and keep all text; review the preview.",
    "Semantic passages": "Experimental: use the local model to find changes between sentences. Slower first preview; not guaranteed to improve topics. Token limits still apply. Use Interview to enforce speaker boundaries.",
    "Short passages": "Specific ideas with less context; usually more passages to analyze.",
    "Standard passages": "A starting point that balances context and passage count.",
    "Long passages": "More context per passage; embedding models may truncate long text.",
    "Paragraphs": "Use each extracted paragraph as a passage; length varies with the source.",
    "Whole document": "One passage per document; may hide smaller ideas and truncate long text.",
}


from qualitative_themes.gui.worker import WorkerThread


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


class AnalysisSetupDialog(QDialog):
    """Choose method, passage size, style and naming before an analysis.

    Usage: exec() it, then read settings() and unit.currentText() if accepted.
    OK stays disabled until the inspection says the setup can run.
    Closing/rejecting waits for any running worker first, so the caller can
    deleteLater() the dialog straight away.
    """
    def __init__(self, directory, documents, settings=None, unit="Standard passages", models=(), parent=None):
        super().__init__(parent)
        self.directory, self.documents = directory, documents
        self.initial = settings or AnalysisSettings()
        self._inspection = None
        self._inspection_restart = False
        self._models_worker = None
        self._reject_requested = False
        self.summary = None
        self.passages = []
        self.preview_index = -1
        self.setWindowTitle("Analysis Setup")
        self.resize(1180, 760)
        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        outer.addLayout(body, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        body.addWidget(scroll, 1)
        body.addWidget(self.build_preview_pane(), 1)
        layout.addWidget(QLabel("Analyze these documents together"))
        selected = QListWidget()
        selected.setAccessibleName('Selected documents for analysis')
        selected.addItems([name for _, name in documents])
        selected.setMaximumHeight(95)
        layout.addWidget(selected)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.method = QComboBox()
        for key, label in ANALYSIS_METHODS.items():
            self.method.addItem(label, key)
        self.method.setCurrentIndex(max(0, self.method.findData(self.initial.analysis_method)))
        form.addRow("Analysis method", self.method)
        self.method_help = QLabel()
        self.method_help.setWordWrap(True)
        form.addRow(self.method_help)
        self.topic_editor = QPlainTextEdit()
        self.topic_editor.setTabChangesFocus(True)
        self.topic_editor.setAccessibleName('Predefined topics')
        self.topic_editor.setMaximumHeight(130)
        self.topic_editor.setPlainText("\n".join(f"{name} | {text}" for name, text in self.initial.topic_definitions))
        form.addRow("Predefined topics", self.topic_editor)
        self.similarity = QDoubleSpinBox()
        self.similarity.setRange(0, 1)
        self.similarity.setDecimals(2)
        self.similarity.setSingleStep(0.05)
        self.similarity.setValue(self.initial.zeroshot_min_similarity)
        self.similarity.setToolTip("Semantic similarity, not confidence. Calibrate using reviewed passages.")
        form.addRow("Minimum similarity", self.similarity)
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
        self.unit.hide()  # state holder; the button row below is what researchers use
        unit_row = QWidget()
        unit_buttons = QGridLayout(unit_row)
        unit_buttons.setContentsMargins(0, 0, 0, 0)
        unit_buttons.setSpacing(4)
        self.unit_group = QButtonGroup(self)
        self.unit_group.setExclusive(True)
        for index, (name, short) in enumerate(UNIT_BUTTONS.items()):
            button = QPushButton(short)
            button.setCheckable(True)
            button.setObjectName("segment")
            button.setToolTip(UNIT_HELP[name])
            button.setChecked(name == unit)
            button.clicked.connect(lambda _checked=False, value=name: self.unit.setCurrentText(value))
            self.unit_group.addButton(button)
            unit_buttons.addWidget(button, index // 4, index % 4)
        self.unit.currentTextChanged.connect(self.sync_unit_buttons)
        form.addRow("Passage size", unit_row)
        self.allow_truncation = QCheckBox("Allow only the start of oversized passages")
        self.allow_truncation.setChecked(self.initial.allow_truncation)
        self.allow_truncation.setToolTip('Keeps the selected passage boundaries, but text beyond the model limit does not contribute to its embedding. This choice is saved with the analysis.')
        form.addRow(self.allow_truncation)
        self.semantic_options = QWidget()
        semantic_row = QFormLayout(self.semantic_options)
        semantic_row.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        semantic_row.setContentsMargins(0, 0, 0, 0)
        self.semantic_threshold = QDoubleSpinBox()
        self.semantic_threshold.setRange(0, 1)
        self.semantic_threshold.setDecimals(2)
        self.semantic_threshold.setSingleStep(0.05)
        self.semantic_threshold.setValue(self.initial.semantic_threshold)
        self.semantic_threshold.setToolTip('Higher values split more often. This is a similarity threshold, not confidence. Compare the preview with Auto.')
        semantic_row.addRow('Boundary sensitivity (experimental)', self.semantic_threshold)
        self.semantic_min_tokens = QSpinBox()
        self.semantic_min_tokens.setRange(0, 4096)
        self.semantic_min_tokens.setSingleStep(32)
        self.semantic_min_tokens.setValue(self.initial.semantic_min_tokens)
        self.semantic_min_tokens.setToolTip('Wait for this many tokens before splitting for a topic change. Recommended: 128. '
                                           'Token limits and short final passages can still produce smaller passages. '
                                           'The target is capped at the model limit. Zero restores unrestricted splitting.')
        semantic_row.addRow('Minimum passage target (tokens)', self.semantic_min_tokens)
        form.addRow(self.semantic_options)
        self._texts = {doc: get_document(self.directory, doc)[1] for doc, _ in documents}
        self.speakers = detect_speakers(self._texts.values())
        self.exclude_interviewer = QCheckBox("Leave out interviewer turns")
        self.exclude_interviewer.setToolTip("Interviewer prompts tend to form their own themes. They stay visible in "
                                            "the documents; they are only left out of theme finding.")
        self.speaker_list = QListWidget()
        self.speaker_list.setAccessibleName("Speaker labels treated as the interviewer")
        self.speaker_list.setMaximumHeight(130)
        saved = {label for label in self.initial.interviewer_labels}
        for row in self.speakers:
            item = QListWidgetItem(f"{row['label']}  ·  {row['documents']} file{'s' if row['documents'] != 1 else ''}"
                                   f" · {row['turns']:,} turns")
            item.setData(Qt.ItemDataRole.UserRole, row["key"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            chosen = row["key"] in saved if saved else row["suggested"]
            item.setCheckState(Qt.CheckState.Checked if chosen else Qt.CheckState.Unchecked)
            self.speaker_list.addItem(item)
        self.speaker_help = QLabel("Tick every label used for the interviewer (e.g. Interviewer, Interviewer1, Q).")
        self.speaker_help.setObjectName("quiet")
        self.speaker_help.setWordWrap(True)
        if self.speakers:
            self.exclude_interviewer.setChecked(self.initial.exclude_interviewer or not saved and
                                                any(row["suggested"] for row in self.speakers))
        else:
            self.exclude_interviewer.setEnabled(False)
            self.exclude_interviewer.setToolTip("No speaker labels (like 'Interviewer1:' or 'P3:') were found.")
            self.speaker_list.hide()
            self.speaker_help.hide()
        self.exclude_interviewer.toggled.connect(self.speaker_list.setEnabled)
        self.exclude_interviewer.toggled.connect(self.inspect)
        self.speaker_list.setEnabled(self.exclude_interviewer.isChecked())
        self.speaker_list.itemChanged.connect(self.speakers_changed)
        form.addRow(self.exclude_interviewer)
        form.addRow(self.speaker_help)
        form.addRow(self.speaker_list)
        self.update_interviewer_count()
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
        self.naming_summary = QLabel()
        self.naming_summary.setWordWrap(True)
        form.addRow(self.naming_summary)
        layout.addLayout(form)
        self.advanced_toggle = QCheckBox("Advanced analysis and local naming setup")
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.minimum = QSpinBox()
        self.minimum.setRange(2, 10000)
        self.minimum.setValue(self.initial.min_topic_size)
        self.minimum.setToolTip("Minimum passages supporting a discovered group; changing this overrides the style default.")
        advanced.addRow("Minimum evidence (passages)", self.minimum)
        self.ignored = QPlainTextEdit()
        self.ignored.setTabChangesFocus(True)
        self.ignored.setAccessibleName('Words to ignore')
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
        advanced.addRow("Optional naming model", self.model)
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
        self.method.currentIndexChanged.connect(self.update_validation)
        self.topic_editor.textChanged.connect(self.update_validation)
        self.similarity.valueChanged.connect(self.update_validation)
        self.style.currentTextChanged.connect(self.style_changed)
        self.unit.currentTextChanged.connect(self.inspect)
        for signal in [self.number_mode.currentIndexChanged, self.number.valueChanged,
                       self.minimum.valueChanged, self.ignored.textChanged, self.phrases.currentIndexChanged,
                       self.auto_names.toggled, self.model.currentIndexChanged]:
            signal.connect(self.update_validation)
        self.update_validation()
        self.inspect()
        self.allow_truncation.toggled.connect(self.update_validation)
        self.semantic_threshold.valueChanged.connect(self.inspect)
        self.semantic_min_tokens.valueChanged.connect(self.inspect)

    def settings(self):
        # Preserve the configured internal embedding identity and execution seed.
        """AnalysisSettings built from the current widgets (embedding model and seed keep their defaults)."""
        base = preset_settings(self.style.currentText())
        return replace(base, analysis_method=self.method.currentData(),
            topic_definitions=parse_topic_definitions(self.topic_editor.toPlainText()) if self.method.currentData() != "general" else (),
            zeroshot_min_similarity=self.similarity.value(), embedding_model=self.initial.embedding_model,
            embedding_revision=self.initial.embedding_revision, seed=self.initial.seed,
            batch_size=self.initial.batch_size, min_topic_size=self.minimum.value(),
            allow_truncation=self.allow_truncation.isChecked() and self.unit.currentText() not in TOKEN_SAFE_UNITS,
            semantic_threshold=self.semantic_threshold.value(),
            semantic_min_tokens=self.semantic_min_tokens.value(),
            ignored_words=parse_ignored_words(self.ignored.toPlainText()),
            ngram_range=(1, 1) if self.phrases.currentIndex() else (1, 2),
            requested_theme_count=self.number.value() if self.number_mode.currentIndex() else None,
            name_themes=self.auto_names.isChecked(), ollama_model=self.model.currentData(),
            exclude_interviewer=self.exclude_interviewer.isChecked() and bool(self.chosen_labels()),
            interviewer_labels=tuple(self.chosen_labels()))

    def style_changed(self, _style):
        self.minimum.setValue(PRESETS[self.style.currentText()]["min_topic_size"])
        self.update_validation()

    def restore_defaults(self):
        self.initial = AnalysisSettings()
        self.method.setCurrentIndex(0)
        self.topic_editor.clear()
        self.similarity.setValue(0.7)
        self.style.setCurrentText("Balanced")
        self.minimum.setValue(5)
        self.unit.setCurrentText("Automatic passages")
        self.allow_truncation.setChecked(False)
        self.semantic_threshold.setValue(0.65)
        self.semantic_min_tokens.setValue(128)
        for index in range(self.speaker_list.count()):
            item = self.speaker_list.item(index)
            suggested = self.speakers[index]["suggested"]
            item.setCheckState(Qt.CheckState.Checked if suggested else Qt.CheckState.Unchecked)
        self.exclude_interviewer.setChecked(any(row["suggested"] for row in self.speakers))
        self.number_mode.setCurrentIndex(0)
        self.number.setValue(5)
        self.ignored.clear()
        self.phrases.setCurrentIndex(0)
        self.auto_names.setChecked(False)
        self.model.setCurrentIndex(0)
        self.update_validation()

    def inspect(self, _unit=None):
        if self._reject_requested:
            return
        self.summary = None
        self.work.setText('Finding experimental semantic boundaries with the local model…' if self.unit.currentText() == 'Semantic passages'
                          else 'Counting selected passages…')
        if self._inspection:
            self._inspection_restart = True
            self._inspection.requestInterruption()
            self.update_validation()
            return
        self._inspection = InspectionWorker(self.directory, [doc for doc, _ in self.documents], self.unit.currentText(), self,
                                            exclude_interviewer=self.excluding(), labels=self.chosen_labels(),
                                            settings=replace(self.initial, semantic_threshold=self.semantic_threshold.value(),
                                                             semantic_min_tokens=self.semantic_min_tokens.value()))
        self._inspection.result.connect(self.inspected)
        self._inspection.failed.connect(lambda message: self.validation.setText(message))
        self._inspection.finished.connect(self.inspection_finished)
        self.update_validation()
        self._inspection.start()

    def inspected(self, unit, summary):
        if (unit == self.unit.currentText() and summary["exclude_interviewer"] == self.excluding()
                and summary["labels"] == tuple(self.chosen_labels())
                and (unit != 'Semantic passages' or (
                    summary.get('segmentation', {}).get('semantic_threshold') == self.semantic_threshold.value()
                    and summary.get('segmentation', {}).get('semantic_min_tokens') == self.semantic_min_tokens.value()))):
            self.summary = summary
            self.passages = summary["passages"]
            self.work.setText(f"{summary['count']:,} passages · {summary['average']} words on average · "
                              f"{summary['duplicates']} duplicates")
            detail = summary.get('segmentation', {})
            if 'max_tokens' in detail:
                self.work.setText(self.work.text() + f"\n{detail['smallest_tokens']}–{detail['largest_tokens']} tokens per passage "
                                  f"(model limit {detail['max_tokens']}); {detail['oversized']} oversized; "
                                  f"{detail['forced_splits']} extra splits within sentences.")
            elif detail.get('token_error'):
                self.work.setText(self.work.text() + '\nToken sizes unavailable: ' + detail['token_error'])
            if unit == 'Interview passages':
                self.work.setText(self.work.text() + f"\n{detail.get('speaker_turns', 0)} detected speaker turns. "
                                  f"{detail.get('speaker_fallbacks', 0)} document(s) used Auto because speaker boundaries were uncertain; "
                                  "all text was retained for those documents, including interviewer text.")
                names = dict(self.documents)
                fallback_names = [names.get(doc, doc) for doc in detail.get('fallback_document_ids', [])]
                if fallback_names:
                    self.work.setText(self.work.text() + '\nAuto fallback: ' + ', '.join(fallback_names[:3])
                                      + (f' (+{len(fallback_names) - 3} more)' if len(fallback_names) > 3 else ''))
                self.work.setToolTip('\n'.join(fallback_names))
            else:
                self.work.setToolTip('')
            if detail.get('experimental'):
                self.work.setText(self.work.text() + f"\nMinimum target: {detail.get('effective_semantic_min_tokens', detail.get('semantic_min_tokens'))} tokens; "
                                  f"avoided {detail.get('suppressed_semantic_breaks', 0)} short-passage splits.")
                self.work.setText(self.work.text() + f"\nExperimental: {detail.get('semantic_breaks', 0)} similarity boundaries; "
                                  f"{detail.get('token_limit_breaks', 0)} size boundaries. "
                                  f"Reused {detail.get('sentence_vectors_reused', 0)} sentence vectors; "
                                  f"computed {detail.get('sentence_vectors_computed', 0)}. Compare with Auto before choosing.")
            self.render_preview()

    # Passage preview ----------------------------------------------------------
    def build_preview_pane(self):
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(8, 0, 0, 0)
        header = QGridLayout()
        title = QLabel("Passage preview")
        title.setObjectName("setupName")
        title.setWordWrap(True)
        header.addWidget(title, 0, 0)
        self.preview_doc = QComboBox()
        self.preview_doc.setAccessibleName("Document to preview")
        for doc, name in self.documents:
            self.preview_doc.addItem(name, doc)
        self.preview_doc.currentIndexChanged.connect(lambda _i: self.render_preview())
        header.addWidget(self.preview_doc, 0, 1, 1, 2)
        self.preview_position = QLabel("")
        self.preview_position.setObjectName("quiet")
        self.preview_position.setWordWrap(True)
        header.addWidget(self.preview_position, 1, 0)
        header.setColumnStretch(0, 1)
        previous = QPushButton("‹")
        previous.setAccessibleName("Previous passage")
        previous.setMinimumWidth(36)
        previous.clicked.connect(lambda: self.step_preview(-1))
        following = QPushButton("›")
        following.setAccessibleName("Next passage")
        following.setMinimumWidth(36)
        following.clicked.connect(lambda: self.step_preview(1))
        header.addWidget(previous, 1, 1)
        header.addWidget(following, 1, 2)
        layout.addLayout(header)
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setObjectName("passagePreview")
        layout.addWidget(self.preview, 1)
        note = QLabel("Shaded bands are passages. Grey text is left out of theme finding. "
                      "The same split is used when you click Analyze.")
        note.setObjectName("quiet")
        note.setWordWrap(True)
        layout.addWidget(note)
        return pane

    def chosen_labels(self):
        return [self.speaker_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.speaker_list.count())
                if self.speaker_list.item(i).checkState() == Qt.CheckState.Checked]

    def excluding(self):
        return self.exclude_interviewer.isChecked() and bool(self.chosen_labels())

    def update_interviewer_count(self):
        labels = self.chosen_labels()
        self.interviewer_turns = count_interviewer_turns(self._texts.values(), labels) if labels else 0
        if self.speakers:
            self.exclude_interviewer.setText(f"Leave out interviewer turns ({self.interviewer_turns:,} turns)"
                                             if labels else "Leave out interviewer turns (choose labels below)")

    def speakers_changed(self, _item=None):
        self.update_interviewer_count()
        self.inspect()

    def sync_unit_buttons(self, unit):
        for button in self.unit_group.buttons():
            button.setChecked(button.toolTip() == UNIT_HELP.get(unit))

    def preview_spans(self):
        doc = self.preview_doc.currentData()
        return [p for p in self.passages if p.document_id == doc]

    def render_preview(self):
        doc = self.preview_doc.currentData()
        if not doc or not hasattr(self, "speaker_list"):
            return
        _name, text, _spans = get_document(self.directory, doc)
        self._preview_coords = TextCoordinates.build(text)
        self._preview_text = text
        self.preview.setPlainText(self._preview_coords.display_text)
        spans = self.preview_spans()
        self.preview_index = 0 if spans else -1
        self.paint_preview()

    def paint_preview(self):
        spans = self.preview_spans()
        coords = getattr(self, "_preview_coords", None)
        if coords is None:
            return
        selections = []

        def band(start, end, colour=None, foreground=None, italic=False):
            q_start, q_end = coords.qt_range(start, end)
            cursor = self.preview.textCursor()
            cursor.setPosition(q_start)
            cursor.setPosition(q_end, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            fmt = QTextCharFormat()
            if colour:
                fmt.setBackground(QColor(colour))
            if foreground:
                fmt.setForeground(QColor(foreground))
            fmt.setFontItalic(italic)
            selection.format = fmt
            selections.append(selection)

        for index, passage in enumerate(spans):
            band(passage.start, passage.end, "#c9e7e3" if index == self.preview_index else
                 ("#eef3f6" if index % 2 else "#f7f9fa"))
        fallback_ids = (self.summary or {}).get('segmentation', {}).get('fallback_document_ids', [])
        if self.excluding() and self.preview_doc.currentData() not in fallback_ids:
            for start, end in interviewer_ranges(self._preview_text, self.chosen_labels()):
                band(start, end, foreground="#8a97a6", italic=True)
        self.preview.setExtraSelections(selections)
        if 0 <= self.preview_index < len(spans):
            q_start, _ = coords.qt_range(spans[self.preview_index].start, spans[self.preview_index].end)
            cursor = self.preview.textCursor()
            cursor.setPosition(q_start)
            self.preview.setTextCursor(cursor)
            self.preview.ensureCursorVisible()
            words = len(spans[self.preview_index].text.split())
            self.preview_position.setText(f"Passage {self.preview_index + 1} of {len(spans)} · {words} words")
        else:
            self.preview_position.setText("No passages in this document")

    def step_preview(self, direction):
        spans = self.preview_spans()
        if spans:
            self.preview_index = (self.preview_index + direction) % len(spans)
            self.paint_preview()

    def inspection_finished(self):
        worker = self._inspection
        if worker is None:
            return
        worker.wait()
        worker.deleteLater()
        self._inspection = None
        if self._reject_requested:
            self.finish_reject()
        elif (self._inspection_restart or worker.unit != self.unit.currentText()
              or worker.exclude_interviewer != self.excluding() or worker.labels != tuple(self.chosen_labels())):
            self._inspection_restart = False
            self.inspect()
        else:
            self.update_validation()

    def update_validation(self, *_args):
        self.naming_summary.setText(
            'Topic finding uses the bundled model. Optional theme naming is off.' if not self.auto_names.isChecked() else
            ('Optional naming model: ' + str(self.model.currentData()) + '. Change it in Advanced setup.'
             if self.model.currentData() else 'No optional naming model selected. Themes will use keywords; choose a model in Advanced setup if desired.'))
        self.semantic_options.setVisible(self.unit.currentText() == 'Semantic passages')
        self.allow_truncation.setVisible(self.unit.currentText() not in TOKEN_SAFE_UNITS)
        method = self.method.currentData()
        self.topic_editor.setEnabled(method != "general")
        self.similarity.setEnabled(method == "zero_shot")
        self.number_mode.setEnabled(method != "zero_shot")
        if method == "zero_shot":
            self.number_mode.setCurrentIndex(0)
        help_text = {
            "general": "Discover recurring themes without predefined topics.",
            "guided": "Steer discovery with comma-separated seed words or phrases. One line: Healthcare access | clinic, medication, insurance",
            "zero_shot": "Assign each passage to its best matching topic above the similarity threshold, then discover other groups. Supplied names are preserved. One line: Healthcare access | Barriers to obtaining treatment and medication",
        }
        self.method_help.setText(help_text[method])
        self.topic_editor.setPlaceholderText("Topic name | seed words or descriptive matching text (one topic per line)")
        self.style_help.setText(STYLE_HELP[self.style.currentText()])
        self.unit_help.setText(UNIT_HELP[self.unit.currentText()])
        self.number.setEnabled(bool(self.number_mode.currentIndex()))
        valid = False
        try:
            settings = self.settings()
            if self.summary is not None:
                if self.summary.get('segmentation', {}).get('oversized', 0) and not settings.allow_truncation:
                    raise ValueError('Some passages exceed the model limit. Choose Auto, or explicitly allow truncation above.')
                validate_settings(settings, self.summary["count"])
                ready = ("Ready. Naming will use keyword fallback if the selected local model is unavailable."
                         if settings.name_themes else "Ready to analyze.")
                warning = small_corpus_warning(settings, self.summary["count"])
                if self.summary['count'] > 5000:
                    warning = (warning + '\n' if warning else '') + 'Large analysis: more than 5,000 passages can be slow. '
                    warning += 'For Semantic, increase the minimum passage target or lower sensitivity; compare Auto, or select fewer documents.'
                if settings.name_themes:
                    warning = (warning + '\n' if warning else '') + 'Automatic naming is skipped above 50 topics. Saved keyword names remain available.'
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
        if index < 0:
            from qualitative_themes.analysis.settings import DEFAULT_NAMING_MODEL
            index = self.model.findData(DEFAULT_NAMING_MODEL)
        self.model.setCurrentIndex(index if index >= 0 else (1 if models else 0))
        self.model_status.setText(f"{len(models)} local model(s) found.")

    def models_finished(self):
        if self._models_worker is None:
            return
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
        """Stop both workers and wait for them before closing (see class docstring)."""
        self._reject_requested = True
        # A caller may delete the dialog immediately after reject(). Never leave
        # parent-owned QThreads running when returning from this method.
        for worker in (self._inspection, self._models_worker):
            if worker:
                worker.requestInterruption()
                worker.wait()
        if self._inspection:
            self.inspection_finished()
        if self._models_worker:
            self.models_finished()
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
