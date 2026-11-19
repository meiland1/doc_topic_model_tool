"""Guided reading: work through a theme's passages core-first, in context, marking each one."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QTextBrowser, QVBoxLayout)

from qualitative_themes.analysis.overview import reading_queue
from qualitative_themes.storage import reading
from qualitative_themes.storage.database import connect

CONTEXT_CHARS = 450
SPLIT_HINT_SHARE = 0.3


def context_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Include the paragraph before and after the passage, at most ``CONTEXT_CHARS`` each way."""
    head = text[:start].rstrip()
    before = text.rfind("\n\n", max(0, len(head) - CONTEXT_CHARS), len(head))
    tail_start = end + (len(text[end:]) - len(text[end:].lstrip()))
    after = text.find("\n\n", tail_start, min(len(text), tail_start + CONTEXT_CHARS))
    return (before + 2 if before >= 0 else max(0, start - CONTEXT_CHARS),
            after if after >= 0 else min(len(text), tail_start + CONTEXT_CHARS))


class GuidedReadingDialog(QDialog):
    """Step through reading_queue() passages, marking each relevant / not relevant.

    Marks are written immediately (storage/reading.py). open_range(...) asks
    MainWindow to show the passage in the workspace.
    """
    open_range = Signal(str, int, int, str)

    def __init__(self, directory, run_id, themes, theme_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Guided reading")
        self.resize(820, 620)
        self.setModal(False)
        self.directory, self.run_id = Path(directory), run_id
        self.queue, self.index = [], 0
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Theme"))
        self.theme = QComboBox()
        for theme in themes:
            self.theme.addItem(theme["label"], theme["theme_id"])
        if theme_id is not None and self.theme.findData(theme_id) >= 0:
            self.theme.setCurrentIndex(self.theme.findData(theme_id))
        self.theme.currentIndexChanged.connect(self.load_queue)
        top.addWidget(self.theme, 1)
        self.unreviewed_only = QCheckBox("Only passages not yet reviewed")
        self.unreviewed_only.toggled.connect(self.load_queue)
        top.addWidget(self.unreviewed_only)
        layout.addLayout(top)
        progress = QHBoxLayout()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("quiet")
        progress.addWidget(self.progress_label, 1)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedWidth(200)
        progress.addWidget(self.progress)
        layout.addLayout(progress)
        self.where = QLabel("")
        self.where.setObjectName("setupName")
        layout.addWidget(self.where)
        self.view = QTextBrowser()
        self.view.setObjectName("readingView")
        self.view.setOpenLinks(False)
        layout.addWidget(self.view, 1)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("setupNotice")
        self.hint.hide()
        layout.addWidget(self.hint)
        buttons = QHBoxLayout()
        self.back = QPushButton("← Back")
        self.back.clicked.connect(lambda: self.move(-1))
        buttons.addWidget(self.back)
        buttons.addStretch()
        self.open_button = QPushButton("Open full document (O)")
        self.open_button.clicked.connect(self.open_document)
        buttons.addWidget(self.open_button)
        self.not_relevant = QPushButton("Not relevant (N)")
        self.not_relevant.clicked.connect(lambda: self.judge("not_relevant"))
        buttons.addWidget(self.not_relevant)
        self.relevant = QPushButton("Relevant (R)")
        self.relevant.setProperty("primary", True)
        self.relevant.clicked.connect(lambda: self.judge("relevant"))
        buttons.addWidget(self.relevant)
        self.skip = QPushButton("Skip →")
        self.skip.clicked.connect(lambda: self.move(1))
        buttons.addWidget(self.skip)
        layout.addLayout(buttons)
        for key, handler in [("R", lambda: self.judge("relevant")), ("N", lambda: self.judge("not_relevant")),
                             ("O", self.open_document), ("Right", lambda: self.move(1)), ("Left", lambda: self.move(-1))]:
            QShortcut(QKeySequence(key), self, activated=handler)
        self._texts = {}
        self.load_queue()

    def text_of(self, document_id):
        if document_id not in self._texts:
            with connect(self.directory) as conn:
                self._texts[document_id] = conn.execute("SELECT canonical_text FROM documents WHERE id=?",
                                                        (document_id,)).fetchone()[0]
        return self._texts[document_id]

    def load_queue(self, *_args):
        theme_id = self.theme.currentData()
        self.queue = reading_queue(self.directory, self.run_id, [theme_id]) if theme_id else []
        if self.unreviewed_only.isChecked():
            self.queue = [item for item in self.queue if item["verdict"] not in {"relevant", "not_relevant"}]
        self.index = 0
        self.show_current()

    def show_current(self):
        total = len(self.queue)
        judged = sum(1 for item in self.queue if item["verdict"] in {"relevant", "not_relevant"})
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(judged)
        self.progress_label.setText(f"{judged} of {total} reviewed")
        for button in (self.relevant, self.not_relevant, self.skip, self.back, self.open_button):
            button.setEnabled(bool(total))
        not_relevant = sum(1 for item in self.queue if item["verdict"] == "not_relevant")
        if judged >= 5 and not_relevant / judged >= SPLIT_HINT_SHARE:
            self.hint.setText(f"{not_relevant} of {judged} passages marked not relevant. This theme may mix two ideas: "
                              "consider splitting or renaming it in the Research Workspace.")
            self.hint.show()
        else:
            self.hint.hide()
        if not total:
            self.where.setText("")
            self.view.setHtml("<p>No passages to read for this theme.</p>")
            return
        item = self.queue[self.index]
        text = self.text_of(item["document_id"])
        a, b = context_bounds(text, item["start"], item["end"])
        fit = item["fit"] or {}
        badge = {"core": "core", "borderline": "borderline"}.get(fit.get("label", ""), "")
        detail = f"  ·  {badge}" if badge else ""
        if fit:
            detail += f"  ·  similarity {fit['similarity']:.2f}"
        verdict = {"relevant": "  ·  marked relevant", "not_relevant": "  ·  marked not relevant",
                   "read": "  ·  opened before"}.get(item["verdict"] or "", "")
        self.where.setText(f"{self.index + 1} of {total}  ·  {item['document']}{detail}{verdict}")

        def para(fragment):
            return html.escape(fragment).replace("\n", "<br>")
        self.view.setHtml(
            "<div style='font-size:12pt; line-height:150%; color:#203044'>"
            f"<span style='color:#7a8796'>{para(text[a:item['start']])}</span>"
            f"<span style='background:#fff2c2; font-weight:600'>{para(text[item['start']:item['end']])}</span>"
            f"<span style='color:#7a8796'>{para(text[item['end']:b])}</span></div>")

    def move(self, direction):
        if self.queue:
            self.index = max(0, min(len(self.queue) - 1, self.index + direction))
            self.show_current()

    def judge(self, verdict):
        if not self.queue:
            return
        item = self.queue[self.index]
        reading.mark(self.directory, item["document_id"], item["start"], item["end"], verdict,
                     item["theme_id"], self.run_id)
        item["verdict"] = verdict
        if self.index < len(self.queue) - 1:
            self.index += 1
        self.show_current()

    def open_document(self):
        if not self.queue:
            return
        item = self.queue[self.index]
        reading.mark(self.directory, item["document_id"], item["start"], item["end"], "read",
                     item["theme_id"], self.run_id)
        if item["verdict"] is None:
            item["verdict"] = "read"
        self.open_range.emit(item["document_id"], item["start"], item["end"], item["theme_id"])
        self.show_current()
