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

from PySide6.QtWidgets import (QMainWindow, QMessageBox, QTabWidget, QVBoxLayout, QWidget)

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
        from qualitative_themes.gui.title_bar import TitleBar
        frame = QWidget()
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)
        self.title_bar = TitleBar()
        self.title_bar.recent_requested.connect(self.open_recent)
        self.title_bar.open_requested.connect(self.open)
        self.title_bar.new_requested.connect(self.create)
        self.title_bar.start_screen_requested.connect(self.show_hub)
        self.title_bar.current_requested.connect(self.back_to_project)
        frame_layout.addWidget(self.title_bar)
        self.pages = QStackedWidget()
        frame_layout.addWidget(self.pages, 1)
        self.setCentralWidget(frame)
        self.hub = Hub()
        self.hub.back_requested.connect(self.back_to_project)
        self.hub.guide_requested.connect(self.show_quick_guide)
        self.hub.new_requested.connect(self.create)
        self.hub.practice_requested.connect(self.start_practice)
        self.hub.open_requested.connect(self.open)
        self.hub.restore_requested.connect(self.restore_project)
        self.hub.recent_requested.connect(self.open_recent)
        self.hub.setup.repair_requested.connect(self.repair_setup)
        self.hub.setup.add_model_requested.connect(self.add_model_pack)
        self.pages.addWidget(self.hub)
        from qualitative_themes.gui.project_guide import ProjectGuide
        self.quick_guide = ProjectGuide(on_start_screen=True)
        self.quick_guide.action_requested.connect(self.guide_action)
        self.pages.addWidget(self.quick_guide)
        root = QWidget()
        self.project_page = root
        self.pages.addWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 4, 12, 8)
        layout.setSpacing(6)
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
        self._build_documents_tab(tab_layout)
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
