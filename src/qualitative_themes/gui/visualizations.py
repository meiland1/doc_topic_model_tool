"""Offline, built-in BERTopic plots and evidence navigation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QLabel, QTabWidget, QPlainTextEdit, QSplitter, QListWidget, QListWidgetItem)
from qualitative_themes.analysis.topics import AnalysisCancelled, list_analysis_runs
from qualitative_themes.analysis.visualizations import build_visualizations
from qualitative_themes.storage.history import analysis_title


class VisualizationWorker(QThread):
    progress = Signal(str)
    result = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, directory, selected_run, parent=None):
        super().__init__(parent)
        self.directory, self.selected_run = directory, selected_run
        self.run_id = None

    def run(self):
        try:
            result = build_visualizations(self.directory, self.selected_run,
                self.progress.emit, self.isInterruptionRequested)
            self.result.emit(result)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class VisualizationTab(QWidget):
    build_requested = Signal()
    cancel_requested = Signal()
    evidence_requested = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.directory = None
        self.result_data = None
        self.viewers = {}
        self._temporary = TemporaryDirectory(prefix='topic-views-')
        self._profile = None
        outer = QVBoxLayout(self)
        actions = QHBoxLayout()
        self.run_choice = QComboBox()
        self.run_choice.setAccessibleName('Analysis to visualize')
        self.run_choice.currentIndexChanged.connect(self.invalidate)
        actions.addWidget(self.run_choice, 1)
        self.build_button = QPushButton('Build / Refresh visualizations')
        self.build_button.clicked.connect(lambda _checked=False: self.build_requested.emit())
        self.build_button.setEnabled(False)
        actions.addWidget(self.build_button)
        self.cancel_button = QPushButton('Cancel')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
        actions.addWidget(self.cancel_button)
        outer.addLayout(actions)
        note = QLabel('Document map: points represent coded passages, with document names in hover details. '
            'Multi-theme ranges appear in each theme at the same position. '
            'The heatmap and hierarchy describe current evidence similarity; hierarchy branches are exploratory groups, not saved parent themes. '
            'Marker-only evidence is excluded from model views. Refresh after coding, renaming, merging, or refinement.')
        note.setWordWrap(True)
        outer.addWidget(note)
        self.status = QLabel('Open a project and choose a saved analysis.')
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        split = QSplitter()
        self.plots = QTabWidget()
        self.pages = {}
        for key, title in [('map','Document map'), ('heatmap','Heatmap'), ('hierarchy','Theme hierarchy')]:
            page = QWidget()
            layout = QVBoxLayout(page)
            message = QLabel('Build visualizations to see this view.')
            message.setWordWrap(True)
            layout.addWidget(message)
            self.pages[key] = (page, layout, message)
            self.plots.addTab(page, title)
        self.tree = QPlainTextEdit()
        self.tree.setReadOnly(True)
        self.tree.setAccessibleName('Text based topic tree')
        self.plots.addTab(self.tree, 'Text topic tree')
        split.addWidget(self.plots)
        details = QWidget()
        body = QVBoxLayout(details)
        body.addWidget(QLabel('Themes and evidence'))
        self.themes = QListWidget()
        self.themes.setAccessibleName('Visualized themes')
        self.themes.currentItemChanged.connect(self.select_theme)
        body.addWidget(self.themes)
        self.theme_details = QLabel('Select a theme to inspect its evidence.')
        self.theme_details.setTextFormat(Qt.TextFormat.PlainText)
        self.theme_details.setWordWrap(True)
        body.addWidget(self.theme_details)
        self.evidence_button = QPushButton('Open evidence in Research Workspace')
        self.evidence_button.setEnabled(False)
        self.evidence_button.clicked.connect(self.open_evidence)
        body.addWidget(self.evidence_button)
        split.addWidget(details)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        outer.addWidget(split, 1)

    def set_project(self, directory):
        self.run_choice.clear()
        self.directory = directory
        self.reload_runs()

    def reload_runs(self, preferred=None):
        selected = preferred or self.run_choice.currentData()
        self.run_choice.blockSignals(True)
        self.run_choice.clear()
        if self.directory:
            used = {}
            for rid, completed, settings in list_analysis_runs(self.directory):
                title = analysis_title(settings, completed)
                used[title] = used.get(title, 0)+1
                self.run_choice.addItem(title if used[title] == 1 else f'{title} ({used[title]})', rid)
        index = self.run_choice.findData(selected)
        self.run_choice.setCurrentIndex(max(0,index))
        self.run_choice.blockSignals(False)
        self.invalidate()

    def set_busy(self, busy):
        self.run_choice.setEnabled(not busy)
        self.build_button.setEnabled(bool(self.directory and self.run_choice.currentData()) and not busy)
        self.evidence_button.setEnabled(bool(self.themes.currentItem()) and not busy)
        self.cancel_button.setEnabled(False)

    def invalidate(self, _index=None):
        self.result_data = None
        self.themes.clear()
        self.tree.clear()
        self.theme_details.setText('Select a theme to inspect its evidence.')
        self.evidence_button.setEnabled(False)
        for key, (page, layout, message) in self.pages.items():
            if key in self.viewers:
                self.viewers[key].hide()
                self.viewers[key].setUrl(QUrl('about:blank'))
            message.setText('Build visualizations to see this view.')
            message.show()
        self.status.setText('Ready to build the selected analysis.' if self.run_choice.currentData() else 'No completed analyses are available.')
        self.build_button.setEnabled(bool(self.run_choice.currentData()))

    def _viewer(self, key):
        if key in self.viewers:
            return self.viewers[key]
        try:
            from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineUrlRequestInterceptor
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError as exc:
            raise ValueError('Qt WebEngine is unavailable. Install the full PySide6 package in your environment to display interactive plots.') from exc
        if self._profile is None:
            class OfflineOnly(QWebEngineUrlRequestInterceptor):
                def interceptRequest(self, request):
                    if request.requestUrl().scheme() not in {'file','data','blob','about','qrc'}:
                        request.block(True)
            self._profile = QWebEngineProfile(self)
            self._interceptor = OfflineOnly(self._profile)
            self._profile.setUrlRequestInterceptor(self._interceptor)
        viewer = QWebEngineView()
        viewer.setPage(QWebEnginePage(self._profile, viewer))
        self.pages[key][1].addWidget(viewer, 1)
        self.viewers[key] = viewer
        return viewer

    def display_result(self, result):
        self.result_data = result
        for key, (page, layout, message) in self.pages.items():
            if key in result['html']:
                path = Path(self._temporary.name)/(key+'.html')
                path.write_text(result['html'][key], encoding='utf-8')
                try:
                    viewer = self._viewer(key)
                    viewer.load(QUrl.fromLocalFile(str(path)))
                    viewer.show(); message.hide()
                except ValueError as exc:
                    message.setText(str(exc)); message.show()
            else:
                message.setText(result['messages'].get(key,'No plot available.')); message.show()
        self.tree.setPlainText(result['tree'])
        self.themes.clear()
        snapshot = result['snapshot']
        for tid in snapshot['theme_topics']:
            theme = snapshot['themes'][tid]
            item = QListWidgetItem(f"{theme['theme']} · {theme['unique_coded_ranges']} ranges")
            item.setData(Qt.ItemDataRole.UserRole, tid)
            self.themes.addItem(item)
        if self.themes.count(): self.themes.setCurrentRow(0)
        self.status.setText('Built from current effective coding. Map uses a 2D PCA projection of local embeddings; '
            'heatmap and hierarchy use rebuilt c-TF-IDF. No saved coding or theme assignments were changed.')

    def select_theme(self, item, _previous=None):
        self.evidence_button.setEnabled(bool(item))
        if not item or not self.result_data: return
        theme = self.result_data['snapshot']['themes'][item.data(Qt.ItemDataRole.UserRole)]
        self.theme_details.setText(f"{theme['theme']}\n{theme['unique_coded_ranges']} ranges · {theme['documents_covered']} documents\nKeywords: " + ', '.join(theme['keywords']))

    def open_evidence(self):
        item = self.themes.currentItem()
        if item and self.result_data:
            self.evidence_requested.emit(self.run_choice.currentData(), item.data(Qt.ItemDataRole.UserRole))


    def dispose(self):
        # Delete WebEngine pages before the shared off-the-record profile.
        for viewer in self.viewers.values():
            viewer.stop()
            viewer.page().deleteLater()
            viewer.deleteLater()
        self.viewers.clear()
        if self._profile is not None:
            self._profile.deleteLater()
            self._profile = None
        self._temporary.cleanup()
