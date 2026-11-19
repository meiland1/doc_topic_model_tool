"""Theme overview: a map of where to read after an analysis.

Cards summarise each theme (keywords, two quotes, spread); a documents × themes
heatmap shows which documents to read for which theme; a coverage bar shows
how much has been reviewed so blind spots stay visible.
"""
from __future__ import annotations

import traceback
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from qualitative_themes.analysis.overview import UNGROUPED_NOTE, build_overview
from qualitative_themes.gui.viewer_parts import swatch_icon, theme_color
from qualitative_themes.storage.documents import get_document


class NumberItem(QTableWidgetItem):
    def __lt__(self, other):
        return (self.data(Qt.ItemDataRole.UserRole) or 0) < (other.data(Qt.ItemDataRole.UserRole) or 0)


class ThemeCard(QFrame):
    """One theme's summary card: label, counts, keywords and sample quotes."""
    def __init__(self, theme, quotes, start_reading):
        super().__init__()
        self.setObjectName("themeCard")
        self.setProperty("ungrouped", theme["ungrouped"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)
        header = QHBoxLayout()
        swatch = QLabel()
        swatch.setPixmap(swatch_icon(theme["colour"], 12).pixmap(12, 12))
        if not theme["ungrouped"]:
            header.addWidget(swatch)
        name = QLabel(theme["label"])
        name.setObjectName("cardTitle")
        name.setWordWrap(True)
        header.addWidget(name, 1)
        layout.addLayout(header)
        spread = QLabel(f"{theme['passages']} passages · {theme['documents']} documents")
        spread.setObjectName("quiet")
        layout.addWidget(spread)
        if theme["ungrouped"]:
            note = QLabel(UNGROUPED_NOTE)
            note.setWordWrap(True)
            note.setObjectName("cardNote")
            note.setMinimumHeight(note.fontMetrics().lineSpacing() * 2 + 4)
            layout.addWidget(note)
        elif theme["keywords"]:
            words = QLabel("Keywords: " + ", ".join(theme["keywords"]))
            words.setWordWrap(True)
            words.setObjectName("cardKeywords")
            layout.addWidget(words)
        for quote in quotes:
            text = QLabel("“" + quote["text"] + "”\n— " + quote["document"])
            text.setWordWrap(True)
            text.setObjectName("cardQuote")
            layout.addWidget(text)
        layout.addStretch()
        button = QPushButton("Start reading →")
        button.setProperty("primary", not theme["ungrouped"])
        button.clicked.connect(lambda: start_reading(theme["theme_id"]))
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)


