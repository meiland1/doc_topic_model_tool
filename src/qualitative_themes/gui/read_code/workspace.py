"""The Read & code tab: CodingWorkspace and the state its parts share.

Left: theme list and the theme/document navigation trees. Middle: the
document viewer. Right: evidence list, notes and comments.

State worth knowing before changing anything here:
  directory, run_id   current project and analysis (run_id None = manual coding only)
  doc_id, source_text the open document and its stored text
  coords              TextCoordinates for source_text; convert every offset
                      through it (canonical <-> Qt) and never use Qt
                      positions as stored offsets
  matches             effective assignments of the selected theme:
                      (assignment_id, doc_id, doc_name, start, end, excerpt)
  match_index         which of `matches` is selected (-1 = none)
  _navigation         current scope: {'kind': 'theme'|'group'|'passage', ...}
  _from_tree          True while a tree click drives select_theme(), so the
                      scope set by the tree isn't overwritten

Every write goes through storage/ (coding, comments, reading) and then
reload_themes() or refresh_layers() redraws from the database. Nothing
here keeps its own copy of research decisions.

The class is split by area into mixins, one file each:
  analysis_bar.py    analysis picker and its actions
  tree_panel.py      theme/document trees (left)
  reader.py          document viewer, highlights, find, stepping (middle)
  coding_actions.py  creating, applying and editing themes
  notes.py           memos drawer (right)
  viewer.py          SourceViewer widget and the undo command
"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QLabel, QSplitter, QVBoxLayout, QWidget)

from qualitative_themes.domain.text_coordinates import TextCoordinates
from qualitative_themes.gui.read_code.comments_panel import CommentsPanel
from qualitative_themes.gui.read_code.analysis_bar import AnalysisBarMixin
from qualitative_themes.gui.read_code.coding_actions import CodingActionsMixin
from qualitative_themes.gui.read_code.notes import NotesMixin
from qualitative_themes.gui.read_code.reader import ReaderMixin
from qualitative_themes.gui.read_code.tree_panel import TreePanelMixin


class CodingWorkspace(AnalysisBarMixin, TreePanelMixin, ReaderMixin, CodingActionsMixin, NotesMixin, QWidget):
    """The Research Workspace tab. MainWindow calls set_project() and the open_* methods."""
    search_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.directory: Path | None = None
        self.doc_id: str | None = None
        self.source_text = ""
        self.coords = TextCoordinates.build("")
        self.matches: list[tuple[str, str, str, int, int, str]] = []
        self.match_index = -1
        self.run_id = None
        self._note_drafts = {}
        self._note_context = None
        self._note_loaded_body = ""
        self._draft_note_id = None
        self._draft_theme_ids = set()
        self._navigation = None
        self._from_tree = False
        self._rebuilding_trees = False
        self._navigation_groups = {}
        self._tree_nodes = {}
        self._nav_themes, self._nav_documents = [], []  # what lazily created tree rows are built from
        outer = QVBoxLayout(self)

        self._build_analysis_header(outer)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter = splitter
        self._browser_sizes = None
        splitter.setChildrenCollapsible(False)

        self._build_tree_panel(splitter)
        self._build_reader_panel(splitter)
        self._build_notes_panel(splitter)
        self.comments_panel = CommentsPanel()
        self.comments_panel.setMinimumWidth(260)
        self.comments_panel.open_range.connect(self.select_source_range)
        self.comments_toggle.toggled.connect(self.comments_panel.setVisible)
        splitter.addWidget(self.comments_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 1)
        splitter.setStretchFactor(3, 1)
        for keys, handler in [("Ctrl+F", self.open_find), (QKeySequence.StandardKey.Undo, self.undo_stack.undo),
                              (QKeySequence.StandardKey.Redo, self.undo_stack.redo)]:
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
        outer.addWidget(splitter, 1)
        self.status = QLabel("Open a project to start coding.")
        outer.addWidget(self.status)
        self.compact_header(outer, self._old_header_rows, self._old_header_widgets)


    # Functions

    def capture_view(self):
        """Current layout as a dict (version 1). Positions are canonical offsets so they survive a reload."""
        from bisect import bisect_right
        cursor = self.viewer.textCursor()
        positions = self.coords.canonical_to_qt
        return {'version': 1, 'run_id': self.run_id, 'document_id': self.doc_id,
                'theme_id': self.current_theme_id(),
                'anchor': max(0, bisect_right(positions, cursor.anchor()) - 1),
                'position': max(0, bisect_right(positions, cursor.position()) - 1),
                'view_mode': self.view_mode.currentIndex(),
                'organization': self.organization_tabs.currentIndex(),
                'scroll': self.viewer.verticalScrollBar().value()}

    def restore_view(self, view):
        """Apply a dict from capture_view(). Ignores anything it doesn't recognize."""
        if not isinstance(view, dict) or view.get('version') != 1:
            return
        run = view.get('run_id')
        if run is None or isinstance(run, str):
            index = self.run_choice.findData(run)
            if index >= 0:
                self.run_choice.setCurrentIndex(index)
        theme = view.get('theme_id')
        if isinstance(theme, str):
            for index in range(self.themes.count()):
                if self.themes.item(index).data(Qt.ItemDataRole.UserRole) == theme:
                    self.themes.setCurrentRow(index)
                    break
        doc = view.get('document_id')
        index = self.document_choice.findData(doc) if isinstance(doc, str) else -1
        if index < 0:
            return  # Unavailable document: keep the normal opening view.
        self.document_choice.setCurrentIndex(index)
        # Theme selection can navigate to another document; ensure this one is loaded.
        if self.doc_id != doc:
            self.load_document()
        for name, widget in [('view_mode', self.view_mode), ('organization', self.organization_tabs)]:
            value = view.get(name)
            if type(value) is int and 0 <= value < widget.count():
                widget.setCurrentIndex(value)
        anchor, position = view.get('anchor'), view.get('position')
        if all(type(value) is int and 0 <= value <= len(self.source_text) for value in (anchor, position)):
            cursor = self.viewer.textCursor()
            cursor.setPosition(self.coords.canonical_to_qt[anchor])
            cursor.setPosition(self.coords.canonical_to_qt[position], QTextCursor.KeepAnchor)
            self.viewer.setTextCursor(cursor)
            self.viewer.ensureCursorVisible()
        scroll = view.get('scroll')
        if type(scroll) is int and scroll >= 0:
            self.viewer.verticalScrollBar().setValue(scroll)

    def set_project(self, directory: Path):
        """Point the workspace at a project. Clears undo history, scope and selection."""
        self.undo_stack.clear()
        self.directory = Path(directory)
        self.doc_id = None
        self.run_id = None
        self._navigation = None
        self._draft_theme_ids.clear()
        self.matches = []
        self.match_index = -1
        self.reload_documents()
        self.reload_runs()
        self.status.setText("Select a theme and document, or create a new theme.")
