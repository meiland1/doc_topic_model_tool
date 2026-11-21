"""Beginner setup with asynchronous corpus inspection and local model discovery."""
from dataclasses import replace
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget)

from qualitative_themes.analysis.segment import DEFAULT_UNIT, TOKEN_SAFE_UNITS, UNITS
from qualitative_themes.analysis.settings import (STYLE_HELP, AnalysisSettings,
    ANALYSIS_METHODS, parse_topic_definitions, preset_settings, validate_settings, small_corpus_warning)
from qualitative_themes.analysis.speakers import count_interviewer_turns, detect_speakers
from qualitative_themes.storage.documents import get_document

UNIT_BUTTONS = {"Automatic passages": "Automatic (recommended)", "Interview passages": "By speaker (interviews)",
                "Paragraphs": "By paragraph", "Whole document": "Whole document"}


UNIT_HELP = {
    "Automatic passages": "Keeps sentences and paragraphs together, and keeps passages short enough for the tool to read in full.",
    "Interview passages": ("Each speaker turn becomes its own passage; very long answers are split. "
                           "Where speaker labels aren't clear, Automatic is used and all text is kept; check the preview."),
    "Paragraphs": "Each paragraph becomes one passage, however long or short it is.",
    "Whole document": "Each document is one passage. Smaller ideas can get lost, and long documents may be cut short.",
}


from qualitative_themes.gui import strings
from qualitative_themes.gui.passage_preview import PassagePreviewMixin
from qualitative_themes.gui.settings_form import (DiscoveryOptions, KeywordOptions, naming_model_choice,
                                                  refill_naming_models)
from qualitative_themes.gui.workers import InspectionWorker, ModelListWorker

NO_MODEL_LABEL = "Don't name them (use keywords)"

# Theme size choices: (stored preset name, what the researcher sees).
THEME_SIZES = [("Broad", "Fewer, broader"), ("Balanced", "Balanced"),
               ("Detailed", "More, specific"), ("Exploratory", "Many small (exploratory)")]


