"""The main window: builds the tabs and routes navigation between them.

MainWindow is the hub for everything else in gui/: it builds the tabs,
owns the worker threads, and routes navigation between tabs (search
results, overview cells and guided reading all open in the workspace).

Its methods are split by job into mixins, one file each:
  project_session.py  open, create, switch, back up and restore projects
  documents_page.py   the Documents tab
  local_setup.py      the naming engine and model packs
  tasks.py            background tasks (analysis, naming, refinement, charts, exports)
  workers.py          the worker threads those tasks run on
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from qualitative_themes.domain.models import Project
from qualitative_themes.analysis.segment import DEFAULT_UNIT
from qualitative_themes.gui.task_progress import TaskProgress
from qualitative_themes.gui.read_code import CodingWorkspace
from qualitative_themes.analysis.settings import AnalysisSettings
from qualitative_themes.gui.visualizations import VisualizationTab


from qualitative_themes.gui.documents_page import DocumentsPageMixin
from qualitative_themes.gui.local_setup import LocalSetupMixin
from qualitative_themes.gui.project_session import ProjectSessionMixin
from qualitative_themes.gui.tasks import TasksMixin
from qualitative_themes.gui.workers import ImportWorker
from qualitative_themes.gui import strings
from qualitative_themes.gui.menus import build_menus


class MainWindow(ProjectSessionMixin, DocumentsPageMixin, LocalSetupMixin, TasksMixin, QMainWindow):
    """Top-level window: start screen (Hub) and the project page with its tabs.

    Tabs: Documents & analysis, Themes, Read & code, Charts, Help.
    The window owns all background work. Only one long task runs at a time
    (stored in self._analysis_worker, which also holds visualization, export
    and refinement workers); `busy` is what every action checks first.

    Typical flow:
      create()/open() -> _show_project() -> import_files() -> start_analysis()
      -> AnalysisWorker -> analysis_ready() -> Overview/Workspace refresh
    """
    def __init__(self):
        super().__init__()
        self.project: Project | None = None
        self._worker: ImportWorker | None = None
        self.analysis_unit = DEFAULT_UNIT
        self._analysis_worker = None
        self._ollama_worker = None
        self.analysis_settings = AnalysisSettings()
        self._close_after_analysis = False
        self.setWindowTitle("Topic Model Tool")
        self.resize(1200, 850)
        from PySide6.QtWidgets import QStackedWidget
        from qualitative_themes.gui.hub import Hub
        self.pages = QStackedWidget()
        self.setCentralWidget(self.pages)
        self.hub = Hub()
        self.hub.new_requested.connect(self.create)
        self.hub.practice_requested.connect(self.start_practice)
        self.hub.open_requested.connect(self.open)
        self.hub.restore_requested.connect(self.restore_project)
        self.hub.recent_requested.connect(self.open_recent)
        self.hub.setup.repair_requested.connect(self.repair_setup)
        self.hub.setup.add_model_requested.connect(self.add_model_pack)
        self.pages.addWidget(self.hub)
        root = QWidget()
        self.project_page = root
        self.pages.addWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        title = QLabel("Topic Model Tool")
        title.setObjectName("applicationTitle")
        header_row = QHBoxLayout()
        self.home_button = QPushButton("← Home")
        self.home_button.setToolTip("Back to the start screen: recent projects and local setup")
        self.home_button.clicked.connect(self.show_hub)
        header_row.addWidget(self.home_button)
        header_row.addWidget(title)
        self.status = QLabel("Create or open a project to begin.")
        self.status.setObjectName("applicationStatus")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(self.status, 1)
        self.offline_badge = QLabel("Offline · nothing leaves this computer")
        self.offline_badge.setObjectName("offlineBadge")
        self.offline_badge.setToolTip("The app makes no network requests. The local AI engine only accepts "
                                      "connections from this computer.")
        header_row.addWidget(self.offline_badge)
        layout.addLayout(header_row)
        self.task_progress = TaskProgress()
        self.task_progress.cancel_requested.connect(self.cancel_analysis)
        layout.addWidget(self.task_progress)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # Tool Navigation tabs
        documents_tab = QWidget()
        tab_layout = QVBoxLayout(documents_tab)
        self.tabs.addTab(documents_tab, strings.TAB_DOCUMENTS)
        from qualitative_themes.gui.overview import OverviewTab
        self.overview = OverviewTab()
        self.overview.start_reading.connect(self.open_guided_reading)
        self.overview.guided_reading.connect(lambda: self.open_guided_reading(None))
        self.overview.open_cell.connect(self.open_overview_cell)
        self.tabs.addTab(self.overview, strings.TAB_THEMES)
        self.workspace = CodingWorkspace()
        self.tabs.addTab(self.workspace, strings.TAB_READ_CODE)
        self.workspace.run_choice.currentIndexChanged.connect(self.sync_overview_run)
        self.workspace.search_requested.connect(self.open_search)
        self.visualizations = VisualizationTab()
        self.tabs.addTab(self.visualizations, strings.TAB_CHARTS)
        self.visualizations.build_requested.connect(self.start_visualizations)
        self.visualizations.cancel_requested.connect(self.cancel_analysis)
        self.visualizations.evidence_requested.connect(self.open_visualization_evidence)
        self.tabs.currentChanged.connect(self.refresh_visualization_runs)
        from qualitative_themes.gui.project_guide import ProjectGuide
        self.project_guide = ProjectGuide()
        self.project_guide.action_requested.connect(self.guide_action)
        self.tabs.addTab(self.project_guide, strings.TAB_HELP)
        actions = QHBoxLayout()

        # Creating and opening projects are in the File menu; only adding documents stays here.
        self.import_button = QPushButton(strings.ADD_DOCUMENTS)
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.import_files)
        actions.addWidget(self.import_button)

        actions.addStretch()
        tab_layout.addLayout(actions)

        # Buttons for running an analysis
        analysis_actions = QHBoxLayout()
        self.analyze_button = QPushButton("▶  " + strings.FIND_THEMES)
        self.analyze_button.clicked.connect(self.start_analysis)
        self.analyze_button.setProperty("primary", True)
        self.analyze_button.setEnabled(False)
        analysis_actions.addWidget(self.analyze_button)

        # Cancel an analysis
        self.cancel_analysis_button = QPushButton("Cancel")
        self.cancel_analysis_button.clicked.connect(self.cancel_analysis)
        self.cancel_analysis_button.setEnabled(False)
        analysis_actions.addWidget(self.cancel_analysis_button)
        tab_layout.addLayout(analysis_actions)
        self.rename_run_button = self.workspace.name_run_button
        self.rename_run_button.clicked.connect(self.name_selected_run)
        self.ollama_model_choice = self.workspace.ollama_model_choice
        self.refresh_models_button = self.workspace.refresh_models_button
        self.refresh_models_button.clicked.connect(self.refresh_ollama_models)
        self.ollama_status = self.workspace.ollama_status
        self.workspace.cancel_task_button.clicked.connect(self.cancel_analysis)
        self.workspace.run_choice.currentIndexChanged.connect(self.update_naming_controls)
        self.workspace.refine_button.clicked.connect(lambda: self.start_refinement())
        self.workspace.review_proposal_button.clicked.connect(self.review_pending_refinement)
        self.workspace.history_button.clicked.connect(self.show_analysis_history)
        self.contents = QSplitter()

        # Project Document file viewer/selector
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.addWidget(QLabel("Documents"))
        self.document_list = QListWidget()
        self.document_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.document_list.setToolTip("Select the documents to include. Themes are found across all selected documents together.")
        self.document_list.currentItemChanged.connect(self.show_document)
        self.document_list.itemSelectionChanged.connect(self.update_document_actions)
        left_lay.addWidget(self.document_list)
        selection_actions = QHBoxLayout()
        self.select_all_documents_button = QPushButton("Select all")
        self.select_all_documents_button.setEnabled(False)
        self.select_all_documents_button.setToolTip("Select every document, to find themes across all of them together.")
        self.select_all_documents_button.clicked.connect(self.document_list.selectAll)
        selection_actions.addWidget(self.select_all_documents_button)
        self.clear_document_selection_button = QPushButton("Select none")
        self.clear_document_selection_button.setEnabled(False)
        self.clear_document_selection_button.clicked.connect(self.document_list.clearSelection)
        selection_actions.addWidget(self.clear_document_selection_button)
        left_lay.addLayout(selection_actions)
        self.document_selection_count = QLabel("0 of 0 documents selected")
        left_lay.addWidget(self.document_selection_count)
        self.remove_documents_button = QPushButton("Remove from project…")
        self.remove_documents_button.setEnabled(False)
        self.remove_documents_button.clicked.connect(self.remove_selected_documents)
        left_lay.addWidget(self.remove_documents_button)
        self.contents.addWidget(left)
        right = QWidget()
        right_lay = QVBoxLayout(right)
        self.preview_title = QLabel("Add documents to see their text here.")
        right_lay.addWidget(self.preview_title)

        # Extracted text of the current document
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        right_lay.addWidget(self.preview)
        self.contents.addWidget(right)
        self.contents.setStretchFactor(1, 3)
        tab_layout.addWidget(self.contents, 1)
        self.import_status = QLabel("")
        self.import_status.setWordWrap(True)
        tab_layout.addWidget(self.import_status)
        self.import_log = QListWidget()
        self.import_log.setMaximumHeight(105)
        self.import_log.setAccessibleName("Document import results")
        tab_layout.addWidget(self.import_log)
        self.hub.refresh_setup()

        build_menus(self)


    # Functions

    @property
    def busy(self) -> bool:
        """True while any background task runs; every action checks this one flag."""
        return bool(self._analysis_worker or self._worker or self._ollama_worker)

    def run_in_workspace(self, method):
        """Show the Read & code tab, then call `method` (Edit menu items act there)."""
        if self.project is None:
            return
        self.tabs.setCurrentWidget(self.workspace)
        method()

    def blocked_by_task(self, action: str) -> bool:
        """True (after telling the researcher why) if a background task is running.

        `action` finishes the sentence "Finish the current task before …".
        """
        if self.busy:
            QMessageBox.information(self, "Task in progress", f"Finish the current task before {action}.")
            return True
        return False

    def closeEvent(self, event):
        """Don't close until background work is safe to drop.

        An active analysis is cancelled first and the window closes itself from
        analysis_finished() (via _close_after_analysis). Unsaved comment drafts
        are flushed or the close is refused.
        """
        if self.recovery.running:
            event.ignore()
            return
        if not self.flush_comments():
            event.ignore()
            return
        for name in ("_engine_worker", "_pack_worker"):
            worker = getattr(self, name, None)
            if worker:
                worker.wait()
        if self._ollama_worker:
            self._close_after_analysis = True
            event.ignore()
            return
        if self._analysis_worker:
            self._close_after_analysis = True
            self.cancel_analysis()
            self.import_status.setText("Waiting for the active analysis stage to finish before closing…")
            event.ignore()
            return
        if self._worker and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait()
        self.visualizations.dispose()
        self.save_reading_position()
        self.recovery.timer.stop()
        super().closeEvent(event)

    def sync_overview_run(self, _index=-1):
        if self.project:
            self.overview.set_run(self.project.directory, self.workspace.run_id)

    def open_guided_reading(self, theme_id=None):
        if not self.project or not self.overview.data:
            return
        from qualitative_themes.gui.reading_mode import GuidedReadingDialog
        if getattr(self, "_reading", None) is not None:
            self._reading.close()
            self._reading.deleteLater()
        self._reading = GuidedReadingDialog(self.project.directory, self.workspace.run_id,
                                            self.overview.data["themes"], theme_id, self)
        self._reading.open_range.connect(self.open_reading_passage)
        self._reading.finished.connect(lambda _r: self.overview.set_run(self.project.directory, self.workspace.run_id))
        self._reading.show()

    def open_reading_passage(self, document_id, start, end, theme_id):
        self.tabs.setCurrentWidget(self.workspace)
        self.workspace.open_passage(theme_id, document_id, start, end)

    def open_overview_cell(self, theme_id, document_id):
        self.tabs.setCurrentWidget(self.workspace)
        self.workspace.open_group(theme_id, document_id)

    def open_search(self):
        if not self.project:
            return
        from qualitative_themes.gui.search_dialog import SearchDialog
        if getattr(self, "_search", None) is None:
            self._search = SearchDialog(self.project.directory, self.workspace.run_id, self)
            self._search.open_result.connect(self.open_search_result)
            self._search_project = self.project.directory
        elif self._search_project != self.project.directory:
            self._search.deleteLater()
            self._search = None
            return self.open_search()
        self._search.run_id = self.workspace.run_id
        self._search.show()
        self._search.raise_()
        self._search.field.setFocus()
        self._search.field.selectAll()

    def open_search_result(self, document_id, start, end):
        self.tabs.setCurrentWidget(self.workspace)
        self.workspace.open_location(document_id, start, end)
