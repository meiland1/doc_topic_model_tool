"""The middle panel: the open document, highlights, find and passage stepping.

Part of CodingWorkspace (see workspace.py): these methods use the shared
state documented there and are not meant to be used on their own.
"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QUndoStack
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QTextEdit, QStackedWidget, QVBoxLayout,
                               QWidget)

from qualitative_themes.domain.text_coordinates import TextCoordinates
from qualitative_themes.storage.coding import (assignment_origin, analysis_document_ids)
from qualitative_themes.storage.documents import get_document, list_documents
from qualitative_themes.gui.viewer_parts import (FindBar, LayerHighlighter, MarkerStrip, SelectionToolbar,
                                                 fill_color, theme_color)

from qualitative_themes.gui.read_code.viewer import SourceViewer
from qualitative_themes.analysis.topics import UNGROUPED_LABEL


class ReaderMixin:
    def _build_reader_panel(self, splitter):
        """Middle panel: document picker, passage navigation, find bar, viewer and coding buttons."""
        # Document viewer header navigates passages within documents
        center = QWidget()
        center_lay = QVBoxLayout(center)
        self.document_choice = QComboBox()
        self.document_choice.currentIndexChanged.connect(self.load_document)
        center_lay.addWidget(self.document_choice)
        view_bar = QHBoxLayout()
        self.view_mode = QComboBox()
        self.view_mode.addItems(["Full document", "Theme excerpts"])
        self.view_mode.setAccessibleName("Evidence view")
        view_bar.addWidget(self.view_mode)
        self.previous_passage = QPushButton("← Previous")
        self.previous_passage.clicked.connect(lambda: self.step(-1))
        view_bar.addWidget(self.previous_passage)
        self.passage_position = QLabel("No passage selected")
        view_bar.addWidget(self.passage_position, 1)
        self.next_passage = QPushButton("Next →")
        self.next_passage.clicked.connect(lambda: self.step(1))
        view_bar.addWidget(self.next_passage)
        self.search_button = QPushButton("Search all documents")
        self.search_button.setToolTip("Search every document (Ctrl+Shift+F)")
        self.search_button.clicked.connect(self.search_requested)
        view_bar.addWidget(self.search_button)
        self.find_button = QPushButton("Find in document")
        self.find_button.setToolTip("Find in this document (Ctrl+F)")
        self.find_button.clicked.connect(self.open_find)
        view_bar.addWidget(self.find_button)
        self.other_themes_toggle = QPushButton("Show other themes faintly")
        self.other_themes_toggle.setCheckable(True)
        self.other_themes_toggle.setChecked(True)
        self.other_themes_toggle.setToolTip("Show other themes' colours faintly behind the selected theme")
        self.other_themes_toggle.toggled.connect(lambda _c: self.refresh_layers())
        view_bar.addWidget(self.other_themes_toggle)
        self.comments_toggle = QPushButton("Comments")
        self.comments_toggle.setCheckable(True)
        self.comments_toggle.setChecked(True)
        self.comments_toggle.setToolTip("Show comments beside the document")
        view_bar.addWidget(self.comments_toggle)
        self.notes_toggle = QPushButton("Memos")
        self.notes_toggle.setCheckable(True)
        view_bar.addWidget(self.notes_toggle)
        center_lay.addLayout(view_bar)
        self.find_bar = FindBar()
        self.find_bar.hide()
        self.find_bar.changed.connect(self.run_find)
        self.find_bar.step.connect(self.step_find)
        self.find_bar.closed.connect(self.close_find)
        center_lay.addWidget(self.find_bar)
        self.evidence_views = QStackedWidget()
        self.view_mode.currentIndexChanged.connect(self.evidence_views.setCurrentIndex)
        self.viewer = SourceViewer()
        self.viewer.setObjectName("documentViewer")
        self.viewer.setReadOnly(True)
        self.viewer.setObjectName("sourceViewer")
        self.viewer.clicked.connect(self.clicked_text)
        self.viewer.number_pressed.connect(self.code_selection_with_number)
        self.layers = LayerHighlighter(self.viewer.document())
        self.markers = MarkerStrip()
        self.markers.jump.connect(self.jump_to_fraction)
        reader = QWidget()
        reader_lay = QHBoxLayout(reader)
        reader_lay.setContentsMargins(0, 0, 0, 0)
        reader_lay.setSpacing(2)
        reader_lay.addWidget(self.viewer, 1)
        reader_lay.addWidget(self.markers)
        self.reader = reader
        self.evidence_views.addWidget(reader)
        self.selection_toolbar = SelectionToolbar(self.viewer.viewport())
        self.selection_toolbar.code_requested.connect(self.code_selection_as)
        self.selection_toolbar.comment_requested.connect(self.comment_on_selection)
        # The toolbar appears only for selections the researcher makes, not when
        # the app moves to a passage.
        self.viewer.selectionChanged.connect(self.selection_toolbar.hide)
        self.viewer.user_selected.connect(self.update_selection_toolbar)
        self.viewer.verticalScrollBar().valueChanged.connect(self.follow_comments)
        self.recent_theme_ids = []
        self.find_matches = []
        self.find_index = -1
        self.undo_stack = QUndoStack(self)
        center_lay.addWidget(self.evidence_views, 1)
        add = QPushButton("Code selection")
        self.add_selection_button = add
        self.view_mode.currentIndexChanged.connect(lambda index: add.setEnabled(index == 0))
        add.clicked.connect(self.add_selection)
        center_lay.addWidget(add)
        splitter.addWidget(center)

        self.occurrence_title = QLabel("Coded passages")
        center_lay.addWidget(self.occurrence_title)
        self.hits = QListWidget()
        self.hits.currentRowChanged.connect(self.jump_to)
        self.hits.itemDoubleClicked.connect(lambda _item: self.view_mode.setCurrentIndex(0))
        self.hits.setToolTip("Select an excerpt to navigate; double-click to open it in the full document.")
        self.hits.setWordWrap(True)
        self.hits.setSpacing(8)
        self.hits.setAccessibleName("Theme evidence excerpts")
        self.hits.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.evidence_views.addWidget(self.hits)
        nav = QHBoxLayout()
        previous = QPushButton("← Previous")
        previous.clicked.connect(lambda: self.step(-1))
        next_button = QPushButton("Next →")
        next_button.clicked.connect(lambda: self.step(1))
        nav.addWidget(previous)
        nav.addWidget(next_button)
        previous.hide()
        next_button.hide()
        self.range_themes = QLabel("Select a coded passage to see its themes.")
        self.range_themes.setWordWrap(True)
        center_lay.addWidget(self.range_themes)
        accept = QPushButton("Keep this suggestion")
        accept.clicked.connect(self.accept_selected_suggestion)
        coding_actions = QHBoxLayout()
        coding_actions.addWidget(accept)
        remove = QPushButton("Uncode selection")
        remove.clicked.connect(self.remove_selected_coding)
        coding_actions.addWidget(remove)
        center_lay.addLayout(coding_actions)

    def reload_documents(self, rebuild_trees=True):
        """Refill the document dropdown with the run's documents (all documents for manual coding).

        rebuild_trees=False skips the navigation rebuild when the caller is about
        to call reload_themes(), which rebuilds the trees anyway.
        """
        if self.directory is None:
            return
        old = self.doc_id
        self.document_choice.blockSignals(True)
        self.document_choice.clear()
        selected = analysis_document_ids(self.directory, self.run_id) if self.run_id else None
        for doc_id, name, status in list_documents(self.directory, include_removed=True):
            if selected is None or doc_id in selected:
                self.document_choice.addItem(name + (" [removed]" if status == "removed" else ""), doc_id)
        index = self.document_choice.findData(old)
        self.document_choice.setCurrentIndex(index if index >= 0 else (0 if self.document_choice.count() else -1))
        self.document_choice.blockSignals(False)
        self.load_document()
        if rebuild_trees and self._tree_nodes:
            self.rebuild_navigation(self.visible_themes(fresh=True))
            self.sync_tree_selection()

    def load_document(self, _index=-1):
        """Show the document selected in the dropdown.

        Pre: any comment draft is saved first; if that fails the selection
        snaps back to the previous document.
        Post: viewer, layers, comments and notes all show the new document. If Qt
        altered the text, coding is disabled for it rather than risk wrong offsets.
        """
        if not self.comments_panel.flush_pending():
            self.document_choice.blockSignals(True)
            self.document_choice.setCurrentIndex(self.document_choice.findData(self.doc_id))
            self.document_choice.blockSignals(False)
            return
        doc_id = self.document_choice.currentData()
        self.doc_id = doc_id
        if 0 <= self.match_index < len(self.matches) and self.matches[self.match_index][1] != doc_id:
            self.match_index = -1
            self.hits.setCurrentRow(-1)
            self.range_themes.setText("Select a coded passage to see its themes.")
        self.viewer.clear()
        self.source_text = ""
        self.coords = TextCoordinates.build("")
        if not doc_id or not self.directory:
            self.comments_panel.set_document(self.directory, None)
            return
        _name, self.source_text, _spans = get_document(self.directory, doc_id)
        self.coords = TextCoordinates.build(self.source_text)
        self.viewer.setPlainText(self.coords.display_text)
        if self.viewer.toPlainText() != self.coords.display_text:
            self.status.setText("Viewer changed text separators; coding is disabled for this document.")
            self.viewer.setEnabled(False)
            return
        self.viewer.setEnabled(True)
        self.comments_panel.set_document(self.directory, doc_id)
        self.refresh_layers()
        if self.find_bar.isVisible():
            self.run_find()
        scope = self._navigation or {}
        if not self._from_tree and scope.get("kind") in {"group", "passage"} and scope.get("document_id") != doc_id and self.current_theme_id():
            self._navigation = {"kind": "group", "theme_id": self.current_theme_id(), "document_id": doc_id}
            self._from_tree = True
            try:
                self.select_theme(self.themes.currentItem())
            finally:
                self._from_tree = False
            return
        self.highlight_matches()
        self.reload_notes()
        self.update_passage_position()

    def select_range(self, doc, start, end):
        for index, match in enumerate(self.matches):
            if match[1] == doc and match[3:5] == (start, end):
                self.hits.setCurrentRow(index)
                break

    def update_passage_position(self):
        scope = self._navigation or {}
        label = "this document" if scope.get("document_id") else "all matching documents"
        self.passage_position.setText(
            f"Passage {self.match_index + 1} of {len(self.matches)} · {label}"
            if self.match_index >= 0 else "No passage selected")
        self.previous_passage.setEnabled(bool(self.matches))
        self.next_passage.setEnabled(bool(self.matches))

    def refresh_layers(self):
        """Repaint theme colour bands and scrollbar markers for the open document.

        Uses the cached _navigation_groups, so it doesn't hit the database.
        """
        if not self.directory or not self.doc_id or not self.viewer.isEnabled():
            self.layers.set_theme_ranges([])
            self.markers.set_markers([])
            return
        try:
            colours = self.theme_colour_index()
            labels = {tid: label for tid, label, _ in self.visible_themes()}
        except Exception:
            return
        current = self.current_theme_id()
        show_others = self.other_themes_toggle.isChecked()
        ranges, markers = [], []
        total = max(1, self.viewer.document().characterCount())
        for (theme_id, doc), rows in self._navigation_groups.items():
            if doc != self.doc_id or theme_id not in colours or labels.get(theme_id) == UNGROUPED_LABEL:
                continue
            index = colours[theme_id]
            selected = theme_id == current
            if not selected and not show_others:
                continue
            # Other themes stay faint so the selected theme and the current passage stand out.
            fill = fill_color(index, 55 if selected or current is None else 28)
            for row in rows:
                q_start, q_end = self.coords.qt_range(row[3], row[4])
                ranges.append((q_start, q_end, fill))
                markers.append((q_start / total, q_end / total, theme_color(index), selected))
        self.layers.set_theme_ranges(ranges)
        markers.sort(key=lambda m: m[3])
        self.markers.set_markers(markers)

    def jump_to_fraction(self, fraction):
        position = int(fraction * max(0, self.viewer.document().characterCount() - 1))
        cursor = self.viewer.textCursor()
        cursor.setPosition(position)
        self.viewer.setTextCursor(cursor)
        self.viewer.centerCursor()

    def select_source_range(self, start, end):
        """Select canonical [start, end) in the viewer and scroll to it."""
        if not self.viewer.isEnabled() or end > len(self.source_text):
            return
        q_start, q_end = self.coords.qt_range(start, end) if end > start else self.coords.qt_range(start, min(start + 1, len(self.source_text)))
        cursor = self.viewer.textCursor()
        cursor.setPosition(q_start)
        cursor.setPosition(q_end, QTextCursor.MoveMode.KeepAnchor)
        self.viewer.setTextCursor(cursor)
        self.viewer.centerCursor()

    def open_find(self):
        if self.view_mode.currentIndex() != 0:
            self.view_mode.setCurrentIndex(0)
        self.find_bar.show()
        selected = self.viewer.textCursor().selectedText()
        if selected and len(selected) < 80 and "\u2029" not in selected:
            self.find_bar.field.setText(selected)
        self.find_bar.field.setFocus()
        self.find_bar.field.selectAll()
        self.run_find()

    def close_find(self):
        self.find_bar.hide()
        self.find_matches, self.find_index = [], -1
        self.layers.set_find([])
        self.viewer.setFocus()

    def run_find(self):
        import re
        needle = self.find_bar.field.text()
        self.find_matches, self.find_index = [], -1
        if needle and self.source_text and self.viewer.isEnabled():
            flags = 0 if self.find_bar.case.isChecked() else re.IGNORECASE
            spans = [(m.start(), m.end()) for m in re.finditer(re.escape(needle), self.source_text, flags)]
            if self.find_bar.coded_only.isChecked():
                coded = [(row[3], row[4]) for (tid, doc), rows in self._navigation_groups.items()
                         if doc == self.doc_id for row in rows]
                spans = [(a, b) for a, b in spans if any(s <= a and b <= e for s, e in coded)]
            self.find_matches = spans
        if self.find_matches:
            self.find_index = 0
        self.show_find()

    def step_find(self, direction):
        if self.find_matches:
            self.find_index = (self.find_index + direction) % len(self.find_matches)
            self.show_find()

    def show_find(self):
        qt = [self.coords.qt_range(a, b) for a, b in self.find_matches]
        self.layers.set_find(qt, self.find_index)
        if not self.find_bar.field.text():
            self.find_bar.count.setText("")
        elif not self.find_matches:
            self.find_bar.count.setText("No matches")
        else:
            self.find_bar.count.setText(f"{self.find_index + 1} of {len(self.find_matches)}")
            q_start, q_end = qt[self.find_index]
            cursor = self.viewer.textCursor()
            cursor.setPosition(q_start)
            self.viewer.setTextCursor(cursor)
            self.viewer.centerCursor()

    def follow_comments(self, _value=None):
        if not self.comments_panel.cards or not self.source_text:
            return
        position = self.viewer.cursorForPosition(QPoint(0, 0)).position()
        try:
            start, _ = self.coords.canonical_range(position, min(position + 1, len(self.coords.display_text)))
        except Exception:
            return
        self.comments_panel.follow(start)

    def step(self, direction: int):
        if self.matches:
            index = 0 if self.match_index < 0 else (self.match_index + direction) % len(self.matches)
            self.hits.setCurrentRow(index)
            self.mark_current_read()

    def mark_current_read(self):
        """Count a passage the researcher stepped to as read, for the coverage indicator."""
        if not self.directory or not 0 <= self.match_index < len(self.matches):
            return
        _id, doc, _name, start, end, _ = self.matches[self.match_index]
        try:
            from qualitative_themes.storage.reading import mark
            mark(self.directory, doc, start, end, "read", self.current_theme_id(), self.run_id)
        except Exception:
            pass

    def open_location(self, document_id, start, end):
        """Open a document at an exact range (used by search)."""
        index = self.document_choice.findData(document_id)
        if index < 0:
            self.run_choice.setCurrentIndex(self.run_choice.findData(None))
            index = self.document_choice.findData(document_id)
        if index < 0:
            return
        self.view_mode.setCurrentIndex(0)
        self.document_choice.setCurrentIndex(index)
        self.select_source_range(start, end)

    def jump_to(self, index: int):
        """Select matches[index]: switch document if needed, select the range, update side panels."""
        if not 0 <= index < len(self.matches):
            return
        self.match_index = index
        _id, doc_id, _name, start, end, _excerpt = self.matches[index]
        if (self._navigation or {}).get("kind") == "passage":
            self._navigation = {"kind": "passage", "theme_id": self.current_theme_id(), "document_id": doc_id, "start": start, "end": end}
        if doc_id != self.doc_id:
            choice = self.document_choice.findData(doc_id)
            if choice >= 0:
                self.document_choice.setCurrentIndex(choice)
        if not self.viewer.isEnabled():
            return
        q_start, q_end = self.coords.qt_range(start, end)
        cursor = self.viewer.textCursor()
        cursor.setPosition(q_start)
        cursor.setPosition(q_end, QTextCursor.MoveMode.KeepAnchor)
        self.viewer.setTextCursor(cursor)
        self.viewer.ensureCursorVisible()
        self.highlight_matches()
        origin = assignment_origin(self.directory, _id)
        self.range_themes.setText(("Suggested by the tool" if origin == "model" else "Your coding") +
                                 "\nThemes: " + ", ".join(self.range_theme_labels(doc_id, start, end)))
        self.reload_notes()
        self.sync_tree_selection()
        self.update_passage_position()
        self.status.setText(f"Occurrence {index + 1} of {len(self.matches)} · [{start}, {end})")

    def clicked_text(self, qt_position: int):
        """Select the evidence row whose range contains a clicked Qt position."""
        if not self.doc_id:
            return
        for index, (_id, doc_id, _name, start, end, _excerpt) in enumerate(self.matches):
            if doc_id == self.doc_id:
                q_start, q_end = self.coords.qt_range(start, end)
                if q_start <= qt_position < q_end:
                    self.hits.setCurrentRow(index)
                    break

    def highlight_matches(self):
        """Draw the selected theme's ranges in the open document (stronger for the current one)."""
        if not self.doc_id or not self.viewer.isEnabled():
            return
        selections = []
        colour_index = self.theme_colour_index().get(self.current_theme_id(), 0)
        for i, (_id, doc_id, _name, start, end, _excerpt) in enumerate(self.matches):
            if doc_id != self.doc_id:
                continue
            scope = self._navigation or {}
            if scope.get("kind") == "passage" and (doc_id, start, end) != (scope.get("document_id"), scope.get("start"), scope.get("end")):
                continue
            q_start, q_end = self.coords.qt_range(start, end)
            cursor = self.viewer.textCursor()
            cursor.setPosition(q_start)
            cursor.setPosition(q_end, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            fmt = QTextCharFormat()
            # The selected theme in its own colour, stronger than the background layer.
            fmt.setBackground(fill_color(colour_index, 200 if i == self.match_index else 120))
            selection.format = fmt
            selections.append(selection)
        self.viewer.setExtraSelections(selections)
