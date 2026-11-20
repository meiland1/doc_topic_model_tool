"""The Documents tab: listing, importing and removing documents.

Part of MainWindow (see main_window.py); not meant to be used on its own.
"""
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QVBoxLayout)

from qualitative_themes.storage.documents import (ImportOutcome, get_document,
                                                   list_documents, remove_documents, restore_documents)


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
        # Cached in refresh_documents: no database query on every selection change.
        self.restore_documents_button.setEnabled(bool(self.project and self._has_removed) and not busy)

    def remove_selected_documents(self):
        if not self.project or self.busy:
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
            traceback.print_exc()
            QMessageBox.warning(self, "Could not remove documents", str(exc))
            return
        self.refresh_documents()
        self.workspace.reload_documents()
        self.workspace.reload_themes()
        self.update_document_actions()
        self.import_status.setText(f"Removed {count} document(s) from the import list. Saved analyses, coding, and notes remain available.")

    def restore_removed_documents(self):
        if not self.project or self.busy:
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
                traceback.print_exc()
                QMessageBox.warning(self, "Could not restore documents", str(exc))
        dialog.deleteLater()

    def refresh_documents(self, select_id: str | None = None):
        self.document_list.clear()
        self.preview.clear()
        self.preview_title.setText("Import documents to preview extracted text.")
        if self.project is None:
            self._has_removed = False
            return
        self._has_removed = any(status == "removed" for _, _, status in
                                list_documents(self.project.directory, include_removed=True))
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
            self, "Import documents", "", "Documents (*.pdf *.docx *.txt)")
        if not paths:
            return
        self.import_button.setEnabled(False)
        self.analyze_button.setEnabled(False)
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
            f"Imported {imported}; restored {restored}; already present {duplicates}; failed {len(failures)}. " +
            " ".join(f"{x.filename}: {x.message}" for x in failures))
