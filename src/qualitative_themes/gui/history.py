"""Paged audit inspection and cancellable research export workers."""
import json
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QVBoxLayout, QWidget)

from qualitative_themes.analysis.topics import AnalysisCancelled
from qualitative_themes.export.research import write_codebook, write_coded_passages
from qualitative_themes.storage.history import analysis_details, history_events, history_runs


class ResearchExportWorker(QThread):
    progress = Signal(str)
    result = Signal(str, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, directory, selected_run, kind, destination, parent=None):
        super().__init__(parent)
        self.directory, self.selected_run = directory, selected_run
        self.kind, self.destination = kind, destination
        self.run_id = None

    def run(self):
        try:
            writer = write_codebook if self.kind == "codebook" else write_coded_passages
            count, _ = writer(self.directory, self.selected_run, self.destination,
                              self.progress.emit, self.isInterruptionRequested)
            self.result.emit(str(self.destination), count)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class AnalysisHistoryDialog(QDialog):
    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.offsets = {"runs": 0, "events": 0}
        self.sizes = {"runs": 50, "events": 100}
        self.widgets = {}
        self.setWindowTitle("Analysis History")
        self.resize(1000, 700)
        layout = QVBoxLayout(self)
        note = QLabel("Saved analyses and project activity are shown newest first. "
                      "Activity includes researcher edits across the project. Times are recorded in UTC.")
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        for key, title in [("runs", "Analyses and previews"), ("events", "Project activity")]:
            page = QWidget()
            outer = QVBoxLayout(page)
            nav = QHBoxLayout()
            previous = QPushButton("Newer")
            previous.clicked.connect(lambda _checked=False, k=key: self.move(k, -1))
            next_button = QPushButton("Older")
            next_button.clicked.connect(lambda _checked=False, k=key: self.move(k, 1))
            count = QLabel()
            nav.addWidget(previous)
            nav.addWidget(next_button)
            nav.addWidget(count)
            outer.addLayout(nav)
            split = QSplitter()
            rows = QListWidget()
            rows.setAccessibleName(title)
            rows.currentItemChanged.connect(lambda current, _old=None, k=key: self.show_details(k, current))
            split.addWidget(rows)
            body = QWidget()
            body_layout = QVBoxLayout(body)
            details = QPlainTextEdit()
            details.setReadOnly(True)
            body_layout.addWidget(details)
            technical_toggle = QCheckBox("Technical details")
            body_layout.addWidget(technical_toggle)
            technical = QPlainTextEdit()
            technical.setReadOnly(True)
            technical.setVisible(False)
            technical_toggle.toggled.connect(technical.setVisible)
            body_layout.addWidget(technical)
            split.addWidget(body)
            split.setStretchFactor(1, 2)
            outer.addWidget(split)
            tabs.addTab(page, title)
            self.widgets[key] = {"rows": rows, "previous": previous, "next": next_button,
                                 "count": count, "details": details, "technical": technical}
            self.load(key)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)

    def move(self, key, direction):
        self.offsets[key] = max(0, self.offsets[key] + self.sizes[key] * direction)
        self.load(key)

    def load(self, key):
        loader = history_runs if key == "runs" else history_events
        records, total = loader(self.directory, self.offsets[key], self.sizes[key])
        widgets = self.widgets[key]
        widgets["rows"].clear()
        widgets["details"].clear()
        widgets["technical"].clear()
        for record in records:
            if key == "runs":
                status = {"completed": "Saved analysis", "segmented": "Passage preview", "proposed": "Pending proposal", "cancelled": "Cancelled proposal"}.get(record["status"], record["status"])
                text = record["title"] + " · " + record["method"] + " · " + status
            else:
                text = record["timestamp"][:19].replace("T", " ") + " · " + record["summary"]
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, record)
            widgets["rows"].addItem(item)
        start = self.offsets[key] + 1 if records else 0
        widgets["count"].setText(f"{start}–{self.offsets[key] + len(records)} of {total}")
        widgets["previous"].setEnabled(self.offsets[key] > 0)
        widgets["next"].setEnabled(self.offsets[key] + len(records) < total)
        if records:
            widgets["rows"].setCurrentRow(0)
        else:
            widgets["details"].setPlainText("No saved activity yet.")

    def show_details(self, key, item):
        if not item:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        if key == "runs":
            text, technical = analysis_details(self.directory, record["id"])
        else:
            text = f"{record['summary']}\n\nRecorded (UTC): {record['timestamp']}"
            technical = record["details"]
        self.widgets[key]["details"].setPlainText(text)
        self.widgets[key]["technical"].setPlainText(json.dumps(technical, ensure_ascii=False, indent=2))
