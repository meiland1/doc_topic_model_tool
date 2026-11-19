"""Search all documents (Ctrl+Shift+F): snippets grouped by document, with theme tags."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from qualitative_themes.storage.coding import list_themes
from qualitative_themes.storage.documents import list_documents
from qualitative_themes.storage.search import search

RESULT_LIMIT = 500


class SearchDialog(QDialog):
    """Non-modal search window. MainWindow reuses one per project and updates run_id before showing it."""
    open_result = Signal(str, int, int)

    def __init__(self, directory, run_id=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search all documents")
        self.resize(820, 600)
        self.setModal(False)
        self.directory, self.run_id = Path(directory), run_id
        self.hits = []
        layout = QVBoxLayout(self)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Search every document… (matches words and word beginnings)")
        self.field.setObjectName("searchField")
        self.field.setClearButtonEnabled(True)
        self.field.textChanged.connect(lambda _t: self._debounce.start())
        self.field.returnPressed.connect(self.open_current)
        layout.addWidget(self.field)
        filters = QHBoxLayout()
        self.documents = QComboBox()
        self.documents.addItem("All documents", None)
        for doc, name, _ in list_documents(directory):
            self.documents.addItem(name, doc)
        self.themes = QComboBox()
        self.themes.addItem("Any theme or none", None)
        for tid, label, _ in list_themes(directory, run_id):
            self.themes.addItem(label, tid)
        self.include_comments = QCheckBox("Include comments")
        self.case = QCheckBox("Match case")
        for widget in (self.documents, self.themes):
            widget.currentIndexChanged.connect(lambda _i: self.run())
            filters.addWidget(widget)
        for box in (self.include_comments, self.case):
            box.toggled.connect(lambda _c: self.run())
            filters.addWidget(box)
        filters.addStretch()
        layout.addLayout(filters)
        self.summary = QLabel("Type at least two characters.")
        self.summary.setObjectName("quiet")
        layout.addWidget(self.summary)
        self.results = QListWidget()
        self.results.setObjectName("searchResults")
        self.results.itemActivated.connect(lambda _item: self.open_current())
        layout.addWidget(self.results, 1)
        footer = QLabel("↑↓ move  ·  Enter opens the passage in the document  ·  Esc closes")
        footer.setObjectName("quiet")
        layout.addWidget(footer)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self.run)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self.field.hasFocus() and self.results.count():
            self.results.setFocus()
            self.results.keyPressEvent(event)
            return
        super().keyPressEvent(event)

    def run(self):
        self._debounce.stop()
        query = self.field.text()
        self.results.clear()
        theme = self.themes.currentData()
        document = self.documents.currentData()
        self.hits = search(self.directory, query, document_ids={document} if document else None,
                           theme_ids={theme} if theme else None, run_id=self.run_id,
                           include_comments=self.include_comments.isChecked(), case_sensitive=self.case.isChecked(),
                           limit=RESULT_LIMIT + 1)
        truncated = len(self.hits) > RESULT_LIMIT
        self.hits = self.hits[:RESULT_LIMIT]
        grouped = {}
        for hit in self.hits:
            grouped.setdefault(hit.document_id, []).append(hit)
        self.hits = [hit for group in grouped.values() for hit in group]
        if len(query.strip()) < 2:
            self.summary.setText("Type at least two characters.")
            return
        documents = len({hit.document_id for hit in self.hits})
        if truncated:
            self.summary.setText(f"Showing first {RESULT_LIMIT} matches. Refine your search or use filters to see more.")
        elif self.hits:
            self.summary.setText(f"{len(self.hits)} {'match' if len(self.hits) == 1 else 'matches'} in "
                                 f"{documents} {'document' if documents == 1 else 'documents'}")
        else:
            self.summary.setText("No matches. Try fewer characters or broaden the filters.")
        current = None
        for hit in self.hits:
            if hit.document_id != current:
                current = hit.document_id
                header = QListWidgetItem(hit.document)
                header.setFlags(Qt.ItemFlag.NoItemFlags)
                font = header.font()
                font.setBold(True)
                header.setFont(font)
                self.results.addItem(header)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, (hit.document_id, hit.start, hit.end))
            if hit.kind == "comment":
                body = (f"<span style='color:#526478'>Comment on “{html.escape(hit.match)}”:</span> "
                        f"{html.escape(hit.comment)}")
            else:
                body = (f"{html.escape(hit.before)}<b style='background:#fff27a'>{html.escape(hit.match)}</b>"
                        f"{html.escape(hit.after)}")
            meta = []
            if hit.page:
                meta.append(f"p. {hit.page}")
            meta.extend(hit.themes)
            label = QLabel(body + (f"<br><span style='color:#526478; font-size:9pt'>{html.escape(' · '.join(meta))}</span>"
                                   if meta else ""))
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setContentsMargins(12, 4, 8, 4)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            width = max(300, self.results.viewport().width() - 24)
            item.setSizeHint(QSize(width, label.heightForWidth(width) + 18))
            self.results.addItem(item)
            self.results.setItemWidget(item, label)
        for row in range(self.results.count()):
            if self.results.item(row).data(Qt.ItemDataRole.UserRole):
                self.results.setCurrentRow(row)
                break

    def open_current(self):
        if self._debounce.isActive():
            self.run()
        item = self.results.currentItem()
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if data:
            self.open_result.emit(*data)
