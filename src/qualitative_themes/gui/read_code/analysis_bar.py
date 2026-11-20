"""The analysis bar: which analysis is shown, and the actions that work on it.

Part of CodingWorkspace (see workspace.py): these methods use the shared
state documented there and are not meant to be used on their own.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QInputDialog, QLabel,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QDialog, QDialogButtonBox, QMenu, QToolButton,
                               QVBoxLayout, QWidget)

from qualitative_themes.analysis.topics import list_analysis_runs
from qualitative_themes.storage.history import analysis_title, rename_analysis
from qualitative_themes.storage.provenance import method_summary, method_label
from qualitative_themes.storage.database import connect
from qualitative_themes.analysis.labels import labeling_report
import json


class AnalysisBarMixin:
    def _build_analysis_header(self, outer):
        """Analysis picker and task buttons above the panels (compact_header() folds most of them into menus)."""
        # Header area for Research Workspace
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
        self._old_header_rows = [run_actions, research_actions, naming_actions, refinement_actions]
        self._old_header_widgets = [header, self.help, self.export_help, self.model_toggle, self.naming_toggle]

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

    def reload_runs(self, selected=None):
        """Refill the analysis dropdown (newest first, then 'Manual coding only') and select `selected`."""
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
        """Selected analysis changed: reset scope, then reload documents and themes for that run."""
        self.run_id = self.run_choice.currentData()
        self._navigation = None
        self._draft_theme_ids.clear()
        self.matches = []
        self.match_index = -1
        self.reload_documents(rebuild_trees=False)
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
