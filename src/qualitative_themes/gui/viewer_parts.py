"""Reader components: theme-colored layer, scrollbar markers, find bar, selection toolbar."""
from __future__ import annotations

from bisect import bisect_left

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import (QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut, QSyntaxHighlighter,
                           QTextCharFormat)
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QToolButton, QWidget)

# Distinct, colour-blind-aware theme hues (Okabe–Ito based) with light fills for text backgrounds.
THEME_COLORS = ["#e69f00", "#56b4e9", "#cc79a7", "#009e73", "#d55e00", "#0072b2", "#b8a100", "#8e6bbf",
                "#4f9d9d", "#a6761d"]


def theme_color(index: int) -> QColor:
    return QColor(THEME_COLORS[index % len(THEME_COLORS)])


def fill_color(index: int, alpha: int = 70) -> QColor:
    colour = theme_color(index)
    colour.setAlpha(alpha)
    return colour


def swatch_icon(index: int, size: int = 10) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(theme_color(index))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, size, size, 2, 2)
    painter.end()
    return QIcon(pixmap)


class LayerHighlighter(QSyntaxHighlighter):
    """Draws coding colours and find matches into the document's own formats.

    It sits underneath the viewer's extra selections, which still mark the
    selected theme's passages, so existing evidence navigation is unchanged.
    """

    def __init__(self, document):
        super().__init__(document)
        self.theme_ranges: list[tuple[int, int, QColor]] = []
        self.find_ranges: list[tuple[int, int]] = []
        self.current_find = -1
        self._starts: list[int] = []

    def set_theme_ranges(self, ranges):
        self.theme_ranges = sorted(ranges, key=lambda r: (r[0], r[1]))
        self._starts = [r[0] for r in self.theme_ranges]
        self.rehighlight()

    def set_find(self, ranges, current=-1):
        self.find_ranges, self.current_find = list(ranges), current
        self.rehighlight()

    def highlightBlock(self, _text):
        block = self.currentBlock()
        start, end = block.position(), block.position() + block.length()
        upto = bisect_left(self._starts, end)
        for s, e, colour in self.theme_ranges[:upto]:
            if e <= start:
                continue
            fmt = QTextCharFormat()
            fmt.setBackground(colour)
            underline = QColor(colour)
            underline.setAlpha(min(255, colour.alpha() * 4))
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
            fmt.setUnderlineColor(underline)
            a, b = max(s, start), min(e, end)
            self.setFormat(a - start, b - a, fmt)
        for index, (s, e) in enumerate(self.find_ranges):
            if e <= start or s >= end:
                continue
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#ff9f1c" if index == self.current_find else "#fff27a"))
            fmt.setForeground(QColor("#1d2a38"))
            a, b = max(s, start), min(e, end)
            self.setFormat(a - start, b - a, fmt)


class MarkerStrip(QWidget):
    """Coloured ticks beside the reader showing where coded passages sit; click to jump."""
    jump = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(14)
        self.setToolTip("Where coded passages appear in this document. Click a mark to jump there.")
        self.setAccessibleName("Theme markers")
        self.markers: list[tuple[float, float, QColor, bool]] = []

    def set_markers(self, markers):
        self.markers = markers
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#eef2f5"))
        height = max(1, self.height() - 4)
        for start, end, colour, strong in self.markers:
            top = 2 + int(start * height)
            bottom = max(top + 3, 2 + int(end * height))
            painter.fillRect(QRect(3 if strong else 5, top, 8 if strong else 4, bottom - top), colour)
        painter.end()

    def mousePressEvent(self, event):
        self.jump.emit(max(0.0, min(1.0, event.position().y() / max(1, self.height()))))


class FindBar(QFrame):
    changed = Signal()
    step = Signal(int)
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("findBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Find in this document")
        self.field.setAccessibleName("Find in document")
        self.field.setClearButtonEnabled(True)
        self.field.textChanged.connect(lambda _t: self.changed.emit())
        self.field.returnPressed.connect(lambda: self.step.emit(1))
        layout.addWidget(self.field, 1)
        self.count = QLabel("")
        self.count.setObjectName("quiet")
        layout.addWidget(self.count)
        previous = QPushButton("‹")
        previous.setAccessibleName("Previous match")
        previous.setFixedWidth(32)
        previous.clicked.connect(lambda: self.step.emit(-1))
        following = QPushButton("›")
        following.setAccessibleName("Next match")
        following.setFixedWidth(32)
        following.clicked.connect(lambda: self.step.emit(1))
        layout.addWidget(previous)
        layout.addWidget(following)
        self.case = QCheckBox("Aa")
        self.case.setToolTip("Match upper and lower case exactly")
        self.case.toggled.connect(lambda _c: self.changed.emit())
        layout.addWidget(self.case)
        self.coded_only = QCheckBox("Coded passages only")
        self.coded_only.toggled.connect(lambda _c: self.changed.emit())
        layout.addWidget(self.coded_only)
        close = QPushButton("✕")
        close.setAccessibleName("Close find")
        close.setFixedWidth(32)
        close.clicked.connect(self.closed)
        layout.addWidget(close)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self.field, activated=self.closed.emit)
        QShortcut(QKeySequence("Shift+Return"), self.field, activated=lambda: self.step.emit(-1))


class SelectionToolbar(QFrame):
    """Floating bar shown over selected text: code it as a theme (or a new one), or comment."""
    code_requested = Signal(str)
    new_theme_requested = Signal()
    comment_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("selectionToolbar")
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(6, 4, 6, 4)
        self.layout_.setSpacing(4)
        self.hide()

    def set_themes(self, themes, recent_ids):
        """``themes``: [(theme_id, label, colour_index)] in shortcut order (1–9)."""
        # Rebuild while hidden: widgets added to a visible parent are shown a tick
        # later, which would make the size hint too small.
        self.hide()
        while self.layout_.count():
            widget = self.layout_.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        label = QLabel("Code as")
        label.setObjectName("toolbarLabel")
        self.layout_.addWidget(label)
        by_id = {tid: (tid, name, colour) for tid, name, colour in themes}
        shown = [by_id[tid] for tid in recent_ids if tid in by_id][:3] or themes[:3]
        for tid, name, colour in shown:
            short = name if len(name) <= 26 else name[:25] + "…"
            button = QPushButton(short.replace("&", "&&"))
            button.setObjectName("toolbarButton")
            button.setIcon(swatch_icon(colour))
            number = next((i for i, row in enumerate(themes, 1) if row[0] == tid), None)
            button.setToolTip(f"Code the selection as {name}" + (f" (key {number})" if number and number <= 9 else ""))
            button.clicked.connect(lambda _c=False, t=tid: self.code_requested.emit(t))
            self.layout_.addWidget(button)
        more = QToolButton()
        more.setText("More…")
        more.setObjectName("toolbarButton")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        for number, (tid, name, _colour) in enumerate(themes, 1):
            action = menu.addAction((f"{number}  " if number <= 9 else "    ") + name.replace("&", "&&"))
            action.triggered.connect(lambda _c=False, t=tid: self.code_requested.emit(t))
        more.setMenu(menu)
        self.layout_.addWidget(more)
        new = QPushButton("+ New theme")
        new.setObjectName("toolbarButton")
        new.setToolTip("Make a new theme and code the selection as it")
        new.clicked.connect(self.new_theme_requested)
        self.layout_.addWidget(new)
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setObjectName("toolbarDivider")
        self.layout_.addWidget(divider)
        comment = QPushButton("Comment")
        comment.setObjectName("toolbarButton")
        comment.clicked.connect(self.comment_requested)
        self.layout_.addWidget(comment)
        self.adjustSize()
