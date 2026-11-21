"""Refinement choices and explicit identity review in the research workspace."""
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QLabel, QMessageBox, QPlainTextEdit, QScrollArea, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from qualitative_themes.analysis.refinement import (
    OPERATIONS, apply_proposal, cancel_proposal, propose_refinement, review_proposal, proposal_evidence,
)
from qualitative_themes.analysis.topics import AnalysisCancelled


from qualitative_themes.gui.worker import WorkerThread
from qualitative_themes.gui.settings_form import KeywordOptions, naming_model_choice
from qualitative_themes.gui import strings


class RefinementWorker(WorkerThread):
    """Build a refinement proposal in the background.

    If cancelled after the proposal was created, the proposal is cancelled too
    so nothing half-reviewed is left behind.
    """
    progress = Signal(str)
    result = Signal(str, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, directory, source_id, operation, settings, unit, parent=None):
        super().__init__(parent)
        self.directory, self.source_id, self.operation = directory, source_id, operation
        self.settings, self.unit = settings, unit
        self.run_id = None

    def run(self):
        try:
            proposal, count = propose_refinement(self.directory, self.source_id, self.operation,
                self.settings, self.unit, self.progress.emit, self.isInterruptionRequested)
            if self.isInterruptionRequested():
                cancel_proposal(self.directory, proposal)
                raise AnalysisCancelled()
            self.result.emit(proposal, count)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class RefinementSetupDialog(QDialog):
    """Choose a refinement operation and its options."""
    def __init__(self, source, models=(), parent=None, operation=None):
        super().__init__(parent)
        self.source = source
        self.setWindowTitle(strings.ADJUST_THEMES)
        self.resize(620, 520)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.operation = QComboBox()
        for key, (title, _) in OPERATIONS.items():
            self.operation.addItem(title, key)
        if operation:
            self.operation.setCurrentIndex(self.operation.findData(operation))
        form.addRow("What would you like to change?", self.operation)
        self.consequences = QLabel()
        self.consequences.setWordWrap(True)
        form.addRow(self.consequences)
        self.target = QSpinBox()
        self.theme_count = len(set(source["output"].assignments) - {-1})
        self.target.setRange(1, max(1, self.theme_count - 1))
        self.target.setValue(max(1, self.theme_count - 1))
        form.addRow('Combine into this many themes (not counting "Not in any theme")', self.target)
        self.keywords = KeywordOptions(form, source["settings"], tooltips=False)
        self.ignored, self.phrases = self.keywords.ignored, self.keywords.phrases
        self.model = naming_model_choice(models, source["settings"].ollama_model, fallback_label="Don't name them")
        form.addRow("AI naming model", self.model)
        self.auto_names = QCheckBox("Give the new themes readable names")
        form.addRow(self.auto_names)
        outer.addLayout(form)
        note = QLabel("The changes are saved separately for you to review. Applying them switches to the adjusted "
                      "analysis; cancelling keeps your current one and all your coding. To change how documents are "
                      "split, use Find themes again.")
        note.setWordWrap(True)
        outer.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Prepare changes")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        self.operation.currentIndexChanged.connect(self.update_options)
        self.auto_names.toggled.connect(self.update_options)
        self.update_options()

    def update_options(self, _value=None):
        operation = self.operation.currentData()
        self.consequences.setText(OPERATIONS[operation][1])
        blocked = operation == "reduce" and self.source["settings"].analysis_method == "zero_shot"
        if blocked:
            self.consequences.setText("Combining themes is turned off when you started from your own list of themes, "
                                      "so your themes stay as you wrote them. Merge themes yourself in Read & code instead.")
        self.target.setEnabled(operation == "reduce" and not blocked)
        self.keywords.set_enabled(operation in {"words", "reduce"})
        self.auto_names.setEnabled(operation in {"words", "reduce"})
        self.model.setEnabled(operation == "names" or
                              (operation in {"words", "reduce"} and self.auto_names.isChecked()))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not blocked and (operation != "reduce" or self.theme_count >= 2))

    def settings(self):
        operation = self.operation.currentData()
        changes = {"name_themes": operation == "names" or self.auto_names.isChecked(),
                   "ollama_model": self.model.currentData()}
        if operation in {"words", "reduce"}:
            changes.update(self.keywords.values())
        if operation == "reduce":
            changes["requested_theme_count"] = self.target.value()
        return replace(self.source["settings"], **changes)


