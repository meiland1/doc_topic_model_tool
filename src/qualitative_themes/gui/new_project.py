"""New project wizard: name it, drop in documents, review the import.

Replaces the old two-dialog flow (pick an empty folder, then name it). The
project folder is created from the name inside a default location the
researcher can change.
"""
from __future__ import annotations

import re
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from qualitative_themes.storage.database import create_project
from qualitative_themes.storage.documents import get_document, import_document

SUPPORTED = {".pdf", ".docx", ".txt"}


def default_parent() -> Path:
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "Topic Model Projects"


def folder_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().rstrip(".")
    return cleaned or "Untitled project"


def available_folder(parent: Path, name: str) -> Path:
    base = parent / folder_name(name)
    candidate, number = base, 2
    while candidate.exists() and any(candidate.iterdir()):
        candidate = base.with_name(f"{base.name} ({number})")
        number += 1
    return candidate


def expand_paths(paths) -> list[Path]:
    """Files to import from dropped files/folders, skipping hidden and unsupported files."""
    found = []
    for path in map(Path, paths):
        if path.is_dir():
            for file in sorted(path.rglob("*")):
                if file.is_file() and file.suffix.lower() in SUPPORTED and \
                        not any(part.startswith(".") for part in file.relative_to(path).parts):
                    found.append(file)
        elif path.is_file() and path.suffix.lower() in SUPPORTED:
            found.append(path)
    unique = []
    for file in found:
        if file not in unique:
            unique.append(file)
    return unique


from qualitative_themes.gui.worker import WorkerThread


class ImportFilesWorker(WorkerThread):
    """Import the chosen files one by one; result(row, ImportOutcome, word_count) per file.

    Unexpected errors become a 'failed' outcome for that file so the batch keeps going.
    """
    result = Signal(int, object, int)

    def __init__(self, directory: Path, files: list[Path], parent=None):
        super().__init__(parent)
        self.directory, self.files = directory, files

    def run(self):
        for row, path in enumerate(self.files):
            if self.isInterruptionRequested():
                break
            try:
                outcome = import_document(self.directory, path)
                words = 0
                if outcome.document_id and outcome.status in {"imported", "restored"}:
                    words = len(get_document(self.directory, outcome.document_id)[1].split())
            except Exception as exc:
                traceback.print_exc()
                from qualitative_themes.storage.documents import ImportOutcome
                outcome, words = ImportOutcome(path.name, "failed", message=str(exc)), 0
            self.result.emit(row, outcome, words)


class DropZone(QFrame):
    """Drag-and-drop target that emits dropped(list_of_paths)."""
    dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 22, 20, 22)
        title = QLabel("Drop PDFs, Word files, or text files here")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        row = QHBoxLayout()
        row.addStretch()
        self.browse_files = QPushButton("Browse files…")
        self.browse_folder = QPushButton("Add a whole folder…")
        row.addWidget(self.browse_files)
        row.addWidget(self.browse_folder)
        row.addStretch()
        layout.addLayout(row)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("active", True)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("active", False)
        self.style().polish(self)

    def dropEvent(self, event):
        self.setProperty("active", False)
        self.style().polish(self)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.dropped.emit(paths)
            event.acceptProposedAction()


