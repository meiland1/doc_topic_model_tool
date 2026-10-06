"""Three-panel source-text workspace with persistent manual seed codings."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTextEdit,
                               QVBoxLayout, QWidget)

from qualitative_themes.domain.text_coordinates import TextCoordinates
from qualitative_themes.storage.coding import (add_coding, add_note, create_theme,
                                                list_notes, list_themes, merge_themes,
                                                occurrences, remove_coding,
                                                rename_theme, themes_for_range,
                                                update_note, assignment_origin,
                                                passage_notes, reject_model_suggestion, analysis_document_ids)
from qualitative_themes.analysis.topics import list_analysis_runs
from qualitative_themes.storage.history import analysis_title, rename_analysis
from qualitative_themes.storage.theme_keywords import theme_keyword_scores
from qualitative_themes.analysis.labels import labeling_report
import json
from qualitative_themes.storage.documents import get_document, list_documents


class SourceViewer(QPlainTextEdit):
    clicked = Signal(int)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if not self.textCursor().hasSelection():
            self.clicked.emit(self.textCursor().position())


class CodingWorkspace(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.directory: Path | None = None
        self.doc_id: str | None = None
        self.source_text = ""
        self.coords = TextCoordinates.build("")
        self.matches: list[tuple[str, str, str, int, int, str]] = []
        self.match_index = -1
        self.run_id = None
        self._draft_theme_ids = set()
        outer = QVBoxLayout(self)
        header = QLabel("Research Workspace")
        header.setStyleSheet("font-size: 19px; font-weight: 700")
        outer.addWidget(header)
        self.help = QLabel("Create a theme, select text in a document, and add the selection to that theme.")
        self.help.setWordWrap(True)
        outer.addWidget(self.help)
        self.run_choice = QComboBox()
        self.run_choice.currentIndexChanged.connect(self.change_run)
        run_actions = QHBoxLayout()
        run_actions.addWidget(self.run_choice, 1)
        self.rename_analysis_button = QPushButton("Rename analysis…")
        self.rename_analysis_button.setEnabled(False)
        self.rename_analysis_button.clicked.connect(self.rename_selected_analysis)
        run_actions.addWidget(self.rename_analysis_button)
        outer.addLayout(run_actions)
        research_actions = QHBoxLayout()
        self.history_button = QPushButton("Analysis History…")
        self.export_codebook_button = QPushButton("Export codebook…")
        self.export_passages_button = QPushButton("Export coded passages…")
        for button in [self.history_button, self.export_codebook_button, self.export_passages_button]:
            button.setEnabled(False)
            research_actions.addWidget(button)
        outer.addLayout(research_actions)
        self.export_help = QLabel("Exports use the selected analysis or Manual coding only. A passage row is one exact document range "
                                 "with all its active themes. Overlaps stay separate; theme counts include multi-theme ranges in each theme.")
        self.export_help.setWordWrap(True)
        outer.addWidget(self.export_help)
        naming_actions = QHBoxLayout()
        self.name_run_button = QPushButton("Preview new theme names")
        self.name_run_button.setEnabled(False)
        naming_actions.addWidget(self.name_run_button)
        self.cancel_task_button = QPushButton("Cancel task")
        self.cancel_task_button.setEnabled(False)
        naming_actions.addWidget(self.cancel_task_button)
        self.model_toggle = QCheckBox("Naming options")
        naming_actions.addWidget(self.model_toggle)
        outer.addLayout(naming_actions)
        refinement_actions = QHBoxLayout()
        self.refine_button = QPushButton("Refine Analysis…")
        self.refine_button.setEnabled(False)
        refinement_actions.addWidget(self.refine_button)
        self.review_proposal_button = QPushButton("Review pending refinement…")
        self.review_proposal_button.setEnabled(False)
        refinement_actions.addWidget(self.review_proposal_button)
        outer.addLayout(refinement_actions)
        self.naming_options = QWidget()
        options = QHBoxLayout(self.naming_options)
        options.addWidget(QLabel("Local Ollama model"))
        self.ollama_model_choice = QComboBox()
        options.addWidget(self.ollama_model_choice)
        self.refresh_models_button = QPushButton("Refresh installed models")
        options.addWidget(self.refresh_models_button)
        self.ollama_status = QLabel("Refresh to check local Ollama.")
        self.ollama_status.setWordWrap(True)
        options.addWidget(self.ollama_status)
        self.naming_options.setVisible(False)
        self.model_toggle.toggled.connect(self.naming_options.setVisible)
        outer.addWidget(self.naming_options)
        self.naming_toggle = QCheckBox("Theme naming details")
        outer.addWidget(self.naming_toggle)
        self.naming_status = QPlainTextEdit()
        self.naming_status.setReadOnly(True)
        self.naming_status.setMaximumHeight(140)
        self.naming_status.setVisible(False)
        self.naming_toggle.toggled.connect(self.naming_status.setVisible)
        outer.addWidget(self.naming_status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.addWidget(QLabel("Themes"))
        self.themes = QListWidget()
        self.themes.currentItemChanged.connect(self.select_theme)
        left_lay.addWidget(self.themes)
        self.theme_keywords_label = QLabel("Keywords: Select a theme.")
        self.theme_keywords_label.setTextFormat(Qt.TextFormat.PlainText)
        self.theme_keywords_label.setWordWrap(True)
        self.theme_keywords_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.theme_keywords_label.setAccessibleName("Selected theme keywords")
        self.theme_keywords_label.setToolTip("BERTopic keywords describe the saved evidence group. "
            "Scores are saved keyword relevance weights displayed as percentages (weight × 100), not probabilities or confidence percentages. "
            "Merged themes show the highest saved score for each keyword across their combined groups. "
            "They are separate from its LLM or researcher-assigned label. "
            "Earlier accepted themes use their latest saved keywords when absent from this analysis.")
        left_lay.addWidget(self.theme_keywords_label)
        new_theme = QPushButton("＋ New Theme")
        new_theme.clicked.connect(self.new_theme)
        left_lay.addWidget(new_theme)
        rename = QPushButton("Rename theme")
        rename.clicked.connect(self.rename_selected_theme)
        left_lay.addWidget(rename)
        merge = QPushButton("Merge theme into…")
        merge.clicked.connect(self.merge_selected_theme)
        left_lay.addWidget(merge)
        splitter.addWidget(left)

        center = QWidget()
        center_lay = QVBoxLayout(center)
        self.document_choice = QComboBox()
        self.document_choice.currentIndexChanged.connect(self.load_document)
        center_lay.addWidget(self.document_choice)
        self.viewer = SourceViewer()
        self.viewer.setReadOnly(True)
        self.viewer.clicked.connect(self.clicked_text)
        center_lay.addWidget(self.viewer)
        add = QPushButton("Add selected text to chosen theme")
        add.clicked.connect(self.add_selection)
        center_lay.addWidget(add)
        splitter.addWidget(center)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        self.occurrence_title = QLabel("Coded passages")
        right_lay.addWidget(self.occurrence_title)
        self.hits = QListWidget()
        self.hits.currentRowChanged.connect(self.jump_to)
        right_lay.addWidget(self.hits)
        nav = QHBoxLayout()
        previous = QPushButton("← Previous")
        previous.clicked.connect(lambda: self.step(-1))
        next_button = QPushButton("Next →")
        next_button.clicked.connect(lambda: self.step(1))
        nav.addWidget(previous)
        nav.addWidget(next_button)
        right_lay.addLayout(nav)
        self.range_themes = QLabel("Select a coded passage to see its themes.")
        self.range_themes.setWordWrap(True)
        right_lay.addWidget(self.range_themes)
        accept = QPushButton("Accept selected suggestion")
        accept.clicked.connect(self.accept_selected_suggestion)
        right_lay.addWidget(accept)
        remove = QPushButton("Remove selected coding")
        remove.clicked.connect(self.remove_selected_coding)
        right_lay.addWidget(remove)
        right_lay.addWidget(QLabel("Research notes"))
        self.note_scope = QComboBox()
        self.note_scope.addItems(["Passage", "Theme", "Document"])
        self.note_scope.currentIndexChanged.connect(self.reload_notes)
        right_lay.addWidget(self.note_scope)
        self.note_list = QListWidget()
        self.note_list.currentItemChanged.connect(self.select_note)
        right_lay.addWidget(self.note_list)
        self.note_editor = QPlainTextEdit()
        self.note_editor.setPlaceholderText("Write an observation or memo…")
        self.note_editor.setMaximumHeight(100)
        right_lay.addWidget(self.note_editor)
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
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 1)
        outer.addWidget(splitter, 1)
        self.status = QLabel("Open a project to start coding.")
        outer.addWidget(self.status)

    def set_project(self, directory: Path):
        self.directory = Path(directory)
        self.doc_id = None
        self.run_id = None
        self._draft_theme_ids.clear()
        self.matches = []
        self.match_index = -1
        self.reload_documents()
        self.reload_runs()
        self.status.setText("Select a theme and document, or create a new theme.")

    def reload_runs(self, selected=None):
        self.run_choice.blockSignals(True)
        self.run_choice.clear()
        used = {}
        for run_id, completed, settings in list_analysis_runs(self.directory):
            values = json.loads(settings)
            title = analysis_title(values, completed)
            used[title] = used.get(title, 0) + 1
            display = title if used[title] == 1 else f"{title} ({used[title]})"
            self.run_choice.addItem(display, run_id)
            self.run_choice.setItemData(self.run_choice.count() - 1,
                f"Completed (UTC): {completed}\nPassage size: {values.get('analysis_unit', 'Passages')}\n"
                f"Style: {values.get('analysis_style', 'Not recorded')}", Qt.ItemDataRole.ToolTipRole)
        self.run_choice.addItem("Manual coding only", None)
        index = self.run_choice.findData(selected) if selected else 0
        self.run_choice.setCurrentIndex(max(0, index))
        self.run_choice.blockSignals(False)
        self.change_run()

    def rename_selected_analysis(self):
        if not self.directory or not self.run_id:
            return
        selected = self.run_id
        row = next(row for row in list_analysis_runs(self.directory) if row[0] == selected)
        current_name = json.loads(row[2]).get("display_name", "")
        name, ok = QInputDialog.getText(self, "Rename analysis", "Analysis name (leave blank for the automatic name)", text=current_name)
        if not ok:
            return
        try:
            rename_analysis(self.directory, selected, name)
            self.reload_runs(selected)
            self.status.setText("Analysis name saved. Themes, passages, and coding are unchanged.")
        except ValueError as exc:
            QMessageBox.warning(self, "Could not rename analysis", str(exc))

    def change_run(self, _index=-1):
        self.run_id = self.run_choice.currentData()
        self._draft_theme_ids.clear()
        self.matches = []
        self.match_index = -1
        self.reload_documents()
        self.reload_themes()
        if self.directory:
            self.naming_status.setPlainText(labeling_report(self.directory, self.run_id))

    def reload_documents(self):
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

    def load_document(self, _index=-1):
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
            return
        _name, self.source_text, _spans = get_document(self.directory, doc_id)
        self.coords = TextCoordinates.build(self.source_text)
        self.viewer.setPlainText(self.coords.display_text)
        if self.viewer.toPlainText() != self.coords.display_text:
            self.status.setText("Viewer changed text separators; coding is disabled for this document.")
            self.viewer.setEnabled(False)
            return
        self.viewer.setEnabled(True)
        self.highlight_matches()
        self.reload_notes()

    def visible_themes(self):
        rows = list_themes(self.directory, self.run_id)
        visible = {row[0] for row in rows}
        rows.extend(row for row in list_themes(self.directory)
                    if row[0] in self._draft_theme_ids and row[0] not in visible)
        return sorted(rows, key=lambda row: row[1].lower())

    def reload_themes(self, select_id: str | None = None):
        if self.directory is None:
            return
        if select_id is None and self.themes.currentItem():
            select_id = self.themes.currentItem().data(Qt.ItemDataRole.UserRole)
        self.themes.blockSignals(True)
        self.themes.clear()
        for theme_id, label, count in self.visible_themes():
            item = QListWidgetItem(f"{label}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, theme_id)
            self.themes.addItem(item)
            if theme_id == select_id:
                self.themes.setCurrentItem(item)
        if self.themes.count() and not self.themes.currentItem():
            self.themes.setCurrentRow(0)
        self.themes.blockSignals(False)
        self.select_theme(self.themes.currentItem())

    def new_theme(self):
        if self.directory is None:
            return
        label, ok = QInputDialog.getText(self, "New theme", "Theme name")
        if not ok:
            return
        try:
            theme_id = create_theme(self.directory, label)
            self._draft_theme_ids.add(theme_id)
            self.reload_themes(theme_id)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not create theme", str(exc))

    def select_theme(self, current, _previous=None):
        self.matches = []
        self.match_index = -1
        self.hits.blockSignals(True)
        self.hits.clear()
        if current and self.directory:
            theme_id = current.data(Qt.ItemDataRole.UserRole)
            words = theme_keyword_scores(self.directory, theme_id, self.run_id)
            self.theme_keywords_label.setText("Keywords: " + (", ".join(f"{word} ({score:.2%})" for word, score in words) if words else "No model keywords saved for this theme."))
            self.matches = occurrences(self.directory, theme_id, self.run_id)
            for _id, _doc, name, start, end, excerpt in self.matches:
                snippet = " ".join(excerpt.split())[:95]
                self.hits.addItem(f"{name} · {start}–{end}\n{snippet}")
            document_count = len({m[1] for m in self.matches})
            self.occurrence_title.setText(f"{len(self.matches)} passages · {document_count} documents")
        else:
            self.theme_keywords_label.setText("Keywords: Select a theme.")
            self.occurrence_title.setText("Coded passages")
        self.hits.blockSignals(False)
        self.range_themes.setText("Select a coded passage to see its themes.")
        if self.matches:
            self.hits.setCurrentRow(0)
        self.highlight_matches()
        self.reload_notes()

    def current_theme_id(self):
        item = self.themes.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def rename_selected_theme(self):
        theme_id = self.current_theme_id()
        if not self.directory or not theme_id:
            self.status.setText("Select a theme to rename.")
            return
        old_label = next(label for tid, label, _ in self.visible_themes() if tid == theme_id)
        label, ok = QInputDialog.getText(self, "Rename theme", "New name", text=old_label)
        if ok:
            try:
                rename_theme(self.directory, theme_id, label)
                self.reload_themes(theme_id)
                self.status.setText(f"Renamed theme to {label.strip()}.")
            except ValueError as exc:
                QMessageBox.warning(self, "Could not rename theme", str(exc))

    def merge_selected_theme(self):
        source_id = self.current_theme_id()
        if not self.directory or not source_id:
            self.status.setText("Select the theme to merge.")
            return
        themes = self.visible_themes()
        source_label = next(label for tid, label, _ in themes if tid == source_id)
        choices = [(tid, label) for tid, label, _ in themes if tid != source_id]
        if not choices:
            self.status.setText("Create another theme before merging.")
            return
        displays = [f"{i+1}. {label}" for i, (_, label) in enumerate(choices)]
        target_display, ok = QInputDialog.getItem(self, "Merge themes", f"Move all work from '{source_label}' into:",
                                                 displays, 0, False)
        if not ok:
            return
        target_id, target_label = choices[displays.index(target_display)]
        if QMessageBox.question(self, "Confirm theme merge",
                                f"Merge '{source_label}' into '{target_label}'? The source theme will no longer appear.") != QMessageBox.StandardButton.Yes:
            return
        try:
            merge_themes(self.directory, source_id, target_id)
            self.reload_themes(target_id)
            self.status.setText(f"Merged {source_label} into {target_label}.")
        except ValueError as exc:
            QMessageBox.warning(self, "Could not merge themes", str(exc))

    def remove_selected_coding(self):
        if not self.directory or not 0 <= self.match_index < len(self.matches):
            self.status.setText("Select a coded passage to remove.")
            return
        coding_id = self.matches[self.match_index][0]
        theme_id = self.current_theme_id()
        try:
            if assignment_origin(self.directory, coding_id) == "model":
                _, doc, _, start, end, _ = self.matches[self.match_index]
                reject_model_suggestion(self.directory, doc, start, end, theme_id)
            else:
                remove_coding(self.directory, coding_id)
            self.reload_themes(theme_id)
            self.status.setText("Removed this passage from the selected theme; other themes remain.")
        except ValueError as exc:
            QMessageBox.warning(self, "Could not remove coding", str(exc))

    def accept_selected_suggestion(self):
        if not self.directory or not 0 <= self.match_index < len(self.matches):
            self.status.setText("Select a suggested passage first.")
            return
        _, doc, _, start, end, _ = self.matches[self.match_index]
        theme = self.current_theme_id()
        try:
            add_coding(self.directory, doc, start, end, theme)
            self.reload_themes(theme)
            self.select_range(doc, start, end)
            self.status.setText("Accepted as researcher coding; retained across later analyses.")
        except ValueError as exc:
            self.status.setText(str(exc))

    def select_range(self, doc, start, end):
        for index, match in enumerate(self.matches):
            if match[1] == doc and match[3:5] == (start, end):
                self.hits.setCurrentRow(index)
                break

    def note_target(self):
        scope = self.note_scope.currentText()
        if scope == "Theme" and self.current_theme_id():
            return {"theme_id": self.current_theme_id()}
        if scope == "Document" and self.doc_id:
            return {"document_id": self.doc_id}
        if scope == "Passage" and 0 <= self.match_index < len(self.matches):
            return {"coding_id": self.matches[self.match_index][0]}
        return None

    def reload_notes(self, _index=-1):
        self.note_list.blockSignals(True)
        self.note_list.clear()
        target = self.note_target()
        if target and self.directory:
            for note_id, body in self.notes_for_target(target):
                item = QListWidgetItem(" ".join(body.split())[:90])
                item.setData(Qt.ItemDataRole.UserRole, note_id)
                self.note_list.addItem(item)
        self.note_list.blockSignals(False)
        self.note_editor.clear()

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
                    self.note_editor.setPlainText(body)
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
            if accepted_range:
                self.reload_themes(self.current_theme_id())
                self.select_range(*accepted_range)
            self.reload_notes()
            self.status.setText("Research note saved.")
        except ValueError as exc:
            self.status.setText(str(exc))

    def save_note_edit(self):
        item = self.note_list.currentItem()
        if not item or not self.directory:
            self.status.setText("Select a saved note to update.")
            return
        try:
            update_note(self.directory, item.data(Qt.ItemDataRole.UserRole), self.note_editor.toPlainText())
            self.reload_notes()
            self.status.setText("Research note updated.")
        except ValueError as exc:
            self.status.setText(str(exc))

    def add_selection(self):
        if not self.directory or not self.doc_id or not self.themes.currentItem() or not self.viewer.isEnabled():
            self.status.setText("Choose a theme and select text in a document first.")
            return
        cursor = self.viewer.textCursor()
        if not cursor.hasSelection():
            self.status.setText("Drag across document text to select a passage first.")
            return
        try:
            start, end = self.coords.canonical_range(cursor.selectionStart(), cursor.selectionEnd())
            doc_id = self.doc_id
            theme_id = self.themes.currentItem().data(Qt.ItemDataRole.UserRole)
            add_coding(self.directory, self.doc_id, start, end, theme_id)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.reload_themes(theme_id)
        self.select_range(doc_id, start, end)
        self.status.setText(f"Saved coding at source characters [{start}, {end}).")

    def step(self, direction: int):
        if self.matches:
            index = 0 if self.match_index < 0 else (self.match_index + direction) % len(self.matches)
            self.hits.setCurrentRow(index)

    def jump_to(self, index: int):
        if not 0 <= index < len(self.matches):
            return
        self.match_index = index
        _id, doc_id, _name, start, end, _excerpt = self.matches[index]
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
        self.range_themes.setText(("Model suggestion" if origin == "model" else "Researcher coding") +
                                 "\nThemes: " + ", ".join(themes_for_range(self.directory, doc_id, start, end, self.run_id)))
        self.reload_notes()
        self.status.setText(f"Occurrence {index + 1} of {len(self.matches)} · [{start}, {end})")

    def clicked_text(self, qt_position: int):
        if not self.doc_id:
            return
        for index, (_id, doc_id, _name, start, end, _excerpt) in enumerate(self.matches):
            if doc_id == self.doc_id:
                q_start, q_end = self.coords.qt_range(start, end)
                if q_start <= qt_position < q_end:
                    self.hits.setCurrentRow(index)
                    break

    def highlight_matches(self):
        if not self.doc_id or not self.viewer.isEnabled():
            return
        selections = []
        for i, (_id, doc_id, _name, start, end, _excerpt) in enumerate(self.matches):
            if doc_id != self.doc_id:
                continue
            q_start, q_end = self.coords.qt_range(start, end)
            cursor = self.viewer.textCursor()
            cursor.setPosition(q_start)
            cursor.setPosition(q_end, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            fmt = QTextCharFormat()
            fmt.setBackground(QColor("#edb95c" if i == self.match_index else "#ffe5a4"))
            selection.format = fmt
            selections.append(selection)
        self.viewer.setExtraSelections(selections)