class RefinementReviewDialog(QDialog):
    """Map each proposed theme to an existing theme identity (or a new one), then Apply."""
    def __init__(self, directory, proposal_id, parent=None):
        super().__init__(parent)
        self.directory, self.proposal_id = directory, proposal_id
        self.applied = False
        self.review = review_proposal(directory, proposal_id)
        self.setWindowTitle("Review the changes")
        self.resize(920, 720)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        review = self.review
        summary = QLabel(f"{OPERATIONS[review['operation']][0]}\n"
            f"Themes: {review['old_theme_count']} → {review['new_theme_count']} · "
            f"Not in any theme: {review['old_uncategorized']} → {review['new_uncategorized']} passages\n"
            f"Exact shared passages: {review['shared_passages']} · "
            f"New passage ranges: {review['new_passages']} · Earlier ranges absent: {review['retired_passages']}\n"
            f"With recommended identities, {review['changed_memberships']} shared passages change theme. "
            "Changing identity choices below also changes which decisions carry forward.")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        help_text = QLabel("Each row is an adjusted theme. Match it to an earlier theme to carry that theme's name, "
            "comments, coding and rejected passages forward, or keep it as a new theme. Each earlier theme can be "
            "matched once. Names you gave themes are never replaced. Nothing you've done is lost: an earlier theme "
            "that isn't matched stays in its original analysis.")
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        self.table = QTableWidget(len(review["themes"]), 4)
        self.table.setHorizontalHeaderLabels(["Adjusted theme", "Passages", "Match to earlier theme", "Shared passages"])
        self.choices = {}
        for row, item in enumerate(review["themes"]):
            for col, text in [(0, item["label"]), (1, str(item["count"]))]:
                cell = QTableWidgetItem(text)
                from PySide6.QtCore import Qt
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, col, cell)
            combo = QComboBox()
            combo.addItem("Keep as a new theme", None)
            for index, candidate in enumerate(item["candidates"]):
                combo.addItem(f"{index+1}. {candidate['label']} · {candidate['shared']} shared", candidate["theme_id"])
            if item["recommended"]:
                combo.setCurrentIndex(combo.findData(item["recommended"]))
            combo.setMinimumWidth(265)
            self.table.setCellWidget(row, 2, combo)
            self.choices[item["theme_id"]] = combo
            text = "Identical passage group" if item["recommended"] else ("Combined/split group — review" if item["ambiguous"] else "No exact shared evidence")
            cell = QTableWidgetItem(text)
            cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 3, cell)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.currentCellChanged.connect(self.show_evidence)
        self.table.resizeColumnsToContents()
        self.table.setMinimumHeight(210)
        layout.addWidget(self.table)
        layout.addWidget(QLabel("Click a theme to see its passages and which old themes they came from."))
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setMinimumHeight(150)
        layout.addWidget(self.evidence)
        self.identity_summary = QLabel()
        self.identity_summary.setWordWrap(True)
        layout.addWidget(self.identity_summary)
        decisions = QLabel("Research decisions in earlier themes:\n" + ("\n".join(
            f"• {item['label']}: {item['codings']} coding decisions, {item['comments']} comments" for item in review["human_decisions"])
            or "No theme-specific human decisions recorded.") +
            "\nDifferent passage sizes do not relocate exact-range rejections; earlier decisions stay saved for inspection.")
        decisions.setWordWrap(True)
        layout.addWidget(decisions)
        self.confirm = QCheckBox("I've checked the changes")
        layout.addWidget(self.confirm)
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        for combo in self.choices.values():
            combo.currentIndexChanged.connect(self.update_summary)
        self.confirm.toggled.connect(self.update_summary)
        self.update_summary()
        if review["themes"]:
            self.table.setCurrentCell(0, 0)

    def show_evidence(self, row, _column=0, _old_row=-1, _old_column=-1):
        if not 0 <= row < len(self.review["themes"]):
            return
        theme = self.review["themes"][row]["theme_id"]
        rows = proposal_evidence(self.directory, self.proposal_id, theme)
        self.evidence.setPlainText("\n\n".join(
            f"{name} · characters {start}–{end}\nEarlier theme: {previous}\n{quote}"
            for name, start, end, quote, previous in rows))

    def mappings(self):
        return {tid: combo.currentData() for tid, combo in self.choices.items()}

    def update_summary(self, _value=None):
        mapping = self.mappings()
        chosen = [tid for tid in mapping.values() if tid]
        old_labels = self.review["old_labels"]
        carried = set(chosen)
        retired = [label for tid, label in old_labels.items() if tid not in carried]
        names = [item["label"] for item in self.review["themes"] if mapping[item["theme_id"]] is None]
        unchanged = sum(next((c["shared"] for c in item["candidates"] if c["theme_id"] == mapping[item["theme_id"]]), 0)
                        for item in self.review["themes"])
        changed = self.review["shared_passages"] - unchanged
        self.identity_summary.setText(f"With these matches, {changed} passages move to a different theme.\n"
            + "New themes: " + (", ".join(names) or "None")
            + "\nEarlier themes not matched: " + (", ".join(retired) or "None")
            + "\nThey stay saved in the original analysis.")
        duplicate = len(chosen) != len(carried)
        self.error.setText("An earlier theme is matched more than once. Keep one of them as a new theme." if duplicate else
                           "Your coding changed since these were prepared; cancel and prepare them again."
                           if self.review["stale"] else "")
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(
            self.confirm.isChecked() and not duplicate and not self.review["stale"])

    def apply(self):
        try:
            apply_proposal(self.directory, self.proposal_id, self.mappings())
        except Exception as exc:
            QMessageBox.warning(self, "Could not apply the changes", str(exc))
            return
        self.applied = True
        self.accept()

    def reject(self):
        if not self.applied:
            try:
                cancel_proposal(self.directory, self.proposal_id)
            except Exception as exc:
                QMessageBox.warning(self, "Could not cancel the changes", str(exc))
                return
        super().reject()