class NewProjectDialog(QDialog):
    """Name a project, pick files, create it and import them.

    After exec(), `project` is the created Project, or None if nothing was
    created. A project can exist even if some imports failed; the dialog
    shows those per file.
    """
    def __init__(self, parent=None, parent_folder: Path | None = None):
        super().__init__(parent)
        self.setWindowTitle("New project")
        self.resize(820, 640)
        self.project = None
        self.files: list[Path] = []
        self._worker = None
        self._parent_folder = Path(parent_folder or default_parent())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        heading = QLabel("New project")
        heading.setObjectName("dialogTitle")
        layout.addWidget(heading)
        fields = QHBoxLayout()
        name_box = QVBoxLayout()
        name_box.addWidget(QLabel("Project name"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. Clinic access interviews")
        self.name.textChanged.connect(self.update_location)
        name_box.addWidget(self.name)
        fields.addLayout(name_box, 1)
        where_box = QVBoxLayout()
        where_box.addWidget(QLabel("Saved in"))
        where_row = QHBoxLayout()
        self.location = QLineEdit()
        self.location.setReadOnly(True)
        self.location.setObjectName("pathField")
        where_row.addWidget(self.location, 1)
        change = QPushButton("Change…")
        change.clicked.connect(self.choose_parent)
        where_row.addWidget(change)
        where_box.addLayout(where_row)
        fields.addLayout(where_box, 1)
        layout.addLayout(fields)
        hint = QLabel("The folder is created for you from the name. You don't need to make an empty folder first.")
        hint.setObjectName("quiet")
        layout.addWidget(hint)
        self.drop = DropZone()
        self.drop.dropped.connect(self.add_paths)
        self.drop.browse_files.clicked.connect(self.browse_files)
        self.drop.browse_folder.clicked.connect(self.browse_folder)
        layout.addWidget(self.drop)
        self.summary = QLabel("No documents yet. You can also add them later.")
        self.summary.setObjectName("setupName")
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["File", "Words", "Status"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        self.primary = QPushButton("Create project")
        self.primary.setProperty("primary", True)
        self.primary.setDefault(True)
        self.primary.clicked.connect(self.primary_clicked)
        buttons.addWidget(self.primary)
        layout.addLayout(buttons)
        self.update_location()

    # Location -------------------------------------------------------------
    def target_folder(self) -> Path:
        return available_folder(self._parent_folder, self.name.text() or "Untitled project")

    def update_location(self, _text=None):
        self.location.setText(str(self.target_folder()))
        self.primary.setEnabled(bool(self.name.text().strip()) or self.project is not None)

    def choose_parent(self):
        folder = QFileDialog.getExistingDirectory(self, "Where should the project folder go?", str(self._parent_folder.parent))
        if folder:
            self._parent_folder = Path(folder)
            self.update_location()

    # Files ------------------------------------------------------------------
    def browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add documents", str(Path.home()), "Documents (*.pdf *.docx *.txt)")
        self.add_paths(paths)

    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add every document in a folder", str(Path.home()))
        if folder:
            self.add_paths([folder])

    def add_paths(self, paths):
        if self.project is not None:
            return
        for path in expand_paths(paths):
            if path in self.files:
                continue
            self.files.append(path)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(path.name))
            self.table.setItem(row, 1, QTableWidgetItem("—"))
            self.table.setItem(row, 2, QTableWidgetItem("Waiting"))
        self.summary.setText(f"{len(self.files)} document(s) ready to import." if self.files else
                             "No supported documents found. Use PDF, Word (.docx), or text files.")

    # Create + import ----------------------------------------------------------
    def primary_clicked(self):
        if self.project is not None and self._worker is None:
            self.accept()
            return
        name = self.name.text().strip()
        if not name:
            return
        try:
            self._parent_folder.mkdir(parents=True, exist_ok=True)
            self.project = create_project(self.target_folder(), name)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.warning(self, "Could not create project", str(exc))
            return
        self.name.setEnabled(False)
        self.drop.setEnabled(False)
        if not self.files:
            self.accept()
            return
        self.primary.setEnabled(False)
        self.primary.setText("Importing…")
        self.cancel_button.setText("Stop importing")
        self._counts = {"ready": 0, "attention": 0, "skipped": 0, "failed": 0}
        self._worker = ImportFilesWorker(self.project.directory, list(self.files), self)
        self._worker.result.connect(self.show_result)
        self._worker.finished.connect(self.import_finished)
        self._worker.start()

    def show_result(self, row, outcome, words):
        scanned = [w for w in outcome.warnings if "OCR" in w or "scanned" in w]
        if outcome.status in {"imported", "restored"}:
            status, key = (("Looks scanned — some pages need OCR", "attention") if scanned else ("Ready", "ready"))
        elif outcome.status == "duplicate":
            status, key = "Already imported, skipped", "skipped"
        else:
            status, key = outcome.message or "Could not import", "failed"
        self._counts[key] += 1
        self.table.setItem(row, 1, QTableWidgetItem(f"{words:,}" if words else "—"))
        item = QTableWidgetItem(status)
        item.setToolTip(" ".join((outcome.message,) + tuple(outcome.warnings)).strip())
        if key in {"attention", "failed"}:
            item.setForeground(Qt.GlobalColor.darkYellow if key == "attention" else Qt.GlobalColor.darkRed)
        self.table.setItem(row, 2, item)
        done = sum(self._counts.values())
        self.summary.setText(f"Imported {done} of {len(self.files)}…")

    def import_finished(self):
        worker, self._worker = self._worker, None
        if worker:
            worker.wait()
            worker.deleteLater()
        c = self._counts
        self.summary.setText(f"{c['ready']} ready · {c['attention']} need attention · {c['skipped']} skipped · "
                             f"{c['failed']} failed. Hover a status for details.")
        self.primary.setText("Open project →")
        self.primary.setEnabled(True)
        self.cancel_button.setText("Close")
        self.cancel_button.clicked.disconnect()
        self.cancel_button.clicked.connect(self.accept)

    def reject(self):
        if self._worker is not None:
            self._worker.requestInterruption()
            return
        if self.project is not None:
            # The project exists already; closing should still open it.
            self.accept()
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait()
        super().closeEvent(event)