class AnalysisSetupDialog(PassagePreviewMixin, QDialog):
    """Choose method, passage size, style and naming before an analysis.

    Usage: exec() it, then read settings() and unit.currentText() if accepted.
    OK stays disabled until the inspection says the setup can run.
    Closing/rejecting waits for any running worker first, so the caller can
    deleteLater() the dialog straight away.
    """
    def __init__(self, directory, documents, settings=None, unit=DEFAULT_UNIT, models=(), parent=None):
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
        self.setWindowTitle(strings.FIND_THEMES)
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
        layout.addWidget(QLabel("Themes are found across these documents together"))
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
        form.addRow("How should themes be found?", self.method)
        self.method_help = QLabel()
        self.method_help.setWordWrap(True)
        form.addRow(self.method_help)
        self.topic_editor = QPlainTextEdit()
        self.topic_editor.setTabChangesFocus(True)
        self.topic_editor.setAccessibleName('Your themes')
        self.topic_editor.setMaximumHeight(130)
        self.topic_editor.setPlainText("\n".join(f"{name} | {text}" for name, text in self.initial.topic_definitions))
        form.addRow("Your themes", self.topic_editor)
        self.similarity = QDoubleSpinBox()
        self.similarity.setRange(0, 1)
        self.similarity.setDecimals(2)
        self.similarity.setSingleStep(0.05)
        self.similarity.setValue(self.initial.zeroshot_min_similarity)
        self.similarity.setToolTip("Higher = only close matches. Lower = more passages, some less related. "
                                   "Try a few values and check the results.")
        form.addRow("How closely a passage must match", self.similarity)
        self.style = QComboBox()
        for key, label in THEME_SIZES:
            self.style.addItem(label, key)
        self.set_style(self.initial.analysis_style)
        form.addRow("Theme size", self.style)
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
        form.addRow("How to split documents", unit_row)
        self.allow_truncation = QCheckBox("Allow very long passages (only the beginning is used)")
        self.allow_truncation.setChecked(self.initial.allow_truncation)
        self.allow_truncation.setToolTip("Keeps your passages as they are, but the tool only reads the first part of a very "
                                         "long one when finding themes. This choice is saved with the analysis.")
        form.addRow(self.allow_truncation)
        self._texts = {doc: get_document(self.directory, doc)[1] for doc, _ in documents}
        self.speakers = detect_speakers(self._texts.values())
        self.exclude_interviewer = QCheckBox("Ignore the interviewer's questions")
        self.exclude_interviewer.setToolTip("Interviewer questions tend to form their own themes. They stay visible in "
                                            "the documents; they're only left out when finding themes.")
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
        self.speaker_help = QLabel("Which speaker labels are the interviewer? (e.g. Interviewer, Q)")
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
        self.number_mode.addItems(["Let the tool decide", "About N"])
        self.number_mode.setCurrentIndex(int(self.initial.requested_theme_count is not None))
        form.addRow("Number of themes", self.number_mode)
        self.number = QSpinBox()
        self.number.setRange(1, 1000)
        self.number.setValue(self.initial.requested_theme_count or 5)
        form.addRow("About how many", self.number)
        self.cap = QSpinBox()
        self.cap.setRange(1, 1000)
        self.cap.setValue(self.initial.max_themes)
        self.cap.setPrefix("Up to ")
        self.cap.setSuffix(" themes")
        self.cap.setAccessibleName("Most themes")
        form.addRow("At most", self.cap)
        explanation = QLabel("Similar themes are combined until there are about N, and never more than the "
                             "limit (\"Not in any theme\" isn't counted). The tool can't make more themes than it "
                             "finds; for more, smaller themes choose a smaller theme size.")
        explanation.setWordWrap(True)
        explanation.setObjectName("quiet")
        form.addRow(explanation)
        self.auto_names = QCheckBox("Give themes readable names (AI, on this computer)")
        self.auto_names.setChecked(self.initial.name_themes)
        form.addRow(self.auto_names)
        self.naming_summary = QLabel()
        self.naming_summary.setWordWrap(True)
        form.addRow(self.naming_summary)
        layout.addLayout(form)
        self.advanced_toggle = QCheckBox("More options")
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced = QFormLayout(self.advanced)
        advanced.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.discovery = DiscoveryOptions(advanced, self.initial)
        self.minimum = self.discovery.minimum
        self.keywords = KeywordOptions(advanced, self.initial)
        self.ignored, self.phrases = self.keywords.ignored, self.keywords.phrases
        self.model = naming_model_choice(models, self.initial.ollama_model, fallback_label=NO_MODEL_LABEL,
                                         unverified_suffix=" (refresh to verify)")
        advanced.addRow("Name themes with", self.model)
        self.refresh_models = QPushButton("Refresh list")
        self.refresh_models.clicked.connect(self.fetch_models)
        advanced.addRow(self.refresh_models)
        self.model_status = QLabel("If naming fails, themes keep keyword names.")
        self.model_status.setWordWrap(True)
        advanced.addRow(self.model_status)
        self.discovery.add_technical_rows(advanced)
        self.advanced.setVisible(False)
        self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)
        defaults = QPushButton("Reset to recommended")
        defaults.clicked.connect(self.restore_defaults)
        layout.addWidget(defaults)
        self.work = QLabel("Counting selected passages…")
        self.work.setWordWrap(True)
        layout.addWidget(self.work)
        self.validation = QLabel()
        self.validation.setWordWrap(True)
        layout.addWidget(self.validation)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(strings.FIND_THEMES)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.method.currentIndexChanged.connect(self.update_validation)
        self.topic_editor.textChanged.connect(self.update_validation)
        self.similarity.valueChanged.connect(self.update_validation)
        self.style.currentTextChanged.connect(self.style_changed)
        self.unit.currentTextChanged.connect(self.inspect)
        for signal in [self.number_mode.currentIndexChanged, self.number.valueChanged, self.cap.valueChanged,
                       *self.discovery.changed_signals(), *self.keywords.changed_signals(),
                       self.auto_names.toggled, self.model.currentIndexChanged]:
            signal.connect(self.update_validation)
        self.update_validation()
        self.inspect()
        self.allow_truncation.toggled.connect(self.update_validation)

    def settings(self):
        """AnalysisSettings built from the current widgets (the embedding model keeps its configured identity)."""
        base = preset_settings(self.style_key())
        return replace(base, analysis_method=self.method.currentData(),
            topic_definitions=parse_topic_definitions(self.topic_editor.toPlainText()) if self.method.currentData() != "general" else (),
            zeroshot_min_similarity=self.similarity.value(), embedding_model=self.initial.embedding_model,
            embedding_revision=self.initial.embedding_revision, batch_size=self.initial.batch_size,
            **self.discovery.values(self.style_key()), max_themes=self.cap.value(),
            allow_truncation=self.allow_truncation.isChecked() and self.unit.currentText() not in TOKEN_SAFE_UNITS,
            **self.keywords.values(),
            requested_theme_count=self.number.value() if self.number_mode.currentIndex() else None,
            name_themes=self.auto_names.isChecked(), ollama_model=self.model.currentData(),
            exclude_interviewer=self.exclude_interviewer.isChecked() and bool(self.chosen_labels()),
            interviewer_labels=tuple(self.chosen_labels()))

    def style_key(self):
        """The stored preset name (Broad/Balanced/Detailed/Exploratory) for the chosen theme size."""
        return self.style.currentData()

    def set_style(self, key):
        self.style.setCurrentIndex(max(0, self.style.findData(key)))

    def style_changed(self, _style):
        self.discovery.style_changed(self.style_key())
        self.update_validation()

    def restore_defaults(self):
        self.initial = AnalysisSettings()
        self.method.setCurrentIndex(0)
        self.topic_editor.clear()
        self.similarity.setValue(0.7)
        self.set_style("Balanced")
        self.discovery.reset("Balanced")
        self.cap.setValue(AnalysisSettings().max_themes)
        self.unit.setCurrentText("Automatic passages")
        self.allow_truncation.setChecked(False)
        for index in range(self.speaker_list.count()):
            item = self.speaker_list.item(index)
            suggested = self.speakers[index]["suggested"]
            item.setCheckState(Qt.CheckState.Checked if suggested else Qt.CheckState.Unchecked)
        self.exclude_interviewer.setChecked(any(row["suggested"] for row in self.speakers))
        self.number_mode.setCurrentIndex(0)
        self.number.setValue(5)
        self.keywords.reset()
        self.auto_names.setChecked(False)
        self.model.setCurrentIndex(0)
        self.update_validation()

    def inspect(self, _unit=None):
        if self._reject_requested:
            return
        self.summary = None
        self.work.setText('Counting selected passages…')
        if self._inspection:
            self._inspection_restart = True
            self._inspection.requestInterruption()
            self.update_validation()
            return
        self._inspection = InspectionWorker(self.directory, [doc for doc, _ in self.documents], self.unit.currentText(), self,
                                            exclude_interviewer=self.excluding(), labels=self.chosen_labels(),
                                            settings=self.initial)
        self._inspection.result.connect(self.inspected)
        self._inspection.failed.connect(lambda message: self.validation.setText(message))
        self._inspection.finished.connect(self.inspection_finished)
        self.update_validation()
        self._inspection.start()

    def inspected(self, unit, summary):
        if (unit == self.unit.currentText() and summary["exclude_interviewer"] == self.excluding()
                and summary["labels"] == tuple(self.chosen_labels())):
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
            self.render_preview()

    def chosen_labels(self):
        return [self.speaker_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.speaker_list.count())
                if self.speaker_list.item(i).checkState() == Qt.CheckState.Checked]

    def excluding(self):
        return self.exclude_interviewer.isChecked() and bool(self.chosen_labels())

    def update_interviewer_count(self):
        labels = self.chosen_labels()
        self.interviewer_turns = count_interviewer_turns(self._texts.values(), labels) if labels else 0
        if self.speakers:
            self.exclude_interviewer.setText(f"Ignore the interviewer's questions ({self.interviewer_turns:,} turns)"
                                             if labels else "Ignore the interviewer's questions (choose labels below)")

    def speakers_changed(self, _item=None):
        self.update_interviewer_count()
        self.inspect()

    def sync_unit_buttons(self, unit):
        for button in self.unit_group.buttons():
            button.setChecked(button.toolTip() == UNIT_HELP.get(unit))

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
            'Themes will keep keyword names.' if not self.auto_names.isChecked() else
            ('Naming with ' + str(self.model.currentData()) + '. Change it under More options.'
             if self.model.currentData() else 'No naming model chosen, so themes will keep keyword names. Choose one under More options.'))
        self.allow_truncation.setVisible(self.unit.currentText() not in TOKEN_SAFE_UNITS)
        method = self.method.currentData()
        self.topic_editor.setEnabled(method != "general")
        self.similarity.setEnabled(method == "zero_shot")
        self.number_mode.setEnabled(method != "zero_shot")
        self.cap.setEnabled(method != "zero_shot")
        if method == "zero_shot":
            self.number_mode.setCurrentIndex(0)
        help_text = {
            "general": "Best for a first look at your data.",
            "guided": ("You list words for themes you expect; the tool still finds others. "
                       "For example: Healthcare access | clinic, medication, insurance"),
            "zero_shot": ("Like working from a codebook: each passage goes to the theme it matches best, then the tool "
                          "looks for other themes in what's left. Your theme names are kept. "
                          "For example: Healthcare access | Barriers to getting treatment and medication"),
        }
        self.method_help.setText(help_text[method])
        self.topic_editor.setPlaceholderText("One theme per line:  Theme name | words or a short description")
        self.style_help.setText(STYLE_HELP[self.style_key()])
        self.unit_help.setText(UNIT_HELP[self.unit.currentText()])
        self.number.setEnabled(bool(self.number_mode.currentIndex()))
        valid = False
        try:
            settings = self.settings()
            if self.summary is not None:
                if self.summary.get('segmentation', {}).get('oversized', 0) and not settings.allow_truncation:
                    raise ValueError('Some passages are too long for the tool to read in full. Choose Automatic, '
                                     'or tick "Allow very long passages" above.')
                validate_settings(settings, self.summary["count"])
                ready = ("Ready. If naming fails, themes keep keyword names."
                         if settings.name_themes else "Ready to find themes.")
                warning = small_corpus_warning(settings, self.summary["count"])
                if self.summary['count'] > 5000:
                    warning = (warning + '\n' if warning else '') + 'More than 5,000 passages can be slow. '
                    warning += 'Select fewer documents, or choose Paragraphs.'
                if settings.name_themes and settings.max_themes > 100:
                    warning = (warning + '\n' if warning else '') + ('Naming more than 100 themes can take a while; '
                                                                     'you\'ll see a time estimate and can cancel.')
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
        self._models_worker.failed.connect(lambda message: self.model_status.setText("The naming engine isn't available; themes will keep keyword names. " + message))
        self._models_worker.finished.connect(self.models_finished)
        self.refresh_models.setEnabled(False)
        self.model_status.setText("Checking which naming models are installed…")
        self.update_validation()
        self._models_worker.start()

    def models_ready(self, models):
        refill_naming_models(self.model, [row["name"] for row in models], fallback_label=NO_MODEL_LABEL)
        self.model_status.setText(f"{len(models)} naming model(s) installed.")

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