class OverviewTab(QWidget):
    """Theme cards and the theme x document matrix for the current run.

    Signals: start_reading(theme_id), guided_reading(), open_cell(theme_id,
    document_id). MainWindow turns them into workspace navigation.
    """
    start_reading = Signal(str)
    open_cell = Signal(str, str)
    guided_reading = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.directory: Path | None = None
        self.run_id = None
        self.data = None
        self.stale = True
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        top = QHBoxLayout()
        title = QLabel("Theme overview")
        title.setObjectName("panelTitle")
        top.addWidget(title)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("quiet")
        top.addWidget(self.subtitle, 1)
        self.coverage_label = QLabel("")
        self.coverage_label.setObjectName("quiet")
        top.addWidget(self.coverage_label)
        self.coverage_bar = QProgressBar()
        self.coverage_bar.setFixedWidth(140)
        self.coverage_bar.setTextVisible(False)
        self.coverage_bar.setToolTip("Share of this analysis's passages you have reviewed or opened.")
        top.addWidget(self.coverage_bar)
        self.guided_button = QPushButton("Guided reading…")
        self.guided_button.clicked.connect(self.guided_reading)
        top.addWidget(self.guided_button)
        self.export_button = QPushButton("Export reading guide…")
        self.export_button.clicked.connect(self.export_guide)
        top.addWidget(self.export_button)
        outer.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setSpacing(14)
        self.empty = QLabel("Run an analysis to see a map of where to read. Each theme gets a card here, "
                            "with a heatmap of which documents mention it.")
        self.empty.setWordWrap(True)
        self.empty.setObjectName("quiet")
        self.body.addWidget(self.empty)
        self.cards_widget = QWidget()
        self.cards = QGridLayout(self.cards_widget)
        self.cards.setSpacing(12)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.cards_widget)
        self.heat_title = QLabel("Where each theme appears")
        self.heat_title.setObjectName("panelTitle")
        self.body.addWidget(self.heat_title)
        heat_help = QLabel("Darker cells have more passages. Click a cell to open that document with the theme "
                           "highlighted. Click a column header to sort documents by that theme.")
        heat_help.setObjectName("quiet")
        heat_help.setWordWrap(True)
        self.heat_help = heat_help
        self.body.addWidget(heat_help)
        self.heatmap = QTableWidget()
        self.heatmap.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.heatmap.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.heatmap.verticalHeader().hide()
        self.heatmap.cellClicked.connect(self.cell_clicked)
        self.heatmap.setMinimumHeight(220)
        self.body.addWidget(self.heatmap)
        self.body.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.show_empty(True)

    def show_empty(self, empty):
        self.empty.setVisible(empty)
        for widget in (self.cards_widget, self.heat_title, self.heat_help, self.heatmap):
            widget.setVisible(not empty)
        self.guided_button.setEnabled(not empty)
        self.export_button.setEnabled(not empty)

    def set_run(self, directory, run_id):
        self.directory, self.run_id = (Path(directory) if directory else None), run_id
        self.stale = True
        if self.isVisible():
            self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self.stale:
            self.refresh()

    def refresh(self):
        self.stale = False
        if not self.directory:
            self.show_empty(True)
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.data = build_overview(self.directory, self.run_id)
        except Exception:
            traceback.print_exc()
            self.data = None
        finally:
            QApplication.restoreOverrideCursor()
        if not self.data or not self.data["themes"]:
            self.show_empty(True)
            return
        self.show_empty(False)
        self.render_cards()
        self.render_heatmap()
        cover = self.data["coverage"]
        if cover:
            self.coverage_label.setText(f"Reviewed {cover['reviewed']} of {cover['passages']} passages")
            self.coverage_bar.setValue(round(cover["share"] * 100))
        self.subtitle.setText("" if self.data["has_fit"] else
                              "  ·  passage fit strength unavailable for this analysis")

    def quote_text(self, quote):
        _name, text, _ = get_document(self.directory, quote["document_id"])
        words = " ".join(text[quote["start"]:quote["end"]].split())
        return words if len(words) <= 220 else words[:217].rsplit(" ", 1)[0] + "…"

    def render_cards(self):
        while self.cards.count():
            widget = self.cards.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for index, theme in enumerate(self.data["themes"]):
            quotes = [{"text": self.quote_text(q), "document": q["document"]} for q in theme["quotes"]]
            card = ThemeCard(theme, quotes, self.start_reading.emit)
            self.cards.addWidget(card, index // 3, index % 3)

    def render_heatmap(self):
        themes = self.data["themes"]
        documents = self.data["documents"]
        matrix = self.data["matrix"]
        cover = (self.data["coverage"] or {}).get("documents", {})
        self.heatmap.setSortingEnabled(False)
        self.heatmap.clear()
        self.heatmap.setColumnCount(len(themes) + 2)
        self.heatmap.setRowCount(len(documents))
        headers = ["Document"] + [t["label"] if len(t["label"]) <= 20 else t["label"][:19] + "…" for t in themes] + ["Reviewed"]
        self.heatmap.setHorizontalHeaderLabels(headers)
        for column, theme in enumerate(themes, 1):
            header = self.heatmap.horizontalHeaderItem(column)
            header.setToolTip(theme["label"])
            if not theme["ungrouped"]:
                header.setIcon(swatch_icon(theme["colour"]))
        peak = max([count for count in matrix.values()] or [1])
        for row, (doc, name) in enumerate(documents):
            item = QTableWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name.lower())
            item.setData(Qt.ItemDataRole.UserRole + 1, doc)
            self.heatmap.setItem(row, 0, item)
            for column, theme in enumerate(themes, 1):
                count = matrix.get((theme["theme_id"], doc), 0)
                cell = NumberItem(str(count) if count else "")
                cell.setData(Qt.ItemDataRole.UserRole, count)
                cell.setData(Qt.ItemDataRole.UserRole + 1, doc)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if count:
                    colour = QColor("#0f766e") if theme["ungrouped"] else theme_color(theme["colour"])
                    colour.setAlpha(int(40 + 200 * count / peak))
                    cell.setBackground(QBrush(colour))
                    cell.setToolTip(f"{count} passages about {theme['label']} in {name}")
                self.heatmap.setItem(row, column, cell)
            share = cover.get(doc, {}).get("share", 0.0)
            reviewed = NumberItem(f"{share:.0%}")
            reviewed.setData(Qt.ItemDataRole.UserRole, share)
            reviewed.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if share == 0:
                reviewed.setForeground(QBrush(QColor("#b42318")))
                reviewed.setToolTip("Nothing in this document has been reviewed yet.")
            self.heatmap.setItem(row, len(themes) + 1, reviewed)
        header = self.heatmap.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in range(1, len(themes) + 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.heatmap.setColumnWidth(column, 175)
        self.heatmap.setSortingEnabled(True)
        self.heatmap.setMinimumHeight(min(600, 60 + 32 * len(documents)))

    def cell_clicked(self, row, column):
        if not self.data or column == 0 or column > len(self.data["themes"]):
            return
        item = self.heatmap.item(row, column)
        if item and item.data(Qt.ItemDataRole.UserRole):
            self.open_cell.emit(self.data["themes"][column - 1]["theme_id"], item.data(Qt.ItemDataRole.UserRole + 1))

    def export_guide(self):
        if not self.directory:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export reading guide", str(Path.home() / "reading-guide.docx"),
                                              "Word document (*.docx)")
        if not path:
            return
        from qualitative_themes.export.reading_guide import export_reading_guide
        destination = Path(path) if path.lower().endswith(".docx") else Path(path + ".docx")
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            count = export_reading_guide(self.directory, self.run_id, destination)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.warning(self, "Could not export the reading guide", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        QMessageBox.information(self, "Reading guide exported", f"Saved a guide to {count} themes:\n{destination}")
