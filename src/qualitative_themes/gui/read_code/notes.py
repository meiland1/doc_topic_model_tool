"""Passage, theme and document notes (memos) in the right-hand drawer.

Part of CodingWorkspace (see workspace.py): these methods use the shared
state documented there and are not meant to be used on their own.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QVBoxLayout,
                               QWidget)

from qualitative_themes.storage.coding import (add_coding, add_note, list_notes,
                                                update_note, assignment_origin, passage_notes)


class NotesMixin:
    def _build_notes_panel(self, splitter):
        """Right drawer: passage, theme and document notes (memos)."""
        right = QWidget()
        right_lay = QVBoxLayout(right)
        # open notes panel and add/review notes to passages/themes/documents
        self.notes_drawer = right
        self.notes_toggle.toggled.connect(right.setVisible)
        self.notes_toggle.setToolTip("Show notes for the current passage, theme, or document. Save drafts before closing the application.")
        right.hide()
        right_lay.addWidget(QLabel("Research notes"))
        self.note_context_label = QLabel("Select a passage, theme, or document.")
        self.note_context_label.setWordWrap(True)
        self.note_context_label.setTextFormat(Qt.TextFormat.PlainText)
        right_lay.addWidget(self.note_context_label)
        self.note_scope = QComboBox()
        self.note_scope.addItems(["Passage", "Theme", "Document"])
        self.note_scope.currentIndexChanged.connect(self.reload_notes)
        right_lay.addWidget(self.note_scope)
        self.note_list = QListWidget()
        self.note_list.currentItemChanged.connect(self.select_note)
        self.note_list.setMaximumHeight(160)
        right_lay.addWidget(self.note_list)
        self.note_preview = QPlainTextEdit()
        self.note_preview.setReadOnly(True)
        self.note_preview.setPlaceholderText("Select a saved note to read it.")
        right_lay.addWidget(self.note_preview, 1)
        start_note = QPushButton("Add note")
        start_note.clicked.connect(self.start_new_note)
        right_lay.addWidget(start_note)
        edit_selected = QPushButton("Edit selected note")
        edit_selected.clicked.connect(self.edit_selected_note)
        right_lay.addWidget(edit_selected)
        self.note_editor = QPlainTextEdit()
        self.note_editor.setPlaceholderText("Write an observation or memo…")
        self.note_editor.setMinimumHeight(100)
        right_lay.addWidget(self.note_editor)
        self.note_editor.hide()
        self.note_editor.textChanged.connect(lambda: self.note_editor.setVisible(bool(self.note_editor.toPlainText()) or self.note_editor.hasFocus()))
        note_actions = QHBoxLayout()
        create_note = QPushButton("Save new note")
        create_note.setToolTip("A passage note on a model suggestion accepts that coding so the note and coding survive reruns.")
        create_note.clicked.connect(self.save_new_note)
        edit_note = QPushButton("Update selected")
        edit_note.clicked.connect(self.save_note_edit)
        note_actions.addWidget(create_note)
        note_actions.addWidget(edit_note)
        right_lay.addLayout(note_actions)
        splitter.addWidget(right)

    def note_target(self):
        scope = self.note_scope.currentText()
        if scope == "Theme" and self.current_theme_id():
            return {"theme_id": self.current_theme_id()}
        if scope == "Document" and self.doc_id:
            return {"document_id": self.doc_id}
        if scope == "Passage" and 0 <= self.match_index < len(self.matches):
            return {"coding_id": self.matches[self.match_index][0]}
        return None

    def remember_note_draft(self):
        if self._note_context:
            body = self.note_editor.toPlainText()
            if body != self._note_loaded_body:
                self._note_drafts[self._note_context] = (body, self._draft_note_id, self._note_loaded_body)

    def start_new_note(self):
        self.remember_note_draft()
        if self.note_editor.toPlainText() != self._note_loaded_body:
            self.status.setText("Save this draft before starting another note for the same target.")
            return
        if self._note_context:
            self._note_drafts.pop(self._note_context, None)
        self.note_list.setCurrentRow(-1)
        self._draft_note_id = None
        self._note_loaded_body = ""
        self.note_preview.clear()
        self.note_editor.clear()
        self.note_editor.show()
        self.note_editor.setFocus()

    def edit_selected_note(self):
        if not self.note_list.currentItem():
            self.status.setText("Select a saved note to edit.")
            return
        self.select_note(self.note_list.currentItem())
        self.note_editor.show()
        self.note_editor.setFocus()

    def reload_notes(self, _index=-1):
        # Capture the old editor before rebinding it to a different source.
        self.remember_note_draft()
        self.note_list.blockSignals(True)
        self.note_list.clear()
        target = self.note_target()
        if target and self.directory:
            for note_id, body in self.notes_for_target(target):
                item = QListWidgetItem(" ".join(body.split())[:90])
                item.setData(Qt.ItemDataRole.UserRole, note_id)
                self.note_list.addItem(item)
        self.note_list.blockSignals(False)
        self._note_context = (str(self.directory), tuple(sorted(target.items()))) if target and self.directory else None
        self._draft_note_id = None
        self._note_loaded_body = ""
        self.note_preview.clear()
        self.note_editor.clear()
        if target and "theme_id" in target:
            context = self.themes.currentItem().text()
        elif target and "document_id" in target:
            context = self.document_choice.currentText()
        elif target:
            match = self.matches[self.match_index]
            context = f"{match[2]} · characters {match[3]}–{match[4]}"
        else:
            context = "Select a target first."
        self.note_context_label.setText(self.note_scope.currentText() + " notes · " + context)
        self.note_editor.hide()
        draft = self._note_drafts.get(self._note_context)
        if draft:
            body, self._draft_note_id, self._note_loaded_body = draft
            self.note_editor.setPlainText(body)
            self.note_context_label.setText(self.note_scope.currentText() + " notes · " + context + "\nRestored unsaved draft (this session)")
            for row in range(self.note_list.count()):
                item = self.note_list.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == self._draft_note_id:
                    self.note_list.blockSignals(True)
                    self.note_list.setCurrentItem(item)
                    self.note_list.blockSignals(False)
                    self.note_preview.setPlainText(self._note_loaded_body)
                    break

    def notes_for_target(self, target):
        if "coding_id" in target and 0 <= self.match_index < len(self.matches):
            _, doc, _, start, end, _ = self.matches[self.match_index]
            return passage_notes(self.directory, doc, self.current_theme_id(), start, end)
        return list_notes(self.directory, **target)

    def select_note(self, current, _previous=None):
        if current and self.directory:
            note_id = current.data(Qt.ItemDataRole.UserRole)
            for saved_id, body in self.notes_for_target(self.note_target()):
                if saved_id == note_id:
                    if self._note_context in self._note_drafts or self.note_editor.toPlainText() != self._note_loaded_body:
                        self.note_preview.setPlainText(body)
                        self.status.setText("An unsaved draft is open. Save it before editing another note; navigation preserves drafts for this session.")
                        return
                    self._draft_note_id = note_id
                    self._note_loaded_body = body
                    self.note_preview.setPlainText(body)
                    self.note_editor.setPlainText(body)
                    self.note_editor.hide()
                    break

    def save_new_note(self):
        target = self.note_target()
        if not self.directory or not target:
            self.status.setText("Select a passage, theme, or document for the note.")
            return
        try:
            body = self.note_editor.toPlainText().strip()
            if not body:
                raise ValueError("Write a note first.")
            accepted_range = None
            if "coding_id" in target and assignment_origin(self.directory, target["coding_id"]) == "model":
                # A note on a suggestion explicitly accepts that exact passage coding.
                _, doc, _, start, end, _ = self.matches[self.match_index]
                target = {"coding_id": add_coding(self.directory, doc, start, end, self.current_theme_id())}
                accepted_range = (doc, start, end)
            add_note(self.directory, body, **target)
            self._note_drafts.pop(self._note_context, None)
            self._note_loaded_body = body
            if accepted_range:
                self.reload_themes(self.current_theme_id())
                self.select_range(*accepted_range)
            self.reload_notes()
            self.status.setText("Research note saved.")
        except ValueError as exc:
            self.status.setText(str(exc))

    def save_note_edit(self):
        item = self.note_list.currentItem()
        if not self._draft_note_id or not self.directory:
            self.status.setText("Select a saved note to update.")
            return
        try:
            update_note(self.directory, self._draft_note_id, self.note_editor.toPlainText())
            self._note_drafts.pop(self._note_context, None)
            self._note_loaded_body = self.note_editor.toPlainText()
            self.reload_notes()
            self.status.setText("Research note updated.")
        except ValueError as exc:
            self.status.setText(str(exc))
