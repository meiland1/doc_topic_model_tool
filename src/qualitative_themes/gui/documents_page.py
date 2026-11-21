"""The Documents & analysis tab: adding, checking and removing documents, and Find themes.

The checkboxes are the selection: themes are found across every ticked
document. Clicking a name only previews it. Check states are remembered
while the app is open, so a refresh or an import doesn't lose them; newly
added documents start ticked.

Part of MainWindow (see main_window.py); not meant to be used on its own.
"""
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget)

from qualitative_themes.storage.documents import (ImportOutcome, get_document,
                                                   list_documents, remove_documents)


from qualitative_themes.gui import strings
from qualitative_themes.gui.workers import ImportWorker


class DocumentsPageMixin:
    def _build_documents_tab(self, tab_layout):
        """Document list (with checkboxes) and preview side by side, Find themes underneath."""
        self._unchecked_ids = set()   # remembered across refreshes; everything else starts ticked
        top = QHBoxLayout()
        heading = QLabel("Documents")
        heading.setObjectName("panelTitle")
        top.addWidget(heading)
        self.document_selection_count = QLabel("0 of 0 documents selected")
        self.document_selection_count.setObjectName("quiet")
        top.addWidget(self.document_selection_count)
        top.addStretch()
        self.import_button = QPushButton(strings.ADD_DOCUMENTS)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.import_files)
        top.addWidget(self.import_button)
        tab_layout.addLayout(top)
        self.contents = QSplitter()
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        selection_actions = QHBoxLayout()
        self.select_all_documents_button = QPushButton("Select all")
        self.select_all_documents_button.setEnabled(False)
        self.select_all_documents_button.setToolTip("Tick every document, to find themes across all of them together.")
        self.select_all_documents_button.clicked.connect(lambda: self.set_all_checked(True))
        selection_actions.addWidget(self.select_all_documents_button)
        self.clear_document_selection_button = QPushButton("Select none")
        self.clear_document_selection_button.setEnabled(False)
        self.clear_document_selection_button.clicked.connect(lambda: self.set_all_checked(False))
        selection_actions.addWidget(self.clear_document_selection_button)
        selection_actions.addStretch()
        self.remove_documents_button = QPushButton("Remove from project…")
        self.remove_documents_button.setEnabled(False)
        self.remove_documents_button.setToolTip("Removes the ticked documents. Add the same file again to bring one back.")
        self.remove_documents_button.clicked.connect(self.remove_selected_documents)
        selection_actions.addWidget(self.remove_documents_button)
        left_lay.addLayout(selection_actions)
        self.document_list = QListWidget()
        self.document_list.setAccessibleName("Documents (ticked ones are included)")
        self.document_list.setToolTip("Select the documents to include. Themes are found across all selected documents together.")
        self.document_list.currentItemChanged.connect(self.show_document)
        self.document_list.itemChanged.connect(self.document_check_changed)
        left_lay.addWidget(self.document_list)
        self.contents.addWidget(left)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        self.preview_title = QLabel("Add documents to see their text here.")
        self.preview_title.setObjectName("setupName")
        right_lay.addWidget(self.preview_title)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        right_lay.addWidget(self.preview)
        self.contents.addWidget(right)
        # Document list and preview split about 40 / 60; the researcher can drag the divider.
        self.contents.setStretchFactor(0, 2)
        self.contents.setStretchFactor(1, 3)
        self.contents.setSizes([400, 600])
        tab_layout.addWidget(self.contents, 1)
        self.import_status = QLabel("")
        self.import_status.setWordWrap(True)
        tab_layout.addWidget(self.import_status)
        self.import_log = QListWidget()
        self.import_log.setMaximumHeight(105)
        self.import_log.setAccessibleName("Document import results")
        tab_layout.addWidget(self.import_log)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Themes are found across all selected documents together."))
        bottom.addStretch()
        self.cancel_analysis_button = QPushButton("Cancel")
        self.cancel_analysis_button.clicked.connect(self.cancel_analysis)
        self.cancel_analysis_button.setEnabled(False)
        bottom.addWidget(self.cancel_analysis_button)
        self.analyze_button = QPushButton("▶  " + strings.FIND_THEMES)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.analyze_button.setProperty("primary", True)
        self.analyze_button.setMinimumHeight(38)
        self.analyze_button.setEnabled(False)
        bottom.addWidget(self.analyze_button)
        tab_layout.addLayout(bottom)

    def checked_document_items(self):
        """The ticked rows, top to bottom. These are the documents an analysis uses."""
        return [self.document_list.item(row) for row in range(self.document_list.count())
                if self.document_list.item(row).checkState() == Qt.CheckState.Checked]

    def set_all_checked(self, checked: bool):
        self.document_list.blockSignals(True)
        for row in range(self.document_list.count()):
            item = self.document_list.item(row)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._remember_check(item)
        self.document_list.blockSignals(False)
        self.update_document_actions()

    def _remember_check(self, item):
        doc = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._unchecked_ids.discard(doc)
        else:
            self._unchecked_ids.add(doc)

    def document_check_changed(self, item):
        self._remember_check(item)
        self.update_document_actions()

    def update_document_actions(self):
        busy = self.busy
        total = self.document_list.count()
        selected = len(self.checked_document_items())
        self.select_all_documents_button.setEnabled(bool(self.project and total and selected < total) and not busy)
        self.clear_document_selection_button.setEnabled(bool(self.project and selected) and not busy)
        self.document_selection_count.setText(f"{selected:,} of {total:,} documents selected")
        self.remove_documents_button.setEnabled(bool(self.project and selected) and not busy)
        self.analyze_button.setEnabled(bool(self.project and selected) and not busy)
        self.analyze_button.setToolTip("" if selected else "Tick at least one document first.")

    def remove_selected_documents(self):
        if not self.project or self.busy:
            return
        items = self.checked_document_items()
        if not items:
            self.import_status.setText("Tick the documents to remove.")
            return
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in items]
        names = "\n".join(item.text() for item in items)
        answer = QMessageBox.question(self, "Remove from project",
            f"Remove these {len(ids)} document(s) from the project?\n\n{names}\n\n"
            "To bring one back later, add the same file again. Its coding and comments come back with it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            count = remove_documents(self.project.directory, ids)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.warning(self, "Could not remove documents", str(exc))
            return
        self.refresh_documents()
        self.workspace.reload_documents()
        self.workspace.reload_themes()
        self.update_document_actions()
        self.import_status.setText(f"Removed {count} document(s). Add the same file again to bring one back with its coding.")

    def refresh_documents(self, select_id: str | None = None):
        self.document_list.blockSignals(True)
        self.document_list.clear()
        self.preview.clear()
        self.preview_title.setText("Add documents to see their text here.")
        if self.project is None:
            self.document_list.blockSignals(False)
            self.update_document_actions()
            return
        for document_id, name, status in list_documents(self.project.directory):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, document_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked if document_id in self._unchecked_ids else Qt.CheckState.Checked)
            self.document_list.addItem(item)
            if select_id == document_id:
                self.document_list.setCurrentItem(item)
        self.document_list.blockSignals(False)
        if self.document_list.count() and self.document_list.currentItem() is None:
            self.document_list.setCurrentRow(0)
        elif self.document_list.currentItem() is not None:
            self.show_document(self.document_list.currentItem())
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
        if self.project is None or self.busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add documents", "", "Documents (*.pdf *.docx *.txt)")
        if not paths:
            return
        self.import_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
        self.import_status.setText(f"Adding {len(paths)} file(s)…")
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
        self.update_document_actions()
        imported = sum(x.status == "imported" for x in self._outcomes)
        restored = sum(x.status == "restored" for x in self._outcomes)
        duplicates = sum(x.status == "duplicate" for x in self._outcomes)
        failures = [x for x in self._outcomes if x.status == "failed"]
        self.import_status.setText(
            f"Added {imported}; brought back {restored}; already in the project {duplicates}; failed {len(failures)}. " +
            " ".join(f"{x.filename}: {x.message}" for x in failures))
