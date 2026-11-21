"""The Documents tab: listing, importing and removing documents.

Part of MainWindow (see main_window.py); not meant to be used on its own.
"""
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QListWidgetItem, QMessageBox)

from qualitative_themes.storage.documents import (ImportOutcome, get_document,
                                                   list_documents, remove_documents)


from qualitative_themes.gui.workers import ImportWorker


class DocumentsPageMixin:
    def update_document_actions(self):
        busy = self.busy
        total = self.document_list.count()
        selected = len(self.document_list.selectedItems())
        self.select_all_documents_button.setEnabled(bool(self.project and total) and not busy)
        self.clear_document_selection_button.setEnabled(bool(self.project and selected) and not busy)
        self.document_selection_count.setText(f"{selected:,} of {total:,} documents selected")
        self.remove_documents_button.setEnabled(bool(self.project and self.document_list.selectedItems()) and not busy)

    def remove_selected_documents(self):
        if not self.project or self.busy:
            return
        items = self.document_list.selectedItems()
        if not items:
            self.import_status.setText("Select documents to remove.")
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
        self.document_list.clear()
        self.preview.clear()
        self.preview_title.setText("Add documents to see their text here.")
        if self.project is None:
            return
        for document_id, name, status in list_documents(self.project.directory):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, document_id)
            self.document_list.addItem(item)
            if select_id == document_id:
                self.document_list.setCurrentItem(item)
        if self.document_list.count() and self.document_list.currentItem() is None:
            self.document_list.setCurrentRow(0)
        self.analyze_button.setEnabled(self.document_list.count() > 0 and not self.busy)
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
        self.analyze_button.setEnabled(self.document_list.count() > 0)
        imported = sum(x.status == "imported" for x in self._outcomes)
        restored = sum(x.status == "restored" for x in self._outcomes)
        duplicates = sum(x.status == "duplicate" for x in self._outcomes)
        failures = [x for x in self._outcomes if x.status == "failed"]
        self.import_status.setText(
            f"Added {imported}; brought back {restored}; already in the project {duplicates}; failed {len(failures)}. " +
            " ".join(f"{x.filename}: {x.message}" for x in failures))
