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

from qualitative_themes.analysis.segment import UNIT_LABELS
from qualitative_themes.gui import strings

NO_ANALYSIS_STATUS = "No analysis: only themes and coding you added"


class AnalysisBarMixin:
    def _build_analysis_header(self, outer):
        """One row above the panels: Showing [analysis], Adjust themes, names, reviewed %, and ⋯.

        Rename, History and Review adjusted themes stay as (hidden) buttons
        because MainWindow connects to and enables them; the ⋯ menu forwards to
        them (see link_menu_action / sync_action_menus).
        """
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Showing"))
        self.run_choice = QComboBox()
        self.run_choice.setAccessibleName("Analysis shown")
        self.run_choice.currentIndexChanged.connect(self.change_run)
        bar.addWidget(self.run_choice, 1)
        self.refine_button = QPushButton(strings.ADJUST_THEMES + "…")
        self.refine_button.setEnabled(False)
        bar.addWidget(self.refine_button)
        self.name_run_button = QPushButton("Suggest new names")
        self.name_run_button.setEnabled(False)
        bar.addWidget(self.name_run_button)
        self.reviewed_label = QLabel("")
        self.reviewed_label.setObjectName("quiet")
        self.reviewed_label.setToolTip("Share of this analysis's passages you have stepped through or marked.")
        bar.addWidget(self.reviewed_label)
        self.cancel_task_button = QPushButton("Cancel")
        self.cancel_task_button.setEnabled(False)
        bar.addWidget(self.cancel_task_button)
        self.rename_analysis_button = QPushButton("Rename analysis…")
        self.history_button = QPushButton("History…")
        self.review_proposal_button = QPushButton("Review adjusted themes…")
        for button in (self.rename_analysis_button, self.history_button, self.review_proposal_button):
            button.setEnabled(False)
            button.hide()
        self.rename_analysis_button.clicked.connect(self.rename_selected_analysis)
        self.analysis_actions_button = QToolButton()
        self.analysis_actions_button.setText("⋯")
        self.analysis_actions_button.setAccessibleName("More analysis actions")
        self.analysis_actions_button.setToolTip("History, details and name of this analysis")
        self.analysis_actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.analysis_menu = QMenu(self.analysis_actions_button)
        self.analysis_actions_button.setMenu(self.analysis_menu)
        self._menu_links = []
        self.link_menu_action(self.analysis_menu, self.history_button)
        self.details_action = self.analysis_menu.addAction("About this analysis…")
        self.details_action.triggered.connect(self.show_analysis_details)
        self.link_menu_action(self.analysis_menu, self.rename_analysis_button)
        self.link_menu_action(self.analysis_menu, self.review_proposal_button)
        self.analysis_menu.addSeparator()
        self.naming_options_action = self.analysis_menu.addAction("Naming model…")
        self.naming_options_action.setCheckable(True)
        self.analysis_menu.aboutToShow.connect(self.sync_action_menus)
        bar.addWidget(self.analysis_actions_button)
        outer.addLayout(bar)

        # Naming model row, shown from ⋯ → Naming model. model_toggle is the switch
        # MainWindow reads; it never appears itself.
        self.model_toggle = QCheckBox("Theme names")
        self.model_toggle.hide()
        self.naming_options = QWidget()
        options = QHBoxLayout(self.naming_options)
        options.setContentsMargins(0, 0, 0, 0)
        options.addWidget(QLabel("Naming model"))
        self.ollama_model_choice = QComboBox()
        options.addWidget(self.ollama_model_choice)
        self.refresh_models_button = QPushButton("Refresh list")
        options.addWidget(self.refresh_models_button)
        self.ollama_status = QLabel("Refresh to check which naming models are installed.")
        self.ollama_status.setWordWrap(True)
        options.addWidget(self.ollama_status, 1)
        self.naming_options.setVisible(False)
        self.model_toggle.toggled.connect(self.naming_options.setVisible)
        self.naming_options_action.toggled.connect(self.model_toggle.setChecked)
        outer.addWidget(self.naming_options)
        # Shown in About this analysis rather than on screen.
        self.method_status = QLabel(NO_ANALYSIS_STATUS)
        self.method_status.setTextFormat(Qt.TextFormat.PlainText)
        self.method_status.setAccessibleName("Selected analysis method")
        self.naming_status = QPlainTextEdit()
        self.naming_status.setReadOnly(True)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        self.sync_action_menus()

    def update_reviewed(self):
        """The 'Reviewed N%' figure in the bar. Blank for My coding only."""
        if not self.directory or not self.run_id:
            self.reviewed_label.setText("")
            return
        from qualitative_themes.storage.reading import coverage
        share = coverage(self.directory, self.run_id)["share"]
        self.reviewed_label.setText(f"Reviewed {share:.0%}")

    def link_menu_action(self, menu, button):
        action = menu.addAction(button.text())
        action.triggered.connect(lambda _checked=False, target=button: target.click())
        self._menu_links.append((action, button))

    def sync_action_menus(self):
        for action, button in self._menu_links:
            action.setEnabled(button.isEnabled())
        self.naming_options_action.setEnabled(bool(self.directory))
        self.naming_options_action.blockSignals(True)
        self.naming_options_action.setChecked(self.model_toggle.isChecked())
        self.naming_options_action.blockSignals(False)
        self.details_action.setEnabled(bool(self.directory))

    def show_analysis_details(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About this analysis")
        dialog.resize(650, 500)
        layout = QVBoxLayout(dialog)
        details = QPlainTextEdit()
        details.setReadOnly(True)
        summary = self.method_status.toolTip() or self.method_status.text()
        naming = self.naming_status.toPlainText()
        used = ""
        if self.directory and self.run_id:
            from qualitative_themes.analysis.settings import settings_report
            with connect(self.directory) as conn:
                row = conn.execute("SELECT settings_json, model_info_json FROM analysis_runs WHERE id=?",
                                   (self.run_id,)).fetchone()
            if row:
                used = "\n\n" + settings_report(json.loads(row[0]), json.loads(row[1] or "{}"))
        details.setPlainText(summary + used + ("\n\n" + naming if naming else ""))
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        dialog.deleteLater()

    def reload_runs(self, selected=None):
        """Refill the analysis dropdown (newest first, then 'My coding only') and select `selected`."""
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
                f"Method: {method_label(values)}\nCompleted (UTC): {completed}\nHow documents were split: {UNIT_LABELS.get(values.get('analysis_unit'), 'Not recorded')}\n"
                f"Style: {values.get('analysis_style', 'Not recorded')}", Qt.ItemDataRole.ToolTipRole)
        self.run_choice.addItem(strings.NO_ANALYSIS, None)
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
            self.method_status.setText(NO_ANALYSIS_STATUS)
            self.method_status.setToolTip("Researcher-led coding without an active model run.")
        if self.directory:
            self.naming_status.setPlainText(labeling_report(self.directory, self.run_id))
        self.update_reviewed()
