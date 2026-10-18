"""Three-panel source-text workspace with persistent manual seed codings."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTextEdit,
                               QStackedWidget, QDialog, QDialogButtonBox, QMenu, QToolButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

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
from qualitative_themes.storage.provenance import method_summary, theme_provenance, origin_text, method_label
from qualitative_themes.storage.database import connect
from qualitative_themes.analysis.labels import labeling_report
import json
from qualitative_themes.storage.documents import get_document, list_documents
from qualitative_themes.storage.navigation import navigation_groups


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
        outer = QVBoxLayout(self)
        header = QLabel("Research Workspace")
        header.setObjectName("workspaceTitle")
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
        self.method_status = QLabel("Analysis method: Manual coding only")
        self.method_status.setObjectName("workspaceMethod")
        self.method_status.setTextFormat(Qt.TextFormat.PlainText)
        self.method_status.setWordWrap(True)
        self.method_status.setAccessibleName("Selected analysis method")
        outer.addWidget(self.method_status)
        self.naming_toggle = QCheckBox("Theme naming details")
        outer.addWidget(self.naming_toggle)
        self.naming_status = QPlainTextEdit()
        self.naming_status.setReadOnly(True)
        self.naming_status.setMaximumHeight(140)
        self.naming_status.setVisible(False)
        self.naming_toggle.toggled.connect(self.naming_status.setVisible)
        outer.addWidget(self.naming_status)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter = splitter
        self._browser_sizes = None
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        self.evidence_browser = left
        left_lay = QVBoxLayout(left)
        left_lay.addWidget(QLabel("Browse evidence"))
        # Internal flat selection controller keeps theme actions independent of
        # which tree contains the selection. Only the two trees are displayed.
        self.themes = QListWidget(self)
        self.themes.hide()
        self.themes.currentItemChanged.connect(self.select_theme)
        self.organization_tabs = QTabWidget()
        self.document_tree = QTreeWidget()
        self.theme_tree = QTreeWidget()
        for tree, label in [(self.document_tree, "By Document"), (self.theme_tree, "By Theme")]:
            tree.setHeaderHidden(True)
            tree.setAccessibleName(label + " evidence tree")
            tree.setUniformRowHeights(True)
            tree.currentItemChanged.connect(self.tree_selected)
            tree.itemClicked.connect(self.tree_clicked)
            tree.itemExpanded.connect(self.populate_passages)
            self.organization_tabs.addTab(tree, label)
        self.organization_tabs.setCurrentIndex(1)
        self.organization_tabs.currentChanged.connect(self.organization_changed)
        left_lay.addWidget(self.organization_tabs)
        tree_help = QLabel("Expand groups to browse passages. Select a document to open it, a theme/document group to highlight its evidence, or a passage to highlight only that excerpt.")
        tree_help.setObjectName("workspaceHelp")
        tree_help.setWordWrap(True)
        left_lay.addWidget(tree_help)
        self.theme_origin_label = QLabel("Origin: Select a theme.")
        self.theme_origin_label.setTextFormat(Qt.TextFormat.PlainText)
        self.theme_origin_label.setWordWrap(True)
        self.theme_origin_label.setAccessibleName("Selected theme origin")
        left_lay.addWidget(self.theme_origin_label)
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
        new_theme = QPushButton("+ New theme")
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
        self.notes_toggle = QPushButton("Notes")
        self.notes_toggle.setCheckable(True)
        view_bar.addWidget(self.notes_toggle)
        center_lay.addLayout(view_bar)
        self.evidence_views = QStackedWidget()
        self.view_mode.currentIndexChanged.connect(self.evidence_views.setCurrentIndex)
        self.viewer = SourceViewer()
        self.viewer.setObjectName("documentViewer")
        self.viewer.setReadOnly(True)
        self.viewer.clicked.connect(self.clicked_text)
        self.evidence_views.addWidget(self.viewer)
        center_lay.addWidget(self.evidence_views, 1)
        add = QPushButton("Add selected text to chosen theme")
        self.add_selection_button = add
        self.view_mode.currentIndexChanged.connect(lambda index: add.setEnabled(index == 0))
        add.clicked.connect(self.add_selection)
        center_lay.addWidget(add)
        splitter.addWidget(center)

        right = QWidget()
        right_lay = QVBoxLayout(right)
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
        accept = QPushButton("Accept selected suggestion")
        accept.clicked.connect(self.accept_selected_suggestion)
        coding_actions = QHBoxLayout()
        coding_actions.addWidget(accept)
        remove = QPushButton("Remove selected coding")
        remove.clicked.connect(self.remove_selected_coding)
        coding_actions.addWidget(remove)
        center_lay.addLayout(coding_actions)
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
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 1)
        outer.addWidget(splitter, 1)
        self.status = QLabel("Open a project to start coding.")
        outer.addWidget(self.status)
        self.compact_header(outer, [run_actions, research_actions, naming_actions, refinement_actions],
                            [header, self.help, self.export_help, self.model_toggle, self.naming_toggle])

    def compact_header(self, outer, old_rows, hidden_widgets):
        # Preserve the existing action buttons/signals so worker busy-state logic
        # and all operations remain shared with the original controls.
        for row in old_rows:
            outer.removeItem(row)
            while row.count():
                entry = row.takeAt(0)
                if entry.widget():
                    entry.widget().hide()
        for widget in hidden_widgets:
            outer.removeWidget(widget)
            widget.hide()
        toolbar = QHBoxLayout()
        self.browse_toggle = QPushButton("Hide evidence")
        self.browse_toggle.setCheckable(True)
        self.browse_toggle.setChecked(True)
        self.browse_toggle.setAccessibleName("Show or hide evidence browser")
        self.browse_toggle.setToolTip("Hide the evidence browser to give more space to the document. Your selection is retained.")
        self.browse_toggle.toggled.connect(self.toggle_evidence_browser)
        toolbar.addWidget(self.browse_toggle)
        toolbar.addWidget(QLabel("Analysis"))
        toolbar.addWidget(self.run_choice, 1)
        self.run_choice.show()
        self.analysis_actions_button = QToolButton()
        self.analysis_actions_button.setText("Analysis actions")
        self.analysis_actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.analysis_menu = QMenu(self.analysis_actions_button)
        self.analysis_actions_button.setMenu(self.analysis_menu)
        self._menu_links = []
        for button in [self.rename_analysis_button, self.refine_button, self.review_proposal_button,
                       self.name_run_button, self.history_button]:
            self.link_menu_action(self.analysis_menu, button)
        self.analysis_menu.addSeparator()
        self.details_action = self.analysis_menu.addAction("Analysis details…")
        self.details_action.triggered.connect(self.show_analysis_details)
        self.naming_options_action = self.analysis_menu.addAction("Show naming options")
        self.naming_options_action.setCheckable(True)
        self.naming_options_action.toggled.connect(self.model_toggle.setChecked)
        self.analysis_menu.aboutToShow.connect(self.sync_action_menus)
        toolbar.addWidget(self.analysis_actions_button)
        self.export_button = QToolButton()
        self.export_button.setText("Export")
        self.export_button.setToolTip(self.export_help.text())
        self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_menu = QMenu(self.export_button)
        self.export_button.setMenu(self.export_menu)
        for button in [self.export_codebook_button, self.export_passages_button]:
            self.link_menu_action(self.export_menu, button)
        self.export_menu.aboutToShow.connect(self.sync_action_menus)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.cancel_task_button)
        self.cancel_task_button.show()
        outer.insertLayout(0, toolbar)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        self.sync_action_menus()

    def link_menu_action(self, menu, button):
        action = menu.addAction(button.text())
        action.triggered.connect(lambda _checked=False, target=button: target.click())
        self._menu_links.append((action, button))

    def sync_action_menus(self):
        for action, button in self._menu_links:
            action.setEnabled(button.isEnabled())
        self.naming_options_action.setEnabled(bool(self.directory))
        self.naming_options_action.setChecked(self.model_toggle.isChecked())
        self.details_action.setEnabled(bool(self.directory))

    def toggle_evidence_browser(self, visible):
        if visible:
            self.evidence_browser.show()
            if self._browser_sizes:
                self.workspace_splitter.setSizes(self._browser_sizes)
        else:
            self._browser_sizes = self.workspace_splitter.sizes()
            self.evidence_browser.hide()
        self.browse_toggle.setText("Hide evidence" if visible else "Browse evidence")

    def show_analysis_details(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Analysis details")
        dialog.resize(650, 500)
        layout = QVBoxLayout(dialog)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        summary = self.method_status.toolTip() or self.method_status.text()
        naming = self.naming_status.toPlainText()
        details.setPlainText(summary + ("\n\n" + naming if naming else "") + "\n\n" + self.export_help.text())
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

    def set_project(self, directory: Path):
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
                f"Method: {method_label(values)}\nCompleted (UTC): {completed}\nPassage size: {values.get('analysis_unit', 'Passages')}\n"
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
        self._navigation = None
        self._draft_theme_ids.clear()
        self.matches = []
        self.match_index = -1
        self.reload_documents()
        self.reload_themes()
        if self.directory and self.run_id:
            with connect(self.directory) as conn:
                saved = conn.execute("SELECT settings_json,model_info_json FROM analysis_runs WHERE id=?", (self.run_id,)).fetchone()
            if saved:
                summary = method_summary(json.loads(saved[0]), json.loads(saved[1]))
                self.method_status.setText(summary.splitlines()[0])
                self.method_status.setToolTip(summary)
        else:
            self.method_status.setText("Analysis method: Manual coding only")
            self.method_status.setToolTip("Researcher-led coding without an active model run.")
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
        if self._tree_nodes:
            self.rebuild_navigation(self.visible_themes())
            self.sync_tree_selection()

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
        previous_navigation = dict(self._navigation) if self._navigation else None
        self.themes.blockSignals(True)
        self.themes.clear()
        rows = self.visible_themes()
        for theme_id, label, count in rows:
            item = QListWidgetItem(f"{label}  ({count})")
            item.setData(Qt.ItemDataRole.UserRole, theme_id)
            self.themes.addItem(item)
            if theme_id == select_id:
                self.themes.setCurrentItem(item)
        if self.themes.count() and not self.themes.currentItem():
            self.themes.setCurrentRow(0)
        self.themes.blockSignals(False)
        self.rebuild_navigation(rows)
        if previous_navigation and previous_navigation.get("theme_id") == self.current_theme_id():
            self._navigation = previous_navigation
            self._from_tree = True
        elif previous_navigation and previous_navigation.get("document_id") and self.current_theme_id():
            self._navigation = {**previous_navigation, "theme_id": self.current_theme_id()}
            self._from_tree = True
        elif self.organization_tabs.currentWidget() is self.document_tree and self.current_theme_id() and self.doc_id:
            self._navigation = {"kind": "group", "theme_id": self.current_theme_id(), "document_id": self.doc_id}
            self._from_tree = True
        try:
            self.select_theme(self.themes.currentItem())
        finally:
            self._from_tree = False
        self.sync_tree_selection()

    @staticmethod
    def navigation_key(data):
        return (data.get("kind"), data.get("theme_id"), data.get("document_id"), data.get("start"), data.get("end"))

    def add_tree_node(self, tree, parent, label, data):
        item = QTreeWidgetItem([label])
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        item.setToolTip(0, label)
        if parent is None:
            tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        self._tree_nodes[tree][self.navigation_key(data)] = item
        return item

    def rebuild_navigation(self, themes):
        documents = [(self.document_choice.itemData(i), self.document_choice.itemText(i)) for i in range(self.document_choice.count())]
        self._navigation_groups = navigation_groups(self.directory, {tid for tid, _, _ in themes}, {doc for doc, _ in documents}, self.run_id)
        self._rebuilding_trees = True
        try:
            for tree in [self.document_tree, self.theme_tree]:
                expanded = {key for key, item in self._tree_nodes.get(tree, {}).items() if item.isExpanded()}
                tree.blockSignals(True)
                tree.clear()
                self._tree_nodes[tree] = {}
                if tree is self.document_tree:
                    for doc, name in documents:
                        groups = [(tid, label) for tid, label, _ in themes if (tid, doc) in self._navigation_groups]
                        passage_count = len({(row[3], row[4]) for tid, _ in groups for row in self._navigation_groups[(tid, doc)]})
                        parent = self.add_tree_node(tree, None,
                            f"{name} ({len(groups)} {'theme' if len(groups) == 1 else 'themes'} · {passage_count} {'passage' if passage_count == 1 else 'passages'})",
                            {"kind": "document", "document_id": doc})
                        parent.setToolTip(0, parent.text(0) + "\nCounts distinct themes and unique source ranges in this document; a range assigned to multiple themes counts once.")
                        for tid, label in groups:
                            self.add_group_node(tree, parent, tid, doc, label)
                else:
                    for tid, label, _ in themes:
                        docs = [(doc, name) for doc, name in documents if (tid, doc) in self._navigation_groups]
                        count = sum(len(self._navigation_groups[(tid, doc)]) for doc, _ in docs)
                        parent = self.add_tree_node(tree, None,
                            f"{label} ({len(docs)} {'document' if len(docs) == 1 else 'documents'} · {count} {'passage' if count == 1 else 'passages'})",
                            {"kind": "theme", "theme_id": tid})
                        for doc, name in docs:
                            self.add_group_node(tree, parent, tid, doc, name)
                # Passage widgets are created only when a group is expanded.
                for key in expanded:
                    item = self._tree_nodes[tree].get(key)
                    if item:
                        self.populate_passages(item)
                        item.setExpanded(True)
                tree.blockSignals(False)
        finally:
            self._rebuilding_trees = False

    def add_group_node(self, tree, parent, theme, document, label):
        count = len(self._navigation_groups[(theme, document)])
        item = self.add_tree_node(tree, parent, f"{label} ({count} {'passage' if count == 1 else 'passages'})",
            {"kind": "group", "theme_id": theme, "document_id": document})
        item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)

    def populate_passages(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get("kind") != "group" or item.childCount():
            return
        tree = item.treeWidget()
        for number, (_aid, doc, _name, start, end, preview) in enumerate(self._navigation_groups.get((data["theme_id"], data["document_id"]), []), 1):
            node = self.add_tree_node(tree, item, f"Passage {number}: {preview}",
                {"kind": "passage", "theme_id": data["theme_id"], "document_id": doc, "start": start, "end": end})
            node.setToolTip(0, f"Source characters [{start}, {end})\n{preview}")

    def tree_clicked(self, item, _column):
        # Reactivate an already selected node after a document-dropdown change.
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and (data != self._navigation or data.get("document_id") not in (None, self.doc_id)):
            self.tree_selected(item)

    def tree_selected(self, current, _previous=None):
        if self._rebuilding_trees or current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        self._navigation = dict(data)
        self._from_tree = True
        try:
            theme = data.get("theme_id")
            self.themes.blockSignals(True)
            self.themes.setCurrentRow(-1)
            if theme:
                for index in range(self.themes.count()):
                    if self.themes.item(index).data(Qt.ItemDataRole.UserRole) == theme:
                        self.themes.setCurrentRow(index)
                        break
            self.themes.blockSignals(False)
            self.select_theme(self.themes.currentItem())
            if data.get("kind") == "document":
                index = self.document_choice.findData(data["document_id"])
                if index >= 0:
                    self.document_choice.setCurrentIndex(index)
                    self.load_document()
                    self.status.setText("Document opened. Select a theme or passage in the tree to highlight evidence.")
        finally:
            self._from_tree = False
        self.sync_tree_selection()

    def organization_changed(self, _index):
        tree = self.organization_tabs.currentWidget()
        if not self._tree_nodes.get(tree):
            return
        data = dict(self._navigation or {})
        if tree is self.document_tree and data.get("kind") == "theme":
            theme = data.get("theme_id")
            doc = self.doc_id
            if (theme, doc) in self._navigation_groups:
                data = {"kind": "group", "theme_id": theme, "document_id": doc}
            elif (theme, doc) not in self._navigation_groups:
                # Empty themes have no document branch until evidence is coded.
                self.sync_tree_selection()
                return
        item = self.find_navigation_node(tree, data)
        if item:
            tree.setCurrentItem(item)
            self.tree_selected(item)
        else:
            tree.blockSignals(True)
            tree.setCurrentItem(None)
            tree.blockSignals(False)

    def find_navigation_node(self, tree, data):
        if data.get("kind") == "passage":
            group = {"kind": "group", "theme_id": data.get("theme_id"), "document_id": data.get("document_id")}
            parent = self._tree_nodes.get(tree, {}).get(self.navigation_key(group))
            if parent:
                self.populate_passages(parent)
        return self._tree_nodes.get(tree, {}).get(self.navigation_key(data))

    def sync_tree_selection(self):
        tree = self.organization_tabs.currentWidget()
        data = self._navigation or {}
        item = self.find_navigation_node(tree, data)
        if item is None and tree is self.document_tree and data.get("theme_id") and self.doc_id:
            item = self.find_navigation_node(tree, {"kind": "group", "theme_id": data["theme_id"], "document_id": self.doc_id})
        tree.blockSignals(True)
        tree.setCurrentItem(item)
        if item:
            parent = item.parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()
            tree.scrollToItem(item)
        tree.blockSignals(False)

    def new_theme(self):
        if self.directory is None:
            return
        label, ok = QInputDialog.getText(self, "New theme", "Theme name")
        if not ok:
            return
        try:
            theme_id = create_theme(self.directory, label)
            self._draft_theme_ids.add(theme_id)
            self._navigation = None
            self.organization_tabs.setCurrentIndex(1)
            self.reload_themes(theme_id)
        except ValueError as exc:
            QMessageBox.warning(self, "Could not create theme", str(exc))

    def select_theme(self, current, _previous=None):
        if not self._from_tree:
            self._navigation = {"kind": "theme", "theme_id": current.data(Qt.ItemDataRole.UserRole)} if current else None
        self.matches = []
        self.match_index = -1
        self.hits.blockSignals(True)
        self.hits.clear()
        if current and self.directory:
            theme_id = current.data(Qt.ItemDataRole.UserRole)
            provenance = theme_provenance(self.directory, theme_id, self.run_id)
            self.theme_origin_label.setText("Origin: " + origin_text(provenance))
            self.theme_origin_label.setToolTip("Supplied categories: " + ", ".join(provenance["supplied_names"]) if provenance["supplied_names"] else
                "Origin describes how this group was produced, not whether its assignments have been accepted.")
            words = theme_keyword_scores(self.directory, theme_id, self.run_id)
            self.theme_keywords_label.setText("Keywords: " + (", ".join(f"{word} ({score:.2%})" for word, score in words) if words else "No model keywords saved for this theme."))
            self.matches = occurrences(self.directory, theme_id, self.run_id)
            document = (self._navigation or {}).get("document_id")
            if document:
                self.matches = [match for match in self.matches if match[1] == document]
            for _id, _doc, name, start, end, excerpt in self.matches:
                snippet = excerpt
                self.hits.addItem(f"{name} · {start}–{end}\n{snippet}")
            document_count = len({m[1] for m in self.matches})
            self.occurrence_title.setText(f"{len(self.matches)} passages · {document_count} documents")
        else:
            self.theme_origin_label.setText("Origin: Select a theme.")
            self.theme_keywords_label.setText("Keywords: Select a theme.")
            self.occurrence_title.setText("Coded passages")
        self.hits.blockSignals(False)
        self.range_themes.setText("Select a coded passage to see its themes.")
        if self.matches:
            scope = self._navigation or {}
            selected = next((i for i, match in enumerate(self.matches) if scope.get("kind") == "passage" and
                             match[1] == scope.get("document_id") and match[3:5] == (scope.get("start"), scope.get("end"))), 0)
            self.hits.setCurrentRow(selected)
        elif (self._navigation or {}).get("document_id"):
            index = self.document_choice.findData(self._navigation["document_id"])
            if index >= 0:
                self.document_choice.setCurrentIndex(index)
        self.highlight_matches()
        self.reload_notes()
        self.update_passage_position()
        self.sync_tree_selection()

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

    def update_passage_position(self):
        scope = self._navigation or {}
        label = "this document" if scope.get("document_id") else "all matching documents"
        self.passage_position.setText(
            f"Passage {self.match_index + 1} of {len(self.matches)} · {label}"
            if self.match_index >= 0 else "No passage selected")
        self.previous_passage.setEnabled(bool(self.matches))
        self.next_passage.setEnabled(bool(self.matches))

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
        self.range_themes.setText(("Model suggestion" if origin == "model" else "Researcher coding") +
                                 "\nThemes: " + ", ".join(themes_for_range(self.directory, doc_id, start, end, self.run_id)))
        self.reload_notes()
        self.sync_tree_selection()
        self.update_passage_position()
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
            fmt.setBackground(QColor("#edb95c" if i == self.match_index else "#ffe5a4"))
            selection.format = fmt
            selections.append(selection)
        self.viewer.setExtraSelections(selections)
