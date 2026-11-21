"""The passage preview pane in the Find themes dialog.

Shows one document with each passage as a shaded band and interviewer text
greyed out, exactly as the analysis will split it. Part of
AnalysisSetupDialog (needs its documents, passages, summary and speaker
choices).
"""
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QComboBox, QGridLayout, QLabel, QPushButton, QTextEdit,
    QVBoxLayout, QWidget)

from qualitative_themes.analysis.speakers import interviewer_ranges
from qualitative_themes.domain.text_coordinates import TextCoordinates
from qualitative_themes.storage.documents import get_document

UNIT_BUTTONS = {"Automatic passages": "Auto", "Interview passages": "Interview",
                "Paragraphs": "Paragraph", "Whole document": "Whole doc"}


UNIT_HELP = {
    "Automatic passages": "Keep sentences and paragraphs together where possible; fit every passage within the local model's token limit.",
    "Interview passages": "Keep different speakers separate and split long answers within the model limit. Uncertain speaker layouts use Auto and keep all text; review the preview.",
    "Paragraphs": "Use each extracted paragraph as a passage; length varies with the source.",
    "Whole document": "One passage per document; may hide smaller ideas and truncate long text.",
}




class PassagePreviewMixin:
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
        note = QLabel("Preview: each shaded band is one passage. Grey text is ignored.")
        note.setObjectName("quiet")
        note.setWordWrap(True)
        layout.addWidget(note)
        return pane

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
